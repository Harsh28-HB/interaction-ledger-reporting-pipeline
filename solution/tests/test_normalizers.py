import pytest

from src.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_header,
    normalize_phone,
)


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+91 98765 43210",
        "09876543210",
        "91-98765-43210",
        "(+91) 9876543210",
        "+919876543210",
    ],
)
def test_phone_formats_normalize_to_canonical(raw):
    assert normalize_phone(raw) == "+919876543210"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "123456789",
        "5123456789",
        "022-2345-6789",
        "+1 415 555 0134",
    ],
)
def test_invalid_phones_are_rejected(raw):
    assert normalize_phone(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-12-01", "2026-12-01"),
        ("01/12/2026", "2026-12-01"),
        ("01-12-2026", "2026-12-01"),
        ("01-Dec-2026", "2026-12-01"),
        ("01-dEc-2026", "2026-12-01"),
    ],
)
def test_supported_dates_normalize(raw, expected):
    assert normalize_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["31/02/2026", "2026-13-01", "soon", ""],
)
def test_invalid_dates_are_rejected(raw):
    assert normalize_date(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12500", "12500.00"),
        ("12,500.50", "12500.50"),
        ("₹12,500", "12500.00"),
        ("Rs 12500.1", "12500.10"),
        ("rs. 12500.125", "12500.13"),
        ("INR 12,500.50", "12500.50"),
    ],
)
def test_supported_decimal_values_normalize(raw, expected):
    assert normalize_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["-1", "1,25", "12,34,567", "N/A"],
)
def test_invalid_decimal_values_are_rejected(raw):
    assert normalize_decimal(raw) is None


def test_enum_is_case_insensitive_and_lowercase():
    assert normalize_enum(" HI ", ["en", "hi"]) == "hi"
    assert normalize_enum("Hindi", ["en", "hi"]) is None


def test_header_normalization_collapses_underscores_and_whitespace():
    assert normalize_header("  Policy__   Expiry ") == "policy expiry"
