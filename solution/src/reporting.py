
import csv
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def parse_timestamp(value):
    if not value:
        return None

    value = value.strip()

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)

        return dt.astimezone(IST)

    except ValueError:
        return None


def load_events(conn, lead_id):
    rows = conn.execute(
        """
        SELECT e.*
        FROM events e
        JOIN event_links l ON l.event_id = e.event_id
        WHERE l.lead_id = ?
          AND l.link_status = 'linked'
        ORDER BY e.id
        """,
        (lead_id,),
    ).fetchall()

    return [
        {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "call_id": row["call_id"],
            "occurred_at": row["occurred_at"],
            "payload": json.loads(row["payload_json"] or "{}"),
        }
        for row in rows
    ]


def build_calls(events, dispositions):
    grouped = defaultdict(list)

    for event in events:
        call_id = event["call_id"]

        if call_id:
            grouped[call_id].append(event)

    calls = []

    for call_id, call_events in grouped.items():
        initiated = [
            e for e in call_events
            if e["event_type"] == "call.initiated"
        ]

        if not initiated:
            continue

        initiated.sort(
            key=lambda e: parse_timestamp(e["occurred_at"])
            or datetime.min.replace(tzinfo=IST)
        )

        start_event = initiated[0]
        start_time = parse_timestamp(start_event["occurred_at"])

        if not start_time:
            continue

        connected = any(
            e["event_type"] == "call.connected"
            for e in call_events
        )

        failed = any(
            e["event_type"] == "call.telephony_failed"
            for e in call_events
        )

        disposition_events = []

        for event in call_events:
            event_type = event["event_type"]

            if event_type not in (
                "disposition.set",
                "disposition.override",
            ):
                continue

            code = event["payload"].get("disposition_code")

            if not code:
                continue

            if code not in dispositions:
                continue

            event_time = parse_timestamp(event["occurred_at"])

            if event_time:
                disposition_events.append(
                    (event_time, event_type, code)
                )

        overrides = [
            item for item in disposition_events
            if item[1] == "disposition.override"
        ]

        regular = [
            item for item in disposition_events
            if item[1] == "disposition.set"
        ]

        if overrides:
            effective = max(overrides, key=lambda x: x[0])
        elif regular:
            effective = max(regular, key=lambda x: x[0])
        else:
            effective = None

        disposition = effective[2] if effective else None

        calls.append({
            "call_id": call_id,
            "started_at": start_time,
            "connected": connected,
            "telephony_failed": failed,
            "disposition": disposition,
            "terminal": dispositions.get(disposition, {}).get(
                "is_terminal", False
            ),
            "success": dispositions.get(disposition, {}).get(
                "is_success", False
            ),
        })

    return sorted(calls, key=lambda c: c["started_at"])


