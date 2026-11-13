
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalize_header(value):
    """Normalize CSV headers and schema aliases."""
    value = str(value or "").strip().lower()
    return re.sub(r"[_\s]+", " ", value)


def normalize_phone(value):
    """
    Normalize Indian mobile numbers.
    Returns canonical +91XXXXXXXXXX or None.
    """
    if value is None or not str(value).strip():
        return None

    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) != 10:
        return None

    if digits[0] not in "6789":
        return None

    return "+91" + digits


def normalize_date(value):
    """
    Accept supported day-first date formats.
    Return YYYY-MM-DD or None.
    """
    if value is None or not str(value).strip():
        return None

    value = str(value).strip()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    return None


def normalize_decimal(value):
    """
    Parse currency and decimal values.
    Returns a decimal string with two places or None.
    """
    if value is None or not str(value).strip():
        return None

    text = str(value).strip()

    text = re.sub(
        r"^(?:₹|Rs\.?|INR)\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    if not re.fullmatch(
        r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?",
        text,
    ):
        return None

    try:
        number = Decimal(text.replace(",", ""))

        if not number.is_finite() or number < 0:
            return None

        number = number.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )

        return format(number, ".2f")

    except (InvalidOperation, ValueError):
        return None


def normalize_enum(value, allowed_values):
    """Validate enums case-insensitively and store lowercase."""
    if value is None or not str(value).strip():
        return None

    value = str(value).strip().lower()
    allowed = {str(v).strip().lower() for v in allowed_values}

    return value if value in allowed else None


def normalize_string(value):
    """Trim string values and convert empty values to None."""
    if value is None:
        return None

    value = str(value).strip()
    return value if value else None


def normalize_value(value, field):
    """
    Normalize a value using its schema field definition.
    Returns (normalized_value, warning).
    """
    value = normalize_string(value)

    if value is None:
        return None, False

    field_type = field.get("type", "string")

    if field_type == "string":
        return value, False

    if field_type == "phone":
        result = normalize_phone(value)
        return result, result is None

    if field_type == "date":
        result = normalize_date(value)
        return result, result is None

    if field_type == "decimal":
        result = normalize_decimal(value)
        return result, result is None

    if field_type == "enum":
        result = normalize_enum(value, field.get("values", []))
        return result, result is None

    return None, True


if __name__ == "__main__":
    print("Phone:", normalize_phone("91-98765-43210"))
    print("Date:", normalize_date("01-Dec-2026"))
    print("Decimal:", normalize_decimal("Rs. 12,500.50"))
    print("Enum:", normalize_enum("EN", ["en", "hi", "kn"]))
