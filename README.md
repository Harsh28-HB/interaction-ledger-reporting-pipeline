# Assignment A: Interaction Ledger

The pipeline ingests the supplied CSV lead drops and JSONL call-event drops into
SQLite, links events to leads, and generates the five required reports.

## Setup

From the repository root, install the dependencies and create persistent local
encryption keys:

```powershell
python -m pip install -r requirements.txt
python -c "from pathlib import Path; from cryptography.fernet import Fernet; import secrets; Path(r'solution\.env').write_text('LEDGER_FERNET_KEY=' + Fernet.generate_key().decode() + '\nLEDGER_HMAC_KEY=' + secrets.token_hex(32) + '\n', encoding='utf-8')"
```

Keep the generated keys stable between runs. The `.env` file and SQLite
database are local and ignored by Git.

## Run

If you need to create the small sample data, run the supplied generator from
the repository root. It writes into `data`, so choose a different `--out`
directory if you need to preserve existing files.

```powershell
python assignment-a-data\tools\generate_data.py --seed 42 --scale small --out data
Set-Location solution
python -m src.main ingest ..\data\drop_1
python -m src.main ingest ..\data\drop_2
python -m src.main report --out reports
```

The reports are written to `solution\reports`:

- `batch_summary.csv`
- `daily_overview.csv`
- `lead_report.csv`
- `rejections.csv`
- `data_quality.json`

The ledger is stored at `solution\interaction_ledger.db`. Ingestion is
idempotent by resolved drop path. Back up the database before any manual
reset or replacement.

## Test

From the repository root:

```powershell
python -m pytest solution\tests\test_pipeline.py solution\tests\test_normalizers.py solution\tests\test_leads.py solution\tests\test_events.py solution\tests\test_reporting.py solution\tests\test_ingestion_pipeline.py -q
```

The tests use a temporary SQLite database and test-only encryption keys; they
do not modify the local ledger.

## Data handling

Phone numbers are canonicalized, encrypted with Fernet, and matched by a
separate HMAC-SHA256 key. Reports use a masked phone value stored at ingestion
time. Event records are append-only; unresolved events remain linked as
orphans and are retried when later lead drops arrive. Each drop is committed
atomically, so a failed drop can be retried without retaining partial writes.
