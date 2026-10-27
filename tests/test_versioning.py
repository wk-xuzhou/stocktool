"""D05：价格快照和数据清单的确定性测试。"""

from dataclasses import replace
from datetime import date
import hashlib

import pandas as pd
import pytest

from data.versioning import (
    PriceSnapshot,
    SourceInput,
    VersionReferencedError,
    VersionRegistry,
    build_data_manifest,
    persist_data_manifest,
    persist_price_snapshot,
)


def source_frame(basis: str, code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": [code, code],
            "trade_date": [date(2026, 10, 22), date(2026, 10, 23)],
            "source_price_basis": [basis, basis],
        }
    )


def make_source_input(tmp_path, basis: str, code: str = "000001") -> SourceInput:
    path = tmp_path / f"{basis}.txt"
    path.write_bytes(f"{code},{basis}\n".encode("gb18030"))
    return SourceInput(
        path=path,
        frame=source_frame(basis, code),
        source="tdx",
        source_version="export-20261025",
    )


def test_manifest_records_source_hash_range_count_basis_and_status(tmp_path):
    source = make_source_input(tmp_path, "actual")

    manifest = build_data_manifest(source, status="complete")

    expected_sha256 = hashlib.sha256(source.path.read_bytes()).hexdigest()
    assert manifest.source == "tdx"
    assert manifest.source_version == "export-20261025"
    assert manifest.price_basis == "actual"
    assert manifest.codes == ("000001",)
    assert manifest.start_date == date(2026, 10, 22)
    assert manifest.end_date == date(2026, 10, 23)
    assert manifest.row_count == 2
    assert manifest.status == "complete"
    assert manifest.files[0].sha256 == expected_sha256
    assert manifest.files[0].path == str(source.path)


def test_snapshot_traces_actual_qfq_hfq_input_versions(tmp_path):
    registry = VersionRegistry()
    manifest_ids = {}
    for basis in ("actual", "qfq", "hfq"):
        manifest = build_data_manifest(make_source_input(tmp_path, basis))
        manifest_ids[basis] = registry.add_manifest(manifest)

    snapshot = registry.add_price_snapshot(manifest_ids, status="complete")

    assert snapshot.status == "complete"
    assert snapshot.input_manifest_ids == manifest_ids
    for basis, manifest_id in manifest_ids.items():
        assert snapshot.source_manifest["inputs"][basis]["manifest_id"] == manifest_id
        assert snapshot.source_manifest["inputs"][basis]["sha256"]


def test_referenced_manifest_cannot_be_overwritten(tmp_path):
    registry = VersionRegistry()
    manifest = build_data_manifest(make_source_input(tmp_path, "actual"))
    manifest_id = registry.add_manifest(manifest)
    registry.add_price_snapshot({"actual": manifest_id}, status="partial")

    replacement = replace(manifest, source_version="export-replaced")
    with pytest.raises(VersionReferencedError, match="已被价格快照引用"):
        registry.replace_manifest(manifest_id, replacement)

    assert registry.get_manifest(manifest_id).source_version == "export-20261025"


def test_manifest_rejects_mixed_basis_and_unknown_status(tmp_path):
    actual = make_source_input(tmp_path, "actual")
    qfq = make_source_input(tmp_path, "qfq")
    mixed = SourceInput(
        path=actual.path,
        frame=pd.concat([actual.frame, qfq.frame], ignore_index=True),
        source="tdx",
        source_version="export-20261025",
    )

    with pytest.raises(ValueError, match="只能包含一种价格口径"):
        build_data_manifest(mixed)
    with pytest.raises(ValueError, match="状态无效"):
        build_data_manifest(actual, status="ready")


def test_manifest_aggregates_multiple_files_of_same_price_basis(tmp_path):
    first = make_source_input(tmp_path, "actual", "000001")
    second_path = tmp_path / "actual-2.txt"
    second_path.write_bytes(b"000002,actual\n")
    second = SourceInput(
        path=second_path,
        frame=source_frame("actual", "000002"),
        source="tdx",
        source_version="export-20261025",
    )

    manifest = build_data_manifest([first, second])

    assert manifest.codes == ("000001", "000002")
    assert manifest.row_count == 4
    assert len(manifest.files) == 2


def test_empty_source_can_be_recorded_as_invalid_with_explicit_basis(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_bytes(b"000001 ")
    source = SourceInput(
        path=path,
        frame=pd.DataFrame(columns=["code", "trade_date", "source_price_basis"]),
        source="tdx",
        source_version="export-20261025",
        price_basis="actual",
    )

    manifest = build_data_manifest(source, status="invalid")

    assert manifest.price_basis == "actual"
    assert manifest.row_count == 0
    assert manifest.start_date is None
    assert manifest.status == "invalid"


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.connection.calls.append((query, params))

    def fetchone(self):
        return (len(self.connection.calls) + 20,)


class FakeConnection:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return FakeCursor(self)


def test_persist_functions_append_manifest_and_snapshot_records(tmp_path):
    conn = FakeConnection()
    manifest = build_data_manifest(make_source_input(tmp_path, "qfq"))

    manifest_id = persist_data_manifest(conn, manifest)
    snapshot = PriceSnapshot(
        id=1,
        input_manifest_ids={"qfq": manifest_id},
        source_manifest={"inputs": {"qfq": {"manifest_id": manifest_id}}},
        status="partial",
    )
    snapshot_id = persist_price_snapshot(conn, snapshot)

    assert manifest_id == 21
    assert snapshot_id == 22
    assert "INSERT INTO data_manifests" in conn.calls[0][0]
    assert "INSERT INTO price_snapshots" in conn.calls[1][0]
    assert conn.calls[1][1][2] == manifest_id


def test_manifest_consumes_generator_without_retaining_source_frames(tmp_path):
    import gc
    import weakref

    references = []

    def sources():
        for code in ("000001", "000002", "000003", "000004"):
            gc.collect()
            assert sum(reference() is not None for reference in references) <= 1
            item = make_source_input(tmp_path, "actual", code)
            references.append(weakref.ref(item.frame))
            yield item

    manifest = build_data_manifest(sources(), status="complete")

    assert manifest.codes == ("000001", "000002", "000003", "000004")
    assert manifest.row_count == 8
    assert len(manifest.files) == 4


def test_manifest_rejects_empty_generator():
    with pytest.raises(ValueError, match="至少需要一个来源文件"):
        build_data_manifest(iter(()))
