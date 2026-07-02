import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_openmeteo_pressure_profile_package.py"


def load_module():
    spec = importlib.util.spec_from_file_location("build_openmeteo_pressure_profile_package", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_pressure_profile_levels_match_product_contract():
    profile = load_module()

    assert profile.PRESSURE_LEVELS_HPA == (
        1000,
        975,
        950,
        925,
        900,
        850,
        800,
        750,
        700,
        650,
        600,
        550,
        500,
        400,
        300,
        200,
    )


def test_pressure_profile_has_eight_fields_per_level():
    profile = load_module()

    assert [field.name for field in profile.PROFILE_FIELDS] == [
        "geopotential_height_m",
        "temperature_c",
        "relative_humidity_pct",
        "dew_point_c",
        "cloud_cover_pct",
        "wind_speed_ms",
        "wind_direction_deg",
        "vertical_velocity_ms",
    ]


def test_pressure_profile_required_variables_are_openmeteo_pressure_api_names():
    profile = load_module()

    variables = profile.required_variables(profile.PROFILE_FIELDS, profile.PRESSURE_LEVELS_HPA)

    assert "temperature_850hPa" in variables
    assert "relative_humidity_850hPa" in variables
    assert "dew_point_850hPa" in variables
    assert "cloud_cover_850hPa" in variables
    assert "wind_speed_850hPa" in variables
    assert "wind_direction_850hPa" in variables
    assert "geopotential_height_850hPa" in variables
    assert "specific_humidity_850hPa" not in variables
    assert "vertical_velocity_850hPa" in variables
