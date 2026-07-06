import Foundation
import Vapor

private struct PointForecastExportPoint: Decodable {
    let latitude: Float
    let longitude: Float
}

private struct PointForecastExportApiQuery: Encodable {
    let latitude: [Float]
    let longitude: [Float]
    let hourly: [String]
    let timezone: [String]
    let timeformat: String
    let models: [String]?
    let domains: String?
    let windSpeedUnit: String?
    let run: String?
    let forecastHours: Int?
    let startHour: [String]
    let endHour: [String]

    enum CodingKeys: String, CodingKey {
        case latitude
        case longitude
        case hourly
        case timezone
        case timeformat
        case models
        case domains
        case windSpeedUnit = "wind_speed_unit"
        case run
        case forecastHours = "forecast_hours"
        case startHour = "start_hour"
        case endHour = "end_hour"
    }
}

private struct PointForecastExportRequest: Decodable {
    let scope: String
    let model: String
    let run: String?
    let startHour: String
    let endHour: String
    let points: [PointForecastExportPoint]
    let variables: [String]

    enum CodingKeys: String, CodingKey {
        case scope
        case model
        case run
        case startHour = "start_hour"
        case endHour = "end_hour"
        case points
        case variables
    }
}

private struct PointForecastExportVariableMetadata: Encodable {
    let unit: String
    let significantDigits: Int

    enum CodingKeys: String, CodingKey {
        case unit
        case significantDigits = "significant_digits"
    }
}

private struct PointForecastExportMetadata: Encodable {
    let layout: String
    let scope: String
    let model: String
    let run: String?
    let points: [PointForecastExportPointMetadata]
    let times: [Int]
    let variables: [String]
    let variableMetadata: [String: PointForecastExportVariableMetadata]

    enum CodingKeys: String, CodingKey {
        case layout
        case scope
        case model
        case run
        case points
        case times
        case variables
        case variableMetadata = "variable_metadata"
    }
}

private struct PointForecastExportPointMetadata: Encodable {
    let latitude: Float
    let longitude: Float
}

/// Export arbitrary point-time values without starting the HTTP API.
///
/// This command uses the same MultiDomains reader layer as the API after
/// request parsing. It removes the localhost HTTP hop while preserving the
/// engine's interpolation, model mixing, and derived-variable calculations.
struct PointForecastExportCommand: AsyncCommand {
    var help: String {
        "Export Open-Meteo reader output for point forecasts"
    }

    struct Signature: CommandSignature {
        @Option(name: "request", help: "JSON request with points, variables, and time range")
        var request: String?

        @Option(name: "output-dir", help: "Directory where float32 variable files are written")
        var outputDirectory: String?
    }

