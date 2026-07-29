"""PII detection and masking.

Customer data is legitimately shown to the customer in a chat response. What
must never happen is that same data landing unmasked in an audit log, a metrics
row, or a dashboard that support staff and auditors can browse.

So masking is applied on the *persistence* path, not the response path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STREET_RE = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Ln|Lane|Dr|Drive|Blvd|Boulevard|Way|Ct|Court|Pl|Place|Ter|Terrace)\b"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


@dataclass
class PIIResult:
    text: str
    found: list[str]

    @property
    def had_pii(self) -> bool:
        return bool(self.found)


def _mask_email(match: re.Match) -> str:
    local, _, domain = match.group(0).partition("@")
    dom_name, _, tld = domain.rpartition(".")
    return f"{local[:1]}***@{dom_name[:1]}***.{tld}"


def _mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"


def _mask_card(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"****-****-****-{digits[-4:]}" if len(digits) >= 4 else "****"


def mask(text: str) -> PIIResult:
    """Return a log-safe copy of ``text`` plus the categories that were found."""
    if not text:
        return PIIResult(text="", found=[])

    found: list[str] = []
    out = text

    def _apply(pattern: re.Pattern, repl, label: str) -> None:
        nonlocal out
        if pattern.search(out):
            found.append(label)
            out = pattern.sub(repl, out)

    # Order matters: SSN and card before the generic phone pattern, which is
    # loose enough to partially match a card number.
    _apply(_SSN_RE, "***-**-****", "ssn")
    _apply(_CARD_RE, _mask_card, "card_number")
    _apply(_EMAIL_RE, _mask_email, "email")
    _apply(_PHONE_RE, _mask_phone, "phone")
    _apply(_STREET_RE, "[STREET REDACTED]", "street_address")
    _apply(_ZIP_RE, "*****", "zip")

    return PIIResult(text=out, found=found)


def scan(text: str) -> list[str]:
    """Detect without rewriting."""
    return mask(text).found
