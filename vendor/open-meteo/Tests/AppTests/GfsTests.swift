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

    @Test func nomadsRegionalPackingNoiseIsRestoredToTheDeclaredLattice() {
        var values: [Float] = [-0.749999, -0.740001, .nan]

        normalizeNomadsRepackedGribValues(
            &values,
            referenceValue: -1047,
            binaryScaleFactor: 0,
            decimalScaleFactor: 2
        )

        #expect(values[0] == -0.75)
        #expect(values[1] == -0.74)
        #expect(values[2].isNaN)
    }

    @Test func nomadsRegionalPackingRestorationSupportsBinaryScaleFactors() {
        var values: [Float] = [0.37499997]

        normalizeNomadsRepackedGribValues(
            &values,
            referenceValue: 0,
            binaryScaleFactor: -3,
            decimalScaleFactor: 0
        )

        #expect(values == [0.375])
    }

    @Test func nomadsSimplePackingReferenceDoesNotUseRoundedEccodesString() {
        var values: [Float] = [98336.19375, 99324.99375]
        let reference = nomadsSimplePackingReferenceValue(
            decodedValues: values,
            decimalScaleFactor: 1
        )

        #expect(reference == 983361.9375)
        normalizeNomadsRepackedGribValues(
            &values,
            referenceValue: reference!,
            binaryScaleFactor: 2,
            decimalScaleFactor: 1
        )

        #expect(values[1] == Float(99324.99375))
        #expect(values[1] < 99325)
    }

    @Test func originalGfsPackingHeaderRestoresTheGlobalPressureLattice() {
        let reference = Float(21938.2578125)
        var bytes = Array("GRIB".utf8) + [UInt8](repeating: 0, count: 12)
        bytes[7] = 2
        bytes += [0, 0, 0, 5, 1]
        bytes += [
            0, 0, 0, 21, 5,
            0, 0, 0, 1,
            0, 3,
            UInt8((reference.bitPattern >> 24) & 0xff),
            UInt8((reference.bitPattern >> 16) & 0xff),
            UInt8((reference.bitPattern >> 8) & 0xff),
            UInt8(reference.bitPattern & 0xff),
            0, 0,
            0, 2,
            11,
            0,
        ]

        let packing = parseGfsOriginalPackingHeader(bytes)
        #expect(packing == GfsOriginalPacking(
            referenceValue: 21938.2578125,
            binaryScaleFactor: 0,
            decimalScaleFactor: 2
        ))

        var regionalValues: [Float] = [Float(273.40255859375)]
        normalizeNomadsRepackedGribValues(
            &regionalValues,
            referenceValue: packing!.referenceValue,
            binaryScaleFactor: packing!.binaryScaleFactor,
            decimalScaleFactor: packing!.decimalScaleFactor
        )
        #expect(regionalValues == [Float(273.402578125)])
    }
}
