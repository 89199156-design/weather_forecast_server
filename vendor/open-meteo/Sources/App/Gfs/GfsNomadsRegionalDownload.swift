import Foundation
import Vapor
@preconcurrency import SwiftEccodes

private enum GfsNomadsRegionalDownloadError: Error {
    case invalidSourceUrl(String)
    case unsupportedSourceProduct(String)
    case unsupportedLevel(String)
    case emptySelection(String)
    case messageCountMismatch(url: String, expected: Int, actual: Int)
    case invalidIndexOffset(String)
    case invalidOriginalPackingHeader(String)
}

struct GfsOriginalPacking: Sendable, Equatable {
    let referenceValue: Double
    let binaryScaleFactor: Int
    let decimalScaleFactor: Int
}

private struct GfsNomadsIndexRecord: Sendable {
    let line: String
    let variable: String
    let level: String
    let offset: Int
}

private actor GfsNomadsRequestGate {
    static let shared = GfsNomadsRequestGate()

    private var previousRequest = Date.distantPast

    func waitForTurn(minimumInterval: TimeInterval) async throws {
        let remaining = minimumInterval - Date().timeIntervalSince(previousRequest)
        if remaining > 0 {
            try await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
        }
        previousRequest = Date()
    }
}

private enum GfsNomadsRegionalDownload {
    private static let pressureVariablesRequiringOriginalPacking = Set([
        "TMP", "UGRD", "VGRD", "HGT", "DZDT",
    ])

    static func requiresOriginalPacking(_ record: GfsNomadsIndexRecord) -> Bool {
        guard record.level.hasSuffix(" mb") else {
            return true
        }
        return pressureVariablesRequiringOriginalPacking.contains(record.variable)
    }

    static func parseIndexRecord(_ line: String) -> GfsNomadsIndexRecord? {
        let parts = line.split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count >= 6, let offset = Int(parts[1]) else {
            return nil
        }
        return GfsNomadsIndexRecord(
            line: line,
            variable: String(parts[3]),
            level: String(parts[4]),
            offset: offset
        )
    }

    static func levelParameter(_ level: String) throws -> String {
        if level.hasSuffix(" mb"), let value = Int(level.dropLast(3)) {
            return "lev_\(value)_mb"
        }
        switch level {
        case "surface":
            return "lev_surface"
        case "mean sea level":
            return "lev_mean_sea_level"
        case "2 m above ground":
            return "lev_2_m_above_ground"
        case "10 m above ground":
            return "lev_10_m_above_ground"
        case "80 m above ground":
            return "lev_80_m_above_ground"
        case "100 m above ground":
            return "lev_100_m_above_ground"
        case "0-0.1 m below ground":
            return "lev_0-0.1_m_below_ground"
        case "0.1-0.4 m below ground":
            return "lev_0.1-0.4_m_below_ground"
        case "0.4-1 m below ground":
            return "lev_0.4-1_m_below_ground"
        case "1-2 m below ground":
            return "lev_1-2_m_below_ground"
        case "0C isotherm":
            return "lev_0C_isotherm"
        case "entire atmosphere":
            return "lev_entire_atmosphere"
        case "entire atmosphere (considered as a single layer)":
            return "lev_entire_atmosphere_\\(considered_as_a_single_layer\\)"
        case "low cloud layer":
            return "lev_low_cloud_layer"
        case "middle cloud layer":
            return "lev_middle_cloud_layer"
        case "high cloud layer":
            return "lev_high_cloud_layer"
        default:
            throw GfsNomadsRegionalDownloadError.unsupportedLevel(level)
        }
    }

    static func filterEndpoint(filename: String) throws -> String {
        if filename.contains(".sfluxgrbf") {
            return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_sflux.pl"
        }
        if filename.contains(".pgrb2b.0p25.") {
            return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25b.pl"
        }
        if filename.contains(".pgrb2.0p25.") {
            return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
        }
        throw GfsNomadsRegionalDownloadError.unsupportedSourceProduct(filename)
    }

