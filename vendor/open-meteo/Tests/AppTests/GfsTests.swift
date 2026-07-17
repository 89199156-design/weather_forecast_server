@testable import App
import Testing

@Suite struct GfsTests {
    @Test func precipitationScalingUsesTheNativeForecastInterval() {
        let oneHour = GfsSurfaceVariable.precipitation.multiplyAdd(
            domain: .gfs013,
            dtSeconds: 3_600
        )
        let threeHours = GfsSurfaceVariable.precipitation.multiplyAdd(
            domain: .gfs013,
            dtSeconds: 10_800
        )
        let showers = GfsSurfaceVariable.showers.multiplyAdd(
            domain: .gfs013,
            dtSeconds: 10_800
        )

        #expect(oneHour?.multiply == 3_600)
        #expect(threeHours?.multiply == 10_800)
        #expect(showers?.multiply == 10_800)
    }
}
