"""D08：通达信日线写入的确定性测试。"""

from pathlib import Path

import pytest

from data import sync
from data.sync import import_price_frame, import_tdx_file
from data.tdx_parser import parse_txt


FIXTURES = Path(__file__).parent / "fixtures" / "tdx"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 2

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def executemany(self, query, rows):
        self.conn.query = query
        self.conn.rows = list(rows)

    def execute(self, query, params=None):
        self.result = ("stocktool",)

    def fetchone(self):
        return getattr(self, "result", None)


class FakeConnection:
    def cursor(self):
        return FakeCursor(self)


@pytest.mark.parametrize(
    "filename,basis,price_column",
    [
        ("actual.txt", "actual", 11),
        ("qfq.txt", "qfq", 3),
        ("hfq.txt", "hfq", 7),
    ],
)
def test_import_price_frame_writes_only_source_basis_columns(
    filename, basis, price_column
):
    conn = FakeConnection()
    frame = parse_txt(FIXTURES / filename)

    inserted = import_price_frame(conn, frame, price_snapshot_id=42)

    assert inserted == 2
    assert "ON CONFLICT (code, trade_date, price_snapshot_id) DO NOTHING" in conn.query
    assert len(conn.rows) == 2
    assert conn.rows[0][0] == "600001"
    assert conn.rows[0][2] == 42
    assert conn.rows[0][16] == basis
    assert conn.rows[0][price_column] is not None
    basis_start = {"qfq": 3, "hfq": 7, "actual": 11}[basis]
    for start in (3, 7, 11):
        if start != basis_start:
            assert conn.rows[0][start : start + 4] == (None, None, None, None)


def test_import_tdx_file_parses_and_inserts_source_file():
    conn = FakeConnection()

    inserted = import_tdx_file(
        conn,
        FIXTURES / "actual.txt",
        price_snapshot_id=7,
        source="tdx",
    )

    assert inserted == 2
    assert all(row[2] == 7 for row in conn.rows)
    assert all(row[19] == "tdx" for row in conn.rows)


def test_import_price_frame_rejects_mixed_basis_before_database_write():
    conn = FakeConnection()
    actual = parse_txt(FIXTURES / "actual.txt")
    qfq = parse_txt(FIXTURES / "qfq.txt")
    import pandas as pd

    mixed = pd.concat([actual.iloc[:1], qfq.iloc[:1]], ignore_index=True)

    with pytest.raises(ValueError, match="只能导入一种价格口径"):
        import_price_frame(conn, mixed, price_snapshot_id=1)

    assert not hasattr(conn, "query")


def test_import_price_frame_rejects_empty_frame():
    conn = FakeConnection()
    frame = parse_txt(FIXTURES / "actual.txt").iloc[:0]

    with pytest.raises(ValueError, match="不能为空"):
        import_price_frame(conn, frame, price_snapshot_id=1)

    assert not hasattr(conn, "query")


def write_tdx_file(path: Path, header: str, rows: list[str]) -> Path:
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


def test_import_tdx_directory_builds_manifest_snapshot_and_imports_files(
    tmp_path, monkeypatch
):
    first = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/22,10,11,9,10.5,100,1050"],
    )
    second = write_tdx_file(
        tmp_path / "SZ#000001.txt",
        "1 测试科技 日线 不复权",
        ["2026/10/23,20,21,19,20.5,200,4100"],
    )
    captured = {}

    def fake_persist_manifest(conn, manifest):
        captured["manifest"] = manifest
        return 101

    def fake_persist_snapshot(conn, snapshot):
        captured["snapshot"] = snapshot
        return 202

    def fake_import(conn, frame, *, price_snapshot_id, source):
        captured.setdefault("frames", []).append(
            (frame.copy(), price_snapshot_id, source)
        )
        return len(frame)

    monkeypatch.setattr(sync, "persist_data_manifest", fake_persist_manifest)
    monkeypatch.setattr(sync, "persist_price_snapshot", fake_persist_snapshot)
    monkeypatch.setattr(sync, "import_price_frame", fake_import)

    report = sync.import_tdx_files(
        FakeConnection(),
        [first, second],
        source_version="tdx-export-20261026",
    )

    manifest = captured["manifest"]
    snapshot = captured["snapshot"]
    assert report.data_manifest_ids == {"actual": 101}
    assert report.price_snapshot_id == 202
    assert report.status == "complete"
    assert report.counts == {
        "files_total": 2,
        "files_imported": 2,
        "files_empty": 0,
        "files_invalid": 0,
        "rows_seen": 2,
        "rows_inserted": 2,
        "rows_skipped": 0,
    }
    assert report.issues == ()
    assert manifest.source == "tdx"
    assert manifest.source_version == "tdx-export-20261026"
    assert manifest.price_basis == "actual"
    assert manifest.codes == ("000001", "600001")
    assert len(manifest.files) == 2
    assert snapshot.status == "complete"
    assert snapshot.input_manifest_ids == {"actual": 101}
    assert snapshot.source_manifest["inputs"]["actual"]["manifest_id"] == 101
    assert snapshot.source_manifest["inputs"]["actual"]["price_basis"] == "actual"
    assert [item[1] for item in captured["frames"]] == [202, 202]
    assert all(item[2] == "tdx" for item in captured["frames"])