    func run(using context: CommandContext, signature: Signature) async throws {
        let logger = context.application.logger
        guard let requestPath = signature.request else {
            throw ForecastApiError.generic(message: "--request is required")
        }
        guard let outputDirectory = signature.outputDirectory else {
            throw ForecastApiError.generic(message: "--output-dir is required")
        }

        let requestData = try Data(contentsOf: URL(fileURLWithPath: requestPath))
        let request = try JSONDecoder().decode(PointForecastExportRequest.self, from: requestData)
        guard !request.points.isEmpty else {
            throw ForecastApiError.generic(message: "points must not be empty")
        }
        guard !request.variables.isEmpty else {
            throw ForecastApiError.generic(message: "variables must not be empty")
        }

        let start = try IsoDateTime(fromIsoString: request.startHour).toTimestamp()
        let endInclusive = try IsoDateTime(fromIsoString: request.endHour).toTimestamp()
        let forecastHours = request.run.map { _ in
            Int((endInclusive.timeIntervalSince1970 - start.timeIntervalSince1970) / 3600) + 1
        }
        let apiQuery = PointForecastExportApiQuery(
            latitude: request.points.map(\.latitude),
            longitude: request.points.map(\.longitude),
            hourly: request.variables,
            timezone: ["UTC"],
            timeformat: "iso8601",
            models: request.scope == "gfs" ? [request.model] : nil,
            domains: request.scope == "cams" ? request.model : nil,
            windSpeedUnit: request.scope == "gfs" ? "ms" : nil,
            run: request.run,
            forecastHours: forecastHours,
            startHour: request.run == nil ? [request.startHour] : [],
            endHour: request.run == nil ? [request.endHour] : []
        )
        let params = try JSONDecoder().decode(ApiQueryParameter.self, from: JSONEncoder().encode(apiQuery))
        let hourlyVariables: [ForecastVariable]
        if request.scope == "cams" {
            guard try CamsReader.MixingVar.load(commaSeparatedOptional: params.hourly)?.isEmpty == false else {
                throw ForecastApiError.generic(message: "hourly variables must not be empty")
            }
            hourlyVariables = []
        } else {
            hourlyVariables = try ForecastVariable.load(commaSeparatedOptional: params.hourly) ?? []
            guard !hourlyVariables.isEmpty else {
                throw ForecastApiError.generic(message: "hourly variables must not be empty")
            }
        }

        let outputURL = URL(fileURLWithPath: outputDirectory, isDirectory: true)
        try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

        var handles: [String: FileHandle] = [:]
        defer {
            for handle in handles.values {
                try? handle.close()
            }
        }
        for variable in request.variables {
            let url = outputURL.appendingPathComponent("\(variable).float32")
            _ = FileManager.default.createFile(atPath: url.path, contents: nil)
            guard let handle = FileHandle(forWritingAtPath: url.path) else {
                throw ForecastApiError.generic(message: "cannot open output file for \(variable)")
            }
            handles[variable] = handle
        }

        let options = try params.readerOptions(
            logger: logger,
            httpClient: context.application.dedicatedHttpClient
        )
        var variableMetadata: [String: PointForecastExportVariableMetadata] = [:]
        var timestamps: [Int]?

        let domains: [MultiDomains]
        if request.scope == "cams" {
            let camsDomains = try params.domains.map { [$0] } ?? CamsQuery.Domain.load(commaSeparatedOptional: params.models) ?? [.auto]
            domains = camsDomains.map(\.multiDomain)
        } else if request.scope == "gfs" {
            domains = try MultiDomains.load(commaSeparatedOptional: params.models)?.map { $0 == .best_match ? .best_match : $0 } ?? [.best_match]
        } else {
            throw ForecastApiError.generic(message: "unknown scope: \(request.scope)")
        }

        let currentTime = Timestamp.now()
        let currentTimeHour0 = currentTime.with(hour: 0)
        let forecastDaysMax = request.scope == "cams" ? 7 : 16
        let forecastDayDefault = request.scope == "cams" ? 5 : 7
        let historyStartDate = request.scope == "cams" ? Timestamp(2013, 1, 1) : Timestamp(2023, 1, 1)
        let pastDaysMax = (currentTimeHour0.timeIntervalSince1970 - historyStartDate.timeIntervalSince1970) / 86400
        let allowedRange = historyStartDate ..< currentTimeHour0.add(days: forecastDaysMax)
        let temporalResolutionDefault = ApiTemporalResolution.hourly
        let biasCorrection = !(params.disable_bias_correction ?? false)
        let prepared = try await params.prepareCoordinates(
            allowTimezones: true,
            logger: logger,
            httpClient: options.httpClient
        )
        guard case .coordinates(let coordinates) = prepared else {
            throw ForecastApiError.generic(message: "bounding box is not supported by export-point-forecast")
        }

        for preparedPoint in coordinates {
            let coordinate = preparedPoint.coordinate
            let timezone = preparedPoint.timezone
            let time = try params.getTimerange2(
                timezone: timezone,
                current: currentTime,
                forecastDaysDefault: forecastDayDefault,
                forecastDaysMax: forecastDaysMax,
                startEndDate: preparedPoint.startEndDate,
                allowedRange: allowedRange,
                pastDaysMax: pastDaysMax
            )
            let readers: [MultiDomainsReader] = try await domains.asyncCompactMap { domain in
                guard let r = try await domain.getReaders(
                    lat: coordinate.latitude,
                    lon: coordinate.longitude,
                    elevation: coordinate.elevation,
                    mode: .land,
                    options: options,
                    biasCorrection: biasCorrection,
                    include15Min: false
                ) else {
                    return nil
                }
                return MultiDomainsReader(
                    domain: domain,
                    readerHourly: r.hourly,
                    readerDaily: r.daily,
                    readerWeekly: r.weekly,
                    readerMonthly: r.monthly,
                    params: params,
                    run: params.run,
                    has15minutely: false,
                    time: time,
                    timezone: timezone,
                    currentTime: currentTime,
                    temporalResolution: temporalResolutionDefault
                )
            }
            guard !readers.isEmpty else {
                throw ForecastApiError.noDataAvailableForThisLocation
            }
            if request.scope == "cams" {
                let hourlyReaders = readers.compactMap(\.readerHourly)
                guard !hourlyReaders.isEmpty else {
                    throw ForecastApiError.noDataAvailableForThisLocation
                }
                let hourlyDt = (params.temporal_resolution ?? temporalResolutionDefault).dtSeconds ?? hourlyReaders[0].modelDtSeconds
                let timeHourlyRead = time.hourlyRead.with(dtSeconds: hourlyDt)
                let timeHourlyDisplay = time.hourlyDisplay.with(dtSeconds: hourlyDt)
                let timeRead = timeHourlyRead.toSettings(run: params.run)
                let timeCount = timeHourlyRead.count
                if timestamps == nil {
                    timestamps = timeHourlyDisplay.map { $0.timeIntervalSince1970 }
                }

                for variable in request.variables {
                    guard let handle = handles[variable] else {
                        continue
                    }
                    guard let output = try await readMixed(readers: hourlyReaders, variable: variable, time: timeRead)?.convertAndRound(params: params) else {
                        let values = Array(repeating: Float.nan, count: timeCount)
                        try handle.write(contentsOf: values.withUnsafeBufferPointer { Data(buffer: $0) })
                        continue
                    }
                    if variableMetadata[variable] == nil {
                        variableMetadata[variable] = PointForecastExportVariableMetadata(
                            unit: "\(output.unit)",
                            significantDigits: output.unit.significantDigits
                        )
                    }
                    try handle.write(contentsOf: output.data.withUnsafeBufferPointer { Data(buffer: $0) })
                }
                continue
            }

            let timeLocal = TimerangeLocal(range: time.dailyRead.range, utcOffsetSeconds: timezone.utcOffsetSeconds)
            let location = ForecastapiResult<MultiDomainsReader>.PerLocation(
                timezone: timezone,
                time: timeLocal,
                locationId: coordinate.locationId,
                results: readers
            )
            for reader in readers {
                try await reader.prefetch(
                    currentVariables: nil,
                    minutely15Variables: nil,
                    hourlyVariables: hourlyVariables,
                    dailyVariables: nil,
                    weeklyVariables: nil,
                    monthlyVariables: nil
                )
            }
            guard let hourly = try await location.hourly(variables: hourlyVariables) else {
                throw ForecastApiError.noDataAvailableForThisLocation
            }
            if timestamps == nil {
                timestamps = hourly.time.map { $0.timeIntervalSince1970 }
            }
            let columns = Dictionary(uniqueKeysWithValues: hourly.columns.map { ($0.variable, $0) })
            let timeCount = hourly.time.count

            for variable in request.variables {
                guard let handle = handles[variable] else {
                    continue
                }
                guard let column = columns[variable] else {
                    let values = Array(repeating: Float.nan, count: timeCount)
                    try handle.write(contentsOf: values.withUnsafeBufferPointer { Data(buffer: $0) })
                    continue
                }
                if variableMetadata[variable] == nil {
                    variableMetadata[variable] = PointForecastExportVariableMetadata(
                        unit: "\(column.unit)",
                        significantDigits: column.unit.significantDigits
                    )
                }
                switch column.data {
                case .float(let values):
                    try handle.write(contentsOf: values.withUnsafeBufferPointer { Data(buffer: $0) })
                case .timestamp:
                    let values = Array(repeating: Float.nan, count: timeCount)
                    try handle.write(contentsOf: values.withUnsafeBufferPointer { Data(buffer: $0) })
                }
            }
        }

        let metadata = PointForecastExportMetadata(
            layout: "point_time",
            scope: request.scope,
            model: request.model,
            run: request.run,
            points: request.points.map { PointForecastExportPointMetadata(latitude: $0.latitude, longitude: $0.longitude) },
            times: timestamps ?? [],
            variables: request.variables,
            variableMetadata: variableMetadata
        )
        let metadataData = try JSONEncoder().encode(metadata)
        try metadataData.write(to: outputURL.appendingPathComponent("metadata.json"), options: .atomic)
    }

    private func readMixed(
        readers: [any GenericReaderProtocol],
        variable: String,
        time: TimerangeDtAndSettings
    ) async throws -> DataAndUnit? {
        var data: [Float]?
        var unit: SiUnit?
        for reader in readers.reversed() {
            guard let output = try await reader.get(mixed: variable, time: time) else {
                continue
            }
            if data == nil {
                data = output.data
                unit = output.unit
            } else if let unit, [.wmoCode, .dimensionless].contains(unit) {
                data?.integrateIfNaN(output.data)
            } else {
                data?.integrateIfNaNSmooth(output.data)
            }
            if data?.containsNaN() == false {
                break
            }
        }
        guard let data, let unit else {
            return nil
        }
        return DataAndUnit(data, unit)
    }
}
