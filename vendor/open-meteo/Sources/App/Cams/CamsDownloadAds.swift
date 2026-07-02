import Foundation
import Vapor
import SwiftNetCDF
import OmFileFormat

/// Download CAMS products through ADS/CDS-compatible APIs.
struct DownloadCamsAdsCommand: AsyncCommand {
    struct Signature: CommandSignature {
        @Argument(name: "domain")
        var domain: String

        @Option(name: "run")
        var run: String?

        @Flag(name: "skip-existing", help: "ONLY FOR TESTING! Do not use in production. May update the database with stale data")
        var skipExisting: Bool

        @Option(name: "only-variables")
        var onlyVariables: String?

        @Option(name: "cdskey", short: "k", help: "CDS API key like: f412e2d2-4123-456...")
        var cdskey: String?

        @Option(name: "upload-s3-bucket", help: "Upload open-meteo database to an S3 bucket after processing")
        var uploadS3Bucket: String?

        @Option(name: "timeinterval", short: "t", help: "Timeinterval to download past forecasts. Format 20220101-20220131")
        var timeinterval: String?

        @Flag(name: "create-netcdf")
        var createNetcdf: Bool

        @Option(name: "concurrent", short: "c", help: "Numer of concurrent download/conversion jobs")
        var concurrent: Int?
    }

    var help: String {
        "Download CAMS air quality forecasts from ADS/CDS"
    }

    func run(using context: CommandContext, signature: Signature) async throws {
        disableIdleSleep()

        let domain = try CamsDomain.load(rawValue: signature.domain)
        let run = try signature.run.flatMap(Timestamp.fromRunHourOrYYYYMMDD) ?? domain.lastRun
        let onlyVariables = try CamsVariable.load(commaSeparatedOptional: signature.onlyVariables)
        let variables = onlyVariables ?? CamsVariable.allCases
        let logger = context.application.logger
        let envCdsKey = WeatherForecastServerSourceConfig.string(
            "WEATHER_CAMS_ADS_KEY",
            fallback: WeatherForecastServerSourceConfig.string("WEATHER_CAMS_CDS_KEY", fallback: "")
        )
        guard let cdskey = signature.cdskey ?? (envCdsKey.isEmpty ? nil : envCdsKey), !cdskey.isEmpty else {
            fatalError("WEATHER_CAMS_ADS_KEY/WEATHER_CAMS_CDS_KEY or --cdskey is required")
        }

        logger.info("Downloading domain '\(domain.rawValue)' run '\(run.iso8601_YYYY_MM_dd_HH_mm)' from ADS/CDS")

        switch domain {
        case .cams_global:
            let handles = try await downloadCamsGlobalArea(
                application: context.application,
                domain: domain,
                run: run,
                variables: variables,
                cdskey: cdskey,
                concurrent: signature.concurrent ?? 1,
                uploadS3Bucket: signature.uploadS3Bucket
            )
            try await GenericVariableHandle.convert(logger: logger, domain: domain, createNetcdf: signature.createNetcdf, run: run, handles: handles, concurrent: signature.concurrent ?? 1, writeUpdateJson: true, uploadS3Bucket: signature.uploadS3Bucket, uploadS3OnlyProbabilities: false)
            return
        case .cams_europe_reanalysis_interim, .cams_europe_reanalysis_validated, .cams_europe_reanalysis_validated_pre2020, .cams_europe_reanalysis_validated_pre2018:
            if let timeinterval = signature.timeinterval {
                let interval = try Timestamp.parseRange(yyyymmdd: timeinterval)
                for month in YearMonth(timestamp: interval.lowerBound)..<YearMonth(timestamp: interval.upperBound) {
                    let run = month.timestamp
                    try await downloadCamsEuropeReanalysis(application: context.application, domain: domain, run: run, skipFilesIfExisting: signature.skipExisting, variables: variables, cdskey: cdskey)
                    try await convertCamsEuropeReanalysis(logger: logger, domain: domain, run: run, variables: variables)
                }
                return
            }
            try await downloadCamsEuropeReanalysis(application: context.application, domain: domain, run: run, skipFilesIfExisting: signature.skipExisting, variables: variables, cdskey: cdskey)
            try await convertCamsEuropeReanalysis(logger: logger, domain: domain, run: run, variables: variables)
            return
        case .cams_europe:
            if let timeinterval = signature.timeinterval {
                for run in try Timestamp.parseRange(yyyymmdd: timeinterval).toRange(dt: 86400).with(dtSeconds: 86400) {
                    let handles = try await downloadCamsEurope(application: context.application, domain: domain, run: run, variables: variables, cdskey: cdskey, forecastHours: 24, concurrent: signature.concurrent ?? 1, uploadS3Bucket: nil)
                    try await GenericVariableHandle.convert(logger: logger, domain: domain, createNetcdf: signature.createNetcdf, run: run, handles: handles, concurrent: signature.concurrent ?? 1, writeUpdateJson: false, uploadS3Bucket: nil, uploadS3OnlyProbabilities: false)
                }
                return
            }
            let handles = try await downloadCamsEurope(application: context.application, domain: domain, run: run, variables: variables, cdskey: cdskey, forecastHours: nil, concurrent: signature.concurrent ?? 1, uploadS3Bucket: signature.uploadS3Bucket)
            try await GenericVariableHandle.convert(logger: logger, domain: domain, createNetcdf: signature.createNetcdf, run: run, handles: handles, concurrent: signature.concurrent ?? 1, writeUpdateJson: true, uploadS3Bucket: signature.uploadS3Bucket, uploadS3OnlyProbabilities: false)
            return
        case .cams_global_greenhouse_gases:
            let concurrent = signature.concurrent ?? 1
            let handles = try await downloadCamsGlobalGreenhouseGases(application: context.application, domain: domain, run: run, skipFilesIfExisting: signature.skipExisting, variables: variables, cdskey: cdskey, concurrent: concurrent, uploadS3Bucket: signature.uploadS3Bucket)
            try await GenericVariableHandle.convert(logger: logger, domain: domain, createNetcdf: signature.createNetcdf, run: run, handles: handles, concurrent: concurrent, writeUpdateJson: true, uploadS3Bucket: signature.uploadS3Bucket, uploadS3OnlyProbabilities: false)
            return
        }
    }

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

