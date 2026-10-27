"""D01：通达信 TXT 解析器的确定性测试。"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data.tdx_parser import TdxParseError, parse_txt


FIXTURES = Path(__file__).parent / "fixtures" / "tdx"


def write_tdx(path: Path, header: str, rows: list[str]) -> Path:
    content = "\r\n".join(
        [
            header,
            "      日期\t    开盘\t    最高\t    最低\t    收盘\t    成交量\t    成交额",
            *rows,
            "#数据来源:通达信",
        ]
    ) + "\r\n"
    path.write_bytes(content.encode("gb18030"))
    return path


def test_parse_tdx_normalizes_code_basis_and_source_price_columns(tmp_path):
    path = write_tdx(
        tmp_path / "SZ#000001.txt",
        "1 测试银行 日线 前复权",
        ["2026/10/23,5.10,5.30,5.00,5.20,1000,5200.00"],
    )

    frame = parse_txt(path)

    assert list(frame["code"]) == ["000001"]
    assert frame.loc[0, "name"] == "测试银行"
    assert frame.loc[0, "trade_date"] == date(2026, 10, 23)
    assert frame.loc[0, "source_price_basis"] == "qfq"
    assert frame.loc[0, "qfq_open"] == pytest.approx(5.10)
    assert frame.loc[0, "qfq_close"] == pytest.approx(5.20)
    assert pd.isna(frame.loc[0, "actual_close"])
    assert pd.isna(frame.loc[0, "hfq_close"])
    assert frame.loc[0, "volume"] == 1000
    assert frame.loc[0, "amount"] == pytest.approx(5200.00)


def test_parse_tdx_keeps_shares_by_default_and_converts_lots_only_explicitly(tmp_path):
    path = write_tdx(
        tmp_path / "SH#600000.txt",
        "600000 测试银行 日线 不复权",
        ["2026/10/23,10.00,10.00,10.00,10.00,10,100.00"],
    )

    shares = parse_txt(path)
    lots = parse_txt(path, volume_unit="lots")

    assert shares.loc[0, "volume"] == 10
    assert lots.loc[0, "volume"] == 1000
    assert shares.loc[0, "amount"] == pytest.approx(100.00)


def test_parse_tdx_rejects_bad_encoding_and_bad_rows(tmp_path):
    utf8_path = tmp_path / "utf8.txt"
    utf8_path.write_text(
        "000001 测试 日线 不复权\n日期 开盘 最高 最低 收盘 成交量 成交额\n",
        encoding="utf-8",
    )
    with pytest.raises(TdxParseError, match="GB18030"):
        parse_txt(utf8_path)

    bad_row = write_tdx(
        tmp_path / "bad.txt",
        "000001 测试 日线 不复权",
        ["2026/10/23,1,1,1,1,100"],
    )
    with pytest.raises(TdxParseError, match="7 列"):
        parse_txt(bad_row)


def test_parse_tdx_rejects_duplicate_dates_and_empty_exports(tmp_path):
    duplicate = write_tdx(
        tmp_path / "duplicate.txt",
        "000001 测试 日线 不复权",
        [
            "2026/10/23,1,1,1,1,100,100",
            "2026/10/23,1,1,1,1,100,100",
        ],
    )
    with pytest.raises(TdxParseError, match="重复交易日"):
        parse_txt(duplicate)

    empty = write_tdx(tmp_path / "empty.txt", "000001 测试 日线 不复权", [])
    with pytest.raises(TdxParseError, match="没有行情数据"):
        parse_txt(empty)


def test_parse_tdx_reads_fixed_gb18030_actual_fixture():
    frame = parse_txt(FIXTURES / "actual.txt")

    assert frame["source_price_basis"].unique().tolist() == ["actual"]
    assert frame["trade_date"].tolist() == [date(2000, 1, 4), date(2000, 1, 5)]
    assert frame["actual_close"].tolist() == [10.0, 10.5]


def test_parse_tdx_can_return_empty_export_metadata_for_manifest(tmp_path):
    empty = write_tdx(tmp_path / "empty.txt", "688827 空数据 日线 前复权", [])

    frame = parse_txt(empty, allow_empty=True)

    assert frame.empty
    assert frame.attrs["tdx_code"] == "688827"
    assert frame.attrs["tdx_name"] == "空数据"
    assert frame.attrs["source_price_basis"] == "qfq"
