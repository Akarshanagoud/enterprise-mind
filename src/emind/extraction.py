"""Structured output extraction: free text -> validated Pydantic objects.

Rules run offline. If a local Ollama model is configured it is tried first with JSON output and
schema validation + retry; any failure falls back to the deterministic rule extractor.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator


class InvoiceRecord(BaseModel):
    invoice_number: str = Field(min_length=1)
    vendor: str | None = None
    invoice_date: date | None = None
    total: float = Field(gt=0)
    currency: str = "USD"

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        v = v.upper()
        if v not in {"USD", "EUR", "GBP", "INR", "JPY", "CAD", "AUD"}:
            raise ValueError("unsupported currency")
        return v


_SYMBOL = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


def _parse_date(s: str) -> date | None:
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), f).replace(tzinfo=timezone.utc).date()
        except ValueError:
            continue
    return None


def extract_invoice_rules(text: str) -> InvoiceRecord:
    num = re.search(r"invoice\s*(?:no\.?|number|#|id)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", text, re.IGNORECASE)
    vendor = re.search(r"(?:vendor|from|supplier|billed by)\s*[:\-]\s*([^\n,;]+)", text, re.IGNORECASE)
    dt = re.search(r"date\s*[:\-]\s*([A-Za-z0-9 ,/\-]+?)(?:\n|$|;)", text, re.IGNORECASE)
    total = re.search(r"(?:grand\s+)?total(?:\s+due)?\s*[:\-]?\s*([$€£₹]|USD|EUR|GBP|INR)?\s*([\d,]+\.?\d*)", text, re.IGNORECASE)
    if not (num and total):
        raise ValueError("could not locate invoice number and total")
    cur = total.group(1) or "$"
    return InvoiceRecord(
        invoice_number=num.group(1),
        vendor=vendor.group(1).strip() if vendor else None,
        invoice_date=_parse_date(dt.group(1)) if dt else None,
        total=float(total.group(2).replace(",", "")),
        currency=_SYMBOL.get(cur, cur.upper()),
    )


def extract_invoice(text: str, ollama_url: str | None = None, model: str = "llama3.2",
                    retries: int = 2) -> tuple[InvoiceRecord, str]:
    """Returns (record, method) where method is 'llm' or 'rules'."""
    if ollama_url:
        prompt = ("Extract JSON with keys invoice_number, vendor, invoice_date (YYYY-MM-DD), total (number), "
                  f"currency (ISO). Return only JSON.\n\n{text}")
        for _ in range(retries):
            try:
                r = httpx.post(f"{ollama_url.rstrip('/')}/api/generate", timeout=60,
                               json={"model": model, "prompt": prompt, "format": "json", "stream": False})
                return InvoiceRecord(**json.loads(r.json()["response"])), "llm"
            except (httpx.HTTPError, ValidationError, ValueError, KeyError):
                continue
    return extract_invoice_rules(text), "rules"
