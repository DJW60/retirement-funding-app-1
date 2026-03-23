from __future__ import annotations

from datetime import date
from typing import Any

from .models import UnusedConcessionalCapAmount
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


def get_financial_year_start_year(as_of_date: date) -> int:
    return int(as_of_date.year if as_of_date.month >= 7 else as_of_date.year - 1)


def format_financial_year(start_year: int) -> str:
    return f"{int(start_year)}-{str(int(start_year) + 1)[-2:]}"


def get_previous_carry_forward_financial_year_labels(as_of_date: date, years: int = 5) -> list[str]:
    current_start_year = get_financial_year_start_year(as_of_date)
    earliest_start_year = max(current_start_year - int(years), 2018)
    return [format_financial_year(start_year) for start_year in range(earliest_start_year, current_start_year)]


def _parse_financial_year_start_year(financial_year: str) -> int:
    return int(str(financial_year).split("-")[0])


def _concessional_cap_history(rules: dict[str, Any]) -> dict[str, float]:
    history = {str(label): float(amount) for label, amount in rules.get("historical_concessional_caps", {}).items()}
    history[str(rules["financial_year"])] = float(rules["concessional_cap"])
    return history


def _concessional_cap_for_financial_year(financial_year: str, rules: dict[str, Any]) -> float:
    history = _concessional_cap_history(rules)
    return float(history.get(str(financial_year), float(rules["concessional_cap"])))


