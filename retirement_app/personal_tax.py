from __future__ import annotations

from datetime import date
from typing import Any

from .rules_loader import get_effective_record


def get_personal_tax_rules(as_of_date: date) -> dict[str, Any]:
    return get_effective_record("personal_income_tax_rules.json", as_of_date)


def _calculate_bracket_tax(taxable_income: float, brackets: list[dict[str, float]]) -> float:
    income = max(float(taxable_income), 0.0)
    selected = brackets[0]
    for bracket in brackets:
        if income >= float(bracket["threshold"]):
            selected = bracket
        else:
            break
    threshold = float(selected["threshold"])
    base_tax = float(selected["base_tax"])
    rate = float(selected["rate"])
    return max(base_tax + max(income - threshold, 0.0) * rate, 0.0)


def _calculate_lito(taxable_income: float, lito_rules: dict[str, float]) -> float:
    income = max(float(taxable_income), 0.0)
    max_offset = float(lito_rules["max_offset"])
    full_amount_up_to = float(lito_rules["full_amount_up_to"])
    phaseout_midpoint = float(lito_rules["phaseout_midpoint"])
    phaseout_end = float(lito_rules["phaseout_end"])
    phaseout_rate_1 = float(lito_rules["phaseout_rate_1"])
    phaseout_rate_2 = float(lito_rules["phaseout_rate_2"])

    if income <= full_amount_up_to:
        return max_offset
    if income <= phaseout_midpoint:
        return max(max_offset - (income - full_amount_up_to) * phaseout_rate_1, 0.0)
    if income <= phaseout_end:
        offset_at_midpoint = max(max_offset - (phaseout_midpoint - full_amount_up_to) * phaseout_rate_1, 0.0)
        return max(offset_at_midpoint - (income - phaseout_midpoint) * phaseout_rate_2, 0.0)
    return 0.0


def calculate_personal_income_tax(*, taxable_income: float, as_of_date: date) -> dict[str, Any]:
    rules = get_personal_tax_rules(as_of_date)
    taxable_income = max(float(taxable_income), 0.0)
    bracket_tax = _calculate_bracket_tax(taxable_income, rules["resident_brackets"])
    lito = min(_calculate_lito(taxable_income, rules["lito"]), bracket_tax)
    income_tax_after_lito = max(bracket_tax - lito, 0.0)
    medicare_levy = taxable_income * float(rules["medicare_levy_rate"]) if taxable_income > 0.0 else 0.0
    total_tax = income_tax_after_lito + medicare_levy

    return {
        "taxable_income": round(taxable_income, 2),
        "bracket_tax": round(bracket_tax, 2),
        "lito": round(lito, 2),
        "medicare_levy": round(medicare_levy, 2),
        "total_tax": round(total_tax, 2),
        "net_income_after_tax": round(max(taxable_income - total_tax, 0.0), 2),
        "rules": rules,
    }


def build_tax_rule_snapshot(as_of_date: date) -> dict[str, Any]:
    rules = get_personal_tax_rules(as_of_date)
    return {
        "financial_year": rules["financial_year"],
        "medicare_levy_rate": float(rules["medicare_levy_rate"]),
        "lito_max_offset": float(rules["lito"]["max_offset"]),
        "tax_free_threshold": float(rules["resident_brackets"][1]["threshold"]),
        "third_bracket_threshold": float(rules["resident_brackets"][2]["threshold"]),
        "top_bracket_threshold": float(rules["resident_brackets"][-1]["threshold"]),
    }