    struct CamsGlobalAreaQuery: Encodable {
        let date: String
        let type: [String]
        let data_format: String
        let download_format: String
        let variable: [String]
        let model_level: [Int]
        let time: String
        let leadtime_hour: [String]
        let area: [Double]
    }

    func downloadCamsGlobalArea(application: Application, domain: CamsDomain, run: Timestamp, variables: [CamsVariable], cdskey: String, concurrent: Int, uploadS3Bucket: String?) async throws -> [GenericVariableHandle] {
        try FileManager.default.createDirectory(atPath: domain.downloadDirectory, withIntermediateDirectories: true)
        let logger = application.logger

        let apiVariables = Array(variables.compactMap { $0.getCamsGlobalAreaApiName() }.uniqued())
        guard !apiVariables.isEmpty else {
            logger.warning("No CAMS global ADS/CDS variables selected")
            return []
        }

        let forecastHours = domain.forecastHours
        let date = run.iso8601_YYYY_MM_dd
        let query = CamsGlobalAreaQuery(
            date: "\(date)/\(date)",
            type: ["forecast"],
            data_format: "netcdf",
            download_format: "unarchived",
            variable: apiVariables,
            model_level: [137],
            time: "\(run.hour.zeroPadded(len: 2)):00",
            leadtime_hour: (0..<forecastHours).map(String.init),
            area: WeatherForecastServerSourceConfig.regionAreaNorthWestSouthEast
        )

        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, deadLineHours: 24)
        var handles = [GenericVariableHandle]()
        let nx = domain.grid.nx
        let ny = domain.grid.ny

