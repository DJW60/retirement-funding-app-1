from __future__ import annotations

from datetime import date
import unittest

from retirement_app.personal_tax import calculate_personal_income_tax


class PersonalTaxTests(unittest.TestCase):
    def test_single_low_income_gets_medicare_levy_reduction(self) -> None:
        tax = calculate_personal_income_tax(
            taxable_income=30000.0,
            as_of_date=date(2026, 3, 25),
            age=45,
        )

        self.assertAlmostEqual(tax["medicare_levy"], 277.82, places=2)
        self.assertAlmostEqual(tax["total_tax"], 1465.82, places=2)

    def test_single_senior_gets_sapto_and_higher_medicare_threshold(self) -> None:
        tax = calculate_personal_income_tax(
            taxable_income=50000.0,
            as_of_date=date(2026, 3, 25),
            age=68,
        )

        self.assertEqual(tax["sapto"], 345.0)
        self.assertAlmostEqual(tax["medicare_levy"], 698.0, places=2)
        self.assertAlmostEqual(tax["total_tax"], 5891.0, places=2)

    def test_couple_family_threshold_can_reduce_medicare_levy_to_zero(self) -> None:
        tax = calculate_personal_income_tax(
            taxable_income=40000.0,
            as_of_date=date(2026, 3, 25),
            age=68,
            relationship_status="couple",
            spouse_taxable_income=0.0,
            spouse_age=68,
        )

        self.assertEqual(tax["sapto"], 477.0)
        self.assertEqual(tax["medicare_levy"], 0.0)
        self.assertAlmostEqual(tax["total_tax"], 2436.0, places=2)

    def test_high_income_non_senior_still_pays_full_medicare_levy_without_sapto(self) -> None:
        tax = calculate_personal_income_tax(
            taxable_income=90000.0,
            as_of_date=date(2026, 3, 25),
            age=55,
        )

        self.assertEqual(tax["sapto"], 0.0)
        self.assertAlmostEqual(tax["medicare_levy"], 1800.0, places=2)


if __name__ == "__main__":
    unittest.main()