    /// Read the GRIB inventory from NOAA's official public S3 bucket instead
    /// of the NOMADS web edge. The inventory describes the same GFS object,
    /// while avoiding transient Akamai redirects that can otherwise stall a
    /// regional subset request before it reaches the NOMADS filter endpoint.
    static func inventoryUrl(sourceUrl: String) throws -> String {
        guard let source = URL(string: sourceUrl) else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
        }
        let path = source.path.split(separator: "/").map(String.init)
        guard let gfsIndex = path.firstIndex(where: { $0.hasPrefix("gfs.") }),
              path.indices.contains(gfsIndex + 3),
              path[gfsIndex + 2] == "atmos" else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
        }
        let objectPath = path[gfsIndex...].joined(separator: "/")
        return "https://noaa-gfs-bdp-pds.s3.amazonaws.com/\(objectPath).idx"
    }

    static func sourceDataUrl(inventoryUrl: String) throws -> String {
        guard inventoryUrl.hasSuffix(".idx") else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(inventoryUrl)
        }
        return String(inventoryUrl.dropLast(4))
    }

    static func filterUrl(sourceUrl: String, records: [GfsNomadsIndexRecord]) throws -> String {
        guard let source = URL(string: sourceUrl) else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
        }
        let path = source.path.split(separator: "/").map(String.init)
        guard let gfsIndex = path.firstIndex(where: { $0.hasPrefix("gfs.") }),
              path.indices.contains(gfsIndex + 2),
              path[gfsIndex + 2] == "atmos",
              let filename = path.last else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
        }
        guard !records.isEmpty else {
            throw GfsNomadsRegionalDownloadError.emptySelection(sourceUrl)
        }

        let variables = Set(records.map(\.variable)).sorted()
        let levels = try Set(records.map(\.level)).map(levelParameter).sorted()
        let region = WeatherForecastServerSourceConfig.region
        var components = URLComponents(string: try filterEndpoint(filename: filename))!
        var items = [
            URLQueryItem(name: "file", value: filename),
            URLQueryItem(name: "dir", value: "/\(path[gfsIndex])/\(path[gfsIndex + 1])/atmos"),
            URLQueryItem(name: "subregion", value: ""),
            URLQueryItem(name: "leftlon", value: String(region.leftLon)),
            URLQueryItem(name: "rightlon", value: String(region.rightLon)),
            URLQueryItem(name: "toplat", value: String(region.topLat)),
            URLQueryItem(name: "bottomlat", value: String(region.bottomLat)),
        ]
        items.append(contentsOf: variables.map { URLQueryItem(name: "var_\($0)", value: "on") })
        items.append(contentsOf: levels.map { URLQueryItem(name: $0, value: "on") })
        components.queryItems = items
        guard let url = components.url?.absoluteString else {
            throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
        }
        return url
    }

    static func matches<Variable: CurlIndexedVariable>(_ line: String, variable: Variable) -> Bool {
        guard let name = variable.gribIndexName else {
            return false
        }
        return variable.exactMatch ? line.hasSuffix(name) : line.contains(name)
    }
}

/// Read the original GRIB2 data-representation metadata from a small prefix of
/// a message. Section 5 starts after the fixed 16-byte indicator section and
/// the variable-length identification/grid/product sections. Its first packing
/// fields are shared by the complex and simple packing templates used by GFS.
func parseGfsOriginalPackingHeader(_ bytes: [UInt8]) -> GfsOriginalPacking? {
    guard bytes.count >= 21,
          bytes[0] == 0x47, bytes[1] == 0x52,
          bytes[2] == 0x49, bytes[3] == 0x42,
          bytes[7] == 2 else {
        return nil
    }

    func uint32(at offset: Int) -> UInt32 {
        UInt32(bytes[offset]) << 24
            | UInt32(bytes[offset + 1]) << 16
            | UInt32(bytes[offset + 2]) << 8
            | UInt32(bytes[offset + 3])
    }
    func int16(at offset: Int) -> Int {
        let raw = UInt16(bytes[offset]) << 8 | UInt16(bytes[offset + 1])
        return Int(Int16(bitPattern: raw))
    }

    var offset = 16
    while offset + 5 <= bytes.count {
        let sectionLength = Int(uint32(at: offset))
        guard sectionLength >= 5, offset + sectionLength <= bytes.count else {
            return nil
        }
        if bytes[offset + 4] == 5 {
            guard sectionLength >= 20 else {
                return nil
            }
            let referenceBits = uint32(at: offset + 11)
            return GfsOriginalPacking(
                referenceValue: Double(Float(bitPattern: referenceBits)),
                binaryScaleFactor: int16(at: offset + 15),
                decimalScaleFactor: int16(at: offset + 17)
            )
        }
        offset += sectionLength
    }
    return nil
}

