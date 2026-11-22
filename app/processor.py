import hashlib
import re

PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
]


def scrub(raw_text: str) -> str:
    """Redige PII com regex. Custo desprezível, seguro para correr no event loop."""
    cleaned = raw_text
    for pattern in PII_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def fingerprint_of(cleaned: str, iterations: int = 120_000) -> str:
    """CPU-bound: runs on executor. pbkdf2_hmac frees GIL."""
    return hashlib.pbkdf2_hmac(
        "sha256", cleaned.encode(), b"webhook-fingerprint", iterations
    ).hex()