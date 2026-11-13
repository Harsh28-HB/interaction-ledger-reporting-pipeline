
import argparse
from pathlib import Path

from src.database import get_connection, init_db
from src.lead_ingestion import ingest_lead_directory, get_stats
from src.event_ingestion import (
    ingest_event_directory,
    get_event_stats,
    load_dispositions,
    relink_orphans,
)
from src.reporting import generate_reports


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT.parent / "assignment-a-data" / "schemas"
DISPOSITIONS_FILE = SCHEMA_DIR / "dispositions.csv"


def get_metric(conn, name):
    row = conn.execute(
        "SELECT metric_value FROM pipeline_metrics WHERE metric_name = ?",
        (name,),
    ).fetchone()
    return row["metric_value"] if row else 0


def add_metric(conn, name, value):
    conn.execute(
        """
        INSERT INTO pipeline_metrics(metric_name, metric_value)
        VALUES (?, ?)
        ON CONFLICT(metric_name)
        DO UPDATE SET metric_value = metric_value + excluded.metric_value
        """,
        (name, value),
    )


def get_quality_stats(conn):
    names = [
        "lead_rows_total",
        "lead_rows_rejected",
        "duplicate_lead_rows_merged",
        "field_warnings",
        "events_total",
        "malformed_events",
        "duplicate_events_dropped",
        "unknown_disposition_events",
    ]

    return {name: get_metric(conn, name) for name in names}


def ingest_drop(drop_path):
    drop_path = Path(drop_path).resolve()

    if not drop_path.exists() or not drop_path.is_dir():
        raise FileNotFoundError(f"Drop directory not found: {drop_path}")

    leads_dir = drop_path / "leads"
    events_dir = drop_path / "events"

    init_db()
    conn = get_connection()

    try:
        # Prevent reprocessing the same drop.
        existing = conn.execute(
            "SELECT 1 FROM ingestion_drops WHERE drop_path = ?",
            (str(drop_path),),
        ).fetchone()

        if existing:
            print(f"Already ingested: {drop_path.name}")
            return

        if not leads_dir.exists() and not events_dir.exists():
            raise FileNotFoundError(
                "Drop must contain a leads/ or events/ directory"
            )

        # Load the workflow disposition catalogue.
        load_dispositions(DISPOSITIONS_FILE, conn)

        lead_stats = get_stats()

        # Process leads before events in this drop.
        if leads_dir.exists():
            ingest_lead_directory(
                leads_dir,
                SCHEMA_DIR,
                conn,
                lead_stats,
            )

        # Retry linking events left orphaned by previous drops.
        relink_orphans(conn)

        event_stats = get_event_stats()

        if events_dir.exists():
            ingest_event_directory(
                events_dir,
                conn,
                event_stats,
            )

        # Re-evaluate orphan events after all new events are available.
        relink_orphans(conn)

        # Persist cumulative quality counters.
        for name in (
            "lead_rows_total",
            "lead_rows_rejected",
            "duplicate_lead_rows_merged",
            "field_warnings",
        ):
            add_metric(conn, name, lead_stats[name])

        for name in (
            "events_total",
            "malformed_events",
            "duplicate_events_dropped",
            "unknown_disposition_events",
        ):
            add_metric(conn, name, event_stats[name])

        conn.execute(
            "INSERT INTO ingestion_drops(drop_path) VALUES (?)",
            (str(drop_path),),
        )

        conn.commit()

        print(f"\nSuccessfully ingested: {drop_path.name}")
        print(f"Lead rows: {lead_stats['lead_rows_total']}")
        print(f"Rejected leads: {lead_stats['lead_rows_rejected']}")
        print(f"Merged duplicates: {lead_stats['duplicate_lead_rows_merged']}")
        print(f"Events read: {event_stats['events_total']}")
        print(f"Malformed events: {event_stats['malformed_events']}")
        print(f"Duplicate events: {event_stats['duplicate_events_dropped']}")

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def create_report(output_dir):
    init_db()
    conn = get_connection()

    try:
        quality_stats = get_quality_stats(conn)

        generate_reports(
            conn,
            output_dir,
            quality_stats=quality_stats,
        )

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Interaction Ledger and Reporting Pipeline"
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest one data drop",
    )
    ingest_parser.add_argument("drop_path")

    report_parser = subparsers.add_parser(
        "report",
        help="Generate the five output reports",
    )
    report_parser.add_argument(
        "--out",
        default="reports",
    )

    args = parser.parse_args()

    if args.command == "ingest":
        ingest_drop(args.drop_path)

    elif args.command == "report":
        create_report(args.out)


if __name__ == "__main__":
    main()
