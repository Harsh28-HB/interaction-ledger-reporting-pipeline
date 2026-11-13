
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolate_database(tmp_path, monkeypatch):
    from src import database

    monkeypatch.setenv(
        "LEDGER_FERNET_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv("LEDGER_HMAC_KEY", "test-only-hmac-key")
    monkeypatch.setattr(
        database,
        "DB_PATH",
        tmp_path / "interaction_ledger.db",
    )
    database.init_db()
