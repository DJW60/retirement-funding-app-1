from __future__ import annotations

from datetime import date
from typing import Any

from .rules_loader import get_effective_record
from .superannuation import get_age_pension_age


def _inflate(value: float, years_from_today: int, inflation_rate: float) -> float:
    return float(value) * ((1.0 + float(inflation_rate)) ** max(int(years_from_today), 0))


def _relationship_key(relationship_status: str) -> str:
    return "single" if relationship_status == "single" else "couple"


def deem_annual_income(
    *,
    financial_assets: float,
    relationship_status: str,
    as_of_date: date,
    years_from_today: int,
    inflation_rate: float,
) -> float:
    deeming = get_effective_record("deeming_rates.json", as_of_date)
    threshold_key = "single_threshold" if relationship_status == "single" else "couple_pensioner_combined_threshold"
    threshold = _inflate(float(deeming[threshold_key]), years_from_today, inflation_rate)
    lower_rate = float(deeming["lower_rate"])
    upper_rate = float(deeming["upper_rate"])
    lower_slice = min(max(float(financial_assets), 0.0), threshold)
    upper_slice = max(float(financial_assets) - threshold, 0.0)
    return (lower_slice * lower_rate) + (upper_slice * upper_rate)


def assess_age_pension(
    *,
    ages: list[int],
    relationship_status: str,
    homeowner_status: str,
    assessable_super_balance: float,
    retirement_financial_assets: float,
    retirement_other_assessable_assets: float,
    annual_other_assessable_income: float,
    as_of_date: date,
    years_from_today: int,
    inflation_rate: float,
) -> dict[str, Any]:
    age_pension_age = get_age_pension_age(as_of_date)
    eligible_person_count = sum(1 for age in ages if int(age) >= age_pension_age)
    if eligible_person_count == 0:
        return {
            "eligible": False,
            "eligible_person_count": 0,
            "annual_pension": 0.0,
            "fortnightly_pension": 0.0,
            "income_test_rate_fortnight": 0.0,
            "assets_test_rate_fortnight": 0.0,
            "deemed_income_annual": 0.0,
            "total_assessable_income_annual": round(float(annual_other_assessable_income), 2),
            "assessable_assets": round(
                max(float(retirement_financial_assets), 0.0)
                + max(float(retirement_other_assessable_assets), 0.0),
                2,
            ),
            "binding_test": "Not yet age eligible",
        }

    rate_rules = get_effective_record("age_pension_rates.json", as_of_date)
    income_rules = get_effective_record("age_pension_income_test.json", as_of_date)
    asset_rules = get_effective_record("age_pension_assets_test.json", as_of_date)

    relationship_key = _relationship_key(relationship_status)
    housing_key = "homeowner" if homeowner_status == "homeowner" else "non_homeowner"

    if relationship_status == "single":
        max_rate_fortnight = _inflate(float(rate_rules["single_fortnight"]), years_from_today, inflation_rate)
    elif eligible_person_count >= 2:
        max_rate_fortnight = _inflate(
            float(rate_rules["couple_combined_fortnight"]),
            years_from_today,
            inflation_rate,
        )
    else:
        max_rate_fortnight = _inflate(
            float(rate_rules["couple_each_fortnight"]),
            years_from_today,
            inflation_rate,
        )

    if relationship_status == "single":
        free_area_fortnight = _inflate(
            float(income_rules["single_free_area_fortnight"]),
            years_from_today,
            inflation_rate,
        )
        income_taper = float(income_rules["single_taper_per_dollar"])
        asset_taper = float(asset_rules["taper_per_1000_fortnight"])
    else:
        free_area_fortnight = _inflate(
            float(income_rules["couple_combined_free_area_fortnight"]),
            years_from_today,
            inflation_rate,
        )
        # One-partner-eligible couples use the partnered thresholds with a single partnered payment.
        income_taper = float(income_rules["couple_each_taper_per_dollar"]) * float(eligible_person_count)
        asset_taper = float(asset_rules["taper_per_1000_fortnight"]) * (
            float(eligible_person_count) / 2.0
        )

    assessable_financial_assets = max(float(assessable_super_balance), 0.0) + max(
        float(retirement_financial_assets),
        0.0,
    )
    deemed_income_annual = deem_annual_income(
        financial_assets=assessable_financial_assets,
        relationship_status=relationship_status,
        as_of_date=as_of_date,
        years_from_today=years_from_today,
        inflation_rate=inflation_rate,
    )
    total_assessable_income_annual = max(float(annual_other_assessable_income), 0.0) + deemed_income_annual
    assessable_income_fortnight = total_assessable_income_annual / 26.0
    income_reduction = max(assessable_income_fortnight - free_area_fortnight, 0.0) * income_taper
    income_test_rate_fortnight = max(max_rate_fortnight - income_reduction, 0.0)

    asset_full_threshold = _inflate(
        float(asset_rules["full_pension_thresholds"][relationship_key][housing_key]),
        years_from_today,
        inflation_rate,
    )
    assessable_assets = assessable_financial_assets + max(float(retirement_other_assessable_assets), 0.0)
    assets_reduction = (
        max(assessable_assets - asset_full_threshold, 0.0)
        / 1000.0
        * asset_taper
    )
    assets_test_rate_fortnight = max(max_rate_fortnight - assets_reduction, 0.0)

    fortnightly_pension = max(min(max_rate_fortnight, income_test_rate_fortnight, assets_test_rate_fortnight), 0.0)
    if fortnightly_pension <= 0.0:
        binding_test = "No Age Pension payable"
    elif income_test_rate_fortnight < assets_test_rate_fortnight - 0.01:
        binding_test = "Income test"
    elif assets_test_rate_fortnight < income_test_rate_fortnight - 0.01:
        binding_test = "Assets test"
    else:
        binding_test = "Tests produce similar result"

    return {
        "eligible": True,
        "eligible_person_count": int(eligible_person_count),
        "annual_pension": round(fortnightly_pension * 26.0, 2),
        "fortnightly_pension": round(fortnightly_pension, 2),
        "income_test_rate_fortnight": round(income_test_rate_fortnight, 2),
        "assets_test_rate_fortnight": round(assets_test_rate_fortnight, 2),
        "deemed_income_annual": round(deemed_income_annual, 2),
        "total_assessable_income_annual": round(total_assessable_income_annual, 2),
        "assessable_assets": round(assessable_assets, 2),
        "binding_test": binding_test,
    }