extension Curl {
    /// Download a NOAA/NOMADS server-side regional GRIB subset while retaining
    /// the official source inventory as the authority for variable matching.
    func downloadNomadsRegionalGfs<Variable: CurlIndexedVariable>(
        url sourceUrls: [String],
        variables: [Variable],
        errorOnMissing: Bool = true
    ) async throws -> [(variable: Variable, message: GribMessage, sourcePacking: GfsOriginalPacking?)] {
        var output = [(variable: Variable, message: GribMessage, sourcePacking: GfsOriginalPacking?)]()
        var matchedNames = Set<String>()

        for sourceUrl in sourceUrls {
            let inventoryUrl = try GfsNomadsRegionalDownload.inventoryUrl(sourceUrl: sourceUrl)
            guard let inventory = try await downloadInMemoryAsync(url: inventoryUrl, minSize: nil).readStringImmutable() else {
                throw GfsNomadsRegionalDownloadError.invalidSourceUrl(sourceUrl)
            }
            let lines = inventory.split(separator: "\n").map(String.init)
            var desiredByLine = [Int: Variable]()
            var desiredRecords = [GfsNomadsIndexRecord]()
            var selectedNames = Set<String>()

            for (index, line) in lines.enumerated() {
                guard let variable = variables.first(where: { GfsNomadsRegionalDownload.matches(line, variable: $0) }),
                      let name = variable.gribIndexName,
                      !matchedNames.contains(name),
                      selectedNames.insert(name).inserted,
                      let record = GfsNomadsRegionalDownload.parseIndexRecord(line) else {
                    continue
                }
                desiredByLine[index] = variable
                desiredRecords.append(record)
            }
            if desiredRecords.isEmpty {
                continue
            }

            let selectedVariables = Set(desiredRecords.map(\.variable))
            let selectedLevels = Set(desiredRecords.map(\.level))
            let filteredLines = lines.enumerated().compactMap { index, line -> (Int, GfsNomadsIndexRecord)? in
                guard let record = GfsNomadsRegionalDownload.parseIndexRecord(line),
                      selectedVariables.contains(record.variable),
                      selectedLevels.contains(record.level) else {
                    return nil
                }
                return (index, record)
            }
            let filteredUrl = try GfsNomadsRegionalDownload.filterUrl(sourceUrl: sourceUrl, records: desiredRecords)
            let sourceDataUrl = try GfsNomadsRegionalDownload.sourceDataUrl(inventoryUrl: inventoryUrl)
            let desiredRecordsByLine = desiredByLine.keys.sorted().compactMap { lineIndex -> (Int, GfsNomadsIndexRecord)? in
                guard lines.indices.contains(lineIndex),
                      let record = GfsNomadsRegionalDownload.parseIndexRecord(lines[lineIndex]) else {
                    return nil
                }
                return (lineIndex, record)
            }
            guard desiredRecordsByLine.count == desiredByLine.count else {
                throw GfsNomadsRegionalDownloadError.invalidIndexOffset(sourceUrl)
            }
            let desiredPackingRecords = desiredRecordsByLine.filter {
                GfsNomadsRegionalDownload.requiresOriginalPacking($0.1)
            }
            let packingMetadataConcurrent = max(
                1,
                min(
                    128,
                    Int(WeatherForecastServerSourceConfig.string(
                        "WEATHER_GFS_PACKING_METADATA_CONCURRENT",
                        fallback: "32"
                    )) ?? 32
                )
            )
            let packingPairs = try await desiredPackingRecords.mapConcurrent(
                nConcurrent: min(packingMetadataConcurrent, max(1, desiredPackingRecords.count))
            ) { lineIndex, record -> (Int, GfsOriginalPacking) in
                let header = try await self.downloadInMemoryAsync(
                    url: sourceDataUrl,
                    range: "\(record.offset)-\(record.offset + 511)",
                    minSize: 64,
                    quiet: true
                )
                guard let packing = parseGfsOriginalPackingHeader(Array(header.readableBytesView)) else {
                    throw GfsNomadsRegionalDownloadError.invalidOriginalPackingHeader(record.line)
                }
                return (lineIndex, packing)
            }
            let sourcePackingByLine = Dictionary(uniqueKeysWithValues: packingPairs)
            let delay = WeatherForecastServerSourceConfig.double(
                "WEATHER_NOMADS_REQUEST_DELAY_SECONDS",
                fallback: 2
            )
            let cachedFilterResponse = Curl.cacheDirectory.map {
                FileManager.default.fileExists(atPath: $0 + "/" + filteredUrl.sha256)
            } ?? false
            if !cachedFilterResponse {
                try await GfsNomadsRequestGate.shared.waitForTurn(minimumInterval: max(0, delay))
            }
            let messages = try await downloadGrib(url: filteredUrl, bzip2Decode: false)
            guard messages.count == filteredLines.count else {
                throw GfsNomadsRegionalDownloadError.messageCountMismatch(
                    url: filteredUrl,
                    expected: filteredLines.count,
                    actual: messages.count
                )
            }

            for ((lineIndex, record), message) in zip(filteredLines, messages) {
                guard let variable = desiredByLine[lineIndex],
                      let name = variable.gribIndexName,
                      matchedNames.insert(name).inserted else {
                    continue
                }
                let sourcePacking = sourcePackingByLine[lineIndex]
                if GfsNomadsRegionalDownload.requiresOriginalPacking(record), sourcePacking == nil {
                    throw GfsNomadsRegionalDownloadError.invalidOriginalPackingHeader(record.line)
                }
                output.append((variable, message, sourcePacking))
            }
        }

        let missing = variables.filter {
            guard let name = $0.gribIndexName else {
                return false
            }
            return !matchedNames.contains(name)
        }
        if !missing.isEmpty {
            for variable in missing {
                logger.error("Variable \(variable) '\(variable.gribIndexName ?? "")' missing from NOMADS regional response")
            }
            if errorOnMissing {
                throw CurlError.didNotFindAllVariablesInGribIndex
            }
        }
        return output
    }
}
