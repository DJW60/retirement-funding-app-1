from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class CashInheritanceEvent:
    amount: float
    trigger_mode: str
    trigger_person_label: str | None = None
    trigger_age: int | None = None
    trigger_calendar_year: int | None = None


@dataclass(frozen=True)
class UnusedConcessionalCapAmount:
    financial_year: str
    amount: float


@dataclass(frozen=True)
class PersonProfile:
    label: str
    birth_date: date
    retirement_age: int
    planning_age: int
    current_super_balance: float
    annual_salary: float
    annual_non_salary_income: float
    super_product_type: str
    annual_salary_growth: float
    employer_super_rate: float
    annual_salary_sacrifice: float
    annual_after_tax_contribution: float
    employer_super_annual_amount_override: float | None = None
    carry_forward_previous_30_june_total_super_balance: float | None = None
    unused_concessional_cap_amounts: tuple[UnusedConcessionalCapAmount, ...] = ()


@dataclass(frozen=True)
class RetirementInputs:
    relationship_status: str
    homeowner_status: str
    primary_person: PersonProfile
    partner_person: PersonProfile | None
    annual_retirement_spending: float
    annual_other_income: float
    annual_other_income_growth: float
    retirement_financial_assets: float
    retirement_other_assessable_assets: float
    include_age_pension: bool
    annual_return_pre: float
    annual_return_post: float
    inflation_rate: float
    use_financial_assets_for_spending: bool = True
    cash_inheritance_event: CashInheritanceEvent | None = None
    target_estate: float = 0.0
    as_of_date: date = field(default_factory=date.today)


@dataclass
class RetirementProjection:
    accumulation_df: pd.DataFrame
    retirement_df: pd.DataFrame
    age_pension_df: pd.DataFrame
    summary: dict[str, Any]
    warnings: list[str]
    rule_snapshot: dict[str, Any]
