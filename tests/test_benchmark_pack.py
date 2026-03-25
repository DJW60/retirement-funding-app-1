from __future__ import annotations

from datetime import date
import unittest

from retirement_app.calculations import estimate_max_sustainable_spending, project_retirement
from retirement_app.benchmark_pack import list_benchmark_cases, resolve_benchmark_case_inputs
from retirement_app.models import PersonProfile, RetirementInputs


def _build_person(label: str, data: dict[str, object]) -> PersonProfile:
    return PersonProfile(
        label=label,
        birth_date=date.fromisoformat(str(data["birth_date"])),
        retirement_age=int(data["retirement_age"]),
        planning_age=int(data["planning_age"]),
        current_super_balance=float(data["current_super_balance"]),
        annual_salary=float(data["annual_salary"]),
        annual_non_salary_income=float(data.get("annual_non_salary_income", 0.0)),
        super_product_type=str(data["super_product_type"]),
        annual_salary_growth=float(data["annual_salary_growth"]),
        employer_super_rate=float(data.get("employer_super_rate", 0.0)),
        annual_salary_sacrifice=float(data.get("annual_salary_sacrifice", 0.0)),
        annual_after_tax_contribution=float(data.get("annual_after_tax_contribution", 0.0)),
    )


def _build_retirement_inputs(case_id: str) -> RetirementInputs:
    data = resolve_benchmark_case_inputs(case_id)
    primary_person = _build_person("Person 1", dict(data["primary_person"]))
    partner_data = data.get("partner_person")
    partner_person = _build_person("Person 2", dict(partner_data)) if isinstance(partner_data, dict) else None
    return RetirementInputs(
        relationship_status=str(data["relationship_status"]),
        homeowner_status=str(data["homeowner_status"]),
        primary_person=primary_person,
        partner_person=partner_person,
        annual_retirement_spending=float(data["annual_retirement_spending"]),
        annual_other_income=float(data["annual_other_income"]),
        annual_other_income_growth=float(data["annual_other_income_growth"]),
        retirement_financial_assets=float(data["retirement_financial_assets"]),
        retirement_other_assessable_assets=float(data["retirement_other_assessable_assets"]),
        include_age_pension=bool(data["include_age_pension"]),
        annual_return_pre=float(data["annual_return_pre"]),
        annual_return_post=float(data["annual_return_post"]),
        inflation_rate=float(data["inflation_rate"]),
        projection_timing_mode=str(data.get("projection_timing_mode", "anniversary")),
        retirement_drawdown_timing_mode=str(data.get("retirement_drawdown_timing_mode", "monthly")),
        use_financial_assets_for_spending=bool(data.get("use_financial_assets_for_spending", True)),
        target_estate=float(data.get("target_estate", 0.0)),
        as_of_date=date(2026, 3, 24),
    )


class BenchmarkPackTests(unittest.TestCase):
    def test_benchmark_pack_lists_expected_cases(self) -> None:
        case_ids = {case["id"] for case in list_benchmark_cases()}

        self.assertIn("single_accumulation_baseline", case_ids)
        self.assertIn("single_accumulation_contribution_change", case_ids)
        self.assertIn("single_account_based_pension_drawdown", case_ids)

    def test_single_accumulation_baseline_uses_age_51_birth_date(self) -> None:
        inputs = resolve_benchmark_case_inputs("single_accumulation_baseline")

        self.assertEqual(inputs["primary_person"]["birth_date"], "1975-01-01")
        self.assertEqual(inputs["primary_person"]["retirement_age"], 67)
        self.assertEqual(inputs["annual_return_pre"], 0.06)

    def test_contribution_change_case_inherits_and_overrides_baseline_inputs(self) -> None:
        inputs = resolve_benchmark_case_inputs("single_accumulation_contribution_change")

        self.assertEqual(inputs["primary_person"]["birth_date"], "1975-01-01")
        self.assertEqual(inputs["primary_person"]["annual_salary_sacrifice"], 10000.0)
        self.assertEqual(inputs["primary_person"]["annual_after_tax_contribution"], 5000.0)
        self.assertEqual(inputs["primary_person"]["current_super_balance"], 250000.0)

    def test_single_accumulation_baseline_matches_captured_source_within_tolerance(self) -> None:
        inputs = _build_retirement_inputs("single_accumulation_baseline")

        projection = project_retirement(inputs)

        self.assertAlmostEqual(projection.summary["first_cashflow_balance"], 894142.0, delta=250.0)

    def test_single_accumulation_contribution_change_matches_captured_source_within_tolerance(self) -> None:
        inputs = _build_retirement_inputs("single_accumulation_contribution_change")

        projection = project_retirement(inputs)

        self.assertAlmostEqual(projection.summary["first_cashflow_balance"], 1260175.0, delta=250.0)

    def test_single_account_based_pension_drawdown_matches_captured_source_within_tolerance(self) -> None:
        inputs = _build_retirement_inputs("single_account_based_pension_drawdown")

        projection = project_retirement(inputs)
        row_2036 = projection.retirement_df[projection.retirement_df["Calendar year"] == 2036].iloc[0]
        row_2054 = projection.retirement_df[projection.retirement_df["Calendar year"] == 2054].iloc[0]

        self.assertAlmostEqual(float(projection.retirement_df.iloc[0]["Actual super draw ($/yr)"]), 35000.0, delta=250.0)
        self.assertAlmostEqual(float(row_2036["Household end super ($)"]), 596471.0, delta=250.0)
        self.assertAlmostEqual(float(row_2054["Household end super ($)"]), 330047.0, delta=250.0)
        self.assertTrue(projection.summary["funds_last_to_planning_age"])

    def test_couple_same_retirement_date_simple_matches_captured_source_within_tolerance(self) -> None:
        inputs = _build_retirement_inputs("couple_same_retirement_date_simple")

        projection = project_retirement(inputs)

        self.assertAlmostEqual(estimate_max_sustainable_spending(inputs), 52919.0, delta=250.0)
        self.assertAlmostEqual(projection.summary["first_cashflow_balance"], 711128.0, delta=250.0)

    def test_couple_age_pension_means_test_matches_captured_source_within_tolerance(self) -> None:
        inputs = _build_retirement_inputs("couple_age_pension_means_test")

        projection = project_retirement(inputs)
        first_retirement_row = projection.retirement_df.iloc[0]
        first_age_pension_row = projection.age_pension_df.iloc[0]

        self.assertAlmostEqual(projection.summary["first_eligible_age_pension"], 47070.4, delta=50.0)
        self.assertEqual(projection.summary["first_eligible_age_pension_binding_test"], "Tests produce similar result")
        self.assertTrue(bool(first_age_pension_row["At full couple Age Pension"]))
        self.assertAlmostEqual(float(first_retirement_row["Actual super draw ($/yr)"]) + float(first_retirement_row["Age Pension ($/yr)"]), 59750.0, delta=250.0)


if __name__ == "__main__":
    unittest.main()
