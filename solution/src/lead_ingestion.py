
import csv
import json
from pathlib import Path

from src.normalizers import (
    normalize_header,
    normalize_phone,
    normalize_value,
)
from src.encryption import encrypt_phone, hash_phone


def load_schema(schema_path):
    with open(schema_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_schema_fields(schema):
    return schema.get("fields", [])


def build_header_mapping(headers, fields):
    aliases = {}

    for field in fields:
        for name in [field["name"]] + field.get("aliases", []):
            aliases[normalize_header(name)] = field["name"]

    return {
        aliases[normalize_header(header)]: header
        for header in headers
        if normalize_header(header) in aliases
    }


def get_stats():
    return {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "rejections_by_reason": {},
    }


def validate_row(row, fields, mapping):
    values = {}
    warnings = 0

    for field in fields:
        name = field["name"]
        raw = row.get(mapping.get(name, ""), "")
        raw = str(raw or "").strip()

        if not raw:
            values[name] = None
            if field.get("required", False):
                return None, f"missing_required:{name}", warnings
            continue

        field_type = field.get("type")

        if field_type == "phone":
            value = normalize_phone(raw)
            if value is None:
                return None, "invalid_phone", warnings
        else:
            value, warning = normalize_value(raw, field)
            if warning:
                warnings += 1

        values[name] = value

    return values, None, warnings


def save_rejection(conn, workflow_id, batch_id, row_number, reason):
    conn.execute(
        """
        INSERT OR IGNORE INTO rejections
        (workflow_id, batch_id, row_number, reason)
        VALUES (?, ?, ?, ?)
        """,
        (workflow_id, batch_id, row_number, reason),
    )


def save_lead(conn, workflow_id, batch_id, values, fields):
    phone_field = next(
        (
            f for f in fields
            if f.get("type") == "phone"
            or f["name"].lower() in (
                "phone", "mobile", "mobile_number", "contact"
            )
        ),
        None,
    )

    if not phone_field:
        raise ValueError(f"No phone field in {workflow_id} schema")

    phone = normalize_phone(values.get(phone_field["name"]))

    if not phone:
        raise ValueError("Invalid phone reached save_lead")

    phone_hash = hash_phone(phone)
    encrypted_phone = encrypt_phone(phone)
    phone_masked = "+91******" + phone[-4:]

    reference_field = next(
        (f for f in fields if f.get("reference", False)),
        None,
    )

    external_ref = (
        values.get(reference_field["name"])
        if reference_field else None
    )

    normal_values = {"_phone_masked": phone_masked}
    pii_values = {}

    for field in fields:
        name = field["name"]
        value = values.get(name)

        if name == phone_field["name"] or value is None:
            continue

        if field.get("pii", False):
            pii_values[name] = encrypt_phone(str(value))
        else:
            normal_values[name] = value

    existing = conn.execute(
        """
        SELECT id, fields_json, pii_encrypted_json
        FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, phone_hash),
    ).fetchone()

    if existing:
        old_values = json.loads(existing["fields_json"] or "{}")
        old_pii = json.loads(existing["pii_encrypted_json"] or "{}")

        old_values.update(normal_values)
        old_pii.update(pii_values)

        conn.execute(
            """
            UPDATE leads
            SET external_ref = COALESCE(?, external_ref),
                fields_json = ?,
                pii_encrypted_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                external_ref,
                json.dumps(old_values),
                json.dumps(old_pii),
                existing["id"],
            ),
        )

        return "duplicate"

    for field in fields:
        name = field["name"]
        if (
            normal_values.get(name) is None
            and field.get("default") is not None
            and not field.get("pii", False)
        ):
            normal_values[name] = field["default"]

    conn.execute(
        """
        INSERT INTO leads (
            workflow_id, phone_hash, encrypted_phone,
            external_ref, batch_id, fields_json,
            pii_encrypted_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workflow_id,
            phone_hash,
            encrypted_phone,
            external_ref,
            batch_id,
            json.dumps(normal_values),
            json.dumps(pii_values),
        ),
    )

    return "inserted"


def ingest_lead_file(csv_path, schema_path, conn, stats=None):
    if stats is None:
        stats = get_stats()

    schema = load_schema(schema_path)
    workflow_id = schema["workflow_id"]
    fields = get_schema_fields(schema)

    csv_path = Path(csv_path)
    batch_id = csv_path.stem

    conn.execute(
        """
        INSERT OR IGNORE INTO batches
        (workflow_id, batch_id, source_file)
        VALUES (?, ?, ?)
        """,
        (workflow_id, batch_id, str(csv_path)),
    )

    with csv_path.open(
        "r", encoding="utf-8-sig", newline=""
    ) as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError(f"CSV has no headers: {csv_path}")

        mapping = build_header_mapping(reader.fieldnames, fields)

        row_number = 0

        for row in reader:
            if not any(str(v or "").strip() for v in row.values()):
                continue

            row_number += 1
            stats["lead_rows_total"] += 1

            values, reason, warnings = validate_row(
                row, fields, mapping
            )

            if reason:
                stats["lead_rows_rejected"] += 1
                reasons = stats["rejections_by_reason"]
                reasons[reason] = reasons.get(reason, 0) + 1

                save_rejection(
                    conn, workflow_id, batch_id,
                    row_number, reason
                )
                continue

            stats["field_warnings"] += warnings

            result = save_lead(
                conn, workflow_id, batch_id, values, fields
            )

            if result == "duplicate":
                stats["duplicate_lead_rows_merged"] += 1

    return stats


def ingest_lead_directory(data_dir, schema_dir, conn, stats=None):
    if stats is None:
        stats = get_stats()

    data_dir = Path(data_dir)
    schema_dir = Path(schema_dir)

    for workflow_dir in sorted(data_dir.iterdir()):
        if not workflow_dir.is_dir():
            continue

        schema_path = schema_dir / f"{workflow_dir.name}.json"

        if not schema_path.exists():
            continue

        for csv_path in sorted(workflow_dir.glob("*.csv")):
            ingest_lead_file(
                csv_path, schema_path, conn, stats
            )

    return stats
