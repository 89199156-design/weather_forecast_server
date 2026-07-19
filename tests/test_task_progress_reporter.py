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
    assert "完成｜任务：GFS 生产更新｜批次：2026071912" in output
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