        do {
            _ = concurrent
            let tempNc = "\(domain.downloadDirectory)/cams-global-area.nc"
            let tempNcDirectory = "\(domain.downloadDirectory)/cams-global-area"
            try? FileManager.default.removeItem(atPath: tempNc)
            try? FileManager.default.removeItem(atPath: tempNcDirectory)
            defer {
                try? FileManager.default.removeItem(atPath: tempNc)
                try? FileManager.default.removeItem(atPath: tempNcDirectory)
            }
            try await curl.downloadCdsApi(dataset: "cams-global-atmospheric-composition-forecasts", query: query, apikey: cdskey, server: "https://ads.atmosphere.copernicus.eu/api", destinationFile: tempNc)

            try FileManager.default.createDirectory(atPath: tempNcDirectory, withIntermediateDirectories: true)
            try Process.spawn(cmd: "unzip", args: ["-od", tempNcDirectory, tempNc])
            let extractedSurfaceNcPath = "\(tempNcDirectory)/data_sfc.nc"
            let extractedModelLevelNcPath = "\(tempNcDirectory)/data_mlev.nc"
            let surfaceNcPath = FileManager.default.fileExists(atPath: extractedSurfaceNcPath) ? extractedSurfaceNcPath : tempNc
            let modelLevelNcPath = FileManager.default.fileExists(atPath: extractedModelLevelNcPath) ? extractedModelLevelNcPath : tempNc

            let surfaceNcFile = FileManager.default.fileExists(atPath: surfaceNcPath) ? try NetCDF.open(path: surfaceNcPath, allowUpdate: false) : nil
            let modelLevelNcFile = FileManager.default.fileExists(atPath: modelLevelNcPath) ? try NetCDF.open(path: modelLevelNcPath, allowUpdate: false) : surfaceNcFile
            let writer = OmSpatialMultistepWriter(domain: domain, run: run, storeOnDisk: true, realm: nil, logger: logger)

            for variable in variables {
                guard let meta = variable.getCamsGlobalMeta() else {
                    continue
                }
                guard let ncFile = meta.isMultiLevel ? modelLevelNcFile : surfaceNcFile else {
                    logger.warning("Could not open CAMS global ADS/CDS NetCDF file for \(variable)")
                    continue
                }
                guard let forecastPeriodVariable = ncFile.getVariable(name: "forecast_period") else {
                    fatalError("Could not open CAMS global ADS/CDS forecast_period variable")
                }
                let forecastPeriods = try forecastPeriodVariable.readCamsGlobalAreaForecastPeriods()
                guard let ncVariable = ncFile.getVariable(name: meta.gribname) else {
                    logger.warning("Could not open CAMS global ADS/CDS NetCDF variable \(meta.gribname)")
                    continue
                }
                for (forecastPeriodIndex, forecastHour) in forecastPeriods.enumerated() {
                    let hour = forecastHour
                    guard hour >= 0 && hour < forecastHours else {
                        continue
                    }
                    let timestamp = run.add(hours: hour)
                    logger.info("Converting CAMS global ADS/CDS NetCDF variable \(variable) \(timestamp.format_YYYYMMddHH) \(meta.gribname)")

                    var data = try ncVariable.readCamsGlobalAreaLevel(forecastPeriodIndex: forecastPeriodIndex, ny: ny, nx: nx)
                    data.flipLatitude(nt: 1, ny: ny, nx: nx)
                    for i in data.indices {
                        data[i] *= meta.scalefactor
                    }
                    try await writer.write(time: timestamp, member: 0, variable: variable, data: data)
                }
            }
            handles.append(contentsOf: try await writer.finalise(completed: true, validTimes: nil, uploadS3Bucket: uploadS3Bucket))
        } catch CdsApiError.restrictedAccessToValidData {
            logger.info("CAMS global ADS/CDS run \(run.iso8601_YYYY_MM_dd_HH_mm) seems to be unavailable. Skipping downloading now.")
        }
        return handles
    }

    /// Download one month of reanalysis data as a zipped NetCDF file
    func downloadCamsEuropeReanalysis(application: Application, domain: CamsDomain, run: Timestamp, skipFilesIfExisting: Bool, variables: [CamsVariable], cdskey: String) async throws {
        let type: String
        let type2: String
        switch domain {
        case .cams_europe_reanalysis_validated, .cams_europe_reanalysis_validated_pre2020, .cams_europe_reanalysis_validated_pre2018:
            type = "validated_reanalysis"
            type2 = "vra"
        case .cams_europe_reanalysis_interim:
            type = "interim_reanalysis"
            type2 = "ira"
        default:
            fatalError()
        }

        let logger = application.logger
        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, deadLineHours: 24)
        let date = run.toComponents()

        for variable in variables {
            guard let meta = variable.getCamsEuMeta(), let fname = meta.reanalysisFileName else {
                continue
            }
            try FileManager.default.createDirectory(atPath: domain.downloadDirectory, withIntermediateDirectories: true)
            let downloadFile = "\(domain.downloadDirectory)download.nc.zip"
            let targetFile = "\(domain.downloadDirectory)cams.eaq.\(type2).ENSa.\(fname).l0.\(date.year)-\(date.month.zeroPadded(len: 2)).nc"

            if FileManager.default.fileExists(atPath: targetFile) {
                continue
            }
            let query = CamsEuropeQuery(
                model: ["ensemble"],
                date: nil,
                type: [type],
                data_format: nil,
                variable: [meta.apiName],
                level: ["0"],
                time: nil,
                leadtime_hour: nil,
                year: [String(date.year)],
                month: [date.month.zeroPadded(len: 2)],
                model_level: nil
            )

            do {
                try await curl.downloadCdsApi(
                    dataset: "cams-europe-air-quality-reanalyses",
                    query: query,
                    apikey: cdskey,
                    server: "https://ads.atmosphere.copernicus.eu/api",
                    destinationFile: downloadFile
                )
                try Process.spawn(cmd: "unzip", args: ["-od", domain.downloadDirectory, downloadFile])
            } catch {
                logger.info("Ignoring error \(error)")
                continue
            }
        }
    }

    /// Process each variable and update time-series optimised files
    func convertCamsEuropeReanalysis(logger: Logger, domain: CamsDomain, run: Timestamp, variables: [CamsVariable]) async throws {
        let om = OmFileSplitter(domain)

        let type2: String
        switch domain {
        case .cams_europe_reanalysis_validated, .cams_europe_reanalysis_validated_pre2020, .cams_europe_reanalysis_validated_pre2018:
            type2 = "vra"
        case .cams_europe_reanalysis_interim:
            type2 = "ira"
        default:
            fatalError()
        }
        let date = run.toComponents()

        for variable in variables {
            guard let meta = variable.getCamsEuMeta(), let fname = meta.reanalysisFileName else {
                continue
            }
            let targetFile = "\(domain.downloadDirectory)cams.eaq.\(type2).ENSa.\(fname).l0.\(date.year)-\(date.month.zeroPadded(len: 2)).nc"
            guard let ncFile = try NetCDF.open(path: targetFile, allowUpdate: false) else {
                logger.info("Missing file, skipping. \(targetFile)")
                continue
            }

            logger.info("Converting \(variable)")
            guard let ncVar = ncFile.getVariable(name: fname) else {
                fatalError("Could not open variable \(fname)")
            }
            guard let ncFloat = ncVar.asType(Float.self) else {
                fatalError("Could not open float variable \(fname)")
            }
            let nTime = ncVar.dimensions.first!.length
            var data2d = Array2DFastSpace(data: try ncFloat.read(), nLocations: domain.grid.count, nTime: nTime).transpose()
            for i in data2d.data.indices {
                if data2d.data[i] <= -999 {
                    data2d.data[i] = .nan
                }
            }

            logger.info("Create om file")
            let startOm = DispatchTime.now()
            let time = TimerangeDt(start: run, nTime: data2d.nTime, dtSeconds: domain.dtSeconds)
            try await om.updateFromTimeOriented(variable: variable.rawValue, array2d: data2d, run: run, time: time, scalefactor: variable.scalefactor)
            logger.info("Update om finished in \(startOm.timeElapsedPretty())")
        }
    }

    /// Download all timesteps and preliminarily covnert it to compressed files
    func downloadCamsEurope(application: Application, domain: CamsDomain, run: Timestamp, variables: [CamsVariable], cdskey: String, forecastHours: Int?, concurrent: Int, uploadS3Bucket: String?) async throws -> [GenericVariableHandle] {
        let logger = application.logger

        try FileManager.default.createDirectory(atPath: domain.downloadDirectory, withIntermediateDirectories: true)

        let forecastHours = forecastHours ?? domain.forecastHours
        let date = run.iso8601_YYYY_MM_dd
        let query = CamsEuropeQuery(
            model: ["ensemble"],
            date: "\(date)/\(date)",
            type: ["forecast"],
            data_format: "grib",
            variable: variables.compactMap { $0.getCamsEuMeta()?.apiName },
            level: ["0"],
            time: "\(run.hour.zeroPadded(len: 2)):00",
            leadtime_hour: (0..<forecastHours).map(String.init),
            year: nil,
            month: nil,
            model_level: nil
        )

        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, deadLineHours: 24)
        var handles = [GenericVariableHandle]()

        do {
            let h = try await curl.withCdsApi(dataset: "cams-europe-air-quality-forecasts", query: query, apikey: cdskey, server: "https://ads.atmosphere.copernicus.eu/api") { messages in
                let writer = OmSpatialMultistepWriter(domain: domain, run: run, storeOnDisk: true, realm: nil, logger: logger)
                try await messages.foreachConcurrent(nConcurrent: concurrent) { message in
                    let attributes = try GribAttributes(message: message)
                    let timestamp = attributes.timestamp
                    guard let variable = CamsVariable.camsEuropeFromGrib(attributes: attributes) else {
                        logger.warning("Could not find \(attributes) in grib")
                        return
                    }
                    logger.info("Converting variable \(variable) \(timestamp.format_YYYYMMddHH) \(message.get(attribute: "name")!)")

                    var grib2d = try message.to2D(nx: domain.grid.nx, ny: domain.grid.ny, shift180LongitudeAndFlipLatitudeIfRequired: false)
                    if attributes.unit == "kg m**-3" {
                        grib2d.array.data.multiplyAdd(multiply: 1e9, add: 0)
                    }
                    try await writer.write(time: timestamp, member: 0, variable: variable, data: grib2d.array.data)
                }
                return try await writer.finalise(completed: true, validTimes: nil, uploadS3Bucket: uploadS3Bucket)
            }
            handles.append(contentsOf: h)
        } catch CdsApiError.restrictedAccessToValidData {
            logger.info("Timestep \(run.iso8601_YYYY_MM_dd) seems to be unavailable. Skipping downloading now.")
        }
        return handles
    }
}

