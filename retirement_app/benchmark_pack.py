from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


def get_benchmark_pack_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "benchmark_pack.json"


@lru_cache(maxsize=1)
def load_benchmark_pack() -> dict[str, Any]:
    with get_benchmark_pack_path().open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def list_benchmark_cases() -> list[dict[str, str]]:
    pack = load_benchmark_pack()
    return [
        {
            "id": str(case["id"]),
            "purpose": str(case["purpose"]),
            "source_tool": str(case["source_tool"]),
        }
        for case in pack.get("cases", [])
    ]


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def resolve_benchmark_case_inputs(case_id: str) -> dict[str, Any]:
    pack = load_benchmark_pack()
    cases = {str(case["id"]): case for case in pack.get("cases", [])}
    if case_id not in cases:
        raise KeyError(f"Unknown benchmark case id: {case_id}")

    case = cases[case_id]
    if "app_inputs" in case:
        return deepcopy(case["app_inputs"])

    baseline_case_id = case.get("baseline_case_id")
    overrides = case.get("app_input_overrides")
    if baseline_case_id is None or overrides is None:
        raise ValueError(f"Benchmark case {case_id} does not define app_inputs or a baseline override.")

    return _deep_merge(resolve_benchmark_case_inputs(str(baseline_case_id)), dict(overrides))

