import Foundation
import Vapor
import SwiftNetCDF
import OmFileFormat

/// Download CAMS Global air-quality forecasts from ECMWF ECPDS.
struct DownloadCamsCommand: AsyncCommand {
    /// Request schema used by the official CAMS ADS datasets.
    struct CamsEuropeQuery: Encodable {
        let model: [String]?
        let date: String?
        let type: [String]?
        let data_format: String?
        let variable: [String]
        let level: [String]?
        let time: String?
        let leadtime_hour: [String]?
        let year: [String]?
        let month: [String]?
        let model_level: [Int]?
    }

    struct Signature: CommandSignature {
        @Argument(name: "domain")
        var domain: String

        @Option(name: "run")
        var run: String?

        @Flag(name: "skip-existing", help: "ONLY FOR TESTING! Do not use in production. May update the database with stale data")
        var skipExisting: Bool

        @Option(name: "only-variables")
        var onlyVariables: String?

        @Option(name: "cdskey", short: "k", help: "ADS/CDS API key")
        var cdskey: String?

        @Option(name: "ftpuser", short: "u", help: "Username for the ECMWF CAMS ECPDS server")
        var ftpuser: String?

        @Option(name: "ftppassword", short: "p", help: "Password for the ECMWF CAMS ECPDS server")
        var ftppassword: String?

        @Option(name: "upload-s3-bucket", help: "Upload open-meteo database to an S3 bucket after processing")
        var uploadS3Bucket: String?

        @Flag(name: "create-netcdf")
        var createNetcdf: Bool

        @Option(name: "concurrent", short: "c", help: "Number of concurrent download/conversion jobs")
        var concurrent: Int?
    }

    var help: String {
        "Download global CAMS air-quality forecasts from ECMWF ECPDS"
    }

    func run(using context: CommandContext, signature: Signature) async throws {
        disableIdleSleep()

        let domain = try CamsDomain.load(rawValue: signature.domain)
        let run = try signature.run.flatMap(Timestamp.fromRunHourOrYYYYMMDD) ?? domain.lastRun
        let variables = try CamsVariable.load(commaSeparatedOptional: signature.onlyVariables) ?? CamsVariable.allCases
        let concurrent = signature.concurrent ?? 1
        context.application.logger.info("Downloading domain '\(domain.rawValue)' run '\(run.iso8601_YYYY_MM_dd_HH_mm)'")

        let handles: [GenericVariableHandle]
        switch domain {
        case .cams_global:
            guard let ftpuser = signature.ftpuser ?? ProcessInfo.processInfo.environment["WEATHER_CAMS_FTP_USER"],
                  !ftpuser.isEmpty else {
                throw Abort(.badRequest, reason: "ftpuser or WEATHER_CAMS_FTP_USER is required")
            }
            guard let ftppassword = signature.ftppassword ?? ProcessInfo.processInfo.environment["WEATHER_CAMS_FTP_PASSWORD"],
                  !ftppassword.isEmpty else {
                throw Abort(.badRequest, reason: "ftppassword or WEATHER_CAMS_FTP_PASSWORD is required")
            }
            handles = try await downloadCamsGlobal(
                application: context.application,
                domain: domain,
                run: run,
                variables: variables,
                user: ftpuser,
                password: ftppassword,
                concurrent: concurrent,
                uploadS3Bucket: signature.uploadS3Bucket
            )
        case .cams_global_greenhouse_gases:
            guard let cdskey = signature.cdskey ?? ProcessInfo.processInfo.environment["WEATHER_CAMS_ADS_KEY"],
                  !cdskey.isEmpty else {
                throw Abort(.badRequest, reason: "cdskey or WEATHER_CAMS_ADS_KEY is required")
            }
            handles = try await downloadCamsGlobalGreenhouseGases(
                application: context.application,
                domain: domain,
                run: run,
                skipFilesIfExisting: signature.skipExisting,
                variables: variables,
                cdskey: cdskey,
                concurrent: concurrent,
                uploadS3Bucket: signature.uploadS3Bucket
            )
        default:
            throw Abort(.badRequest, reason: "Only cams_global and cams_global_greenhouse_gases are enabled")
        }
        try await GenericVariableHandle.convert(
            application: context.application,
            domain: domain,
            createNetcdf: signature.createNetcdf,
            run: run,
            handles: handles,
            concurrent: concurrent,
            writeUpdateJson: true,
            uploadS3Bucket: signature.uploadS3Bucket,
            uploadS3OnlyProbabilities: false
        )
    }

