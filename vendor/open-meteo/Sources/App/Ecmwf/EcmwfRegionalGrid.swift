import Foundation
import OmFileFormat

/// Product-source boundary for the regional ECMWF replica.
///
/// The default remains the unmodified upstream global grid. Production enables
/// the regional grid explicitly and downloads the same indexed ECMWF GRIB
/// messages, then crops decoded values on their native 0.25 degree lattice
/// before any Open-Meteo conversion, interpolation, or compression occurs.
enum EcmwfRegionalSourceConfig {
    static func string(_ key: String, fallback: String) -> String {
        guard let raw = ProcessInfo.processInfo.environment[key]?
            .trimmingCharacters(in: .whitespacesAndNewlines),
            !raw.isEmpty
        else {
            return fallback
        }
        return raw
    }

    static func double(_ key: String, fallback: Double) -> Double {
        Double(string(key, fallback: String(fallback))) ?? fallback
    }

    static var enabled: Bool {
        ["1", "true", "yes", "on"].contains(
            string("WEATHER_ECMWF_REGIONAL_GRID", fallback: "false").lowercased()
        )
    }

    static var officialSurfaceElevationPath: String? {
        guard enabled else {
            return nil
        }
        let path = string("WEATHER_ECMWF_OFFICIAL_HSURF", fallback: "")
        return path.isEmpty ? nil : path
    }

    static var bounds: (
        leftLon: Double,
        rightLon: Double,
        bottomLat: Double,
        topLat: Double
    ) {
        (
            leftLon: double("WEATHER_ECMWF_STORAGE_LEFT_LON", fallback: 68.0),
            rightLon: double("WEATHER_ECMWF_STORAGE_RIGHT_LON", fallback: 142.0),
            bottomLat: double("WEATHER_ECMWF_STORAGE_BOTTOM_LAT", fallback: -2.0),
            topLat: double("WEATHER_ECMWF_STORAGE_TOP_LAT", fallback: 60.0)
        )
    }

    static func regularGridSlice(
        base: RegularGrid,
        bounds: (
            leftLon: Double,
            rightLon: Double,
            bottomLat: Double,
            topLat: Double
        )
    ) -> (x0: Int, y0: Int, nx: Int, ny: Int) {
        guard bounds.leftLon <= bounds.rightLon,
            bounds.bottomLat <= bounds.topLat
        else {
            fatalError("Configured ECMWF storage bounds are not ordered")
        }
        let epsilon = 1e-9
        let x0 = max(
            0,
            Int(
                ceil(
                    (bounds.leftLon - Double(base.lonMin)) /
                        Double(base.dx) - epsilon
                )
            )
        )
        let x1 = min(
            base.nx - 1,
            Int(
                floor(
                    (bounds.rightLon - Double(base.lonMin)) /
                        Double(base.dx) + epsilon
                )
            )
        )
        let y0 = max(
            0,
            Int(
                ceil(
                    (bounds.bottomLat - Double(base.latMin)) /
                        Double(base.dy) - epsilon
                )
            )
        )
        let y1 = min(
            base.ny - 1,
            Int(
                floor(
                    (bounds.topLat - Double(base.latMin)) /
                        Double(base.dy) + epsilon
                )
            )
        )
        guard x0 <= x1, y0 <= y1 else {
            fatalError("Configured ECMWF storage bounds do not overlap the source grid")
        }
        return (x0: x0, y0: y0, nx: x1 - x0 + 1, ny: y1 - y0 + 1)
    }
}

struct EcmwfRegionalRegularGrid: Gridable {
    let base: RegularGrid
    let x0: Int
    let y0: Int
    let nx: Int
    let ny: Int

    var searchRadius: Int {
        base.searchRadius
    }

    var crsWkt2: String {
        let sw = getCoordinates(gridpoint: 0)
        let ne = getCoordinates(gridpoint: nx * ny - 1)
        return """
            GEOGCRS["WGS 84",
                DATUM["World Geodetic System 1984",
                    ELLIPSOID["WGS 84",6378137,298.257223563]],
                CS[ellipsoidal,2],
                    AXIS["latitude",north],
                    AXIS["longitude",east],
                    ANGLEUNIT["degree",0.0174532925199433]
                USAGE[
                    SCOPE["grid"],
                    BBOX[\(sw.latitude),\(sw.longitude),\(ne.latitude),\(ne.longitude)]]]
            """
    }

