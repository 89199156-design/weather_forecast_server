import time
from io import StringIO
from pathlib import Path

from scripts.task_progress_reporter import RETURN_CODE_PREFIX, report_progress


def test_reporter_outputs_compact_chinese_progress_and_keeps_raw_log(tmp_path, capsys):
    watched = tmp_path / "staging"
    watched.mkdir()
    (watched / "payload.om").write_bytes(b"x" * 1024)
    raw_log = tmp_path / "raw.log"
    stream = StringIO(
        '{"stage":"downloading","product":"gfs013_surface","coverage_id":"gfs_2026071912"}\n'
        + RETURN_CODE_PREFIX
        + "0\n"
    )

    result = report_progress(
        task="GFS 生产更新",
        default_stage="检查最新批次",
        watch_roots=[watched],
        log_file=raw_log,
        interval_seconds=3600,
        input_stream=stream,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "开始｜任务：GFS 生产更新" in output
    assert "完成｜任务：GFS 生产更新｜目标批次：2026071912" in output
    assert "最后处理批次：2026071912" in output
    assert "gfs013_surface" in raw_log.read_text(encoding="utf-8")
    assert RETURN_CODE_PREFIX not in raw_log.read_text(encoding="utf-8")


def test_reporter_returns_failure_and_points_to_raw_log(tmp_path, capsys):
    raw_log = tmp_path / "raw.log"
    result = report_progress(
        task="CAMS 生产更新",
        default_stage="检查最新批次",
        watch_roots=[],
        log_file=raw_log,
        interval_seconds=3600,
        input_stream=StringIO("download cams_global run=2026071900\n" + RETURN_CODE_PREFIX + "125\n"),
    )

    output = capsys.readouterr().out
    assert result == 125
    assert "失败｜任务：CAMS 生产更新" in output
    assert "阶段：下载 CAMS 全球空气质量" in output
    assert "退出码：125" in output


def test_reporter_emits_progress_while_child_output_is_quiet(tmp_path, capsys):
    class QuietStream:
        def __iter__(self):
            yield 'download cams_global run=2026071900\n'
            time.sleep(0.08)
            yield RETURN_CODE_PREFIX + "0\n"

    raw_log = tmp_path / "raw.log"
    result = report_progress(
        task="CAMS 生产更新",
        default_stage="检查最新批次",
        watch_roots=[],
        log_file=raw_log,
        interval_seconds=0.02,
        input_stream=QuietStream(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "进度｜任务：CAMS 生产更新" in output
    assert "阶段：下载 CAMS 全球空气质量" in output


def test_reporter_shows_target_and_current_gfs_run_product_and_forecast_hour(
    tmp_path, capsys
):
    class GfsStream:
        def __iter__(self):
            yield "2026-07-20T18:35:54Z [OPENMETEO_GFS_PROBE] complete official run=2026072012\n"
            yield "2026-07-20T18:49:09Z [OPENMETEO_GFS_OM] download role=previous-complete run=2026072006 horizon=384\n"
            yield "Downloading GFS input group=gfs013_surface run=2026072006\n"
            yield "[ INFO ] Downloading forecastHour 62\n"
            time.sleep(0.05)
            yield RETURN_CODE_PREFIX + "0\n"

    raw_log = tmp_path / "raw.log"
    result = report_progress(
        task="GFS 生产更新",
        default_stage="检查最新批次",
        watch_roots=[],
        log_file=raw_log,
        interval_seconds=0.02,
        input_stream=GfsStream(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "阶段：下载 GFS 0.13°地面层" in output
    assert "目标批次：2026072012" in output
    assert "当前批次：2026072006" in output
    assert "当前时效：f062/f384" in output


def test_reporter_distinguishes_gfs025_pressure_from_surface(tmp_path, capsys):
    class PressureStream:
        def __iter__(self):
            yield "official run=2026072012\n"
            yield "download role=previous-complete run=2026072006 horizon=384\n"
            yield "Downloading GFS input group=gfs025_pressure levels=1000,500 run=2026072006\n"
            yield "[ INFO ] Downloading forecastHour 12\n"
            time.sleep(0.05)
            yield RETURN_CODE_PREFIX + "0\n"

    result = report_progress(
        task="GFS 生产更新",
        default_stage="检查最新批次",
        watch_roots=[],
        log_file=tmp_path / "raw.log",
        interval_seconds=0.02,
        input_stream=PressureStream(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "阶段：下载 GFS 0.25°气压层" in output
    assert "当前批次：2026072006" in output
