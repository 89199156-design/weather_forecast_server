/**
 dataset = "cams-global-greenhouse-gas-forecasts"
 request = {
     "variable": [
         "carbon_dioxide",
         "carbon_monoxide",
         "methane"
     ],
     "model_level": ["137"],
     "date": ["2024-10-22/2024-10-22"],
     "leadtime_hour": [
         "0",
         "3",
        ...
         "117",
         "120"
     ],
     "data_format": "grib"
 */
import Vapor
import OmFileFormat

extension DownloadCamsAdsCommand {
    /**
     Download CAMS Global Greenhouse Gas forecast from ADS
     */
    func downloadCamsGlobalGreenhouseGases(application: Application, domain: CamsDomain, run: Timestamp, skipFilesIfExisting: Bool, variables: [CamsVariable], cdskey: String, concurrent: Int, uploadS3Bucket: String?) async throws -> [GenericVariableHandle] {
        let date = run.iso8601_YYYY_MM_dd
        let forecastHours = domain.forecastHours
        let logger = application.logger
        let curl = Curl(logger: logger, client: application.dedicatedHttpClient, deadLineHours: 24)
        let regionalSlice = domain.regionalDownloadSlice
        let sourceNx = regionalSlice?.fullNx ?? domain.grid.nx
        let sourceNy = regionalSlice?.fullNy ?? domain.grid.ny
        let query = CamsEuropeQuery(
            model: nil,
            date: "\(date)/\(date)",
            type: nil,
            data_format: "grib",
            variable: variables.compactMap { $0.getCamsGlobalGreenhouseGasesMeta()?.apiname },
            level: nil,
            time: nil,
            leadtime_hour: stride(from: 0, through: forecastHours - 1, by: domain.dtHours).map(String.init),
            year: nil,
            month: nil,
            model_level: [137]
        )
        return try await curl.withCdsApi(
            dataset: "cams-global-greenhouse-gas-forecasts",
            query: query,
            apikey: cdskey,
            server: "https://ads.atmosphere.copernicus.eu/api"
        ) { messages in
            let writer = OmSpatialMultistepWriter(domain: domain, run: run, storeOnDisk: true, realm: nil, logger: logger)
            try await messages.foreachConcurrent(nConcurrent: concurrent) { message in
                let attributes = try GribAttributes(message: message)
                let timestamp = attributes.timestamp
                let shortName = attributes.shortName
                guard let variable = CamsVariable.allCases.first(where: { $0.getCamsGlobalGreenhouseGasesMeta()?.gribShortName == shortName }) else {
                    fatalError("Could not find variable for \(attributes)")
                }

                logger.info("Converting variable \(variable) \(timestamp.format_YYYYMMddHH) \(message.get(attribute: "name")!)")

                var grib2d = GribArray2D(nx: sourceNx, ny: sourceNy)
                try grib2d.load(message: message)
                if let scaling = variable.getCamsGlobalGreenhouseGasesMeta()?.scalefactor {
                    grib2d.array.data.multiplyAdd(multiply: scaling, add: 0)
                }
                grib2d.array.shift180LongitudeAndFlipLatitude()
                var data = grib2d.array.data
                if let regionalSlice = regionalSlice {
                    data = data.sliceGrid(x0: regionalSlice.x0, y0: regionalSlice.y0, nx: regionalSlice.nx, ny: regionalSlice.ny, sourceNx: sourceNx)
                }
                try await writer.write(time: timestamp, member: 0, variable: variable, data: data)
            }
            return try await writer.finalise(completed: true, validTimes: nil, uploadS3Bucket: uploadS3Bucket)
        }
    }
}
