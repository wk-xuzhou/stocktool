"""行情输入清单和价格快照的最小版本化流程。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Iterable, Literal, Mapping

import pandas as pd


PriceBasis = Literal["actual", "qfq", "hfq"]
ManifestStatus = Literal["building", "complete", "partial", "invalid"]
_PRICE_BASES = frozenset(("actual", "qfq", "hfq"))
_STATUSES = frozenset(("building", "complete", "partial", "invalid"))


class VersionReferencedError(RuntimeError):
    """尝试覆盖已经被价格快照引用的输入版本。"""


@dataclass(frozen=True)
class SourceInput:
    """一组来源文件及其已解析内容。"""

    path: str | Path
    frame: pd.DataFrame
    source: str
    source_version: str
    price_basis: PriceBasis | None = None


@dataclass(frozen=True)
class SourceFileRecord:
    path: str
    sha256: str
    codes: tuple[str, ...]
    start_date: date | None
    end_date: date | None
    row_count: int


@dataclass(frozen=True)
class DataManifest:
    id: int | None
    source: str
    source_version: str
    price_basis: PriceBasis
    codes: tuple[str, ...]
    start_date: date | None
    end_date: date | None
    row_count: int
    status: ManifestStatus
    files: tuple[SourceFileRecord, ...]

    def to_dict(self) -> dict[str, object]:
        """返回可直接写入 JSONB 的清单结构。"""

        return {
            "manifest_id": self.id,
            "source": self.source,
            "source_version": self.source_version,
            "price_basis": self.price_basis,
            "codes": list(self.codes),
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "row_count": self.row_count,
            "status": self.status,
            "files": [
                {
                    "path": record.path,
                    "sha256": record.sha256,
                    "codes": list(record.codes),
                    "start_date": (
                        record.start_date.isoformat() if record.start_date else None
                    ),
                    "end_date": (
                        record.end_date.isoformat() if record.end_date else None
                    ),
                    "row_count": record.row_count,
                }
                for record in self.files
            ],
        }


@dataclass(frozen=True)
class PriceSnapshot:
    id: int
    input_manifest_ids: dict[str, int]
    source_manifest: dict[str, object]
    status: ManifestStatus
    parent_snapshot_id: int | None = None


def persist_data_manifest(conn, manifest: DataManifest) -> int:
    """将清单追加写入数据库并返回数据库生成的 ID。"""

    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO data_manifests
               (source, source_version, price_basis, start_date, end_date,
                code_count, row_count, source_manifest, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
               RETURNING id""",
            (
                manifest.source,
                manifest.source_version,
                manifest.price_basis,
                manifest.start_date,
                manifest.end_date,
                len(manifest.codes),
                manifest.row_count,
                json.dumps(manifest.to_dict(), ensure_ascii=False),
                manifest.status,
            ),
        )
        row = cursor.fetchone()
    return int(row[0])


def persist_price_snapshot(conn, snapshot: PriceSnapshot) -> int:
    """将价格快照及其 actual/qfq/hfq 清单引用追加写入数据库。"""

    input_ids = snapshot.input_manifest_ids
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO price_snapshots
               (source_manifest, input_actual_manifest_id,
                input_qfq_manifest_id, input_hfq_manifest_id, status,
                parent_price_snapshot_id)
               VALUES (%s::jsonb, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                json.dumps(snapshot.source_manifest, ensure_ascii=False),
                input_ids.get("actual"),
                input_ids.get("qfq"),
                input_ids.get("hfq"),
                snapshot.status,
                snapshot.parent_snapshot_id,
            ),
        )
        row = cursor.fetchone()
    return int(row[0])


def _validate_status(status: str) -> ManifestStatus:
    if status not in _STATUSES:
        raise ValueError(f"状态无效：{status}")
    return status  # type: ignore[return-value]


def _normalise_dates(frame: pd.DataFrame) -> pd.Series:
    try:
        dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
    except (TypeError, ValueError) as exc:
        raise ValueError("trade_date 必须是有效日期") from exc
    return dates


def _source_file_record(source: SourceInput) -> tuple[SourceFileRecord, PriceBasis]:
    frame = source.frame
    required = {"code", "trade_date", "source_price_basis"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"输入缺少字段：{', '.join(sorted(missing))}")

    raw_bases = set(frame["source_price_basis"].dropna().astype(str))
    if frame.empty and not raw_bases:
        if source.price_basis not in _PRICE_BASES:
            raise ValueError("空输入必须显式提供 actual/qfq/hfq 价格口径")
        raw_bases = {source.price_basis}
    if len(raw_bases) != 1 or not raw_bases.issubset(_PRICE_BASES):
        raise ValueError("输入只能包含一种价格口径：actual/qfq/hfq")
    basis = next(iter(raw_bases))

    file_path = Path(source.path)
    try:
        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"无法读取来源文件：{file_path}") from exc

    if frame.empty:
        record = SourceFileRecord(
            path=str(file_path),
            sha256=sha256,
            codes=(),
            start_date=None,
            end_date=None,
            row_count=0,
        )
        return record, basis  # type: ignore[return-value]

    codes = tuple(sorted(set(frame["code"].astype(str))))
    if any(not code for code in codes):
        raise ValueError("code 不能为空")
    dates = _normalise_dates(frame)
    return (
        SourceFileRecord(
            path=str(file_path),
            sha256=sha256,
            codes=codes,
            start_date=min(dates),
            end_date=max(dates),
            row_count=len(frame),
        ),
        basis,  # type: ignore[return-value]
    )


