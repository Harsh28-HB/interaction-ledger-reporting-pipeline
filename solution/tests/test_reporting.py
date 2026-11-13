import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.database import get_connection
from src.encryption import hash_phone
from src.event_ingestion import load_dispositions
from src.lead_ingestion import load_schema, save_lead, save_rejection
from src.reporting import build_calls, generate_reports, parse_timestamp


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "assignment-a-data" / "schemas"
DISPOSITIONS = SCHEMAS / "dispositions.csv"
IST = ZoneInfo("Asia/Kolkata")


def add_motor_lead(conn, external_ref, phone, batch_id):
    schema = load_schema(SCHEMAS / "motor_renewal.json")
    values = {
        "customer_name": external_ref,
        "phone": phone,
        "policy_number": external_ref,
        "policy_expiry": None,
        "premium_amount": None,
        "preferred_language": None,
    }
    save_lead(
        conn,
        schema["workflow_id"],
        batch_id,
        values,
        schema["fields"],
    )
    return conn.execute(
        """
        SELECT id FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        ("motor_renewal", hash_phone(phone)),
    ).fetchone()["id"]


def add_event(conn, lead_id, event_id, call_id, event_type, time, payload=None):
    conn.execute(
        """
        INSERT INTO events (
            event_id, workflow_id, event_type, call_id,
            occurred_at, phone_hash, payload_json, ingestion_drop
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            "motor_renewal",
            event_type,
            call_id,
            time,
            None,
            json.dumps(payload or {}),
            "test_drop",
        ),
    )
    conn.execute(
        """
        INSERT INTO event_links(event_id, lead_id, link_status)
        VALUES (?, ?, 'linked')
        """,
        (event_id, lead_id),
    )


def test_timestamp_parsing_converts_to_ist():
    assert parse_timestamp("2026-10-03 10:01:00") == datetime(
        2026, 10, 3, 10, 1, tzinfo=IST
    )
    assert parse_timestamp("2026-10-03T04:31:00Z") == datetime(
        2026, 10, 3, 10, 1, tzinfo=IST
    )
    assert parse_timestamp("2026-10-03T10:01:00+05:30") == datetime(
        2026, 10, 3, 10, 1, tzinfo=IST
    )
    assert parse_timestamp("not a timestamp") is None


def test_build_calls_uses_override_and_ignores_uninitiated_calls():
    dispositions = {
        "renewed": {"is_terminal": True, "is_success": True},
        "not_interested": {"is_terminal": True, "is_success": False},
    }
    events = [
        {
            "event_type": "call.connected",
            "call_id": "uninitiated",
            "occurred_at": "2026-10-03T10:00:00+05:30",
            "payload": {},
        },
        {
            "event_type": "call.initiated",
            "call_id": "call-1",
            "occurred_at": "2026-10-03T04:30:00Z",
            "payload": {},
        },
        {
            "event_type": "call.connected",
            "call_id": "call-1",
            "occurred_at": "2026-10-03T10:01:00+05:30",
            "payload": {},
        },
        {
            "event_type": "disposition.set",
            "call_id": "call-1",
            "occurred_at": "2026-10-03T11:00:00+05:30",
            "payload": {"disposition_code": "not_interested"},
        },
        {
            "event_type": "disposition.override",
            "call_id": "call-1",
            "occurred_at": "2026-10-03T10:30:00+05:30",
            "payload": {"disposition_code": "renewed"},
        },
    ]

    calls = build_calls(events, dispositions)

    assert len(calls) == 1
    assert calls[0]["started_at"] == datetime(
        2026, 10, 3, 10, 0, tzinfo=IST
    )
    assert calls[0]["connected"] is True
    assert calls[0]["disposition"] == "renewed"
    assert calls[0]["terminal"] is True
    assert calls[0]["success"] is True


