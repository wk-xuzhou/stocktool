"""direct qfq 与 actual 的只读流式配对扫描。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import zip_longest
from pathlib import Path
import re
from typing import Literal, Iterator

from data.tdx_parser import TdxParseError


PairStatus = Literal["complete", "partial", "invalid"]
_MISSING = object()
_HEADER = re.compile(
    r"\s*(?P<code>\d{1,6})\s+.+?\s+日线\s+"
    r"(?P<basis>不复权|前复权|后复权)\s*"
)
_BASIS_MAP = {"不复权": "actual", "前复权": "qfq", "后复权": "hfq"}
_PRICE_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class PairReport:
    file_count_actual: int
    file_count_qfq: int
    missing_files: tuple[str, ...]
    empty_pair_files: tuple[str, ...]
    date_mismatch_files: tuple[str, ...]
    row_count_mismatch_files: tuple[str, ...]
    code_mismatch_files: tuple[str, ...]
    duplicate_date_files: tuple[str, ...]
    paired_rows: int
    volume_mismatch_rows: int
    amount_mismatch_rows: int
    ohlc_difference_rows: int
    ohlc_difference_cells: dict[str, int]
    actual_basis: tuple[str, ...]
    qfq_basis: tuple[str, ...]
    status: PairStatus


def _files(directory: str | Path) -> dict[str, Path]:
    path = Path(directory)
    return {file_path.name: file_path for file_path in path.glob("*.txt")}


def _open_export(path: Path, expected_basis: str) -> tuple[str, Iterator[str], bool]:
    try:
        text_file = path.open("r", encoding="gb18030", newline=None)
        header = text_file.readline().strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise TdxParseError(f"无法读取文件头：{path}") from exc
    match = _HEADER.fullmatch(header)
    if not match:
        text_file.close()
        raise TdxParseError(f"文件头无法识别：{path}")

    code = match.group("code").zfill(6)
    basis = _BASIS_MAP[match.group("basis")]
    if basis != expected_basis:
        text_file.close()
        label = "actual" if expected_basis == "actual" else "qfq"
        raise ValueError(f"{label} 目录文件头必须是 {expected_basis}：{path.name}")
    filename_code = re.search(r"#(\d{6})\.txt$", path.name)
    filename_mismatch = bool(filename_code and filename_code.group(1) != code)

    text_file.readline()  # 列名
    return code, text_file, filename_mismatch


def _rows(file_obj: Iterator[str], path: Path) -> Iterator[tuple[str, list[str]]]:
    for line_number, raw_line in enumerate(file_obj, start=3):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 7:
            raise TdxParseError(f"{path.name} 第 {line_number} 行不是 7 列")
        try:
            datetime.strptime(parts[0], "%Y/%m/%d")
            for value in parts[1:5]:
                Decimal(value)
            volume = Decimal(parts[5])
            amount = Decimal(parts[6])
            if not volume.is_finite() or not amount.is_finite() or not volume % 1 == 0:
                raise ValueError
        except (InvalidOperation, ValueError) as exc:
            raise TdxParseError(f"{path.name} 第 {line_number} 行字段无效") from exc
        yield parts[0], parts[1:]


def scan_direct_qfq(
    actual_dir: str | Path,
    qfq_dir: str | Path,
) -> PairReport:
    """流式校验文件集合、证券代码、日期和量额，不推导复权价格。"""

    actual_files = _files(actual_dir)
    qfq_files = _files(qfq_dir)
    names = sorted(actual_files.keys() | qfq_files.keys())
    missing_files = tuple(
        name for name in names if name not in actual_files or name not in qfq_files
    )
    common_names = sorted(actual_files.keys() & qfq_files.keys())
    empty_pair_files: list[str] = []
    date_mismatch_files: list[str] = []
    row_count_mismatch_files: list[str] = []
    code_mismatch_files: list[str] = []
    duplicate_date_files: list[str] = []
    actual_basis: set[str] = set()
    qfq_basis: set[str] = set()
    paired_rows = 0
    volume_mismatch_rows = 0
    amount_mismatch_rows = 0
    ohlc_difference_rows = 0
    ohlc_difference_cells = dict.fromkeys(_PRICE_FIELDS, 0)

    for name in common_names:
        actual_code, actual_file, actual_filename_mismatch = _open_export(
            actual_files[name], "actual"
        )
        qfq_code, qfq_file, qfq_filename_mismatch = _open_export(qfq_files[name], "qfq")
        actual_basis.add("actual")
        qfq_basis.add("qfq")
        try:
            if (
                actual_code != qfq_code
                or actual_filename_mismatch
                or qfq_filename_mismatch
            ):
                code_mismatch_files.append(name)
                continue

            actual_iter = iter(_rows(actual_file, actual_files[name]))
            qfq_iter = iter(_rows(qfq_file, qfq_files[name]))
            actual_count = 0
            qfq_count = 0
            file_date_mismatch = False
            file_has_duplicate = False
            actual_previous_date = None
            qfq_previous_date = None
            for actual_row, qfq_row in zip_longest(
                actual_iter, qfq_iter, fillvalue=_MISSING
            ):
                if actual_row is not _MISSING:
                    actual_count += 1
                    if actual_row[0] == actual_previous_date:
                        file_has_duplicate = True
                    actual_previous_date = actual_row[0]
                if qfq_row is not _MISSING:
                    qfq_count += 1
                    if qfq_row[0] == qfq_previous_date:
                        file_has_duplicate = True
                    qfq_previous_date = qfq_row[0]
                if actual_row is _MISSING or qfq_row is _MISSING:
                    continue
                actual_date, actual_values = actual_row
                qfq_date, qfq_values = qfq_row
                if actual_date != qfq_date:
                    file_date_mismatch = True
                    continue
                paired_rows += 1
                if Decimal(actual_values[4]) != Decimal(qfq_values[4]):
                    volume_mismatch_rows += 1
                if Decimal(actual_values[5]) != Decimal(qfq_values[5]):
                    amount_mismatch_rows += 1
                row_has_price_difference = False
                for index, field in enumerate(_PRICE_FIELDS):
                    if Decimal(actual_values[index]) != Decimal(qfq_values[index]):
                        ohlc_difference_cells[field] += 1
                        row_has_price_difference = True
                if row_has_price_difference:
                    ohlc_difference_rows += 1

            if actual_count == 0 and qfq_count == 0:
                empty_pair_files.append(name)
            elif actual_count != qfq_count:
                row_count_mismatch_files.append(name)
            if file_date_mismatch:
                date_mismatch_files.append(name)
            if file_has_duplicate:
                duplicate_date_files.append(name)
        finally:
            actual_file.close()
            qfq_file.close()

    structural_issue = bool(
        missing_files
        or date_mismatch_files
        or row_count_mismatch_files
        or code_mismatch_files
        or duplicate_date_files
        or volume_mismatch_rows
        or amount_mismatch_rows
    )
    if structural_issue:
        status: PairStatus = "invalid"
    elif empty_pair_files:
        status = "partial"
    else:
        status = "complete"

    return PairReport(
        file_count_actual=len(actual_files),
        file_count_qfq=len(qfq_files),
        missing_files=missing_files,
        empty_pair_files=tuple(empty_pair_files),
        date_mismatch_files=tuple(date_mismatch_files),
        row_count_mismatch_files=tuple(row_count_mismatch_files),
        code_mismatch_files=tuple(code_mismatch_files),
        duplicate_date_files=tuple(duplicate_date_files),
        paired_rows=paired_rows,
        volume_mismatch_rows=volume_mismatch_rows,
        amount_mismatch_rows=amount_mismatch_rows,
        ohlc_difference_rows=ohlc_difference_rows,
        ohlc_difference_cells=ohlc_difference_cells,
        actual_basis=tuple(sorted(actual_basis)),
        qfq_basis=tuple(sorted(qfq_basis)),
        status=status,
    )
