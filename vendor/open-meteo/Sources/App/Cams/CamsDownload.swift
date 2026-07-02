import Foundation
import Vapor
import SwiftNetCDF
import OmFileFormat

/// Download CAMS Europe and Global air quality forecasts
struct DownloadCamsCommand: AsyncCommand {
    struct Signature: CommandSignature {
        @Argument(name: "domain")
        var domain: String

        @Option(name: "run")
        var run: String?

        @Flag(name: "skip-existing", help: "ONLY FOR TESTING! Do not use in production. May update the database with stale data")
        var skipExisting: Bool

        @Option(name: "only-variables")
        var onlyVariables: String?

        @Option(name: "ftpuser", short: "u", help: "Username for the ECMWF CAMS FTP server")
        var ftpuser: String?

        @Option(name: "ftppassword", short: "p", help: "Password for the ECMWF CAMS FTP server")
        var ftppassword: String?

        @Option(name: "upload-s3-bucket", help: "Upload open-meteo database to an S3 bucket after processing")
        var uploadS3Bucket: String?

        @Flag(name: "create-netcdf")
        var createNetcdf: Bool

        @Option(name: "concurrent", short: "c", help: "Numer of concurrent download/conversion jobs")
        var concurrent: Int?
    }

    var help: String {
        "Download global CAMS air quality forecasts from ECMWF FTP/ECPDS"
    }

    func run(using context: CommandContext, signature: Signature) async throws {
        disableIdleSleep()

        let domain = try CamsDomain.load(rawValue: signature.domain)

        let run = try signature.run.flatMap(Timestamp.fromRunHourOrYYYYMMDD) ?? domain.lastRun

        let onlyVariables = try CamsVariable.load(commaSeparatedOptional: signature.onlyVariables)

        let logger = context.application.logger
        logger.info("Downloading domain '\(domain.rawValue)' run '\(run.iso8601_YYYY_MM_dd_HH_mm)'")

        let variables = onlyVariables ?? CamsVariable.allCases
        switch domain {
        case .cams_global:
            let envFtpUser = WeatherForecastServerSourceConfig.string("WEATHER_CAMS_FTP_USER", fallback: "")
            let envFtpPassword = WeatherForecastServerSourceConfig.string("WEATHER_CAMS_FTP_PASSWORD", fallback: "")
            let ftpuser = signature.ftpuser ?? (envFtpUser.isEmpty ? nil : envFtpUser)
            let ftppassword = signature.ftppassword ?? (envFtpPassword.isEmpty ? nil : envFtpPassword)

            guard let ftpuser = ftpuser, let ftppassword = ftppassword, !ftpuser.isEmpty, !ftppassword.isEmpty else {
                fatalError("Both WEATHER_CAMS_FTP_USER and WEATHER_CAMS_FTP_PASSWORD are required for CAMS global FTP/ECPDS download")
            }
            logger.info("Using CAMS global FTP/ECPDS source")
            let handles = try await downloadCamsGlobal(application: context.application, domain: domain, run: run, variables: variables, user: ftpuser, password: ftppassword, uploadS3Bucket: signature.uploadS3Bucket)
            try await GenericVariableHandle.convert(logger: logger, domain: domain, createNetcdf: signature.createNetcdf, run: run, handles: handles, concurrent: signature.concurrent ?? 1, writeUpdateJson: true, uploadS3Bucket: signature.uploadS3Bucket, uploadS3OnlyProbabilities: false)
            return
        default:
            fatalError("download-cams only supports cams_global from ECMWF FTP/ECPDS")
        }
    }

    /// Download from the ECMWF CAMS ftp/http server
    /// This data is also available via the ADC API, but queue times are 4 hours!
    func downloadCamsGlobal(application: Application, domain: CamsDomain, run: Timestamp, variables: [CamsVariable], user: String, password: String, uploadS3Bucket: String?) async throws -> [GenericVariableHandle] {
        try FileManager.default.createDirectory(atPath: domain.downloadDirectory, withIntermediateDirectories: true)
        let logger = application.logger

        let nx = domain.grid.nx
        let ny = domain.grid.ny
        let regionalSlice = CamsRegionalDownload.slice(domain: domain)
        let sourceNx = regionalSlice?.fullNx ?? nx
        let sourceNy = regionalSlice?.fullNy ?? ny

        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, retryUnauthorized: true)
        Process.alarm(seconds: 6 * 3600)
        defer { Process.alarm(seconds: 0) }

        let dateRun = run.format_YYYYMMddHH
        let remoteDir = "https://\(user):\(password)@aux.ecmwf.int/ecpds/data/file/CAMS_GLOBAL/\(dateRun)/"
        /// The surface level of multi-level files is available in the `CAMS_GLOBAL_ADDITIONAL` directory
        let remoteDirAdditional = "https://\(user):\(password)@aux.ecmwf.int/ecpds/data/file/CAMS_GLOBAL_ADDITIONAL/\(dateRun)/"
        
        let timestamps = (0..<domain.forecastHours).map { run.add(hours: $0) }