def test_import_tdx_directory_records_empty_file_as_partial_without_writing_empty_frame(
    tmp_path, monkeypatch
):
    valid = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 前复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    empty = write_tdx_file(
        tmp_path / "SH#688827.txt",
        "688827 空数据 日线 前复权",
        [],
    )
    captured = {}

    def fake_persist_manifest(conn, manifest):
        captured["manifest"] = manifest
        return 301

    def fake_persist_snapshot(conn, snapshot):
        captured["snapshot"] = snapshot
        return 302

    monkeypatch.setattr(sync, "persist_data_manifest", fake_persist_manifest)
    monkeypatch.setattr(sync, "persist_price_snapshot", fake_persist_snapshot)

    def fake_import(conn, frame, *, price_snapshot_id, source):
        captured.setdefault("frames", []).append(frame.copy())
        return len(frame)

    monkeypatch.setattr(sync, "import_price_frame", fake_import)

    report = sync.import_tdx_files(
        FakeConnection(),
        [valid, empty],
        source_version="tdx-export-20261026",
    )

    assert report.status == "partial"
    assert report.counts == {
        "files_total": 2,
        "files_imported": 1,
        "files_empty": 1,
        "files_invalid": 0,
        "rows_seen": 1,
        "rows_inserted": 1,
        "rows_skipped": 0,
    }
    assert len(report.issues) == 1
    assert "没有行情数据" in report.issues[0]
    assert captured["manifest"].status == "partial"
    assert len(captured["manifest"].files) == 2
    assert {record.path for record in captured["manifest"].files} == {
        str(valid),
        str(empty),
    }
    assert captured["snapshot"].status == "partial"
    assert len(captured["frames"]) == 1
    assert len(captured["frames"][0]) == 1


@pytest.mark.parametrize(
    "basis,header,price_column",
    [
        ("qfq", "600001 测试银行 日线 前复权", "qfq_close"),
        ("hfq", "600001 测试银行 日线 后复权", "hfq_close"),
    ],
)
def test_import_tdx_directory_traces_optional_source_basis(
    tmp_path, monkeypatch, basis, header, price_column
):
    path = write_tdx_file(
        tmp_path / f"{basis}.txt",
        header,
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    captured = {}

    def fake_persist_manifest(conn, manifest):
        captured["manifest"] = manifest
        return 401

    def fake_persist_snapshot(conn, snapshot):
        captured["snapshot"] = snapshot
        return 402

    monkeypatch.setattr(sync, "persist_data_manifest", fake_persist_manifest)
    monkeypatch.setattr(sync, "persist_price_snapshot", fake_persist_snapshot)

    def fake_import(conn, frame, *, price_snapshot_id, source):
        captured["frame"] = frame
        return len(frame)

    monkeypatch.setattr(sync, "import_price_frame", fake_import)

    report = sync.import_tdx_files(
        FakeConnection(),
        [path],
        source_version="tdx-export-20261026",
    )

    assert report.status == "complete"
    assert captured["manifest"].price_basis == basis
    assert captured["snapshot"].input_manifest_ids == {basis: 401}
    assert captured["snapshot"].source_manifest["inputs"][basis]["price_basis"] == basis
    assert captured["frame"].loc[0, "source_price_basis"] == basis
    assert captured["frame"].loc[0, price_column] == pytest.approx(10.5)
    assert report.counts["rows_inserted"] == 1
    assert path.exists()


def test_import_tdx_files_rejects_mixed_price_bases_before_persistence(
    tmp_path, monkeypatch
):
    actual = write_tdx_file(
        tmp_path / "actual.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    qfq = write_tdx_file(
        tmp_path / "qfq.txt",
        "600001 测试银行 日线 前复权",
        ["2026/10/23,8,9,7,8.5,100,850"],
    )
    calls = []
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda conn, manifest: calls.append(manifest)
    )

    with pytest.raises(ValueError, match="同一批次只能导入一种价格口径"):
        sync.import_tdx_files(
            FakeConnection(), [actual, qfq], source_version="tdx-export-20261026"
        )

    assert calls == []



