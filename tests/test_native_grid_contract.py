from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from native_grid_contract import cams_domain_grids, gfs_domain_grids


class NativeGridContractTests(unittest.TestCase):
    def test_default_gfs_grids_match_vendored_swift_regional_slice(self):
        grids = gfs_domain_grids()

        self.assertEqual((grids["ncep_gfs013"]["nx"], grids["ncep_gfs013"]["ny"]), (597, 495))
        self.assertEqual((grids["ncep_gfs025"]["nx"], grids["ncep_gfs025"]["ny"]), (281, 233))
        self.assertEqual(grids["ncep_gfs013"]["halo_cells"], 0)
        self.assertEqual(grids["ncep_gfs025"]["halo_cells"], 0)
        self.assertEqual(grids["ncep_gfs013"]["dt_seconds"], 3600)
        self.assertEqual(grids["ncep_gfs013"]["om_file_length"], 481)

    def test_default_cams_grid_matches_vendored_swift_regional_slice(self):
        grid = cams_domain_grids()["cams_global"]

        self.assertEqual((grid["nx"], grid["ny"]), (176, 146))
        self.assertEqual(grid["halo_cells"], 0)
        self.assertAlmostEqual(grid["lon_min"], 70.0)
        self.assertAlmostEqual(grid["lat_min"], 0.0)
        self.assertEqual(grid["om_file_length"], 217)

    def test_custom_bounds_are_recorded(self):
        grid = gfs_domain_grids(72.0, 138.0, 2.0, 56.0)["ncep_gfs025"]

        self.assertEqual(
            grid["requested_bounds"],
            {"left_lon": 72.0, "right_lon": 138.0, "bottom_lat": 2.0, "top_lat": 56.0},
        )


if __name__ == "__main__":
    unittest.main()
