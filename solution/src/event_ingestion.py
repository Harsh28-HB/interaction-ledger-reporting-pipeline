
import csv
import json
from pathlib import Path

from src.normalizers import normalize_phone
from src.encryption import hash_phone


def load_dispositions(dispositions_path, conn):
    """Load the workflow disposition catalogue."""
    with open(dispositions_path, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            conn.execute(
                """
                INSERT OR REPLACE INTO dispositions
                (workflow_id, disposition_code, label,
                 is_terminal, is_success)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["workflow_id"].strip(),
                    row["code"].strip(),
                    row.get("label", "").strip(),
                    int(row["is_terminal"].strip().lower() == "true"),
                    int(row["is_success"].strip().lower() == "true"),
                ),
            )


def get_event_stats():
    return {
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "orphan_events": 0,
        "unknown_disposition_events": 0,
    }


def resolve_event_lead(conn, event):
    """Find a lead using phone, or call_id for overrides."""
    workflow_id = event.get("workflow_id")
    event_type = event.get("event_type")

    if event_type == "disposition.override":
        call_id = event.get("call_id")

        if not call_id:
            return None

        result = conn.execute(
            """
            SELECT el.lead_id
            FROM events e
            JOIN event_links el ON el.event_id = e.event_id
            WHERE e.call_id = ?
              AND e.workflow_id = ?
              AND el.lead_id IS NOT NULL
            LIMIT 1
            """,
            (call_id, workflow_id),
        ).fetchone()

        return result["lead_id"] if result else None

    phone = normalize_phone(event.get("phone"))

    if not phone or not workflow_id:
        return None

    phone_hash = hash_phone(phone)

    result = conn.execute(
        """
        SELECT id FROM leads
        WHERE workflow_id = ? AND phone_hash = ?
        """,
        (workflow_id, phone_hash),
    ).fetchone()

    return result["id"] if result else None


def is_known_disposition(conn, event):
    """Check disposition codes against the supplied catalogue."""
    event_type = event.get("event_type", "")

    if not event_type.startswith("disposition."):
        return True

    code = (event.get("payload") or {}).get("disposition_code")

    if not code:
        return False

    result = conn.execute(
        """
        SELECT 1 FROM dispositions
        WHERE workflow_id = ? AND disposition_code = ?
        """,
        (event.get("workflow_id"), code),
    ).fetchone()

    return result is not None


def insert_event(conn, event, drop_name, lead_id):
    """Append an event and its current lead-link status."""
    phone = normalize_phone(event.get("phone"))
    phone_hash = hash_phone(phone) if phone else None

    event_id = event["event_id"]

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
            event.get("workflow_id", ""),
            event.get("event_type", ""),
            event.get("call_id"),
            event.get("occurred_at"),
            phone_hash,
            json.dumps(event.get("payload") or {}),
            drop_name,
        ),
    )

    status = "linked" if lead_id is not None else "orphan"

    conn.execute(
        """
        INSERT INTO event_links(event_id, lead_id, link_status)
        VALUES (?, ?, ?)
        """,
        (event_id, lead_id, status),
    )


def relink_orphans(conn):
    """Retry linking previously orphaned events after new leads arrive."""
    orphans = conn.execute(
        """
        SELECT e.*, el.link_status
        FROM events e
        JOIN event_links el ON el.event_id = e.event_id
        WHERE el.lead_id IS NULL
          AND el.link_status = 'orphan'
        ORDER BY e.id
        """
    ).fetchall()

    linked_count = 0

    for row in orphans:
        event = {
            "event_id": row["event_id"],
            "workflow_id": row["workflow_id"],
            "event_type": row["event_type"],
            "call_id": row["call_id"],
            "occurred_at": row["occurred_at"],
            "payload": json.loads(row["payload_json"]),
        }

        # Retrieve the phone from its stored HMAC for direct matching.
        if row["phone_hash"]:
            lead = conn.execute(
                """
                SELECT id FROM leads
                WHERE workflow_id = ? AND phone_hash = ?
                """,
                (row["workflow_id"], row["phone_hash"]),
            ).fetchone()
        else:
            lead = None

        if event["event_type"] == "disposition.override":
            lead_id = resolve_event_lead(conn, event)
        else:
            lead_id = lead["id"] if lead else None

        if lead_id is not None:
            conn.execute(
                """
                UPDATE event_links
                SET lead_id = ?, link_status = 'linked'
                WHERE event_id = ?
                """,
                (lead_id, row["event_id"]),
            )
            linked_count += 1

    return linked_count


def ingest_event_file(event_path, conn, stats=None):
    """Read, validate, deduplicate and link a JSONL event file."""
    if stats is None:
        stats = get_event_stats()

    event_path = Path(event_path)
    drop_name = event_path.parent.parent.name

    with event_path.open("r", encoding="utf-8-sig") as file:
        for line in file:
            if not line.strip():
                continue

            stats["events_total"] += 1

            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                stats["malformed_events"] += 1
                continue

            if not isinstance(event, dict) or not event.get("event_id"):
                stats["malformed_events"] += 1
                continue

            event_id = str(event["event_id"])

            existing = conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?",
                (event_id,),
            ).fetchone()

            if existing:
                stats["duplicate_events_dropped"] += 1
                continue

            if not event.get("workflow_id") or not event.get("event_type"):
                stats["malformed_events"] += 1
                continue

            lead_id = resolve_event_lead(conn, event)

            if lead_id is None:
                stats["orphan_events"] += 1
                insert_event(conn, event, drop_name, None)
                continue

            known = is_known_disposition(conn, event)

            if not known:
                stats["unknown_disposition_events"] += 1

            insert_event(conn, event, drop_name, lead_id)

    return stats


def ingest_event_directory(events_dir, conn, stats=None):
    """Ingest all JSONL event files in filename order."""
    if stats is None:
        stats = get_event_stats()

    events_dir = Path(events_dir)

    for event_path in sorted(events_dir.glob("*.jsonl")):
        ingest_event_file(event_path, conn, stats)

    return stats
