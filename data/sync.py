"""通达信行情的清单化、版本化写入接口。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
from pathlib import Path
from typing import Iterable

import pandas as pd

from data.tdx_parser import parse_txt
from data.versioning import (
    DataManifest,
    PriceSnapshot,
    SourceInput,
    build_data_manifest,
    persist_data_manifest,
    persist_price_snapshot,
)


_PRICE_FIELDS = ("open", "high", "low", "close")
_BASES = ("qfq", "hfq", "actual")
_INSERT_COLUMNS = (
    "code",
    "trade_date",
    "price_snapshot_id",
    *(f"{basis}_{field}" for basis in _BASES for field in _PRICE_FIELDS),
    "adjustment_factor",
    "source_price_basis",
    "volume",
    "amount",
    "source",
)
_INSERT_SQL = f"""INSERT INTO daily_price ({', '.join(_INSERT_COLUMNS)})
VALUES ({', '.join(['%s'] * len(_INSERT_COLUMNS))})
ON CONFLICT (code, trade_date, price_snapshot_id) DO NOTHING"""


@dataclass(frozen=True)
class SyncReport:
    """一次显式文件批次导入的结果和可追溯版本 ID。"""

    data_manifest_ids: dict[str, int]
    price_snapshot_id: int
    counts: dict[str, int]
    issues: tuple[str, ...]
    status: str

    @property
    def data_manifest_id(self) -> int | None:
        """单一价格口径批次的便捷访问器；多口径时返回 None。"""

        if len(self.data_manifest_ids) != 1:
            return None
        return next(iter(self.data_manifest_ids.values()))


def _database_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def import_price_frame(
    conn,
    frame: pd.DataFrame,
    *,
    price_snapshot_id: int,
    source: str = "tdx",
) -> int:
    """幂等写入单一价格口径的已解析日线数据。"""

    if frame.empty:
        raise ValueError("行情数据帧不能为空")
    if price_snapshot_id <= 0:
        raise ValueError("price_snapshot_id 必须是正整数")
    if not source.strip():
        raise ValueError("source 不能为空")
    if "source_price_basis" not in frame:
        raise ValueError("缺少 source_price_basis")

    bases = set(frame["source_price_basis"].dropna().astype(str))
    if len(bases) != 1 or not bases.issubset({"actual", "qfq", "hfq"}):
        raise ValueError("一次只能导入一种价格口径：actual/qfq/hfq")
    basis = next(iter(bases))
    required = {"code", "trade_date", "volume", "amount"}
    required.update(f"{basis}_{field}" for field in _PRICE_FIELDS)
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"行情数据帧缺少字段：{', '.join(sorted(missing))}")

    rows = []
    for record in frame.to_dict(orient="records"):
        values = [
            str(record["code"]),
            record["trade_date"],
            price_snapshot_id,
        ]
        for price_basis in _BASES:
            for field in _PRICE_FIELDS:
                column = f"{price_basis}_{field}"
                values.append(_database_value(record.get(column)))
        values.extend(
            (
                None,
                basis,
                _database_value(record["volume"]),
                _database_value(record["amount"]),
                source,
            )
        )
        rows.append(tuple(values))

    with conn.cursor() as cursor:
        cursor.executemany(_INSERT_SQL, rows)
        inserted = cursor.rowcount
    return max(inserted, 0)


def import_tdx_file(
    conn,
    path: str | Path,
    *,
    price_snapshot_id: int,
    source: str = "tdx",
) -> int:
    """解析一个通达信 TXT 并幂等写入指定价格快照。"""

    return import_price_frame(
        conn,
        parse_txt(path),
        price_snapshot_id=price_snapshot_id,
        source=source,
    )


def _input_metadata(manifest: DataManifest, manifest_id: int) -> dict[str, object]:
    metadata = manifest.to_dict()
    metadata["manifest_id"] = manifest_id
    return metadata


def _parse_verified_file(
    path: Path, *, expected_sha256: str | None = None
) -> tuple[pd.DataFrame, str]:
    """检查解析前后文件内容一致，次遍额外比对首遍清单校验和。"""

    try:
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and before != expected_sha256:
            raise ValueError(f"来源文件在预检后发生变化：{path}")
        frame = parse_txt(path, allow_empty=True)
        after = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"无法读取来源文件：{path}") from exc
    if before != after:
        raise ValueError(f"来源文件在解析期间发生变化：{path}")
    return frame, before


def _filter_new_dates(
    frame: pd.DataFrame, after_dates: dict[str, date] | None
) -> pd.DataFrame:
    if frame.empty or after_dates is None:
        return frame
    parsed_dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
    latest_dates = frame["code"].map(after_dates)
    keep = latest_dates.isna() | (parsed_dates > latest_dates)
    return frame.loc[keep].reset_index(drop=True)


def import_tdx_files(
    conn,
    paths: Iterable[str | Path],
    *,
    source_version: str,
    source: str = "tdx",
    expected_basis: str | None = None,
    after_dates: dict[str, date] | None = None,
    parent_snapshot_id: int | None = None,
) -> SyncReport:
    """导入显式指定的 TXT 批次，并生成清单及共同价格快照。

    同一批次只允许一种源价格口径，确保主键冲突时不会丢弃另一口径数据。
    数据库事务的提交和回滚由调用方控制。
    """

    if not source.strip():
        raise ValueError("source 不能为空")
    if not source_version.strip():
        raise ValueError("source_version 不能为空")

    file_paths = sorted((Path(path) for path in paths), key=lambda path: str(path))
    if not file_paths:
        raise ValueError("至少需要一个通达信 TXT 文件")
    if len(set(file_paths)) != len(file_paths):
        raise ValueError("导入文件列表不能包含重复路径")

    empty_paths: list[Path] = []
    checksums: dict[Path, str] = {}
    rows_seen = 0
    rows_to_import = 0
    batch_basis: str | None = None

    def source_inputs():
        nonlocal rows_seen, rows_to_import, batch_basis
        # 首遍完整预检，只保留文件元数据；任意坏输入都在数据库写入前失败。
        for path in file_paths:
            frame, checksum = _parse_verified_file(path)
            checksums[path] = checksum
            basis = (
                frame.attrs["source_price_basis"]
                if frame.empty
                else str(frame.iloc[0]["source_price_basis"])
            )
            if batch_basis is not None and basis != batch_basis:
                raise ValueError("同一批次只能导入一种价格口径")
            batch_basis = basis
            if expected_basis is not None and basis != expected_basis:
                raise ValueError(
                    f"输入文件价格口径为 {basis}，与要求的 {expected_basis} 不一致"
                )
            if frame.empty:
                empty_paths.append(path)
            rows_seen += len(frame)
            rows_to_import += len(_filter_new_dates(frame, after_dates))
            yield SourceInput(
                path=path,
                frame=frame,
                source=source,
                source_version=source_version,
                price_basis=basis,
            )

    manifest = build_data_manifest(source_inputs())
    for record in manifest.files:
        if record.sha256 != checksums[Path(record.path)]:
            raise ValueError(f"来源文件在预检期间发生变化：{record.path}")
    if len(empty_paths) == len(file_paths):
        status = "invalid"
    elif empty_paths:
        status = "partial"
    else:
        status = "complete"
    manifest = replace(manifest, status=status)
    issues = tuple(f"文件没有行情数据：{path}" for path in empty_paths)

    if after_dates is not None and rows_to_import == 0:
        return SyncReport(
            data_manifest_ids={},
            price_snapshot_id=parent_snapshot_id or 0,
            counts={
                "files_total": len(file_paths),
                "files_imported": 0,
                "files_empty": len(empty_paths),
                "files_invalid": 0,
                "rows_seen": rows_seen,
                "rows_inserted": 0,
                "rows_skipped": rows_seen,
            },
            issues=issues,
            status=status,
        )

    manifests = {manifest.price_basis: manifest}
    manifest_ids = {
        basis: persist_data_manifest(conn, manifest)
        for basis, manifest in sorted(manifests.items())
    }
    snapshot_source_manifest = {
        "inputs": {
            basis: _input_metadata(manifests[basis], manifest_id)
            for basis, manifest_id in sorted(manifest_ids.items())
        }
    }
    if parent_snapshot_id is not None:
        snapshot_source_manifest["parent_price_snapshot_id"] = parent_snapshot_id
    snapshot = PriceSnapshot(
        id=0,
        input_manifest_ids=manifest_ids,
        source_manifest=snapshot_source_manifest,
        status=status,
        parent_snapshot_id=parent_snapshot_id,
    )
    snapshot_id = persist_price_snapshot(conn, snapshot)

    rows_inserted = 0
    files_imported = 0
    # 次遍按文件解析并写入；重新核验校验和，防止预检后源文件被替换。
    # 次遍失败时调用方必须回滚整个事务，不能提交此前已写入的版本或行情。
    for path in file_paths:
        frame, _ = _parse_verified_file(path, expected_sha256=checksums[path])
        frame = _filter_new_dates(frame, after_dates)
        if frame.empty:
            continue
        files_imported += 1
        rows_inserted += import_price_frame(
            conn,
            frame,
            price_snapshot_id=snapshot_id,
            source=source,
        )

    return SyncReport(
        data_manifest_ids=manifest_ids,
        price_snapshot_id=snapshot_id,
        counts={
            "files_total": len(file_paths),
            "files_imported": files_imported,
            "files_empty": len(empty_paths),
            "files_invalid": 0,
            "rows_seen": rows_seen,
            "rows_inserted": rows_inserted,
            "rows_skipped": rows_seen - rows_inserted,
        },
        issues=issues,
        status=status,
    )


def import_tdx_directory(
    conn,
    directory: str | Path,
    *,
    source_version: str,
    source: str = "tdx",
    expected_basis: str | None = None,
    after_dates: dict[str, date] | None = None,
    parent_snapshot_id: int | None = None,
) -> SyncReport:
    """按文件名稳定排序导入目录中的 TXT，不读取其他文件。"""

    directory_path = Path(directory)
    if not directory_path.is_dir():
        raise ValueError(f"行情目录不存在或不是目录：{directory_path}")
    paths = sorted(
        (path for path in directory_path.iterdir() if path.is_file() and path.suffix.lower() == ".txt"),
        key=lambda path: str(path),
    )
    if not paths:
        raise ValueError(f"行情目录没有 TXT 文件：{directory_path}")
    return import_tdx_files(
        conn,
        paths,
        source_version=source_version,
        source=source,
        expected_basis=expected_basis,
        after_dates=after_dates,
        parent_snapshot_id=parent_snapshot_id,
    )


def _latest_actual_snapshot(conn) -> tuple[int, dict[str, date]]:
    with conn.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_row = cursor.fetchone()
        database_name = database_row[0] if database_row else None
        if database_name != "stocktool":
            raise ValueError(
                f"增量导入仅允许连接 stocktool 数据库，当前数据库为 {database_name!r}"
            )
        cursor.execute(
            """SELECT id FROM price_snapshots
               WHERE input_actual_manifest_id IS NOT NULL
               ORDER BY id DESC LIMIT 1"""
        )
        snapshot_row = cursor.fetchone()
        if not snapshot_row:
            raise ValueError("stocktool 中没有 actual 历史快照；请先执行历史全量导入")
        snapshot_id = int(snapshot_row[0])
        cursor.execute(
            """SELECT code, MAX(trade_date)
               FROM daily_price_for_snapshot(%s) GROUP BY code""",
            (snapshot_id,),
        )
        latest_dates = {
            str(code): pd.Timestamp(trade_date).date()
            for code, trade_date in cursor.fetchall()
        }
        if not latest_dates:
            raise ValueError(
                "stocktool 的 actual 基线快照没有行情数据；请先执行历史全量导入"
            )
    return snapshot_id, latest_dates


def run(
    mode: str,
    conn,
    *,
    input_dir: str | Path,
    source_version: str,
    source: str = "tdx",
) -> SyncReport:
    """执行显式 actual 历史全量、增量或源 qfq 导入；事务由调用方控制。"""

    if mode not in {"full_actual", "incremental", "direct_qfq"}:
        if mode == "full_qfq":
            raise NotImplementedError("full_qfq 需等待可审计复权因子，实现尚未启用")
        raise ValueError(f"不支持的同步模式：{mode}")
    if mode == "incremental":
        parent_snapshot_id, latest_dates = _latest_actual_snapshot(conn)
        return import_tdx_directory(
            conn,
            input_dir,
            source_version=source_version,
            source=source,
            expected_basis="actual",
            after_dates=latest_dates,
            parent_snapshot_id=parent_snapshot_id,
        )
    with conn.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database_row = cursor.fetchone()
    if not database_row or database_row[0] != "stocktool":
        database_name = database_row[0] if database_row else None
        raise ValueError(
            f"同步仅允许连接 stocktool 数据库，当前数据库为 {database_name!r}"
        )
    expected_basis = "actual" if mode == "full_actual" else "qfq"
    return import_tdx_directory(
        conn,
        input_dir,
        source_version=source_version,
        source=source,
        expected_basis=expected_basis,
    )