def build_age_pension_rule_snapshot(as_of_date: date) -> dict[str, Any]:
    rate_rules = get_effective_record("age_pension_rates.json", as_of_date)
    income_rules = get_effective_record("age_pension_income_test.json", as_of_date)
    asset_rules = get_effective_record("age_pension_assets_test.json", as_of_date)
    deeming_rules = get_effective_record("deeming_rates.json", as_of_date)

    return {
        "single_max_rate_fortnight": float(rate_rules["single_fortnight"]),
        "couple_combined_max_rate_fortnight": float(rate_rules["couple_combined_fortnight"]),
        "single_income_free_area_fortnight": float(income_rules["single_free_area_fortnight"]),
        "couple_income_free_area_fortnight": float(income_rules["couple_combined_free_area_fortnight"]),
        "single_homeowner_assets_full_pension": float(asset_rules["full_pension_thresholds"]["single"]["homeowner"]),
        "couple_homeowner_assets_full_pension": float(asset_rules["full_pension_thresholds"]["couple"]["homeowner"]),
        "single_deeming_threshold": float(deeming_rules["single_threshold"]),
        "couple_deeming_threshold": float(deeming_rules["couple_pensioner_combined_threshold"]),
        "lower_deeming_rate": float(deeming_rules["lower_rate"]),
        "upper_deeming_rate": float(deeming_rules["upper_rate"]),
    }
