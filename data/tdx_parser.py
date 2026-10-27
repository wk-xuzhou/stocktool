"""通达信日线 TXT 解析与价格口径规范化。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
from typing import Literal

import pandas as pd


VolumeUnit = Literal["shares", "lots"]

_BASIS_MAP = {
    "不复权": "actual",
    "前复权": "qfq",
    "后复权": "hfq",
}
_PRICE_FIELDS = ("open", "high", "low", "close")
_OUTPUT_COLUMNS = (
    "code",
    "name",
    "trade_date",
    "actual_open",
    "actual_high",
    "actual_low",
    "actual_close",
    "qfq_open",
    "qfq_high",
    "qfq_low",
    "qfq_close",
    "hfq_open",
    "hfq_high",
    "hfq_low",
    "hfq_close",
    "volume",
    "amount",
    "source_price_basis",
)


class TdxParseError(ValueError):
    """通达信 TXT 无法安全解析时抛出。"""


def _parse_header(line: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"\s*(?P<code>\d{1,6})\s+(?P<name>.+?)\s+日线\s+(?P<basis>不复权|前复权|后复权)\s*",
        line,
    )
    if not match:
        raise TdxParseError("文件头无法识别；请确认文件为 GB18030 编码的通达信 TXT")

    code = match.group("code").zfill(6)
    return code, match.group("name").strip(), _BASIS_MAP[match.group("basis")]


def _parse_number(value: str, *, line_no: int, label: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise TdxParseError(f"第 {line_no} 行的{label}不是数字") from exc


def parse_txt(
    path: str | Path,
    *,
    volume_unit: VolumeUnit = "shares",
    allow_empty: bool = False,
) -> pd.DataFrame:
    """解析单个通达信日线 TXT 文件。

    文件头决定源价格口径：不复权映射为 ``actual``，前复权映射为
    ``qfq``，后复权映射为 ``hfq``。其他价格口径列保留为缺失值。
    """

    if volume_unit not in ("shares", "lots"):
        raise ValueError("volume_unit 必须是 'shares' 或 'lots'")

    file_path = Path(path)
    try:
        text = file_path.read_bytes().decode("gb18030")
    except UnicodeDecodeError as exc:
        raise TdxParseError("文件不是有效的 GB18030 编码") from exc
    except OSError as exc:
        raise TdxParseError(f"无法读取文件：{file_path}") from exc

    lines = text.splitlines()
    if len(lines) < 2:
        raise TdxParseError("文件缺少通达信文件头或列名")

    code, name, source_basis = _parse_header(lines[0])
    if not all(label in lines[1] for label in ("日期", "开盘", "最高", "最低", "收盘")):
        raise TdxParseError("列名行无法识别，缺少日期或 OHLC 字段")

    records: list[dict[str, object]] = []
    seen_dates: set[date] = set()
    for line_no, raw_line in enumerate(lines[2:], start=3):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            raise TdxParseError(f"第 {line_no} 行不是 7 列")

        try:
            trade_date = datetime.strptime(parts[0], "%Y/%m/%d").date()
        except ValueError as exc:
            raise TdxParseError(f"第 {line_no} 行日期格式错误") from exc
        if trade_date in seen_dates:
            raise TdxParseError(f"第 {line_no} 行存在重复交易日：{trade_date.isoformat()}")
        seen_dates.add(trade_date)

        prices = {
            field: _parse_number(value, line_no=line_no, label=field)
            for field, value in zip(_PRICE_FIELDS, parts[1:5], strict=True)
        }
        try:
            volume_value = float(parts[5])
            if not volume_value.is_integer():
                raise ValueError
        except ValueError as exc:
            raise TdxParseError(f"第 {line_no} 行成交量不是整数") from exc
        volume = int(volume_value) * (100 if volume_unit == "lots" else 1)
        amount = _parse_number(parts[6], line_no=line_no, label="成交额")

        record: dict[str, object] = {
            "code": code,
            "name": name,
            "trade_date": trade_date,
            "volume": volume,
            "amount": amount,
            "source_price_basis": source_basis,
        }
        for basis in _BASIS_MAP.values():
            for field in _PRICE_FIELDS:
                record[f"{basis}_{field}"] = (
                    prices[field] if basis == source_basis else float("nan")
                )
        records.append(record)

    if not records:
        if not allow_empty:
            raise TdxParseError(f"文件没有行情数据：{file_path.name}")
        frame = pd.DataFrame(columns=_OUTPUT_COLUMNS)
        frame.attrs["tdx_code"] = code
        frame.attrs["tdx_name"] = name
        frame.attrs["source_price_basis"] = source_basis
        return frame

    return pd.DataFrame.from_records(records, columns=_OUTPUT_COLUMNS)
