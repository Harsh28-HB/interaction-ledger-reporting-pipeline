
import pytest

from src.normalizers import (
    normalize_phone,
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
)

from src.encryption import (
    encrypt_phone,
    decrypt_phone,
    hash_phone,
)

from src.lead_ingestion import build_header_mapping
from src.database import get_table_names


# -------------------------------
# PHONE NORMALIZATION
# -------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9876543210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("01234567890", None),
        ("12345", None),
        ("", None),
    ],
)
def test_phone_normalization(raw, expected):
    assert normalize_phone(raw) == expected


# -------------------------------
# DATE NORMALIZATION
# -------------------------------

def test_valid_date():
    assert normalize_date("01-Dec-2026") == "2026-12-01"


def test_invalid_date():
    assert normalize_date("31/02/2026") is None


def test_iso_date():
    assert normalize_date("2026-12-01") == "2026-12-01"


# -------------------------------
# DECIMAL NORMALIZATION
# -------------------------------

def test_decimal_with_currency():
    assert normalize_decimal("Rs. 12,500.50") == "12500.50"


def test_invalid_decimal():
    assert normalize_decimal("invalid") is None


def test_negative_decimal():
    assert normalize_decimal("-500") is None


# -------------------------------
# ENUM NORMALIZATION
# -------------------------------

def test_enum_case_normalization():
    assert normalize_enum("EN", ["en", "hi", "kn"]) == "en"


def test_invalid_enum():
    assert normalize_enum("xyz", ["en", "hi"]) is None


# -------------------------------
# HEADER NORMALIZATION
# -------------------------------

def test_header_normalization():
    assert normalize_header(" Phone_Number ") == "phone number"


def test_header_alias_mapping():
    fields = [
        {
            "name": "phone",
            "aliases": ["mobile", "contact"],
        }
    ]

    result = build_header_mapping(
        ["Customer Name", "Mobile"],
        fields,
    )

    assert result == {"phone": "Mobile"}


# -------------------------------
# ENCRYPTION
# -------------------------------

def test_encryption_round_trip():
    phone = "+919876543210"

    encrypted = encrypt_phone(phone)

    assert encrypted != phone
    assert decrypt_phone(encrypted) == phone


def test_hmac_deterministic():
    phone = "+919876543210"

    assert hash_phone(phone) == hash_phone(phone)


def test_hmac_not_plaintext():
    phone = "+919876543210"

    assert hash_phone(phone) != phone


# -------------------------------
# DATABASE
# -------------------------------

def test_database_tables_exist():
    tables = get_table_names()

    required = {
        "leads",
        "events",
        "batches",
        "rejections",
        "dispositions",
        "event_links",
        "ingestion_drops",
        "pipeline_metrics",
    }

    assert required.issubset(set(tables))
