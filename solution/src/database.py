
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parents[1] / "interaction_ledger.db"


def get_connection():
    """Create a database connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn):
    """Commit successful operations and roll back failed ones."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db():
    """Create all database tables and indexes."""

    conn = get_connection()

    try:
        with transaction(conn):

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workflow_id, batch_id)
                );

                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    phone_hash TEXT NOT NULL,
                    encrypted_phone TEXT NOT NULL,
                    external_ref TEXT,
                    batch_id TEXT NOT NULL,
                    pii_encrypted_json TEXT NOT NULL DEFAULT '{}',
                    fields_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workflow_id, phone_hash)
                );

                CREATE TABLE IF NOT EXISTS rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    UNIQUE(workflow_id, batch_id, row_number)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    workflow_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    call_id TEXT,
                    occurred_at TEXT,
                    phone_hash TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    ingestion_drop TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS event_links (
                    event_id TEXT PRIMARY KEY,
                    lead_id INTEGER,
                    link_status TEXT NOT NULL DEFAULT 'orphan',
                    FOREIGN KEY(event_id)
                        REFERENCES events(event_id),
                    FOREIGN KEY(lead_id)
                        REFERENCES leads(id)
                );

                CREATE TABLE IF NOT EXISTS ingestion_drops (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    drop_path TEXT NOT NULL UNIQUE,
                    processed_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS dispositions (
                    workflow_id TEXT NOT NULL,
                    disposition_code TEXT NOT NULL,
                    label TEXT,
                    is_terminal INTEGER NOT NULL DEFAULT 0,
                    is_success INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(workflow_id, disposition_code)
                );

                CREATE TABLE IF NOT EXISTS pipeline_metrics (
                    metric_name TEXT PRIMARY KEY,
                    metric_value INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_leads_workflow
                    ON leads(workflow_id);

                CREATE INDEX IF NOT EXISTS idx_leads_external_ref
                    ON leads(workflow_id, external_ref);

                CREATE INDEX IF NOT EXISTS idx_leads_phone_hash
                    ON leads(workflow_id, phone_hash);

                CREATE INDEX IF NOT EXISTS idx_events_call
                    ON events(call_id);

                CREATE INDEX IF NOT EXISTS idx_events_workflow
                    ON events(workflow_id, event_type);

                CREATE INDEX IF NOT EXISTS idx_events_phone
                    ON events(workflow_id, phone_hash);

                CREATE INDEX IF NOT EXISTS idx_events_occurred
                    ON events(occurred_at);

                CREATE INDEX IF NOT EXISTS idx_event_links_lead
                    ON event_links(lead_id);

                CREATE INDEX IF NOT EXISTS idx_rejections_batch
                    ON rejections(workflow_id, batch_id);

                CREATE TRIGGER IF NOT EXISTS prevent_event_update
                BEFORE UPDATE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'Events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS prevent_event_delete
                BEFORE DELETE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'Events are append-only');
                END;
            """)

        print("Database initialized successfully.")
        print(f"Database location: {DB_PATH}")

    finally:
        conn.close()


def get_table_names():
    """Return all application table names."""
    conn = get_connection()

    try:
        rows = conn.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()

        return [row["name"] for row in rows]

    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("\nCreated tables:")
    for table in get_table_names():
        print(f"  - {table}")
