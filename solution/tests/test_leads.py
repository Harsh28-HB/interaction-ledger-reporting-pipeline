import csv
import json
from pathlib import Path

from src.database import get_connection
from src.encryption import decrypt_phone
from src.lead_ingestion import (
    get_stats,
    ingest_lead_file,
    load_schema,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "assignment-a-data" / "schemas"


def write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def test_lead_rows_skip_blanks_and_use_schema_order(tmp_path):
    path = tmp_path / "batch_001.csv"
    headers = [
        "Name", "Mobile", "Order ID", "Product",
        "Order Date", "Language", "unknown column",
    ]
    write_csv(path, headers, [
        dict.fromkeys(headers, ""),
        {
            "Name": "",
            "Mobile": "invalid",
            "Order ID": "",
            "Order Date": "31/02/2026",
            "Language": "Hindi",
        },
        {
            "Name": "Private Name",
            "Mobile": "5123456789",
            "Order ID": "",
        },
        {
            "Name": "Private Name",
            "Mobile": "9876543210",
            "Order ID": "",
            "Order Date": "31/02/2026",
            "Language": "Hindi",
        },
        {
            "Name": "Valid Name",
            "Mobile": "9876543211",
            "Order ID": "ORDER-1",
            "Product": "Phone",
            "Order Date": "31/02/2026",
            "Language": "Hindi",
            "unknown column": "ignored",
        },
    ])

    conn = get_connection()
    try:
        stats = ingest_lead_file(
            path,
            SCHEMAS / "policy_feedback.json",
            conn,
        )
        conn.commit()

        rejected = conn.execute(
            """
            SELECT row_number, reason
            FROM rejections
            ORDER BY row_number
            """
        ).fetchall()
        stored_rejections = str([tuple(row) for row in rejected])

        assert stats["lead_rows_total"] == 4
        assert stats["lead_rows_rejected"] == 3
        assert stats["field_warnings"] == 2
        assert [tuple(row) for row in rejected] == [
            (1, "missing_required:customer_name"),
            (2, "invalid_phone"),
            (3, "missing_required:order_id"),
        ]
        assert "Private Name" not in stored_rejections
        assert "5123456789" not in stored_rejections
        assert "9876543210" not in stored_rejections
    finally:
        conn.close()


def test_duplicate_merge_preserves_batch_and_last_non_null_values(tmp_path):
    headers = [
        "customer_name", "phone", "policy_number",
        "policy_expiry", "premium_amount", "preferred_language",
    ]
    first_path = tmp_path / "batch_001.csv"
    second_path = tmp_path / "batch_002.csv"
    write_csv(first_path, headers, [
        {
            "customer_name": "Original Name",
            "phone": "9876543210",
            "policy_number": "POLICY-1",
            "policy_expiry": "01-Dec-2026",
            "premium_amount": "1000",
            "preferred_language": "hi",
        },
        {
            "customer_name": "Default Name",
            "phone": "9876543211",
            "policy_number": "POLICY-DEFAULT",
        },
    ])
    write_csv(second_path, headers, [{
        "customer_name": "Updated Name",
        "phone": "+91 98765 43210",
        "policy_number": "POLICY-2",
        "policy_expiry": "",
        "premium_amount": "Rs. 1,200",
        "preferred_language": "Hindi",
    }])

    schema = load_schema(SCHEMAS / "motor_renewal.json")
    conn = get_connection()
    try:
        stats = get_stats()
        ingest_lead_file(
            first_path, SCHEMAS / "motor_renewal.json", conn, stats
        )
        ingest_lead_file(
            second_path, SCHEMAS / "motor_renewal.json", conn, stats
        )
        conn.commit()

        lead = conn.execute(
            """
            SELECT * FROM leads
            WHERE workflow_id = ? AND external_ref = ?
            """,
            ("motor_renewal", "POLICY-2"),
        ).fetchone()
        fields = json.loads(lead["fields_json"])
        pii = json.loads(lead["pii_encrypted_json"])
        stored_values = (
            lead["encrypted_phone"]
            + lead["phone_hash"]
            + lead["fields_json"]
            + lead["pii_encrypted_json"]
        )
        default_lead = conn.execute(
            """
            SELECT fields_json FROM leads
            WHERE workflow_id = ? AND external_ref = ?
            """,
            ("motor_renewal", "POLICY-DEFAULT"),
        ).fetchone()

        assert stats["duplicate_lead_rows_merged"] == 1
        assert stats["field_warnings"] == 1
        assert lead["batch_id"] == "batch_001"
        assert fields["policy_expiry"] == "2026-12-01"
        assert fields["premium_amount"] == "1200.00"
        assert fields["preferred_language"] == "hi"
        assert fields["_phone_masked"] == "+91******3210"
        assert decrypt_phone(pii["customer_name"]) == "Updated Name"
        assert decrypt_phone(lead["encrypted_phone"]) == "+919876543210"
        assert "+919876543210" not in stored_values
        assert json.loads(default_lead["fields_json"])[
            "preferred_language"
        ] == "en"
    finally:
        conn.close()
