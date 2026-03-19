from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any


_RULES_DIR = Path(__file__).resolve().parent / "rules"


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


@lru_cache(maxsize=None)
def load_rule_file(filename: str) -> dict[str, Any]:
    path = _RULES_DIR / filename
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_effective_record(filename: str, as_of_date: date) -> dict[str, Any]:
    payload = load_rule_file(filename)
    records = payload.get("records", [])
    selected: dict[str, Any] | None = None

    for record in sorted(records, key=lambda item: item["effective_from"]):
        effective_from = _parse_iso_date(record.get("effective_from"))
        effective_to = _parse_iso_date(record.get("effective_to"))
        if effective_from and as_of_date < effective_from:
            continue
        if effective_to and as_of_date > effective_to:
            continue
        selected = record

    if selected is None:
        raise ValueError(f"No rules in {filename} apply on {as_of_date.isoformat()}.")
    return selected


def get_rule_metadata(filename: str) -> dict[str, Any]:
    payload = load_rule_file(filename)
    return {
        "name": payload.get("name"),
        "source_name": payload.get("source_name"),
        "source_url": payload.get("source_url"),
        "notes": payload.get("notes", []),
    }