def test_import_tdx_files_rejects_expected_basis_mismatch_before_persistence(
    tmp_path, monkeypatch
):
    actual = write_tdx_file(
        tmp_path / "actual.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    calls = []
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda conn, manifest: calls.append(manifest)
    )

    with pytest.raises(ValueError, match="与要求的 qfq 不一致"):
        sync.import_tdx_files(
            FakeConnection(),
            [actual],
            source_version="tdx-export-20261026",
            expected_basis="qfq",
        )

    assert calls == []



def test_import_tdx_files_rejects_duplicate_paths_before_database_write(tmp_path):
    path = write_tdx_file(
        tmp_path / "actual.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )

    with pytest.raises(ValueError, match="重复路径"):
        sync.import_tdx_files(
            FakeConnection(), [path, path], source_version="tdx-export-20261026"
        )


def test_import_tdx_directory_ignores_non_txt_and_checks_expected_basis(
    tmp_path, monkeypatch
):
    actual = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    (tmp_path / "README.md").write_text("not an export", encoding="utf-8")
    monkeypatch.setattr(sync, "persist_data_manifest", lambda conn, manifest: 1)
    monkeypatch.setattr(sync, "persist_price_snapshot", lambda conn, snapshot: 2)
    monkeypatch.setattr(sync, "import_price_frame", lambda *args, **kwargs: 1)

    report = sync.import_tdx_directory(
        FakeConnection(),
        tmp_path,
        source_version="tdx-export-20261026",
        expected_basis="actual",
    )

    assert report.counts["files_total"] == 1
    assert report.data_manifest_id == 1
    assert actual.exists()

    with pytest.raises(ValueError, match="价格口径为 actual"):
        sync.import_tdx_directory(
            FakeConnection(),
            tmp_path,
            source_version="tdx-export-20261026",
            expected_basis="qfq",
        )


def test_run_dispatches_incremental_and_direct_qfq_modes(tmp_path, monkeypatch):
    actual = write_tdx_file(
        tmp_path / "actual.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,10,11,9,10.5,100,1050"],
    )
    calls = []

    def fake_directory(conn, directory, **kwargs):
        calls.append((directory, kwargs))
        return "report"

    monkeypatch.setattr(sync, "import_tdx_directory", fake_directory)
    monkeypatch.setattr(sync, "_latest_actual_snapshot", lambda conn: (8, {}))

    assert sync.run(
        "incremental", FakeConnection(), input_dir=tmp_path, source_version="v1"
    ) == "report"
    assert calls[0][0] == tmp_path
    assert calls[0][1]["expected_basis"] == "actual"

    assert sync.run(
        "direct_qfq", FakeConnection(), input_dir=tmp_path, source_version="v2"
    ) == "report"
    assert calls[1][1]["expected_basis"] == "qfq"
    assert actual.exists()

    with pytest.raises(NotImplementedError, match="full_qfq"):
        sync.run(
            "full_qfq",
            FakeConnection(),
            input_dir=tmp_path,
            source_version="v3",
        )


class IncrementalCursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        self.conn.queries.append((query, params))
        if "current_database()" in query:
            self.result = (self.conn.database_name,)
        elif "FROM price_snapshots" in query:
            self.result = (8,) if self.conn.snapshot_exists else None
        elif "MAX(trade_date)" in query:
            self.result = self.conn.latest_snapshot_rows
        elif "INSERT INTO daily_price" in query and "SELECT" in query:
            self.conn.copied_snapshot = params

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result


class IncrementalConnection:
    def __init__(
        self, database_name="stocktool", latest_snapshot_rows=None, snapshot_exists=None
    ):
        self.database_name = database_name
        self.latest_snapshot_rows = latest_snapshot_rows or []
        self.snapshot_exists = (
            bool(self.latest_snapshot_rows)
            if snapshot_exists is None
            else snapshot_exists
        )
        self.queries = []
        self.copied_snapshot = None

    def cursor(self):
        return IncrementalCursor(self)


