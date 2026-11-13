from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import os
import hmac
import hashlib
from cryptography.fernet import Fernet, InvalidToken


def get_fernet():
    """Load the encryption key from environment variables."""
    key = os.environ.get("LEDGER_FERNET_KEY")

    if not key:
        raise RuntimeError("LEDGER_FERNET_KEY is not configured")

    return Fernet(key.encode())


def get_hmac_key():
    """Load the separate HMAC key."""
    key = os.environ.get("LEDGER_HMAC_KEY")

    if not key:
        raise RuntimeError("LEDGER_HMAC_KEY is not configured")

    return key.encode()


def encrypt_phone(phone):
    """Encrypt a canonical phone number."""
    if not phone:
        return None

    return get_fernet().encrypt(phone.encode()).decode()


def decrypt_phone(encrypted_phone):
    """Decrypt a phone number when authorised."""
    if not encrypted_phone:
        return None

    try:
        return get_fernet().decrypt(
            encrypted_phone.encode()
        ).decode()
    except InvalidToken:
        raise ValueError("Phone decryption failed: invalid key or data")


def hash_phone(phone):
    """Generate deterministic HMAC-SHA256 for phone matching."""
    if not phone:
        return None

    return hmac.new(
        get_hmac_key(),
        phone.encode(),
        hashlib.sha256
    ).hexdigest()


if __name__ == "__main__":
    sample = "+919876543210"

    encrypted = encrypt_phone(sample)
    hashed = hash_phone(sample)
    decrypted = decrypt_phone(encrypted)

    print("Encrypted:", encrypted)
    print("HMAC:", hashed)
    print("Decrypted:", decrypted)
    print("Encryption test:", decrypted == sample)
