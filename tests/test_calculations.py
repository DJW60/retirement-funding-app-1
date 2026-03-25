from __future__ import annotations

from dataclasses import replace
from datetime import date
import unittest

from retirement_app.calculations import (
    _build_household_salary_sacrifice_inputs,
    estimate_max_sustainable_spending,
    estimate_required_annual_salary_sacrifice,
    project_retirement,
)
from retirement_app.models import PersonProfile, RetirementInputs, UnusedConcessionalCapAmount


def build_rounding_regression_inputs() -> RetirementInputs:
    return RetirementInputs(
        relationship_status="couple",
        homeowner_status="non_homeowner",
        primary_person=PersonProfile(
            label="Person 1",
            birth_date=date(1959, 6, 30),
            retirement_age=69,
            planning_age=91,
            current_super_balance=720000,
            annual_salary=175000,
            annual_non_salary_income=22500,
            super_product_type="accumulation",
            annual_salary_growth=0.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=20000,
            annual_after_tax_contribution=4000,
        ),
        partner_person=PersonProfile(
            label="Person 2",
            birth_date=date(1971, 6, 30),
            retirement_age=57,
            planning_age=76,
            current_super_balance=580000,
            annual_salary=60000,
            annual_non_salary_income=20000,
            super_product_type="transition_to_retirement_income_stream",
            annual_salary_growth=0.0,
            employer_super_rate=0.11,
            annual_salary_sacrifice=0,
            annual_after_tax_contribution=6000,
        ),
        annual_retirement_spending=105000,
        annual_other_income=0,
        annual_other_income_growth=0.02,
        retirement_financial_assets=0,
        retirement_other_assessable_assets=110000,
        include_age_pension=True,
        annual_return_pre=0.04,
        annual_return_post=0.04,
        inflation_rate=0.02,
        target_estate=380000,
        as_of_date=date(2026, 3, 20),
    )


def build_financial_asset_drawdown_inputs(*, use_financial_assets_for_spending: bool) -> RetirementInputs:
    return RetirementInputs(
        relationship_status="single",
        homeowner_status="homeowner",
        primary_person=PersonProfile(
            label="Person 1",
            birth_date=date(1957, 1, 1),
            retirement_age=69,
            planning_age=70,
            current_super_balance=0,
            annual_salary=0,
            annual_non_salary_income=0,
            super_product_type="accumulation",
            annual_salary_growth=0.0,
            employer_super_rate=0.0,
            annual_salary_sacrifice=0.0,
            annual_after_tax_contribution=0.0,
        ),
        partner_person=None,
        annual_retirement_spending=10000,
        annual_other_income=0,
        annual_other_income_growth=0.0,
        retirement_financial_assets=100000,
        retirement_other_assessable_assets=0,
        include_age_pension=False,
        annual_return_pre=0.0,
        annual_return_post=0.0,
        inflation_rate=0.0,
        use_financial_assets_for_spending=use_financial_assets_for_spending,
        target_estate=0,
        as_of_date=date(2026, 3, 23),
    )


def build_account_based_pension_inputs(*, retirement_drawdown_timing_mode: str) -> RetirementInputs:
    return RetirementInputs(
        relationship_status="single",
        homeowner_status="homeowner",
        primary_person=PersonProfile(
            label="Person 1",
            birth_date=date(1959, 1, 1),
            retirement_age=67,
            planning_age=95,
            current_super_balance=600000.0,
            annual_salary=0.0,
            annual_non_salary_income=0.0,
            super_product_type="account_based_pension",
            annual_salary_growth=0.0,
            employer_super_rate=0.0,
            annual_salary_sacrifice=0.0,
            annual_after_tax_contribution=0.0,
        ),
        partner_person=None,
        annual_retirement_spending=35000.0,
        annual_other_income=0.0,
        annual_other_income_growth=0.0,
        retirement_financial_assets=0.0,
        retirement_other_assessable_assets=0.0,
        include_age_pension=False,
        annual_return_pre=0.06,
        annual_return_post=0.06,
        inflation_rate=0.0,
        retirement_drawdown_timing_mode=retirement_drawdown_timing_mode,
        use_financial_assets_for_spending=False,
        target_estate=0.0,
        as_of_date=date(2026, 3, 25),
    )


