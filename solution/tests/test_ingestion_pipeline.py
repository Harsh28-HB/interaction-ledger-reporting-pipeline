import csv
import json

import pytest

from src import main
from src.database import get_connection


def create_lead_csv(path, rows):
    headers = [
        "customer_name", "phone", "policy_number",
        "policy_expiry", "premium_amount", "preferred_language",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def create_event_file(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


def query_snapshot():
    conn = get_connection()
    try:
        tables = (
            "batches", "leads", "events", "event_links",
            "rejections", "pipeline_metrics", "ingestion_drops",
        )
        counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in tables
        }
        metrics = [
            tuple(row)
            for row in conn.execute(
                "SELECT metric_name, metric_value "
                "FROM pipeline_metrics ORDER BY metric_name"
            ).fetchall()
        ]
        return counts, metrics
    finally:
        conn.close()


def test_incremental_drops_relink_and_reingest_is_idempotent(tmp_path):
    input_root = tmp_path / "input"
    drop1 = input_root / "drop_1"
    drop2 = input_root / "drop_2"

    create_lead_csv(
        drop1 / "leads" / "motor_renewal" / "batch_001.csv",
        [{
            "customer_name": "First Customer",
            "phone": "9876543210",
            "policy_number": "POLICY-1",
        }],
    )
    first_event = {
        "event_id": "evt-known",
        "event_type": "call.initiated",
        "workflow_id": "motor_renewal",
        "call_id": "call-1",
        "occurred_at": "2026-10-01T10:00:00+05:30",
        "phone": "+919876543210",
        "payload": {},
    }
    orphan_event = {
        "event_id": "evt-orphan",
        "event_type": "call.initiated",
        "workflow_id": "motor_renewal",
        "call_id": "call-2",
        "occurred_at": "2026-10-01T11:00:00+05:30",
        "phone": "+918123456789",
        "payload": {},
    }
    create_event_file(
        drop1 / "events" / "events_0001.jsonl",
        [first_event, orphan_event],
    )

    main.ingest_drop(drop1)

    conn = get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM event_links WHERE link_status='orphan'"
        ).fetchone()[0] == 1
    finally:
        conn.close()

    create_lead_csv(
        drop2 / "leads" / "motor_renewal" / "batch_002.csv",
        [
            {
                "customer_name": "Updated Customer",
                "phone": "+91 98765 43210",
                "policy_number": "POLICY-2",
            },
            {
                "customer_name": "Second Customer",
                "phone": "8123456789",
                "policy_number": "POLICY-3",
            },
        ],
    )
    connected_event = {
        "event_id": "evt-connected",
        "event_type": "call.connected",
        "workflow_id": "motor_renewal",
        "call_id": "call-1",
        "occurred_at": "2026-10-01T10:01:00+05:30",
        "phone": "9876543210",
        "payload": {},
    }
    create_event_file(
        drop2 / "events" / "events_1001.jsonl",
        [first_event, connected_event],
    )

    main.ingest_drop(drop2)
    main.create_report(tmp_path / "reports")
    before_repeat = query_snapshot()
    before_reports = {
        path.name: path.read_bytes()
        for path in (tmp_path / "reports").iterdir()
    }

    conn = get_connection()
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM event_links WHERE link_status='orphan'"
        ).fetchone()[0] == 0
        lead = conn.execute(
            """
            SELECT external_ref, batch_id FROM leads
            WHERE phone_hash = (
                SELECT phone_hash FROM leads
                WHERE external_ref = 'POLICY-2'
            )
            """
        ).fetchone()
        assert tuple(lead) == ("POLICY-2", "batch_001")
    finally:
        conn.close()

    main.ingest_drop(drop1)
    main.create_report(tmp_path / "reports")

    assert query_snapshot() == before_repeat
    assert {
        path.name: path.read_bytes()
        for path in (tmp_path / "reports").iterdir()
    } == before_reports

    with (tmp_path / "reports" / "data_quality.json").open(
        "r", encoding="utf-8"
    ) as file:
        quality = json.load(file)
    assert quality["events_total"] == 4
    assert quality["duplicate_events_dropped"] == 1
    assert quality["duplicate_lead_rows_merged"] == 1
    assert quality["orphan_events"] == 0


def test_failed_drop_rolls_back_all_ingestion_changes(tmp_path, monkeypatch):
    drop = tmp_path / "drop_rollback"
    create_lead_csv(
        drop / "leads" / "motor_renewal" / "batch_001.csv",
        [{
            "customer_name": "Rollback Customer",
            "phone": "9876543210",
            "policy_number": "POLICY-ROLLBACK",
        }],
    )
    create_event_file(
        drop / "events" / "events_0001.jsonl",
        [],
    )

    def fail_event_ingestion(*args, **kwargs):
        raise RuntimeError("injected event ingestion failure")

    monkeypatch.setattr(main, "ingest_event_directory", fail_event_ingestion)

    with pytest.raises(RuntimeError, match="injected event ingestion failure"):
        main.ingest_drop(drop)

    conn = get_connection()
    try:
        for table in (
            "batches", "leads", "events", "rejections",
            "pipeline_metrics", "ingestion_drops", "dispositions",
        ):
            assert conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0] == 0
    finally:
        conn.close()