        let handles = try await timestamps.enumerated().asyncFlatMap { (i,timestamp) -> [GenericVariableHandle] in
            let hour = (timestamp.timeIntervalSince1970 - run.timeIntervalSince1970) / 3600
            logger.info("Downloading hour \(hour)")
            let writer = OmSpatialTimestepWriter(domain: domain, run: run, time: timestamp, storeOnDisk: true, realm: nil, logger: logger)

            for variable in variables {
                guard let meta = variable.getCamsGlobalMeta() else {
                   continue
                }
                /// Multi level name `z_cams_c_ecmf_20220811120000_prod_fc_ml137_000_aermr03.nc`
                /// Surface level name `z_cams_c_ecmf_20220803000000_prod_fc_sfc_012_uvbed.nc`
                let levelType = meta.isMultiLevel ? "ml137" : "sfc"
                let dir = meta.isMultiLevel ? remoteDirAdditional : remoteDir
                let remoteFile = "\(dir)z_cams_c_ecmf_\(dateRun)0000_prod_fc_\(levelType)_\(hour.zeroPadded(len: 3))_\(meta.gribname).nc"
                let tempNc = "\(domain.downloadDirectory)/temp.nc"
                try await curl.download(url: remoteFile, toFile: tempNc, bzip2Decode: false)

                guard let ncFile = try NetCDF.open(path: tempNc, allowUpdate: false) else {
                    fatalError("Could not open nc file for \(variable)")
                }
                guard let ncVar = ncFile.getVariable(name: meta.gribname) else {
                    fatalError("Could not open nc variable for \(meta.gribname)")
                }

                var data = try ncVar.readLevel()
                data.shift180LongitudeAndFlipLatitude(nt: 1, ny: sourceNy, nx: sourceNx)
                if let regionalSlice = regionalSlice {
                    data = data.sliceGrid(x0: regionalSlice.x0, y0: regionalSlice.y0, nx: regionalSlice.nx, ny: regionalSlice.ny, sourceNx: sourceNx)
                }

                for i in data.indices {
                    data[i] *= meta.scalefactor
                }
                
                try await writer.write(member: 0, variable: variable, data: data)
            }
            let completed = i == timestamps.count - 1
            return try await writer.finalise(completed: completed, validTimes: Array(timestamps[0...i]), uploadS3Bucket: uploadS3Bucket)
        }
        await curl.printStatistics()
        return handles
    }

}

private struct CamsRegionalDownload {
    struct Slice {
        let fullNx: Int
        let fullNy: Int
        let x0: Int
        let y0: Int
        let nx: Int
        let ny: Int
    }

    static func slice(domain: CamsDomain) -> Slice? {
        guard domain == .cams_global else {
            return nil
        }
        let slice = WeatherForecastServerSourceConfig.regularGridSlice(
            fullNx: 900,
            fullNy: 451,
            latMin: -90,
            lonMin: -180,
            dx: 0.4,
            dy: 0.4,
            region: WeatherForecastServerSourceConfig.region
        )
        return Slice(fullNx: 900, fullNy: 451, x0: slice.x0, y0: slice.y0, nx: slice.nx, ny: slice.ny)
    }
}

private extension Array where Element == Float {
    func sliceGrid(x0: Int, y0: Int, nx: Int, ny: Int, sourceNx: Int) -> [Float] {
        var output = [Float](repeating: .nan, count: nx * ny)
        for y in 0..<ny {
            let sourceStart = (y0 + y) * sourceNx + x0
            let targetStart = y * nx
            output.replaceSubrange(targetStart..<(targetStart + nx), with: self[sourceStart..<(sourceStart + nx)])
        }
        return output
    }
}

fileprivate extension Variable {
    func readLevel() throws -> [Float] {
        /// m137 files are double... for whatever reason
        if let ncDouble = self.asType(Double.self) {
            guard dimensions.count == 3,
                    dimensions[0].length == 1,
                    dimensions[1].length == 451,
                    dimensions[2].length == 900 else {
                fatalError("Wrong dimensions. Got \(dimensions)")
            }
            return try ncDouble.read().map(Float.init)
        }

        guard let ncFloat = self.asType(Float.self) else {
            fatalError("Not a float nc variable")
        }
        if dimensions.count == 2 {
            // surface file
            guard dimensions.count == 2,
                    dimensions[0].length == 451,
                    dimensions[1].length == 900 else {
                fatalError("Wrong dimensions. Got \(dimensions)")
            }
            return try ncFloat.read()
        }
        if dimensions.count == 3 {
            // surface file, but with time inside...
            guard dimensions.count == 3,
                    dimensions[0].length == 1,
                    dimensions[1].length == 451,
                    dimensions[2].length == 900 else {
                fatalError("Wrong dimensions. Got \(dimensions)")
            }
            return try ncFloat.read(offset: [0, 0, 0], count: [1, dimensions[1].length, dimensions[2].length])
        }
        /*if dimensions.count == 4 {
            // pressure level file -> read `last` level e.g. 10 meter above ground
            // dimensions time, level, lat, lon
            precondition(dimensions[0].length == 0)
            precondition(dimensions[1].length > 10)
            precondition(dimensions[2].length > 200)
            precondition(dimensions[3].length > 200)
            return try ncFloat.read(offset: [0, dimensions[1].length-1,0,0], count: [1, 1, dimensions[2].length, dimensions[3].length])
        }*/
        fatalError("Wrong dimensions \(dimensionsFlat)")
    }
}
