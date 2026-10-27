"""P02：direct qfq 与 actual 配对扫描的确定性测试。"""

from pathlib import Path

import pytest

from data.qfq_pair import scan_direct_qfq


FIXTURES = Path(__file__).parent / "fixtures" / "tdx"


def write_export(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def test_scan_direct_qfq_pairs_dates_and_volume_amount(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    write_export(actual_dir / "SH#600001.txt", (FIXTURES / "actual.txt").read_bytes())
    write_export(qfq_dir / "SH#600001.txt", (FIXTURES / "qfq.txt").read_bytes())

    report = scan_direct_qfq(actual_dir, qfq_dir)

    assert report.file_count_actual == 1
    assert report.file_count_qfq == 1
    assert report.missing_files == ()
    assert report.paired_rows == 2
    assert report.date_mismatch_files == ()
    assert report.volume_mismatch_rows == 0
    assert report.amount_mismatch_rows == 0
    assert report.ohlc_difference_rows == 2
    assert report.ohlc_difference_cells == {
        "open": 2,
        "high": 2,
        "low": 2,
        "close": 2,
    }
    assert report.code_mismatch_files == ()
    assert report.status == "complete"


def test_scan_direct_qfq_marks_empty_pair_partial_and_reports_basis(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    empty_actual = (
        "000001 测试 日线 不复权\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        "#数据来源:通达信\n"
    ).encode("gb18030")
    empty_qfq = (
        "000001 测试 日线 前复权\n日期 开盘 最高 最低 收盘 成交量 成交额\n"
        "#数据来源:通达信\n"
    ).encode("gb18030")
    write_export(actual_dir / "SH#600001.txt", empty_actual.replace(b"000001", b"600001"))
    write_export(qfq_dir / "SH#600001.txt", empty_qfq.replace(b"000001", b"600001"))

    report = scan_direct_qfq(actual_dir, qfq_dir)

    assert report.status == "partial"
    assert report.empty_pair_files == ("SH#600001.txt",)
    assert report.actual_basis == ("actual",)
    assert report.qfq_basis == ("qfq",)


def test_scan_direct_qfq_rejects_missing_counterpart(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    write_export(actual_dir / "SH#600001.txt", (FIXTURES / "actual.txt").read_bytes())

    report = scan_direct_qfq(actual_dir, qfq_dir)

    assert report.status == "invalid"
    assert report.missing_files == ("SH#600001.txt",)
    assert report.paired_rows == 0


def test_scan_direct_qfq_rejects_non_direct_qfq_header(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    write_export(actual_dir / "SH#600001.txt", (FIXTURES / "actual.txt").read_bytes())
    write_export(qfq_dir / "SH#600001.txt", (FIXTURES / "hfq.txt").read_bytes())

    with pytest.raises(ValueError, match="qfq 目录文件头必须是 qfq"):
        scan_direct_qfq(actual_dir, qfq_dir)


def test_scan_direct_qfq_marks_security_code_mismatch_invalid(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    actual = (FIXTURES / "actual.txt").read_bytes().replace(b"000001", b"600001")
    qfq = (FIXTURES / "qfq.txt").read_bytes().replace(b"600001", b"600002")
    write_export(actual_dir / "SH#600001.txt", actual)
    write_export(qfq_dir / "SH#600001.txt", qfq)

    report = scan_direct_qfq(actual_dir, qfq_dir)

    assert report.status == "invalid"
    assert report.code_mismatch_files == ("SH#600001.txt",)


def test_scan_direct_qfq_marks_duplicate_dates_invalid(tmp_path):
    actual_dir = tmp_path / "actual"
    qfq_dir = tmp_path / "qfq"
    actual_dir.mkdir()
    qfq_dir.mkdir()
    actual = (FIXTURES / "actual.txt").read_bytes()
    duplicate_actual = actual.replace(b"2000/01/05,", b"2000/01/04,")
    qfq = (FIXTURES / "qfq.txt").read_bytes()
    duplicate_qfq = qfq.replace(b"2000/01/05,", b"2000/01/04,")
    assert duplicate_actual != actual
    assert duplicate_qfq != qfq
    write_export(actual_dir / "SH#600001.txt", duplicate_actual)
    write_export(qfq_dir / "SH#600001.txt", duplicate_qfq)

    report = scan_direct_qfq(actual_dir, qfq_dir)

    assert report.status == "invalid"
    assert report.duplicate_date_files == ("SH#600001.txt",)
