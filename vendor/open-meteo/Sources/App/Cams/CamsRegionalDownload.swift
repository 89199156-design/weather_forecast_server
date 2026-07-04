struct CamsRegionalDownloadSlice {
    let fullNx: Int
    let fullNy: Int
    let x0: Int
    let y0: Int
    let nx: Int
    let ny: Int
}

extension CamsDomain {
    var regionalDownloadSlice: CamsRegionalDownloadSlice? {
        switch self {
        case .cams_global:
            let slice = WeatherForecastServerSourceConfig.regularGridSlice(
                fullNx: 900,
                fullNy: 451,
                latMin: -90,
                lonMin: -180,
                dx: 0.4,
                dy: 0.4,
                region: WeatherForecastServerSourceConfig.region
            )
            return CamsRegionalDownloadSlice(fullNx: 900, fullNy: 451, x0: slice.x0, y0: slice.y0, nx: slice.nx, ny: slice.ny)
        case .cams_global_greenhouse_gases:
            let slice = WeatherForecastServerSourceConfig.regularGridSlice(
                fullNx: 3600,
                fullNy: 1801,
                latMin: -90,
                lonMin: -180,
                dx: 0.1,
                dy: 0.1,
                region: WeatherForecastServerSourceConfig.region
            )
            return CamsRegionalDownloadSlice(fullNx: 3600, fullNy: 1801, x0: slice.x0, y0: slice.y0, nx: slice.nx, ny: slice.ny)
        default:
            return nil
        }
    }
}

extension Array where Element == Float {
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
