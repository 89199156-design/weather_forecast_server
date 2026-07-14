

enum FullRunsVariables {
    /// Function to select which variables that should be stored as full run data in `./data_run`
    /// Takes all surface variables below 200m and a selection of pressure levels
    static func includes(_ variable: String) -> Bool {
        if let level = getPressureLevel(variable) {
            return FullRunsVariables.pressureLevelsToKeep.contains(level)
        }
        if let height = getModelLevel(variable) {
            return height <= 200
        }
        return true
    }
    
    /// Extract pressure level from variable string like `temperature_100hPa`
    fileprivate static func getPressureLevel(_ variable: String) -> Int? {
        guard let pos = variable.lastIndex(of: "_"), let posEnd = variable[pos..<variable.endIndex].range(of: "hPa") else {
            return nil
        }
        let start = variable.index(after: pos)
        let levelString = variable[start..<posEnd.lowerBound]
        return Int(levelString)
    }
    
    /// Extract height above ground like `temperature_2m`
    fileprivate static func getModelLevel(_ variable: String) -> Int? {
        guard let pos = variable.lastIndex(of: "_"), let posEnd = variable[pos..<variable.endIndex].range(of: "m") else {
            return nil
        }
        let start = variable.index(after: pos)
        let levelString = variable[start..<posEnd.lowerBound]
        return Int(levelString)
    }
    
    /// Pressure levels to keep
    /// Keep the same pressure profile required by the Shanghai OM products.
    /// This is storage selection only; values and interpolation remain upstream.
    fileprivate static let pressureLevelsToKeep = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 50]
}