private extension CamsVariable {
    func getCamsGlobalAreaApiName() -> String? {
        switch self {
        case .aerosol_optical_depth:
            return "total_aerosol_optical_depth_550nm"
        case .pm2_5:
            return "particulate_matter_2.5um"
        case .pm10:
            return "particulate_matter_10um"
        case .uv_index:
            return "uv_biologically_effective_dose"
        case .carbon_monoxide:
            return "carbon_monoxide"
        case .nitrogen_dioxide:
            return "nitrogen_dioxide"
        case .sulphur_dioxide:
            return "sulphur_dioxide"
        case .ozone:
            return "ozone"
        case .dust:
            return "dust_aerosol_0.9-20um_mixing_ratio"
        default:
            return nil
        }
    }
}

fileprivate extension Variable {
    func readCamsGlobalAreaForecastPeriods() throws -> [Int] {
        if let ncFloat = self.asType(Float.self) {
            return try ncFloat.read().map { Int($0.rounded()) }
        }
        if let ncDouble = self.asType(Double.self) {
            return try ncDouble.read().map { Int($0.rounded()) }
        }
        if let ncInt32 = self.asType(Int32.self) {
            return try ncInt32.read().map(Int.init)
        }
        fatalError("Could not read CAMS global ADS/CDS forecast_period as numeric variable")
    }

    func readCamsGlobalAreaLevel(forecastPeriodIndex: Int, ny: Int, nx: Int) throws -> [Float] {
        if let ncDouble = self.asType(Double.self) {
            switch dimensions.count {
            case 4:
                guard dimensions[0].length > forecastPeriodIndex,
                      dimensions[1].length == 1,
                      dimensions[2].length == ny,
                      dimensions[3].length == nx else {
                    fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions. Got \(dimensions)")
                }
                return try ncDouble.read(offset: [forecastPeriodIndex, 0, 0, 0], count: [1, 1, ny, nx]).map(Float.init)
            case 5:
                guard dimensions[0].length > forecastPeriodIndex,
                      dimensions[1].length == 1,
                      dimensions[2].length == 1,
                      dimensions[3].length == ny,
                      dimensions[4].length == nx else {
                    fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions. Got \(dimensions)")
                }
                return try ncDouble.read(offset: [forecastPeriodIndex, 0, 0, 0, 0], count: [1, 1, 1, ny, nx]).map(Float.init)
            default:
                fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions \(dimensionsFlat)")
            }
        }

        guard let ncFloat = self.asType(Float.self) else {
            fatalError("Not a float nc variable")
        }
        switch dimensions.count {
        case 4:
            guard dimensions[0].length > forecastPeriodIndex,
                  dimensions[1].length == 1,
                  dimensions[2].length == ny,
                  dimensions[3].length == nx else {
                fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions. Got \(dimensions)")
            }
            return try ncFloat.read(offset: [forecastPeriodIndex, 0, 0, 0], count: [1, 1, ny, nx])
        case 5:
            guard dimensions[0].length > forecastPeriodIndex,
                  dimensions[1].length == 1,
                  dimensions[2].length == 1,
                  dimensions[3].length == ny,
                  dimensions[4].length == nx else {
                fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions. Got \(dimensions)")
            }
            return try ncFloat.read(offset: [forecastPeriodIndex, 0, 0, 0, 0], count: [1, 1, 1, ny, nx])
        default:
            fatalError("Wrong CAMS global ADS/CDS NetCDF dimensions \(dimensionsFlat)")
        }
    }
}