def build_data_manifest(
    source: SourceInput | Iterable[SourceInput],
    *,
    status: ManifestStatus = "building",
) -> DataManifest:
    """根据一个或多个已解析源文件生成一条不可变数据清单。"""

    manifest_status = _validate_status(status)
    sources = (source,) if isinstance(source, SourceInput) else source
    source_names: set[str] = set()
    source_versions: set[str] = set()
    bases: set[PriceBasis] = set()
    file_records: list[SourceFileRecord] = []
    # 每个输入只提取元数据，不将生成器或已处理 DataFrame 转成完整列表。
    for item in sources:
        source_names.add(item.source)
        source_versions.add(item.source_version)
        if len(source_names) != 1 or not item.source.strip():
            raise ValueError("同一清单的 source 必须一致且非空")
        if len(source_versions) != 1 or not item.source_version.strip():
            raise ValueError("同一清单的 source_version 必须一致且非空")
        record, basis = _source_file_record(item)
        bases.add(basis)
        if len(bases) != 1:
            raise ValueError("同一数据清单只能包含一种价格口径")
        file_records.append(record)
    if not file_records:
        raise ValueError("至少需要一个来源文件")
    records = tuple(file_records)
    codes = tuple(sorted({code for record in records for code in record.codes}))
    start_dates = [record.start_date for record in records if record.start_date]
    end_dates = [record.end_date for record in records if record.end_date]
    return DataManifest(
        id=None,
        source=next(iter(source_names)),
        source_version=next(iter(source_versions)),
        price_basis=next(iter(bases)),
        codes=codes,
        start_date=min(start_dates) if start_dates else None,
        end_date=max(end_dates) if end_dates else None,
        row_count=sum(record.row_count for record in records),
        status=manifest_status,
        files=records,
    )


class VersionRegistry:
    """用于确定性测试和离线构建的不可变版本注册器。

    生产数据库写入将在同步任务中复用 DataManifest/PriceSnapshot 的结构；
    注册器本身不读取网络，也不覆盖已被快照引用的清单。
    """

    def __init__(self) -> None:
        self._next_manifest_id = 1
        self._next_snapshot_id = 1
        self._manifests: dict[int, DataManifest] = {}
        self._snapshots: dict[int, PriceSnapshot] = {}

    def add_manifest(self, manifest: DataManifest) -> int:
        manifest_id = self._next_manifest_id
        self._next_manifest_id += 1
        self._manifests[manifest_id] = replace(manifest, id=manifest_id)
        return manifest_id

    def get_manifest(self, manifest_id: int) -> DataManifest:
        try:
            return self._manifests[manifest_id]
        except KeyError as exc:
            raise KeyError(f"数据清单不存在：{manifest_id}") from exc

    def replace_manifest(self, manifest_id: int, manifest: DataManifest) -> None:
        if any(
            manifest_id in snapshot.input_manifest_ids.values()
            for snapshot in self._snapshots.values()
        ):
            raise VersionReferencedError(
                f"数据清单 {manifest_id} 已被价格快照引用，不能覆盖已引用版本"
            )
        self.get_manifest(manifest_id)
        self._manifests[manifest_id] = replace(manifest, id=manifest_id)

    def add_price_snapshot(
        self,
        input_manifest_ids: Mapping[str, int],
        *,
        status: ManifestStatus = "building",
        parent_snapshot_id: int | None = None,
    ) -> PriceSnapshot:
        snapshot_status = _validate_status(status)
        if not input_manifest_ids:
            raise ValueError("价格快照至少需要一个输入清单")

        normalized_ids = {str(basis): int(manifest_id) for basis, manifest_id in input_manifest_ids.items()}
        if not set(normalized_ids).issubset(_PRICE_BASES):
            raise ValueError("价格快照输入口径必须是 actual/qfq/hfq")
        if parent_snapshot_id is not None and parent_snapshot_id not in self._snapshots:
            raise ValueError(f"父价格快照不存在：{parent_snapshot_id}")

        inputs: dict[str, object] = {}
        for basis, manifest_id in normalized_ids.items():
            manifest = self.get_manifest(manifest_id)
            if manifest.price_basis != basis:
                raise ValueError(
                    f"输入清单 {manifest_id} 的口径为 {manifest.price_basis}，不能标记为 {basis}"
                )
            inputs[basis] = {
                "manifest_id": manifest_id,
                "source": manifest.source,
                "source_version": manifest.source_version,
                "price_basis": manifest.price_basis,
                "sha256": [record.sha256 for record in manifest.files],
                "codes": list(manifest.codes),
                "start_date": (
                    manifest.start_date.isoformat() if manifest.start_date else None
                ),
                "end_date": manifest.end_date.isoformat() if manifest.end_date else None,
                "row_count": manifest.row_count,
                "status": manifest.status,
            }

        snapshot_id = self._next_snapshot_id
        self._next_snapshot_id += 1
        snapshot = PriceSnapshot(
            id=snapshot_id,
            input_manifest_ids=normalized_ids,
            source_manifest={"inputs": inputs},
            status=snapshot_status,
            parent_snapshot_id=parent_snapshot_id,
        )
        self._snapshots[snapshot_id] = snapshot
        return snapshot
