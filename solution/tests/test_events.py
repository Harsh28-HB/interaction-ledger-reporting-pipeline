import json
import sqlite3
from pathlib import Path

import pytest

from src.database import get_connection
from src.encryption import hash_phone
from src.event_ingestion import (
    get_event_stats,
    ingest_event_file,
    load_dispositions,
    relink_orphans,
)
from src.lead_ingestion import load_schema, save_lead
from src.reporting import build_calls, load_events


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "assignment-a-data" / "schemas"
DISPOSITIONS = SCHEMAS / "dispositions.csv"


def write_jsonl(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            line if isinstance(line, str) else json.dumps(line)
            for line in lines
        ) + "\n",
        encoding="utf-8",
    )


def add_motor_lead(conn, phone):
    schema = load_schema(SCHEMAS / "motor_renewal.json")
    values = {
        "customer_name": "Test Customer",
        "phone": phone,
        "policy_number": f"POLICY-{phone[-4:]}",
        "policy_expiry": None,
        "premium_amount": None,
        "preferred_language": None,
    }
    save_lead(
        conn,
        schema["workflow_id"],
        "batch_001",
        values,
        schema["fields"],
    )
    return conn.execute(
        """
        SELECT id FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (
            schema["workflow_id"],
            hash_phone(phone),
        ),
    ).fetchone()["id"]


def test_events_dedupe_malformed_unknown_and_relink_orphans(tmp_path):
    conn = get_connection()
    try:
        load_dispositions(DISPOSITIONS, conn)
        first_event = {
            "event_id": "evt-call",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call-1",
            "occurred_at": "2026-10-01T04:30:00Z",
            "phone": "91-98765-43210",
            "payload": {},
        }
        override_event = {
            "event_id": "evt-override",
            "event_type": "disposition.override",
            "workflow_id": "motor_renewal",
            "call_id": "call-1",
            "occurred_at": "2026-10-01T11:00:00+05:30",
            "payload": {"disposition_code": "renewed"},
        }
        permanent_orphan = {
            "event_id": "evt-orphan",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call-orphan",
            "occurred_at": "2026-10-01T10:00:00+05:30",
            "phone": "8123456789",
            "payload": {},
        }
        drop1 = tmp_path / "drop_1" / "events" / "events_0001.jsonl"
        write_jsonl(
            drop1,
            [
                first_event,
                override_event,
                "{malformed",
                {"event_type": "call.initiated"},
                first_event,
                permanent_orphan,
            ],
        )

        first_stats = ingest_event_file(
            drop1, conn, get_event_stats()
        )
        assert first_stats == {
            "events_total": 6,
            "malformed_events": 2,
            "duplicate_events_dropped": 1,
            "orphan_events": 3,
            "unknown_disposition_events": 0,
        }

        lead_id = add_motor_lead(conn, "+919876543210")
        assert relink_orphans(conn) == 2
        assert conn.execute(
            """
            SELECT COUNT(*) FROM event_links
            WHERE link_status = 'orphan'
            """
        ).fetchone()[0] == 1
        override_link = conn.execute(
            """
            SELECT lead_id, link_status FROM event_links
            WHERE event_id = 'evt-override'
            """
        ).fetchone()
        assert override_link["lead_id"] == lead_id
        assert override_link["link_status"] == "linked"

        second_event = {
            "event_id": "evt-unknown-disposition",
            "event_type": "disposition.set",
            "workflow_id": "motor_renewal",
            "call_id": "call-1",
            "occurred_at": "2026-10-01T12:00:00+05:30",
            "phone": "+919876543210",
            "payload": {"disposition_code": "not-a-disposition"},
        }
        drop2 = tmp_path / "drop_2" / "events" / "events_1001.jsonl"
        write_jsonl(drop2, [first_event, second_event])
        second_stats = ingest_event_file(
            drop2, conn, get_event_stats()
        )

        assert second_stats["events_total"] == 2
        assert second_stats["duplicate_events_dropped"] == 1
        assert second_stats["unknown_disposition_events"] == 1

        assert relink_orphans(conn) == 0
        calls = build_calls(
            load_events(conn, lead_id),
            {
                "renewed": {"is_terminal": True, "is_success": True},
            },
        )
        assert len(calls) == 1
        assert calls[0]["disposition"] == "renewed"
        assert calls[0]["success"] is True

        add_motor_lead(conn, "+918123456789")
        assert relink_orphans(conn) == 1
        assert conn.execute(
            """
            SELECT COUNT(*) FROM event_links
            WHERE link_status = 'orphan'
            """
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_event_ledger_is_append_only(tmp_path):
    conn = get_connection()
    try:
        load_dispositions(DISPOSITIONS, conn)
        add_motor_lead(conn, "+919876543210")
        event_path = (
            tmp_path / "drop_1" / "events" / "events_0001.jsonl"
        )
        write_jsonl(event_path, [{
            "event_id": "evt-immutable",
            "event_type": "call.initiated",
            "workflow_id": "motor_renewal",
            "call_id": "call-immutable",
            "occurred_at": "2026-10-01T10:00:00+05:30",
            "phone": "+919876543210",
            "payload": {},
        }])
        ingest_event_file(event_path, conn, get_event_stats())

        with pytest.raises(sqlite3.IntegrityError, match="Events are append-only"):
            conn.execute(
                "UPDATE events SET payload_json = '{}' "
                "WHERE event_id = 'evt-immutable'"
            )

        with pytest.raises(sqlite3.IntegrityError, match="Events are append-only"):
            conn.execute(
                "DELETE FROM events WHERE event_id = 'evt-immutable'"
            )
    finally:
        conn.close()
