from __future__ import annotations

from datetime import date
import math
from typing import Any

from .rules_loader import get_effective_record
from .superannuation import get_age_pension_age


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


def _round_offset_for_tax_return(offset: float) -> float:
    return float(math.ceil(max(float(offset), 0.0) - 1e-9))


def _calculate_sapto(
    *,
    rebate_income: float,
    relationship_status: str,
    sapto_age_condition_met: bool,
    sapto_rules: dict[str, Any],
    spouse_rebate_income: float = 0.0,
) -> float:
    if not sapto_age_condition_met:
        return 0.0

    reduction_rate = float(sapto_rules["reduction_rate"])
    if relationship_status == "couple":
        rule = sapto_rules["couple_each"]
        combined_rebate_income = max(float(rebate_income), 0.0) + max(float(spouse_rebate_income), 0.0)
        if (combined_rebate_income / 2.0) >= float(rule["cut_out_threshold"]):
            return 0.0
    else:
        rule = sapto_rules["single"]
        if float(rebate_income) >= float(rule["cut_out_threshold"]):
            return 0.0

    raw_offset = float(rule["max_offset"]) - max(float(rebate_income) - float(rule["shading_out_threshold"]), 0.0) * reduction_rate
    return max(min(_round_offset_for_tax_return(raw_offset), float(rule["max_offset"])), 0.0)


def _calculate_reduced_medicare_levy(
    *,
    taxable_income: float,
    lower_threshold: float,
    upper_threshold: float,
    levy_rate: float,
) -> float:
    income = max(float(taxable_income), 0.0)
    if income <= 0.0 or income <= float(lower_threshold):
        return 0.0
    full_levy = income * float(levy_rate)
    if income >= float(upper_threshold):
        return full_levy
    phase_in_range = max(float(upper_threshold) - float(lower_threshold), 0.0)
    if phase_in_range <= 0.0:
        return full_levy
    phase_in_rate = float(levy_rate) * float(upper_threshold) / phase_in_range
    return min(full_levy, max(income - float(lower_threshold), 0.0) * phase_in_rate)


def _build_medicare_thresholds(
    *,
    rules: dict[str, Any],
    relationship_status: str,
    sapto_entitled: bool,
    dependent_children: int,
) -> tuple[tuple[float, float], tuple[float, float] | None]:
    medicare_rules = rules["medicare_levy_low_income_thresholds"]
    single_key = "sapto" if sapto_entitled else "standard"
    single_thresholds = medicare_rules["single"][single_key]
    single_pair = (float(single_thresholds["lower"]), float(single_thresholds["upper"]))

    if relationship_status != "couple":
        return single_pair, None

    family_key = "sapto" if sapto_entitled else "standard"
    family_thresholds = medicare_rules["family"][family_key]
    lower = float(family_thresholds["lower"]) + max(int(dependent_children), 0) * float(
        medicare_rules["family"]["additional_child_lower"]
    )
    upper = float(family_thresholds["upper"]) + max(int(dependent_children), 0) * float(
        medicare_rules["family"]["additional_child_upper"]
    )
    return single_pair, (lower, upper)


def _calculate_medicare_levy(
    *,
    taxable_income: float,
    levy_rate: float,
    relationship_status: str,
    sapto_entitled: bool,
    rules: dict[str, Any],
    spouse_taxable_income: float = 0.0,
    dependent_children: int = 0,
) -> float:
    income = max(float(taxable_income), 0.0)
    if income <= 0.0:
        return 0.0

    full_levy = income * float(levy_rate)
    single_thresholds, family_thresholds = _build_medicare_thresholds(
        rules=rules,
        relationship_status=relationship_status,
        sapto_entitled=sapto_entitled,
        dependent_children=dependent_children,
    )
    individual_levy = _calculate_reduced_medicare_levy(
        taxable_income=income,
        lower_threshold=single_thresholds[0],
        upper_threshold=single_thresholds[1],
        levy_rate=levy_rate,
    )

    if relationship_status != "couple" or family_thresholds is None:
        return individual_levy

    family_income = income + max(float(spouse_taxable_income), 0.0)
    if family_income <= 0.0:
        return 0.0

    family_levy = _calculate_reduced_medicare_levy(
        taxable_income=family_income,
        lower_threshold=family_thresholds[0],
        upper_threshold=family_thresholds[1],
        levy_rate=levy_rate,
    )
    apportioned_family_levy = family_levy * (income / family_income)
    return min(full_levy, individual_levy, apportioned_family_levy)


