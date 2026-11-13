# Submission

## Candidate

- **Name:** Not provided
- **Assignment chosen:** A (Data)
- **Time spent:** Not tracked; no estimate provided

## How to run

From a clean clone, using PowerShell at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -c "from pathlib import Path; from cryptography.fernet import Fernet; import secrets; Path(r'solution\.env').write_text('LEDGER_FERNET_KEY=' + Fernet.generate_key().decode() + '\nLEDGER_HMAC_KEY=' + secrets.token_hex(32) + '\n', encoding='utf-8')"
python assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data
Set-Location solution
python -m src.main ingest ..\data\drop_1
python -m src.main ingest ..\data\drop_2
python -m src.main report --out reports
Set-Location ..
python -m pytest solution\tests\test_pipeline.py solution\tests\test_normalizers.py solution\tests\test_leads.py solution\tests\test_events.py solution\tests\test_reporting.py solution\tests\test_ingestion_pipeline.py -q
```

Keep the generated `.env` keys stable for an existing ledger. The generator
overwrites files in its output directory; use a different `--out` path if
existing data must be preserved. The reports are `solution\reports\batch_summary.csv`,
`daily_overview.csv`, `lead_report.csv`, `rejections.csv`, and `data_quality.json`.

## Design

The pipeline uses SQLite as a durable ledger. `leads` has a unique
`(workflow_id, phone_hash)` key so repeat uploads merge into one lead per
workflow. Phone numbers are normalized and encrypted with Fernet; a distinct
HMAC-SHA256 key provides deterministic matching without storing plaintext
numbers. Non-PII normalized fields and the ingestion-time masked phone are
stored separately from encrypted PII.

`events` is append-only and has a globally unique event ID. `event_links`
records a linked lead or an orphan, allowing later lead drops to relink
previously unmatched events without changing the event itself. Disposition
definitions drive call outcomes and lead buckets. Rejections, cumulative
quality metrics, source batches, and processed drop paths are persisted in
dedicated tables.

Each drop is processed in one transaction: load dispositions, ingest leads,
relink old orphans, ingest events, relink again, update metrics, and record the
drop. Any error rolls back all writes from that drop. Reports are generated
from the ledger and cumulative metrics.

## Decisions and assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| Use normalized phone HMAC plus workflow ID as the lead identity | Supports deterministic matching without plaintext phone storage | Use raw phone as a database key |
| Preserve events as append-only and store link state separately | Later relinking must not rewrite source event data | Update or replace the event when a lead arrives |
| Deduplicate events by event ID | The generator can redeliver the same event across files or drops | Treat each delivery as a separate event |
| Treat naive event timestamps as Asia/Kolkata and convert aware timestamps to it | Keeps daily report dates consistent with the assignment timezone | Interpret naive timestamps as UTC |
| Track drop idempotency by its resolved path | Makes rerunning the same local drop a no-op | Hash and retain the complete source directory |
| Keep keys in a local ignored `.env` file | Keeps secrets out of source while allowing repeat runs | Embed keys or generate new keys on each run |

## Failure modes considered

- Invalid required fields and phone numbers become reason-coded rejections;
  rejected source values are not copied into the rejection report.
- Invalid optional values are counted as warnings and omitted from normalized
  values; declared defaults are applied when appropriate.
- Malformed JSONL records are counted and skipped. Duplicate event IDs are
  counted and ignored.
- Unknown disposition codes are retained as source events and counted, but
  are not treated as recognized dispositions in lead outcome calculations.
- Orphans remain in the ledger and are retried after later leads are ingested.
- A failed drop rolls back atomically. Re-ingesting a completed drop path does
  not change the ledger.
- Key rotation, schema migrations, and content-based deduplication for a drop
  moved to a different path are not implemented.

## Testing

The Assignment A suite covers normalization, schema-driven lead validation,
duplicate lead merging and PII storage, event deduplication/linking/orphan
relinking, report calculations and UTF-8 output, drop-level rollback, and
idempotent re-ingestion. Tests use a temporary database and test-only keys.
All 61 Assignment A tests passed.

An isolated seed-2026 small-data run also verified both-drop ingestion,
report generation, repeat-ingestion invariance, no report BOM, and no
decrypted lead phone in database text or reports. No large-scale performance
test was run.

## Results

On the isolated seed-2026 small dataset, ingesting both drops and generating
all five reports took 1.28 seconds in the local environment, excluding data
generation. The run processed 2,070 lead rows (88 rejected, 70 duplicate
rows merged, 127 field warnings) and 9,027 event lines (8 malformed, 271
duplicate deliveries, 19 unknown dispositions). It stored 1,912 leads and
8,748 events; 422 events remained orphaned. Re-ingesting both drops left the
database contents and report bytes unchanged.

## If I had another day

1. Run the same validation at the large-data scale and profile the report
   aggregation queries.
2. Add explicit schema migration/version handling.
3. Add operational guidance for key rotation and database backup/restore.