class CalculationConsistencyTests(unittest.TestCase):
    def test_projection_does_not_flag_subcent_shortfall(self) -> None:
        inputs = build_rounding_regression_inputs()

        projection = project_retirement(inputs)

        self.assertFalse(projection.summary["has_spending_shortfall"])
        self.assertTrue(projection.summary["is_on_track"])

    def test_max_sustainable_spending_result_is_still_on_track(self) -> None:
        inputs = build_rounding_regression_inputs()

        max_spending = estimate_max_sustainable_spending(inputs)
        projection = project_retirement(replace(inputs, annual_retirement_spending=max_spending))

        self.assertGreaterEqual(max_spending, inputs.annual_retirement_spending)
        self.assertTrue(projection.summary["is_on_track"])

    def test_required_salary_sacrifice_result_is_still_on_track(self) -> None:
        inputs = build_rounding_regression_inputs()

        required_salary_sacrifice = estimate_required_annual_salary_sacrifice(inputs)
        self.assertIsNotNone(required_salary_sacrifice)

        adjusted_inputs = _build_household_salary_sacrifice_inputs(inputs, required_salary_sacrifice)
        projection = project_retirement(adjusted_inputs)

        self.assertTrue(projection.summary["is_on_track"])

    def test_financial_assets_can_fund_spending_before_super_draw(self) -> None:
        inputs = build_financial_asset_drawdown_inputs(use_financial_assets_for_spending=True)

        projection = project_retirement(inputs)
        first_row = projection.retirement_df.iloc[0]

        self.assertEqual(first_row["Financial assets draw used ($/yr)"], 10000.0)
        self.assertEqual(first_row["Actual super draw ($/yr)"], 0.0)
        self.assertEqual(first_row["Spending shortfall ($/yr)"], 0.0)
        self.assertEqual(projection.summary["final_financial_assets_balance"], 80000.0)
        self.assertEqual(projection.summary["final_total_resources"], 80000.0)

    def test_financial_assets_remain_untouched_when_drawdown_is_disabled(self) -> None:
        inputs = build_financial_asset_drawdown_inputs(use_financial_assets_for_spending=False)

        projection = project_retirement(inputs)
        first_row = projection.retirement_df.iloc[0]

        self.assertEqual(first_row["Financial assets draw used ($/yr)"], 0.0)
        self.assertEqual(first_row["Spending shortfall ($/yr)"], 10000.0)
        self.assertEqual(projection.summary["final_financial_assets_balance"], 100000.0)
        self.assertFalse(projection.summary["is_on_track"])

    def test_projection_uses_entered_carry_forward_cap_amounts_in_current_warning_check(self) -> None:
        inputs = build_rounding_regression_inputs()
        adjusted_primary_person = replace(
            inputs.primary_person,
            current_super_balance=470000.0,
            annual_salary=175000.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=25000.0,
            annual_after_tax_contribution=0.0,
            carry_forward_previous_30_june_total_super_balance=470000.0,
            unused_concessional_cap_amounts=(
                UnusedConcessionalCapAmount(financial_year="2020-21", amount=1000.0),
                UnusedConcessionalCapAmount(financial_year="2021-22", amount=2000.0),
                UnusedConcessionalCapAmount(financial_year="2022-23", amount=3000.0),
                UnusedConcessionalCapAmount(financial_year="2023-24", amount=4000.0),
                UnusedConcessionalCapAmount(financial_year="2024-25", amount=6000.0),
            ),
        )

        projection = project_retirement(replace(inputs, primary_person=adjusted_primary_person))

        self.assertFalse(
            any(
                warning.startswith("Person 1: Planned concessional contributions exceed the available concessional cap")
                for warning in projection.warnings
            )
        )
        self.assertFalse(
            any(
                warning.startswith("Person 1: Entered carry-forward concessional cap amounts are not applied")
                for warning in projection.warnings
            )
        )

    def test_monthly_retirement_drawdown_reduces_later_balance_relative_to_year_end_draw(self) -> None:
        monthly_projection = project_retirement(
            build_account_based_pension_inputs(retirement_drawdown_timing_mode="monthly")
        )
        year_end_projection = project_retirement(
            build_account_based_pension_inputs(retirement_drawdown_timing_mode="year_end_annual")
        )

        monthly_age_95 = float(
            monthly_projection.retirement_df[monthly_projection.retirement_df["Person 1 age"] == 95].iloc[0][
                "Household end super ($)"
            ]
        )
        year_end_age_95 = float(
            year_end_projection.retirement_df[year_end_projection.retirement_df["Person 1 age"] == 95].iloc[0][
                "Household end super ($)"
            ]
        )

        self.assertAlmostEqual(
            float(monthly_projection.retirement_df.iloc[0]["Actual super draw ($/yr)"]),
            35000.0,
            places=2,
        )
        self.assertAlmostEqual(
            float(year_end_projection.retirement_df.iloc[0]["Actual super draw ($/yr)"]),
            35000.0,
            places=2,
        )
        self.assertLess(monthly_age_95, year_end_age_95)

if __name__ == "__main__":
    unittest.main()