def write_csv(path, headers, rows):
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=headers,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def generate_reports(conn, output_dir, quality_stats=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load disposition definitions.
    disposition_rows = conn.execute(
        """
        SELECT workflow_id, disposition_code,
               is_terminal, is_success
        FROM dispositions
        """
    ).fetchall()

    dispositions = defaultdict(dict)

    for row in disposition_rows:
        dispositions[row["workflow_id"]][row["disposition_code"]] = {
            "is_terminal": bool(row["is_terminal"]),
            "is_success": bool(row["is_success"]),
        }

    # Load all leads.
    lead_rows = conn.execute(
        "SELECT * FROM leads ORDER BY workflow_id, external_ref"
    ).fetchall()

    lead_data = []
    batch_counts = defaultdict(lambda: defaultdict(int))
    daily = defaultdict(lambda: {
        "total_calls": 0,
        "connected_calls": 0,
        "success_leads": set(),
    })

    for lead in lead_rows:
        workflow = lead["workflow_id"]
        events = load_events(conn, lead["id"])

        calls = build_calls(
            events,
            dispositions[workflow],
        )

        if not calls:
            bucket = "yet_to_reach_out"
        elif any(call["terminal"] for call in calls):
            bucket = "completed"
        elif all(call["telephony_failed"] for call in calls):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"

        connected_count = sum(
            call["connected"] for call in calls
        )

        last_call = calls[-1] if calls else None

        current_disposition = None

        for call in reversed(calls):
            if call["disposition"]:
                current_disposition = call["disposition"]
                break

        ever_success = any(call["success"] for call in calls)

        fields = json.loads(lead["fields_json"] or "{}")
        masked_phone = fields.get("_phone_masked", "")

        last_call_at = (
            last_call["started_at"].isoformat()
            if last_call else ""
        )

        external_ref = lead["external_ref"] or ""

        lead_data.append({
            "workflow_id": workflow,
            "external_ref": external_ref,
            "batch_id": lead["batch_id"],
            "phone_masked": masked_phone,
            "bucket": bucket,
            "total_calls": len(calls),
            "connected_calls": connected_count,
            "last_call_at": last_call_at,
            "current_disposition": current_disposition or "",
            "ever_success": str(ever_success).lower(),
        })

        batch_key = (workflow, lead["batch_id"])
        batch_counts[batch_key]["total_leads"] += 1
        batch_counts[batch_key][bucket] += 1

        for call in calls:
            day = call["started_at"].date().isoformat()
            key = (workflow, day)

            daily[key]["total_calls"] += 1

            if call["connected"]:
                daily[key]["connected_calls"] += 1

            if call["success"]:
                daily[key]["success_leads"].add(lead["id"])

    # 1. Batch summary.
    batch_rows = []

    for (workflow, batch), counts in sorted(batch_counts.items()):
        batch_rows.append({
            "workflow_id": workflow,
            "batch_id": batch,
            "total_leads": counts["total_leads"],
            "yet_to_reach_out": counts["yet_to_reach_out"],
            "in_progress_telephony_failed":
                counts["in_progress_telephony_failed"],
            "in_progress_dialed": counts["in_progress_dialed"],
            "completed": counts["completed"],
        })

    write_csv(
        output_dir / "batch_summary.csv",
        [
            "workflow_id", "batch_id", "total_leads",
            "yet_to_reach_out", "in_progress_telephony_failed",
            "in_progress_dialed", "completed",
        ],
        batch_rows,
    )

    # 2. Daily overview.
    daily_rows = []

    for (workflow, day), values in sorted(daily.items()):
        total = values["total_calls"]
        connected = values["connected_calls"]

        daily_rows.append({
            "workflow_id": workflow,
            "date": day,
            "total_calls": total,
            "connected_calls": connected,
            "connectivity_rate": f"{connected / total:.4f}",
            "success_leads": len(values["success_leads"]),
        })

    write_csv(
        output_dir / "daily_overview.csv",
        [
            "workflow_id", "date", "total_calls",
            "connected_calls", "connectivity_rate",
            "success_leads",
        ],
        daily_rows,
    )

    # 3. Lead report.
    lead_data.sort(
        key=lambda row: (
            row["workflow_id"],
            row["external_ref"],
        )
    )

    write_csv(
        output_dir / "lead_report.csv",
        [
            "workflow_id", "external_ref", "batch_id",
            "phone_masked", "bucket", "total_calls",
            "connected_calls", "last_call_at",
            "current_disposition", "ever_success",
        ],
        lead_data,
    )

    # 4. Rejections.
    rejection_rows = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id, batch_id, row_number
        """
    ).fetchall()

    write_csv(
        output_dir / "rejections.csv",
        [
            "workflow_id", "batch_id",
            "row_number", "reason",
        ],
        [dict(row) for row in rejection_rows],
    )

    # 5. Data quality.
    rejected_count = conn.execute(
        "SELECT COUNT(*) FROM rejections"
    ).fetchone()[0]

    orphan_count = conn.execute(
        """
        SELECT COUNT(*) FROM event_links
        WHERE link_status = 'orphan'
        """
    ).fetchone()[0]

    total_events = conn.execute(
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]

    lead_count = len(lead_rows)

    quality = {
        "lead_rows_total": 0,
        "lead_rows_rejected": rejected_count,
        "rejections_by_reason": {},
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
        "events_total": total_events,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
        "orphan_events": orphan_count,
        "unknown_disposition_events": 0,
    }

    if quality_stats:
        quality.update(quality_stats)

    quality["lead_rows_rejected"] = rejected_count
    quality["orphan_events"] = orphan_count

    reasons = conn.execute(
        """
        SELECT reason, COUNT(*) AS count
        FROM rejections
        GROUP BY reason
        ORDER BY reason
        """
    ).fetchall()

    quality["rejections_by_reason"] = {
        row["reason"]: row["count"] for row in reasons
    }

    with open(
        output_dir / "data_quality.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(quality, file, indent=2)

    print(f"Reports generated in: {output_dir.resolve()}")
    print(f"Leads processed: {lead_count}")
    print(f"Events stored: {total_events}")
    print(f"Orphan events: {orphan_count}")
