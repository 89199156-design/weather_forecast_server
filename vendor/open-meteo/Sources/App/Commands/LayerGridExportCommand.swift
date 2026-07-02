import Foundation
import Vapor

private struct LayerGridExportRequest: Decodable {
    let scope: String
    let model: String
    let run: String?
    let startHour: String
    let endHour: String
    let width: Int
    let height: Int
    let latitudes: [Float]
    let longitudes: [Float]
    let variables: [String]
    let chunkSize: Int?

    enum CodingKeys: String, CodingKey {
        case scope
        case model
        case run
        case startHour = "start_hour"
        case endHour = "end_hour"
        case width
        case height
        case latitudes
        case longitudes
        case variables
        case chunkSize = "chunk_size"
    }
}

private struct LayerGridExportMetadata: Encodable {
    let layout: String
    let scope: String
    let model: String
    let run: String?
    let width: Int
    let height: Int
    let times: [Int]
    let variables: [String]
}

/// Export point-time values for a regional layer grid without starting the HTTP API.
///
/// This command deliberately uses the same MultiDomains reader layer that the
/// forecast controllers use after request parsing. It removes the local HTTP
/// hop, but keeps Open-Meteo's variable parsing, time/run handling, model
/// mixing, interpolation, and derived-variable implementation in the engine.
struct LayerGridExportCommand: AsyncCommand {
    var help: String {
        "Export Open-Meteo reader output for a layer grid"
    }

    struct Signature: CommandSignature {
        @Option(name: "request", help: "JSON request produced by scripts/build_openmeteo_layers.py")
        var request: String

        @Option(name: "output-dir", help: "Directory where float32 variable files are written")
        var outputDirectory: String
    }

    func run(using context: CommandContext, signature: Signature) async throws {
        let logger = context.application.logger
        let requestData = try Data(contentsOf: URL(fileURLWithPath: signature.request))
        let request = try JSONDecoder().decode(LayerGridExportRequest.self, from: requestData)

        guard request.width == request.longitudes.count else {
            throw ForecastApiError.generic(message: "width does not match longitude count")
        }
        guard request.height == request.latitudes.count else {
            throw ForecastApiError.generic(message: "height does not match latitude count")
        }
        guard !request.variables.isEmpty else {
            throw ForecastApiError.generic(message: "variables must not be empty")
        }

        let domain = try MultiDomains.load(rawValue: request.model)
        let start = try IsoDateTime(fromIsoString: request.startHour).toTimestamp()
        let endInclusive = try IsoDateTime(fromIsoString: request.endHour).toTimestamp()
        let timeRange = TimerangeDt(start: start, to: endInclusive.add(hours: 1), dtSeconds: 3600)
        let run = try request.run.map { try IsoDateTime(fromIsoString: $0) }
        let timeSettings = timeRange.toSettings(run: run)
        let timestamps = timeRange.map { $0.timeIntervalSince1970 }

        let outputURL = URL(fileURLWithPath: signature.outputDirectory, isDirectory: true)
        try FileManager.default.createDirectory(at: outputURL, withIntermediateDirectories: true)

        let metadata = LayerGridExportMetadata(
            layout: "point_time",
            scope: request.scope,
            model: request.model,
            run: request.run,
            width: request.width,
            height: request.height,
            times: timestamps,
            variables: request.variables
        )
        let metadataData = try JSONEncoder().encode(metadata)
        try metadataData.write(to: outputURL.appendingPathComponent("metadata.json"), options: .atomic)

        var handles: [String: FileHandle] = [:]
        defer {
            for handle in handles.values {
                try? handle.close()
            }
        }

        for variable in request.variables {
            let url = outputURL.appendingPathComponent("\(variable).float32")
            FileManager.default.createFile(atPath: url.path, contents: nil)
            guard let handle = FileHandle(forWritingAtPath: url.path) else {
                throw ForecastApiError.generic(message: "cannot open output file for \(variable)")
            }
            handles[variable] = handle
        }

        let options = try GenericReaderOptions(
            logger: logger,
            httpClient: context.application.dedicatedHttpClient
        )
        let chunkSize = max(1, request.chunkSize ?? 250)
        let totalPoints = request.width * request.height
        var completed = 0

        while completed < totalPoints {
            let chunkEnd = min(completed + chunkSize, totalPoints)
            var buffers: [String: [Float]] = [:]
            buffers.reserveCapacity(request.variables.count)
            for variable in request.variables {
                buffers[variable] = []
                buffers[variable]?.reserveCapacity((chunkEnd - completed) * timestamps.count)
            }

            for flatIndex in completed..<chunkEnd {
                let y = flatIndex / request.width
                let x = flatIndex - y * request.width
                let lat = request.latitudes[y]
                let lon = request.longitudes[x]
                let readers = try await domain.getReader(
                    lat: lat,
                    lon: lon,
                    elevation: .nan,
                    mode: .land,
                    options: options,
                    include15Min: false
                )

                for variable in request.variables {
                    let values = try await readMixed(readers: readers, variable: variable, time: timeSettings)?.data
                    buffers[variable]?.append(contentsOf: values ?? Array(repeating: Float.nan, count: timestamps.count))
                }
            }

            for variable in request.variables {
                guard let values = buffers[variable], let handle = handles[variable] else {
                    continue
                }
                try handle.write(contentsOf: values.withUnsafeBufferPointer { Data(buffer: $0) })
            }

            completed = chunkEnd
            logger.info("export-layer-grid \(request.scope) completed \(completed)/\(totalPoints)")
        }
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