    func findPoint(lat: Float, lon: Float) -> Int? {
        guard let (x, y) = base.findPointXy(lat: lat, lon: lon),
            x >= x0,
            y >= y0,
            x < x0 + nx,
            y < y0 + ny
        else {
            return nil
        }
        return (y - y0) * nx + (x - x0)
    }

    func findPointInterpolated(lat: Float, lon: Float) -> GridPoint2DFraction? {
        guard let point = base.findPointInterpolated(lat: lat, lon: lon) else {
            return nil
        }
        let x = point.gridpoint % base.nx
        let y = point.gridpoint / base.nx
        guard x >= x0,
            y >= y0,
            x < x0 + nx,
            y < y0 + ny
        else {
            return nil
        }
        return GridPoint2DFraction(
            gridpoint: (y - y0) * nx + (x - x0),
            xFraction: point.xFraction,
            yFraction: point.yFraction
        )
    }

    func findBox(boundingBox: BoundingBoxWGS84) -> [Int]? {
        guard let slice = base.findBox(boundingBox: boundingBox) else {
            return nil
        }
        let yRange = max(slice.yRange.lowerBound, y0)..<min(
            slice.yRange.upperBound,
            y0 + ny
        )
        let xRange = max(slice.xRange.lowerBound, x0)..<min(
            slice.xRange.upperBound,
            x0 + nx
        )
        guard !xRange.isEmpty, !yRange.isEmpty else {
            return []
        }
        var gridpoints = [Int]()
        gridpoints.reserveCapacity(xRange.count * yRange.count)
        for y in yRange {
            for x in xRange {
                gridpoints.append((y - y0) * nx + (x - x0))
            }
        }
        return gridpoints
    }

    func estimatedNumberOfGridCells(
        boundingBox: BoundingBoxWGS84
    ) -> Int? {
        findBox(boundingBox: boundingBox)?.count
    }

    func getCoordinates(gridpoint: Int) -> (latitude: Float, longitude: Float) {
        let y = gridpoint / nx
        let x = gridpoint - y * nx
        return base.getCoordinates(gridpoint: (y + y0) * base.nx + x + x0)
    }
}

extension EcmwfDomain {
    var sourceGrid: RegularGrid {
        switch self {
        case .ifs04, .ifs04_ensemble:
            return RegularGrid(
                nx: 900,
                ny: 451,
                latMin: -90,
                lonMin: -180,
                dx: 360 / 900,
                dy: 180 / 450
            )
        case .ifs025, .ifs025_ensemble, .aifs025, .wam025, .wam025_ensemble,
            .aifs025_single, .aifs025_ensemble, .ifs025_ensemble_mean,
            .wam025_ensemble_mean, .aifs025_ensemble_mean:
            return RegularGrid(
                nx: 1440,
                ny: 721,
                latMin: -90,
                lonMin: -180,
                dx: 360 / 1440,
                dy: 180 / (721 - 1)
            )
        }
    }

    var regionalSlice: (x0: Int, y0: Int, nx: Int, ny: Int)? {
        guard [.ifs025, .ifs025_ensemble, .ifs025_ensemble_mean].contains(self),
            EcmwfRegionalSourceConfig.enabled
        else {
            return nil
        }
        return EcmwfRegionalSourceConfig.regularGridSlice(
            base: sourceGrid,
            bounds: EcmwfRegionalSourceConfig.bounds
        )
    }

    func cropToRuntimeGrid(_ source: Array2D) -> Array2D {
        guard let slice = regionalSlice else {
            return source
        }
        guard source.nx == sourceGrid.nx, source.ny == sourceGrid.ny else {
            fatalError("ECMWF decoded source grid does not match its native lattice")
        }
        var output = [Float]()
        output.reserveCapacity(slice.nx * slice.ny)
        for y in 0..<slice.ny {
            let start = (slice.y0 + y) * source.nx + slice.x0
            output.append(contentsOf: source.data[start..<(start + slice.nx)])
        }
        return Array2D(data: output, nx: slice.nx, ny: slice.ny)
    }

    func cropOfficialSurfaceElevation(_ source: [Float]) -> [Float] {
        guard let slice = regionalSlice else {
            return source
        }
        guard source.count == sourceGrid.nx * sourceGrid.ny else {
            fatalError(
                "Pinned ECMWF HSURF grid does not match the global IFS lattice"
            )
        }
        var output = [Float]()
        output.reserveCapacity(slice.nx * slice.ny)
        for y in 0..<slice.ny {
            let start = (slice.y0 + y) * sourceGrid.nx + slice.x0
            output.append(contentsOf: source[start..<(start + slice.nx)])
        }
        return output
    }
}
