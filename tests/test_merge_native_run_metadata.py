import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "merge_native_run_metadata.py"
spec = importlib.util.spec_from_file_location("merge_native_run_metadata", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_partial_metadata_is_additive_and_keeps_hour_zero():
    original = {
        "reference_time": "2026-07-13T18:00:00Z",
        "created_at": "old",
        "variables": ["temperature_2m", "cloud_cover"],
        "valid_times": ["2026-07-13T18:00Z", "2026-07-13T19:00Z"],
    }
    partial = {
        "reference_time": "2026-07-13T18:00:00Z",
        "created_at": "new",
        "variables": ["uv_index_clear_sky"],
        "valid_times": ["2026-07-13T19:00Z"],
    }

    merged = module.merge_metadata(original, partial)

    assert merged["created_at"] == "new"
    assert merged["variables"] == ["temperature_2m", "cloud_cover", "uv_index_clear_sky"]
    assert merged["valid_times"] == ["2026-07-13T18:00Z", "2026-07-13T19:00Z"]


def test_reference_time_must_match():
    try:
        module.merge_metadata(
            {"reference_time": "2026-07-13T12:00:00Z"},
            {"reference_time": "2026-07-13T18:00:00Z"},
        )
    except ValueError as exc:
        assert "reference_time" in str(exc)
    else:
        raise AssertionError("mismatched reference_time must fail")