    func downloadCamsGlobal(
        application: Application,
        domain: CamsDomain,
        run: Timestamp,
        variables: [CamsVariable],
        user: String,
        password: String,
        concurrent: Int,
        uploadS3Bucket: String?
    ) async throws -> [GenericVariableHandle] {
        try FileManager.default.createDirectory(atPath: domain.downloadDirectory, withIntermediateDirectories: true)
        let logger = application.logger

        let nx = domain.grid.nx
        let ny = domain.grid.ny
        let regionalSlice = domain.regionalDownloadSlice
        let sourceNx = regionalSlice?.fullNx ?? nx
        let sourceNy = regionalSlice?.fullNy ?? ny

        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, retryUnauthorized: true)
        Process.alarm(seconds: 6 * 3600)
        defer { Process.alarm(seconds: 0) }

        let dateRun = run.format_YYYYMMddHH
        let remoteDir = "https://\(user):\(password)@aux.ecmwf.int/ecpds/data/file/CAMS_GLOBAL/\(dateRun)/"
        let remoteDirAdditional = "https://\(user):\(password)@aux.ecmwf.int/ecpds/data/file/CAMS_GLOBAL_ADDITIONAL/\(dateRun)/"
        let timestamps = (0..<domain.forecastHours).map { run.add(hours: $0) }

        let handles = try await timestamps.enumerated().asyncFlatMap { (i, timestamp) -> [GenericVariableHandle] in
            let hour = (timestamp.timeIntervalSince1970 - run.timeIntervalSince1970) / 3600
            logger.info("Downloading hour \(hour)")
            let writer = OmSpatialTimestepWriter(domain: domain, run: run, time: timestamp, storeOnDisk: true, realm: nil, logger: logger)

            let jobs = variables.compactMap { variable -> CamsGlobalDownloadJob? in
                guard let meta = variable.getCamsGlobalMeta() else {
                    return nil
                }
                let levelType = meta.isMultiLevel ? "ml137" : "sfc"
                let dir = meta.isMultiLevel ? remoteDirAdditional : remoteDir
                let remoteFile = "\(dir)z_cams_c_ecmf_\(dateRun)0000_prod_fc_\(levelType)_\(hour.zeroPadded(len: 3))_\(meta.gribname).nc"
                let tempNc = "\(domain.downloadDirectory)/temp_\(hour.zeroPadded(len: 3))_\(meta.gribname).nc"
                return CamsGlobalDownloadJob(
                    variable: variable,
                    gribname: meta.gribname,
                    scalefactor: meta.scalefactor,
                    remoteFile: remoteFile,
                    tempNc: tempNc
                )
            }

            defer {
                for job in jobs {
                    try? FileManager.default.removeItem(atPath: job.tempNc)
                }
            }

            try await jobs.foreachConcurrent(nConcurrent: max(1, concurrent)) { job in
                try await curl.download(url: job.remoteFile, toFile: job.tempNc, bzip2Decode: false)
            }

            for job in jobs {
                guard let ncFile = try NetCDF.open(path: job.tempNc, allowUpdate: false) else {
                    fatalError("Could not open nc file for \(job.variable)")
                }
                guard let ncVar = ncFile.getVariable(name: job.gribname) else {
                    fatalError("Could not open nc variable for \(job.gribname)")
                }

                var data = try ncVar.readLevel()
                data.shift180LongitudeAndFlipLatitude(nt: 1, ny: sourceNy, nx: sourceNx)
                if let regionalSlice = regionalSlice {
                    data = data.sliceGrid(
                        x0: regionalSlice.x0,
                        y0: regionalSlice.y0,
                        nx: regionalSlice.nx,
                        ny: regionalSlice.ny,
                        sourceNx: sourceNx
                    )
                }
                for index in data.indices {
                    data[index] *= job.scalefactor
                }
                try await writer.write(member: 0, variable: job.variable, data: data)
            }

            let completed = i == timestamps.count - 1
            return try await writer.finalise(
                application: application,
                completed: completed,
                validTimes: Array(timestamps[0...i]),
                uploadS3Bucket: uploadS3Bucket
            )
        }
        await curl.printStatistics()
        return handles
    }

    struct CamsGlobalDownloadJob: Sendable {
        let variable: CamsVariable
        let gribname: String
        let scalefactor: Float
        let remoteFile: String
        let tempNc: String
    }
}

fileprivate extension Variable {
    func readLevel() throws -> [Float] {
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
            guard dimensions[0].length == 451,
                  dimensions[1].length == 900 else {
                fatalError("Wrong dimensions. Got \(dimensions)")
            }
            return try ncFloat.read()
        }
        if dimensions.count == 3 {
            guard dimensions[0].length == 1,
                  dimensions[1].length == 451,
                  dimensions[2].length == 900 else {
                fatalError("Wrong dimensions. Got \(dimensions)")
            }
            return try ncFloat.read(
                offset: [0, 0, 0],
                count: [1, dimensions[1].length, dimensions[2].length]
            )
        }
        fatalError("Wrong dimensions \(dimensionsFlat)")
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