def test_incremental_import_filters_rows_by_latest_date_and_copies_snapshot(
    tmp_path, monkeypatch
):
    path = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        [
            "2026/10/22,10,11,9,10.5,100,1050",
            "2026/10/23,11,12,10,11.5,110,1265",
            "2026/10/26,12,13,11,12.5,120,1500",
        ],
    )
    conn = IncrementalConnection(
        latest_snapshot_rows=[("600001", "2026-10-23")]
    )
    captured = {}
    monkeypatch.setattr(sync, "persist_data_manifest", lambda *args: 101)
    monkeypatch.setattr(sync, "persist_price_snapshot", lambda *args: 202)

    def capture_import(conn, frame, *, price_snapshot_id, source):
        captured["frame"] = frame.copy()
        return len(frame)

    monkeypatch.setattr(sync, "import_price_frame", capture_import)

    report = sync.run(
        "incremental", conn, input_dir=tmp_path, source_version="tdx-delta"
    )

    assert [str(value) for value in captured["frame"]["trade_date"]] == [
        "2026-10-26"
    ]
    assert conn.copied_snapshot == (202, 8)
    copy_query = next(
        query for query, _ in conn.queries if "INSERT INTO daily_price" in query
    )
    assert "SELECT" in copy_query and "WHERE price_snapshot_id = %s" in copy_query
    assert report.price_snapshot_id == 202
    assert report.counts["rows_seen"] == 3
    assert report.counts["rows_skipped"] == 2


def test_incremental_import_with_no_new_dates_does_not_create_snapshot(
    tmp_path, monkeypatch
):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,11,12,10,11.5,110,1265"],
    )
    conn = IncrementalConnection(
        latest_snapshot_rows=[("600001", "2026-10-23")]
    )
    writes = []
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda *args: writes.append("manifest")
    )
    monkeypatch.setattr(
        sync, "persist_price_snapshot", lambda *args: writes.append("snapshot")
    )

    report = sync.run(
        "incremental", conn, input_dir=tmp_path, source_version="tdx-noop"
    )

    assert writes == []
    assert report.price_snapshot_id == 8
    assert report.counts["rows_seen"] == 1
    assert report.counts["rows_inserted"] == 0
    assert not any("INSERT INTO daily_price" in query for query, _ in conn.queries)


def test_incremental_import_rejects_empty_database_without_writes(tmp_path):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/26,12,13,11,12.5,120,1500"],
    )
    conn = IncrementalConnection()

    with pytest.raises(ValueError, match="历史全量导入"):
        sync.run(
            "incremental", conn, input_dir=tmp_path, source_version="tdx-delta"
        )

    assert not any("INSERT" in query for query, _ in conn.queries)


def test_incremental_import_rejects_empty_actual_snapshot(tmp_path):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/26,12,13,11,12.5,120,1500"],
    )
    conn = IncrementalConnection(snapshot_exists=True)

    with pytest.raises(ValueError, match="基线快照没有行情数据"):
        sync.run(
            "incremental", conn, input_dir=tmp_path, source_version="tdx-delta"
        )

    assert not any("INSERT" in query for query, _ in conn.queries)


def test_incremental_import_rejects_non_stocktool_database(tmp_path):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/26,12,13,11,12.5,120,1500"],
    )
    conn = IncrementalConnection(database_name="other_project")

    with pytest.raises(ValueError, match="stocktool.*other_project"):
        sync.run(
            "incremental", conn, input_dir=tmp_path, source_version="tdx-delta"
        )

    assert len(conn.queries) == 1


def test_full_actual_import_creates_baseline_without_requiring_existing_snapshot(
    tmp_path, monkeypatch
):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,11,12,10,11.5,110,1265"],
    )
    conn = IncrementalConnection()
    captured = {}
    monkeypatch.setattr(sync, "persist_data_manifest", lambda *args: 101)

    def capture_snapshot(conn, snapshot):
        captured["snapshot"] = snapshot
        return 202

    monkeypatch.setattr(sync, "persist_price_snapshot", capture_snapshot)
    monkeypatch.setattr(sync, "import_price_frame", lambda *args, **kwargs: 1)

    report = sync.run(
        "full_actual", conn, input_dir=tmp_path, source_version="tdx-history"
    )

    assert report.data_manifest_ids == {"actual": 101}
    assert report.price_snapshot_id == 202
    assert report.counts["rows_inserted"] == 1
    assert report.status == "complete"
    assert captured["snapshot"].input_manifest_ids == {"actual": 101}
    assert "parent_price_snapshot_id" not in captured["snapshot"].source_manifest
    assert not any("FROM price_snapshots" in query for query, _ in conn.queries)
    assert conn.copied_snapshot is None


def test_full_actual_import_rejects_non_stocktool_database(tmp_path):
    conn = IncrementalConnection(database_name="other_project")

    with pytest.raises(ValueError, match="stocktool.*other_project"):
        sync.run(
            "full_actual", conn, input_dir=tmp_path, source_version="tdx-history"
        )

    assert len(conn.queries) == 1


