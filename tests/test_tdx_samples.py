"""校验 P01 固定样例；样例为人工数据，不依赖本机原始行情。"""

from decimal import Decimal
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "tdx"


@pytest.mark.parametrize(
    "filename,basis",
    [
        ("actual.txt", "不复权"),
        ("qfq.txt", "前复权"),
        ("hfq.txt", "后复权"),
    ],
)
def test_samples_preserve_observed_export_structure(filename, basis):
    raw = (FIXTURES / filename).read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    lines = raw.decode("gb18030").splitlines()
    assert lines[0] == f"600001 测试样例 日线 {basis}"
    assert lines[1].split() == ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额"]
    assert lines[-1] == "#数据来源:通达信"
    assert all(len(line.split(",")) == 7 for line in lines[2:-1])


def test_sample_volume_is_already_shares_and_amount_is_yuan():
    lines = (FIXTURES / "actual.txt").read_bytes().decode("gb18030").splitlines()
    for line in lines[2:-1]:
        _, _, high, low, _, volume, amount = line.split(",")
        high, low, volume, amount = map(Decimal, (high, low, volume, amount))
        assert low <= amount / volume <= high
        assert amount / (volume * 100) < low


def test_paired_samples_do_not_imply_a_single_multiplicative_factor():
    rows = []
    for filename in ("actual.txt", "hfq.txt"):
        lines = (FIXTURES / filename).read_bytes().decode("gb18030").splitlines()
        rows.append(lines[2].split(","))
    actual, hfq = rows
    assert actual[0] == hfq[0]
    assert actual[5:] == hfq[5:]
    close_factor = Decimal(hfq[4]) / Decimal(actual[4])
    assert abs(Decimal(actual[2]) * close_factor - Decimal(hfq[2])) > Decimal("0.01")


def test_direct_qfq_fixture_pairs_with_actual_by_date_and_keeps_volume_amount():
    actual_lines = (FIXTURES / "actual.txt").read_bytes().decode("gb18030").splitlines()
    qfq_lines = (FIXTURES / "qfq.txt").read_bytes().decode("gb18030").splitlines()
    actual = {line.split(",")[0]: line.split(",")[1:] for line in actual_lines[2:-1]}
    qfq = {line.split(",")[0]: line.split(",")[1:] for line in qfq_lines[2:-1]}

    assert actual.keys() == qfq.keys()
    for trade_date in actual:
        assert actual[trade_date][4:] == qfq[trade_date][4:]
        assert actual[trade_date][:4] != qfq[trade_date][:4]