def _normalise_unused_concessional_cap_amounts(
    unused_concessional_cap_amounts: tuple[UnusedConcessionalCapAmount, ...],
) -> tuple[UnusedConcessionalCapAmount, ...]:
    amounts_by_year: dict[str, float] = {}
    for entry in unused_concessional_cap_amounts:
        label = str(entry.financial_year)
        amounts_by_year[label] = max(float(amounts_by_year.get(label, 0.0)) + float(entry.amount), 0.0)
    ordered_years = sorted(amounts_by_year, key=_parse_financial_year_start_year)
    return tuple(
        UnusedConcessionalCapAmount(financial_year=label, amount=round(amounts_by_year[label], 2))
        for label in ordered_years
        if amounts_by_year[label] > 0.0
    )


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
    employer_super_annual_amount_override: float | None = None,
    prior_30_june_total_super_balance: float | None = None,
    unused_concessional_cap_amounts: tuple[UnusedConcessionalCapAmount, ...] = (),
    financial_year_start_year: int | None = None,
    as_of_date: date,
) -> dict[str, Any]:
    rules = get_super_rules(as_of_date)
    financial_year_start_year = (
        int(financial_year_start_year)
        if financial_year_start_year is not None
        else get_financial_year_start_year(as_of_date)
    )
    financial_year = format_financial_year(financial_year_start_year)
    carry_forward_balance_limit = float(rules.get("carry_forward_total_super_balance_limit", 500000.0))

    employer_contribution = (
        float(employer_super_annual_amount_override)
        if employer_super_annual_amount_override is not None
        else float(annual_salary) * float(employer_super_rate)
    )
    concessional_contribution = employer_contribution + float(annual_salary_sacrifice)
    non_concessional_contribution = float(annual_after_tax_contribution)
    contributions_tax = max(concessional_contribution, 0.0) * float(rules["contributions_tax_rate"])
    net_contribution = max(concessional_contribution - contributions_tax, 0.0) + non_concessional_contribution
    general_concessional_cap = _concessional_cap_for_financial_year(financial_year, rules)

    prior_unused_amounts = _normalise_unused_concessional_cap_amounts(unused_concessional_cap_amounts)
    carry_forward_eligible = (
        prior_30_june_total_super_balance is not None
        and float(prior_30_june_total_super_balance) < carry_forward_balance_limit
    )
    carry_forward_available = (
        sum(float(entry.amount) for entry in prior_unused_amounts) if carry_forward_eligible else 0.0
    )
    available_concessional_cap = general_concessional_cap + carry_forward_available
    excess_before_carry_forward = max(concessional_contribution - general_concessional_cap, 0.0)
    carry_forward_applied = min(excess_before_carry_forward, carry_forward_available)
    excess_concessional_contribution = max(concessional_contribution - available_concessional_cap, 0.0)

    remaining_prior_amounts: list[UnusedConcessionalCapAmount] = []
    carry_forward_remaining_to_apply = carry_forward_applied
    carry_forward_applied_by_year: list[UnusedConcessionalCapAmount] = []
    for entry in prior_unused_amounts:
        applied = min(float(entry.amount), carry_forward_remaining_to_apply)
        remaining = max(float(entry.amount) - applied, 0.0)
        carry_forward_remaining_to_apply -= applied
        if applied > 0.0:
            carry_forward_applied_by_year.append(
                UnusedConcessionalCapAmount(financial_year=entry.financial_year, amount=round(applied, 2))
            )
        if remaining > 0.0:
            remaining_prior_amounts.append(
                UnusedConcessionalCapAmount(financial_year=entry.financial_year, amount=round(remaining, 2))
            )

    current_year_unused_concessional_cap = max(general_concessional_cap - concessional_contribution, 0.0)
    next_financial_year_start_year = financial_year_start_year + 1
    next_earliest_start_year = max(next_financial_year_start_year - 5, 2018)
    next_unused_cap_amounts: list[UnusedConcessionalCapAmount] = [
        entry
        for entry in remaining_prior_amounts
        if _parse_financial_year_start_year(entry.financial_year) >= next_earliest_start_year
    ]
    if current_year_unused_concessional_cap > 0.0:
        next_unused_cap_amounts.append(
            UnusedConcessionalCapAmount(
                financial_year=financial_year,
                amount=round(current_year_unused_concessional_cap, 2),
            )
        )
    next_unused_concessional_cap_amounts = _normalise_unused_concessional_cap_amounts(tuple(next_unused_cap_amounts))

    warnings: list[str] = []
    if (
        employer_super_annual_amount_override is None
        and float(employer_super_rate) + 1e-9 < float(rules["super_guarantee_rate"])
    ):
        warnings.append(
            "Employer super rate is below the 2025-26 standard SG rate of 12%. "
            "That may be valid for some historical or special cases, but it is below the current default."
        )
    if excess_concessional_contribution > 0.0:
        warnings.append(
            "Planned concessional contributions exceed the available concessional cap after the entered carry-forward amounts. "
            "This version does not yet model excess concessional contributions tax."
        )
    if prior_unused_amounts and not carry_forward_eligible:
        warnings.append(
            "Entered carry-forward concessional cap amounts are not applied because the prior 30 June total super balance "
            f"is not below {carry_forward_balance_limit:,.0f}."
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
        "financial_year": financial_year,
        "employer_contribution": round(employer_contribution, 2),
        "concessional_contribution": round(concessional_contribution, 2),
        "general_concessional_cap": round(general_concessional_cap, 2),
        "available_concessional_cap": round(available_concessional_cap, 2),
        "carry_forward_balance_limit": round(carry_forward_balance_limit, 2),
        "carry_forward_eligible": bool(carry_forward_eligible),
        "carry_forward_available": round(carry_forward_available, 2),
        "carry_forward_applied": round(carry_forward_applied, 2),
        "carry_forward_applied_by_year": carry_forward_applied_by_year,
        "current_year_unused_concessional_cap": round(current_year_unused_concessional_cap, 2),
        "next_unused_concessional_cap_amounts": next_unused_concessional_cap_amounts,
        "excess_concessional_contribution": round(excess_concessional_contribution, 2),
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
        "carry_forward_balance_limit": float(rules.get("carry_forward_total_super_balance_limit", 500000.0)),
        "non_concessional_cap": float(rules["non_concessional_cap"]),
        "contributions_tax_rate": float(rules["contributions_tax_rate"]),
        "division_293_threshold": float(rules["division_293_threshold"]),
        "general_transfer_balance_cap": float(rules["general_transfer_balance_cap"]),
        "age_pension_age": int(rules["age_pension_age"]),
        "tris_maximum_rate": float(rules["tris_maximum_rate"]),
        "minimum_pension_rate_under_65": float(rules["account_based_pension_minimum_factors"][0]["rate"]),
    }
