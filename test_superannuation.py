from __future__ import annotations

from datetime import date
import unittest

from retirement_app.models import UnusedConcessionalCapAmount
from retirement_app.superannuation import calculate_super_contribution_summary


class SuperContributionSummaryTests(unittest.TestCase):
    def test_carry_forward_extends_available_concessional_cap_when_balance_is_under_limit(self) -> None:
        summary = calculate_super_contribution_summary(
            annual_salary=175000.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=25000.0,
            annual_after_tax_contribution=0.0,
            prior_30_june_total_super_balance=470000.0,
            unused_concessional_cap_amounts=(
                UnusedConcessionalCapAmount(financial_year="2020-21", amount=1000.0),
                UnusedConcessionalCapAmount(financial_year="2021-22", amount=2000.0),
                UnusedConcessionalCapAmount(financial_year="2022-23", amount=3000.0),
                UnusedConcessionalCapAmount(financial_year="2023-24", amount=4000.0),
                UnusedConcessionalCapAmount(financial_year="2024-25", amount=6000.0),
            ),
            as_of_date=date(2026, 3, 23),
        )

        self.assertEqual(summary["financial_year"], "2025-26")
        self.assertTrue(summary["carry_forward_eligible"])
        self.assertEqual(summary["general_concessional_cap"], 30000.0)
        self.assertEqual(summary["carry_forward_available"], 16000.0)
        self.assertEqual(summary["available_concessional_cap"], 46000.0)
        self.assertEqual(summary["carry_forward_applied"], 16000.0)
        self.assertEqual(summary["excess_concessional_contribution"], 0.0)
        self.assertEqual(
            tuple((entry.financial_year, entry.amount) for entry in summary["carry_forward_applied_by_year"]),
            (
                ("2020-21", 1000.0),
                ("2021-22", 2000.0),
                ("2022-23", 3000.0),
                ("2023-24", 4000.0),
                ("2024-25", 6000.0),
            ),
        )
        self.assertEqual(summary["next_unused_concessional_cap_amounts"], ())
        self.assertFalse(
            any("available concessional cap" in warning for warning in summary["warnings"])
        )

    def test_carry_forward_is_blocked_when_previous_balance_is_not_below_limit(self) -> None:
        summary = calculate_super_contribution_summary(
            annual_salary=175000.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=25000.0,
            annual_after_tax_contribution=0.0,
            prior_30_june_total_super_balance=500000.0,
            unused_concessional_cap_amounts=(
                UnusedConcessionalCapAmount(financial_year="2020-21", amount=16000.0),
            ),
            as_of_date=date(2026, 3, 23),
        )

        self.assertFalse(summary["carry_forward_eligible"])
        self.assertEqual(summary["carry_forward_available"], 0.0)
        self.assertEqual(summary["available_concessional_cap"], 30000.0)
        self.assertEqual(summary["excess_concessional_contribution"], 16000.0)
        self.assertTrue(
            any("available concessional cap" in warning for warning in summary["warnings"])
        )
        self.assertTrue(
            any("not below 500,000" in warning for warning in summary["warnings"])
        )

    def test_fixed_employer_amount_override_replaces_percentage_based_employer_super(self) -> None:
        summary = calculate_super_contribution_summary(
            annual_salary=175000.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=0.0,
            annual_after_tax_contribution=0.0,
            employer_super_annual_amount_override=18000.0,
            as_of_date=date(2026, 3, 23),
        )

        self.assertEqual(summary["employer_contribution"], 18000.0)
        self.assertEqual(summary["concessional_contribution"], 18000.0)
        self.assertEqual(summary["contributions_tax"], 2700.0)
        self.assertEqual(summary["net_contribution"], 15300.0)
        self.assertFalse(
            any(
                "SG-equivalent amount" in warning
                for warning in summary["warnings"]
            )
        )

    def test_zero_fixed_employer_override_does_not_add_percentage_based_sg_amount(self) -> None:
        summary = calculate_super_contribution_summary(
            annual_salary=82000.0,
            employer_super_rate=0.12,
            annual_salary_sacrifice=0.0,
            annual_after_tax_contribution=0.0,
            employer_super_annual_amount_override=0.0,
            as_of_date=date(2026, 3, 23),
        )

        self.assertEqual(summary["employer_contribution"], 0.0)
        self.assertEqual(summary["concessional_contribution"], 0.0)
        self.assertEqual(summary["net_contribution"], 0.0)


if __name__ == "__main__":
    unittest.main()