def test_full_actual_import_rejects_adjusted_prices_before_writes(
    tmp_path, monkeypatch
):
    write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 前复权",
        ["2026/10/23,11,12,10,11.5,110,1265"],
    )
    writes = []
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda *args: writes.append("manifest")
    )

    with pytest.raises(ValueError, match="actual"):
        sync.run(
            "full_actual", IncrementalConnection(), input_dir=tmp_path,
            source_version="tdx-history",
        )

    assert writes == []


def test_import_batch_does_not_retain_all_parsed_frames(tmp_path, monkeypatch):
    import gc
    import weakref

    for code in ("600001", "600002", "600003", "600004"):
        write_tdx_file(
            tmp_path / f"SH#{code}.txt",
            f"{code} 测试银行 日线 不复权",
            ["2026/10/23,11,12,10,11.5,110,1265"],
        )
    parsed_frames = []
    original_parse = sync.parse_txt

    def tracked_parse(*args, **kwargs):
        gc.collect()
        # 上一个迭代项可以仍在调用栈，但不能保留整个批次的数据帧。
        assert sum(reference() is not None for reference in parsed_frames) <= 1
        frame = original_parse(*args, **kwargs)
        parsed_frames.append(weakref.ref(frame))
        return frame

    captured = {}

    def capture_manifest(conn, manifest):
        captured["manifest"] = manifest
        return 101

    monkeypatch.setattr(sync, "parse_txt", tracked_parse)
    monkeypatch.setattr(sync, "persist_data_manifest", capture_manifest)
    monkeypatch.setattr(sync, "persist_price_snapshot", lambda *args: 202)
    monkeypatch.setattr(sync, "import_price_frame", lambda *args, **kwargs: 1)

    report = sync.import_tdx_directory(
        FakeConnection(), tmp_path, source_version="tdx-history"
    )

    assert report.counts["rows_inserted"] == 4
    assert len(captured["manifest"].files) == 4
    assert captured["manifest"].row_count == 4


def test_import_rejects_file_changed_after_preflight(tmp_path, monkeypatch):
    path = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,11,12,10,11.5,110,1265"],
    )
    writes = []
    monkeypatch.setattr(sync, "persist_data_manifest", lambda *args: 101)

    def change_source(conn, snapshot):
        path.write_bytes(path.read_bytes().replace(b"11.5", b"12.5"))
        return 202

    monkeypatch.setattr(sync, "persist_price_snapshot", change_source)
    monkeypatch.setattr(
        sync, "import_price_frame", lambda *args, **kwargs: writes.append("price")
    )

    with pytest.raises(ValueError, match="文件.*变化"):
        sync.import_tdx_directory(
            FakeConnection(), tmp_path, source_version="tdx-history"
        )

    assert writes == []


def test_import_rejects_file_changed_during_parse_before_database_writes(
    tmp_path, monkeypatch
):
    path = write_tdx_file(
        tmp_path / "SH#600001.txt",
        "600001 测试银行 日线 不复权",
        ["2026/10/23,11,12,10,11.5,110,1265"],
    )
    original_parse = sync.parse_txt
    writes = []

    def change_during_parse(*args, **kwargs):
        frame = original_parse(*args, **kwargs)
        path.write_bytes(path.read_bytes().replace(b"11.5", b"12.5"))
        return frame

    monkeypatch.setattr(sync, "parse_txt", change_during_parse)
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda *args: writes.append("manifest")
    )

    with pytest.raises(ValueError, match="文件.*变化"):
        sync.import_tdx_directory(
            FakeConnection(), tmp_path, source_version="tdx-history"
        )

    assert writes == []


@pytest.mark.parametrize("include_valid,status", [(True, "partial"), (False, "invalid")])
def test_incremental_noop_does_not_report_empty_input_as_complete(
    tmp_path, monkeypatch, include_valid, status
):
    write_tdx_file(
        tmp_path / "SH#688827.txt", "688827 空数据 日线 不复权", []
    )
    if include_valid:
        write_tdx_file(
            tmp_path / "SH#600001.txt", "600001 测试银行 日线 不复权",
            ["2026/10/23,11,12,10,11.5,110,1265"],
        )
    writes = []
    monkeypatch.setattr(
        sync, "persist_data_manifest", lambda *args: writes.append("manifest")
    )
    conn = IncrementalConnection(latest_snapshot_rows=[("600001", "2026-10-23")])

    report = sync.run(
        "incremental", conn, input_dir=tmp_path, source_version="tdx-empty-noop"
    )

    assert report.status == status
    assert report.price_snapshot_id == 8
    assert report.counts["rows_inserted"] == 0
    assert report.counts["files_empty"] == 1
    assert report.issues and writes == []