def calculate_personal_income_tax(
    *,
    taxable_income: float,
    as_of_date: date,
    age: int | None = None,
    relationship_status: str = "single",
    rebate_income: float | None = None,
    spouse_taxable_income: float = 0.0,
    spouse_rebate_income: float | None = None,
    spouse_age: int | None = None,
    dependent_children: int = 0,
) -> dict[str, Any]:
    rules = get_personal_tax_rules(as_of_date)
    taxable_income = max(float(taxable_income), 0.0)
    rebate_income = taxable_income if rebate_income is None else max(float(rebate_income), 0.0)
    spouse_rebate_income = (
        max(float(spouse_taxable_income), 0.0) if spouse_rebate_income is None else max(float(spouse_rebate_income), 0.0)
    )

    bracket_tax = _calculate_bracket_tax(taxable_income, rules["resident_brackets"])
    lito = min(_calculate_lito(taxable_income, rules["lito"]), bracket_tax)
    income_tax_after_lito = max(bracket_tax - lito, 0.0)

    age_pension_age = get_age_pension_age(as_of_date)
    sapto_age_condition_met = age is not None and int(age) >= age_pension_age
    spouse_sapto_age_condition_met = spouse_age is not None and int(spouse_age) >= age_pension_age
    sapto = min(
        _calculate_sapto(
            rebate_income=rebate_income,
            relationship_status=relationship_status,
            sapto_age_condition_met=sapto_age_condition_met,
            sapto_rules=rules["sapto"],
            spouse_rebate_income=spouse_rebate_income,
        ),
        income_tax_after_lito,
    )
    income_tax_after_offsets = max(income_tax_after_lito - sapto, 0.0)

    medicare_levy = _calculate_medicare_levy(
        taxable_income=taxable_income,
        levy_rate=float(rules["medicare_levy_rate"]),
        relationship_status=relationship_status,
        sapto_entitled=sapto > 0.0,
        rules=rules,
        spouse_taxable_income=spouse_taxable_income,
        dependent_children=dependent_children,
    )
    total_tax = income_tax_after_offsets + medicare_levy

    return {
        "taxable_income": round(taxable_income, 2),
        "rebate_income": round(rebate_income, 2),
        "bracket_tax": round(bracket_tax, 2),
        "lito": round(lito, 2),
        "sapto": round(sapto, 2),
        "sapto_age_condition_met": bool(sapto_age_condition_met),
        "spouse_sapto_age_condition_met": bool(spouse_sapto_age_condition_met),
        "income_tax_after_offsets": round(income_tax_after_offsets, 2),
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
        "sapto_single_max_offset": float(rules["sapto"]["single"]["max_offset"]),
        "sapto_couple_each_max_offset": float(rules["sapto"]["couple_each"]["max_offset"]),
        "medicare_single_lower_threshold": float(rules["medicare_levy_low_income_thresholds"]["single"]["standard"]["lower"]),
        "medicare_single_sapto_lower_threshold": float(rules["medicare_levy_low_income_thresholds"]["single"]["sapto"]["lower"]),
        "medicare_family_lower_threshold": float(rules["medicare_levy_low_income_thresholds"]["family"]["standard"]["lower"]),
        "medicare_family_sapto_lower_threshold": float(rules["medicare_levy_low_income_thresholds"]["family"]["sapto"]["lower"]),
        "tax_free_threshold": float(rules["resident_brackets"][1]["threshold"]),
        "third_bracket_threshold": float(rules["resident_brackets"][2]["threshold"]),
        "top_bracket_threshold": float(rules["resident_brackets"][-1]["threshold"]),
    }
