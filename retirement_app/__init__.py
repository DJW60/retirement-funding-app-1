"""Retirement funding planner package."""

from .calculations import (
    estimate_max_sustainable_spending,
    estimate_required_annual_contribution,
    estimate_required_annual_salary_sacrifice,
    project_retirement,
)
from .models import CashInheritanceEvent, RetirementInputs, RetirementProjection

__all__ = [
    "CashInheritanceEvent",
    "RetirementInputs",
    "RetirementProjection",
    "estimate_max_sustainable_spending",
    "estimate_required_annual_contribution",
    "estimate_required_annual_salary_sacrifice",
    "project_retirement",
]
