from __future__ import annotations

from datetime import date
from typing import Any

from .rules_loader import get_effective_record


def calculate_age(birth_date: date, as_of_date: date) -> int:
    years = as_of_date.year - birth_date.year
    if (as_of_date.month, as_of_date.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def get_super_rules(as_of_date: date) -> dict[str, Any]:
    return get_effective_record("super_rules.json", as_of_date)


def get_age_pension_age(as_of_date: date) -> int:
    return int(get_super_rules(as_of_date)["age_pension_age"])


def calculate_preservation_age(birth_date: date, as_of_date: date) -> int:
    rules = get_super_rules(as_of_date)
    for schedule in rules["preservation_age_schedule"]:
        born_before = schedule.get("born_before")
        born_from = schedule.get("born_from")
        born_to = schedule.get("born_to")

        if born_before and birth_date < date.fromisoformat(born_before):
            return int(schedule["preservation_age"])
        if born_from and birth_date < date.fromisoformat(born_from):
            continue
        if born_to and birth_date > date.fromisoformat(born_to):
            continue
        if born_from or born_to:
            return int(schedule["preservation_age"])
        if born_from and birth_date >= date.fromisoformat(born_from):
            return int(schedule["preservation_age"])

    raise ValueError("No preservation age rule matched the supplied birth date.")


def calculate_super_contribution_summary(
    *,
    annual_salary: float,
    employer_super_rate: float,
    annual_salary_sacrifice: float,
    annual_after_tax_contribution: float,
    as_of_date: date,
) -> dict[str, Any]:
    rules = get_super_rules(as_of_date)

    employer_contribution = float(annual_salary) * float(employer_super_rate)
    concessional_contribution = employer_contribution + float(annual_salary_sacrifice)
    non_concessional_contribution = float(annual_after_tax_contribution)
    contributions_tax = max(concessional_contribution, 0.0) * float(rules["contributions_tax_rate"])
    net_contribution = max(concessional_contribution - contributions_tax, 0.0) + non_concessional_contribution

    warnings: list[str] = []
    if float(employer_super_rate) + 1e-9 < float(rules["super_guarantee_rate"]):
        warnings.append(
            "Employer super rate is below the 2025-26 standard SG rate of 12%. "
            "That may be valid for some historical or special cases, but it is below the current default."
        )
    if concessional_contribution > float(rules["concessional_cap"]):
        warnings.append(
            "Planned concessional contributions exceed the standard concessional cap. "
            "This version does not yet model carry-forward cap availability or excess contributions tax."
        )
    if non_concessional_contribution > float(rules["non_concessional_cap"]):
        warnings.append(
            "Planned after-tax contributions exceed the standard non-concessional cap. "
            "This version does not yet model bring-forward eligibility."
        )
    if float(annual_salary) + concessional_contribution > float(rules["division_293_threshold"]):
        warnings.append(
            "Income plus concessional contributions exceeds the Division 293 threshold. "
            "Extra contributions tax may apply and is not modelled yet."
        )

    return {
        "employer_contribution": round(employer_contribution, 2),
        "concessional_contribution": round(concessional_contribution, 2),
        "non_concessional_contribution": round(non_concessional_contribution, 2),
        "contributions_tax": round(contributions_tax, 2),
        "net_contribution": round(net_contribution, 2),
        "warnings": warnings,
        "rules": rules,
    }


def get_account_based_pension_minimum_rate(*, age: int, as_of_date: date) -> float:
    rules = get_super_rules(as_of_date)
    for factor in rules["account_based_pension_minimum_factors"]:
        if int(factor["min_age"]) <= int(age) <= int(factor["max_age"]):
            return float(factor["rate"])
    raise ValueError(f"No account-based pension minimum factor found for age {age}.")


def build_super_rule_snapshot(as_of_date: date) -> dict[str, Any]:
    rules = get_super_rules(as_of_date)
    return {
        "financial_year": rules["financial_year"],
        "super_guarantee_rate": float(rules["super_guarantee_rate"]),
        "concessional_cap": float(rules["concessional_cap"]),
        "non_concessional_cap": float(rules["non_concessional_cap"]),
        "contributions_tax_rate": float(rules["contributions_tax_rate"]),
        "division_293_threshold": float(rules["division_293_threshold"]),
        "general_transfer_balance_cap": float(rules["general_transfer_balance_cap"]),
        "age_pension_age": int(rules["age_pension_age"]),
        "tris_maximum_rate": float(rules["tris_maximum_rate"]),
        "minimum_pension_rate_under_65": float(rules["account_based_pension_minimum_factors"][0]["rate"]),
    }