def test_reports_match_headers_sorting_and_derived_values(tmp_path):
    conn = get_connection()
    try:
        load_dispositions(DISPOSITIONS, conn)
        leads = {
            "POL-B": add_motor_lead(
                conn, "POL-B", "+919876543210", "batch_001"
            ),
            "POL-A": add_motor_lead(
                conn, "POL-A", "+919876543211", "batch_002"
            ),
            "POL-C": add_motor_lead(
                conn, "POL-C", "+919876543212", "batch_001"
            ),
            "POL-D": add_motor_lead(
                conn, "POL-D", "+919876543213", "batch_002"
            ),
            "POL-E": add_motor_lead(
                conn, "POL-E", "+919876543214", "batch_001"
            ),
            "POL-F": add_motor_lead(
                conn, "POL-F", "+919876543215", "batch_002"
            ),
        }

        def initiated(ref, call_id, clock):
            add_event(
                conn, leads[ref], f"{call_id}-init", call_id,
                "call.initiated", f"2026-10-01T{clock}:00+05:30",
            )

        def event(ref, call_id, event_type, suffix, clock, payload=None):
            add_event(
                conn, leads[ref], f"{call_id}-{suffix}", call_id,
                event_type, f"2026-10-01T{clock}:00+05:30", payload,
            )

        initiated("POL-B", "b1", "10:00")
        event("POL-B", "b1", "call.connected", "connected", "10:01")
        event(
            "POL-B", "b1", "disposition.set", "disposition", "10:05",
            {"disposition_code": "renewed"},
        )
        initiated("POL-B", "b2", "12:00")
        event("POL-B", "b2", "call.connected", "connected", "12:01")
        event(
            "POL-B", "b2", "disposition.set", "disposition", "12:05",
            {"disposition_code": "renewed"},
        )
        initiated("POL-B", "b3", "18:00")

        initiated("POL-A", "a1", "13:00")
        event(
            "POL-A", "a1", "disposition.set", "disposition", "13:05",
            {"disposition_code": "renewed"},
        )

        initiated("POL-D", "d1", "14:00")
        event(
            "POL-D", "d1", "call.telephony_failed", "failed", "14:01",
            {"reason": "carrier_rejected"},
        )

        initiated("POL-E", "e1", "15:00")
        event(
            "POL-E", "e1", "call.telephony_failed", "failed", "15:01",
            {"reason": "carrier_rejected"},
        )
        initiated("POL-E", "e2", "16:00")

        initiated("POL-F", "f1", "17:00")
        event(
            "POL-F", "f1", "call.telephony_failed", "failed", "17:01",
            {"reason": "carrier_rejected"},
        )
        event(
            "POL-F", "f1", "disposition.set", "disposition", "17:05",
            {"disposition_code": "renewed"},
        )

        save_rejection(
            conn, "motor_renewal", "batch_002", 2,
            "missing_required:policy_number",
        )
        save_rejection(
            conn, "motor_renewal", "batch_001", 4,
            "invalid_phone",
        )

        report_dir = tmp_path / "reports"
        generate_reports(conn, report_dir)

        expected_headers = {
            "batch_summary.csv": [
                "workflow_id", "batch_id", "total_leads",
                "yet_to_reach_out", "in_progress_telephony_failed",
                "in_progress_dialed", "completed",
            ],
            "daily_overview.csv": [
                "workflow_id", "date", "total_calls",
                "connected_calls", "connectivity_rate",
                "success_leads",
            ],
            "lead_report.csv": [
                "workflow_id", "external_ref", "batch_id",
                "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at",
                "current_disposition", "ever_success",
            ],
            "rejections.csv": [
                "workflow_id", "batch_id", "row_number", "reason",
            ],
        }
        for filename, headers in expected_headers.items():
            with (report_dir / filename).open(
                "r", encoding="utf-8", newline=""
            ) as file:
                assert next(csv.reader(file)) == headers

        with (report_dir / "batch_summary.csv").open(
            "r", encoding="utf-8", newline=""
        ) as file:
            batch_rows = list(csv.DictReader(file))
        assert [(row["workflow_id"], row["batch_id"]) for row in batch_rows] == [
            ("motor_renewal", "batch_001"),
            ("motor_renewal", "batch_002"),
        ]

        with (report_dir / "daily_overview.csv").open(
            "r", encoding="utf-8", newline=""
        ) as file:
            daily_rows = list(csv.DictReader(file))
        assert daily_rows == [{
            "workflow_id": "motor_renewal",
            "date": "2026-10-01",
            "total_calls": "8",
            "connected_calls": "2",
            "connectivity_rate": "0.2500",
            "success_leads": "3",
        }]

        with (report_dir / "lead_report.csv").open(
            "r", encoding="utf-8", newline=""
        ) as file:
            lead_rows = list(csv.DictReader(file))
        assert [row["external_ref"] for row in lead_rows] == [
            "POL-A", "POL-B", "POL-C", "POL-D", "POL-E", "POL-F",
        ]
        lead_b = next(row for row in lead_rows if row["external_ref"] == "POL-B")
        assert lead_b["phone_masked"] == "+91******3210"
        assert lead_b["total_calls"] == "3"
        assert lead_b["last_call_at"] == "2026-10-01T18:00:00+05:30"
        assert lead_b["current_disposition"] == "renewed"
        assert lead_b["ever_success"] == "true"
        assert next(row for row in lead_rows if row["external_ref"] == "POL-C")[
            "bucket"
        ] == "yet_to_reach_out"
        assert next(row for row in lead_rows if row["external_ref"] == "POL-D")[
            "bucket"
        ] == "in_progress_telephony_failed"
        assert next(row for row in lead_rows if row["external_ref"] == "POL-E")[
            "bucket"
        ] == "in_progress_dialed"
        assert next(row for row in lead_rows if row["external_ref"] == "POL-F")[
            "bucket"
        ] == "completed"

        with (report_dir / "rejections.csv").open(
            "r", encoding="utf-8", newline=""
        ) as file:
            rejection_rows = list(csv.DictReader(file))
        assert [
            (row["batch_id"], row["row_number"])
            for row in rejection_rows
        ] == [("batch_001", "4"), ("batch_002", "2")]

        quality = json.loads(
            (report_dir / "data_quality.json").read_text(
                encoding="utf-8"
            )
        )
        assert set(quality) == {
            "lead_rows_total",
            "lead_rows_rejected",
            "rejections_by_reason",
            "duplicate_lead_rows_merged",
            "field_warnings",
            "events_total",
            "malformed_events",
            "duplicate_events_dropped",
            "orphan_events",
            "unknown_disposition_events",
        }
        assert quality["lead_rows_rejected"] == 2
        assert quality["orphan_events"] == 0
    finally:
        conn.close()
