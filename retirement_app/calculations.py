from __future__ import annotations

from dataclasses import replace
import math

import pandas as pd

from .age_pension import assess_age_pension, build_age_pension_rule_snapshot
from .models import CashInheritanceEvent, PersonProfile, RetirementInputs, RetirementProjection
from .personal_tax import build_tax_rule_snapshot, calculate_personal_income_tax
from .superannuation import (
    build_super_rule_snapshot,
    calculate_age,
    calculate_preservation_age,
    calculate_super_contribution_summary,
    get_financial_year_start_year,
    get_account_based_pension_minimum_rate,
)


_MAX_SEARCH_SALARY_SACRIFICE = 300_000.0
_MAX_SEARCH_SPENDING = 500_000.0
_SEARCH_STEPS = 40
_CURRENCY_TOLERANCE = 0.005
_SUPER_PRODUCT_TYPES = {
    "accumulation",
    "account_based_pension",
    "transition_to_retirement_income_stream",
}
_INHERITANCE_TRIGGER_MODES = {"person_age", "calendar_year"}


def _household_people(inputs: RetirementInputs) -> list[PersonProfile]:
    people = [inputs.primary_person]
    if inputs.partner_person is not None:
        people.append(inputs.partner_person)
    return people


def _current_age(person: PersonProfile, as_of_date) -> int:
    return calculate_age(person.birth_date, as_of_date)


def _super_product_label(value: str) -> str:
    if value == "account_based_pension":
        return "Account-based pension"
    if value == "transition_to_retirement_income_stream":
        return "Transition to retirement income stream"
    return "Accumulation"


def _round_currency(value: float) -> float:
    return round(float(value), 2)


def _round_currency_down(value: float) -> float:
    return math.floor((float(value) + 1e-9) * 100.0) / 100.0


def _round_currency_up(value: float) -> float:
    return math.ceil((float(value) - 1e-9) * 100.0) / 100.0


def _positive_currency_gap(value: float) -> float:
    value = float(value)
    return value if value > _CURRENCY_TOLERANCE else 0.0


def _dedupe_warnings(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for message in messages:
        if message in seen:
            continue
        seen.add(message)
        ordered.append(message)
    return ordered


def _person_by_label(inputs: RetirementInputs, label: str) -> PersonProfile | None:
    for person in _household_people(inputs):
        if person.label == label:
            return person
    return None


def _validate_person(person: PersonProfile, as_of_date) -> None:
    current_age = _current_age(person, as_of_date)
    if person.retirement_age < current_age:
        raise ValueError(f"{person.label} retirement age must be at least the current age.")
    if person.planning_age <= person.retirement_age:
        raise ValueError(f"{person.label} planning age must be greater than retirement age.")

    non_negative_fields = {
        "current_super_balance": person.current_super_balance,
        "annual_salary": person.annual_salary,
        "annual_non_salary_income": person.annual_non_salary_income,
        "annual_salary_sacrifice": person.annual_salary_sacrifice,
        "annual_after_tax_contribution": person.annual_after_tax_contribution,
    }
    if person.employer_super_annual_amount_override is not None:
        non_negative_fields["employer_super_annual_amount_override"] = person.employer_super_annual_amount_override
    if person.carry_forward_previous_30_june_total_super_balance is not None:
        non_negative_fields["carry_forward_previous_30_june_total_super_balance"] = (
            person.carry_forward_previous_30_june_total_super_balance
        )
    for field_name, value in non_negative_fields.items():
        if float(value) < 0.0:
            raise ValueError(f"{person.label} {field_name} cannot be negative.")

    for entry in person.unused_concessional_cap_amounts:
        if float(entry.amount) < 0.0:
            raise ValueError(f"{person.label} unused concessional cap amount cannot be negative.")

    rate_fields = {
        "annual_salary_growth": person.annual_salary_growth,
        "employer_super_rate": person.employer_super_rate,
    }
    for field_name, value in rate_fields.items():
        if float(value) <= -1.0:
            raise ValueError(f"{person.label} {field_name} must be greater than -100%.")

    if person.super_product_type not in _SUPER_PRODUCT_TYPES:
        raise ValueError(
            f"{person.label} super_product_type must be one of: "
            f"{', '.join(sorted(_SUPER_PRODUCT_TYPES))}."
        )


def _validate_inputs(inputs: RetirementInputs) -> None:
    if inputs.relationship_status not in {"single", "couple"}:
        raise ValueError("relationship_status must be 'single' or 'couple'.")
    if inputs.homeowner_status not in {"homeowner", "non_homeowner"}:
        raise ValueError("homeowner_status must be 'homeowner' or 'non_homeowner'.")

    if inputs.relationship_status == "single" and inputs.partner_person is not None:
        raise ValueError("Single mode must not include a partner profile.")
    if inputs.relationship_status == "couple" and inputs.partner_person is None:
        raise ValueError("Couple mode requires a partner profile.")

    for person in _household_people(inputs):
        _validate_person(person, inputs.as_of_date)

    non_negative_fields = {
        "annual_retirement_spending": inputs.annual_retirement_spending,
        "annual_other_income": inputs.annual_other_income,
        "retirement_financial_assets": inputs.retirement_financial_assets,
        "retirement_other_assessable_assets": inputs.retirement_other_assessable_assets,
        "target_estate": inputs.target_estate,
    }
    for field_name, value in non_negative_fields.items():
        if float(value) < 0.0:
            raise ValueError(f"{field_name} cannot be negative.")

    rate_fields = {
        "annual_other_income_growth": inputs.annual_other_income_growth,
        "annual_return_pre": inputs.annual_return_pre,
        "annual_return_post": inputs.annual_return_post,
        "inflation_rate": inputs.inflation_rate,
    }
    for field_name, value in rate_fields.items():
        if float(value) <= -1.0:
            raise ValueError(f"{field_name} must be greater than -100%.")

    if inputs.cash_inheritance_event is not None:
        _validate_cash_inheritance_event(inputs.cash_inheritance_event, inputs)


def _validate_cash_inheritance_event(event: CashInheritanceEvent, inputs: RetirementInputs) -> None:
    if float(event.amount) < 0.0:
        raise ValueError("cash inheritance amount cannot be negative.")
    if event.trigger_mode not in _INHERITANCE_TRIGGER_MODES:
        raise ValueError("cash inheritance trigger_mode must be 'person_age' or 'calendar_year'.")

    if event.trigger_mode == "calendar_year":
        if event.trigger_calendar_year is None:
            raise ValueError("cash inheritance trigger_calendar_year is required for calendar_year mode.")
        if int(event.trigger_calendar_year) < int(inputs.as_of_date.year):
            raise ValueError("cash inheritance calendar year cannot be before the as_of date year.")
        return

    if event.trigger_person_label is None:
        raise ValueError("cash inheritance trigger_person_label is required for person_age mode.")
    trigger_person = _person_by_label(inputs, event.trigger_person_label)
    if trigger_person is None:
        raise ValueError("cash inheritance trigger_person_label must match a person in the household.")
    if event.trigger_age is None:
        raise ValueError("cash inheritance trigger_age is required for person_age mode.")
    current_age = _current_age(trigger_person, inputs.as_of_date)
    if int(event.trigger_age) < int(current_age):
        raise ValueError("cash inheritance trigger_age cannot be before the selected person's current age.")


def _resolve_cash_inheritance_trigger_offset(
    event: CashInheritanceEvent,
    *,
    inputs: RetirementInputs,
    current_ages: dict[str, int],
) -> int:
    if event.trigger_mode == "calendar_year":
        return max(int(event.trigger_calendar_year) - int(inputs.as_of_date.year), 0)
    return max(int(event.trigger_age) - int(current_ages[event.trigger_person_label]), 0)


def _salary_for_year(person: PersonProfile, year_offset: int) -> float:
    return float(person.annual_salary) * ((1.0 + float(person.annual_salary_growth)) ** int(year_offset))


def _inflate(value: float, years: int, growth_rate: float) -> float:
    return float(value) * ((1.0 + float(growth_rate)) ** max(int(years), 0))


def _build_household_salary_sacrifice_inputs(
    inputs: RetirementInputs,
    total_household_salary_sacrifice: float,
) -> RetirementInputs:
    people = _household_people(inputs)
    weight_total = sum(max(float(person.annual_salary), 1.0) for person in people)
    allocated_people: list[PersonProfile] = []
    remaining = float(total_household_salary_sacrifice)

    for index, person in enumerate(people):
        if index == len(people) - 1:
            allocation = max(remaining, 0.0)
        else:
            weight = (
                max(float(person.annual_salary), 1.0) / weight_total
                if weight_total > 0.0
                else 1.0 / len(people)
            )
            allocation = max(float(total_household_salary_sacrifice) * weight, 0.0)
            remaining -= allocation
        allocated_people.append(replace(person, annual_salary_sacrifice=round(allocation, 2)))

    primary_person = allocated_people[0]
    partner_person = allocated_people[1] if len(allocated_people) > 1 else None
    return replace(inputs, primary_person=primary_person, partner_person=partner_person)


def _available_employment_income(person: PersonProfile, year_offset: int) -> float:
    salary = _salary_for_year(person, year_offset)
    return max(
        salary - float(person.annual_salary_sacrifice) - float(person.annual_after_tax_contribution),
        0.0,
    )


def _taxable_work_income(person: PersonProfile, year_offset: int) -> float:
    salary = _salary_for_year(person, year_offset)
    return max(salary - float(person.annual_salary_sacrifice), 0.0)


def _non_salary_income_for_year(person: PersonProfile, year_offset: int, growth_rate: float) -> float:
    return _inflate(person.annual_non_salary_income, year_offset, growth_rate)


def _is_super_assessable_for_age_pension(
    person: PersonProfile,
    *,
    age: int,
    age_pension_age: int,
) -> bool:
    if int(age) >= int(age_pension_age):
        return True
    return person.super_product_type != "accumulation"


def _can_access_unrestricted_super(*, age: int, preservation_age: int, is_retired: bool) -> bool:
    return int(age) >= 65 or (int(age) >= int(preservation_age) and bool(is_retired))


def _can_draw_from_super(
    person: PersonProfile,
    *,
    age: int,
    preservation_age: int,
    is_retired: bool,
) -> bool:
    if person.super_product_type == "transition_to_retirement_income_stream":
        return int(age) >= int(preservation_age)
    return _can_access_unrestricted_super(age=age, preservation_age=preservation_age, is_retired=is_retired)


def _first_cashflow_offset(
    person: PersonProfile,
    *,
    current_age: int,
    preservation_age: int,
) -> int:
    retirement_offset = max(int(person.retirement_age - current_age), 0)
    if person.super_product_type == "transition_to_retirement_income_stream":
        return max(int(preservation_age - current_age), 0)
    return retirement_offset


def _minimum_required_super_draw(
    person: PersonProfile,
    *,
    age: int,
    preservation_age: int,
    is_retired: bool,
    start_balance: float,
    as_of_date,
) -> float:
    if person.super_product_type == "accumulation":
        return 0.0
    if not _can_draw_from_super(person, age=age, preservation_age=preservation_age, is_retired=is_retired):
        return 0.0
    return max(float(start_balance), 0.0) * get_account_based_pension_minimum_rate(
        age=int(age),
        as_of_date=as_of_date,
    )


def _maximum_allowed_super_draw(
    person: PersonProfile,
    *,
    age: int,
    preservation_age: int,
    is_retired: bool,
    start_balance: float,
    after_return_balance: float,
    tris_maximum_rate: float,
) -> float:
    if not _can_draw_from_super(person, age=age, preservation_age=preservation_age, is_retired=is_retired):
        return 0.0
    if person.super_product_type == "transition_to_retirement_income_stream":
        return min(max(float(after_return_balance), 0.0), max(float(start_balance), 0.0) * float(tris_maximum_rate))
    return max(float(after_return_balance), 0.0)


def _allocate_additional_draw(
    *,
    target_total_draw: float,
    minimum_draw_by_person: dict[str, float],
    maximum_draw_by_person: dict[str, float],
) -> dict[str, float]:
    draw_by_person = {
        label: min(float(minimum_draw_by_person[label]), float(maximum_draw_by_person[label]))
        for label in minimum_draw_by_person
    }
    remaining = max(float(target_total_draw) - sum(draw_by_person.values()), 0.0)
    if remaining <= 1e-9:
        return draw_by_person

    capacities = {
        label: max(float(maximum_draw_by_person[label]) - float(draw_by_person[label]), 0.0)
        for label in draw_by_person
    }
    total_capacity = sum(capacities.values())
    if total_capacity <= 1e-9:
        return draw_by_person

    ordered_labels = [label for label, capacity in capacities.items() if capacity > 0.0]
    for index, label in enumerate(ordered_labels):
        capacity = capacities[label]
        if remaining <= 1e-9:
            break
        if index == len(ordered_labels) - 1:
            allocation = min(capacity, remaining)
        else:
            share = capacity / total_capacity if total_capacity > 0.0 else 0.0
            allocation = min(capacity, remaining * share)
        draw_by_person[label] += allocation
        remaining -= allocation
        total_capacity -= capacity

    if remaining > 1e-6:
        for label in ordered_labels:
            extra_capacity = max(float(maximum_draw_by_person[label]) - float(draw_by_person[label]), 0.0)
            if extra_capacity <= 0.0:
                continue
            allocation = min(extra_capacity, remaining)
            draw_by_person[label] += allocation
            remaining -= allocation
            if remaining <= 1e-9:
                break

    return draw_by_person


def project_retirement(inputs: RetirementInputs) -> RetirementProjection:
    _validate_inputs(inputs)

    people = _household_people(inputs)
    current_ages = {person.label: _current_age(person, inputs.as_of_date) for person in people}
    preservation_ages = {
        person.label: calculate_preservation_age(person.birth_date, inputs.as_of_date)
        for person in people
    }
    retirement_year_offsets = {
        person.label: max(int(person.retirement_age - current_ages[person.label]), 0)
        for person in people
    }
    planning_year_offsets = {
        person.label: max(int(person.planning_age - current_ages[person.label]), 0)
        for person in people
    }
    cashflow_start_year_offsets = {
        person.label: _first_cashflow_offset(
            person,
            current_age=current_ages[person.label],
            preservation_age=preservation_ages[person.label],
        )
        for person in people
    }

    first_cashflow_year_offset = min(cashflow_start_year_offsets.values())
    first_retirement_year_offset = min(retirement_year_offsets.values())
    full_retirement_year_offset = max(retirement_year_offsets.values())
    final_year_offset = max(planning_year_offsets.values())

    super_snapshot = build_super_rule_snapshot(inputs.as_of_date)
    age_pension_snapshot = build_age_pension_rule_snapshot(inputs.as_of_date)
    tax_snapshot = build_tax_rule_snapshot(inputs.as_of_date)
    age_pension_age = int(super_snapshot["age_pension_age"])
    tris_maximum_rate = float(super_snapshot["tris_maximum_rate"])
    cash_inheritance_event = inputs.cash_inheritance_event
    inheritance_trigger_offset: int | None = None
    inheritance_within_horizon = False
    current_financial_year_start_year = get_financial_year_start_year(inputs.as_of_date)

    if cash_inheritance_event is not None and float(cash_inheritance_event.amount) > 0.0:
        inheritance_trigger_offset = _resolve_cash_inheritance_trigger_offset(
            cash_inheritance_event,
            inputs=inputs,
            current_ages=current_ages,
        )
        inheritance_within_horizon = inheritance_trigger_offset <= final_year_offset

    warnings: list[str] = []
    for person in people:
        current_contribution_summary = calculate_super_contribution_summary(
            annual_salary=float(person.annual_salary),
            employer_super_rate=float(person.employer_super_rate),
            annual_salary_sacrifice=float(person.annual_salary_sacrifice),
            annual_after_tax_contribution=float(person.annual_after_tax_contribution),
            employer_super_annual_amount_override=person.employer_super_annual_amount_override,
            prior_30_june_total_super_balance=person.carry_forward_previous_30_june_total_super_balance,
            unused_concessional_cap_amounts=person.unused_concessional_cap_amounts,
            financial_year_start_year=current_financial_year_start_year,
            as_of_date=inputs.as_of_date,
        )
        warnings.extend(f"{person.label}: {warning}" for warning in current_contribution_summary["warnings"])

        if (
            person.super_product_type != "accumulation"
            and float(person.current_super_balance) > float(super_snapshot["general_transfer_balance_cap"])
        ):
            warnings.append(
                f"{person.label}: current super balance is above the general transfer balance cap. "
                "This planner does not yet model personal transfer balance cap history."
            )
        if person.super_product_type == "account_based_pension":
            warnings.append(
                f"{person.label}: account-based pension minimum drawdowns are applied using the age-based ATO factors. "
                "This version does not yet pro-rate the first pension year or split balances between accumulation and pension accounts."
            )
            if current_ages[person.label] < 65 and not (
                current_ages[person.label] >= preservation_ages[person.label]
                and retirement_year_offsets[person.label] == 0
            ):
                warnings.append(
                    f"{person.label}: account-based pension drawings are assumed unavailable until an unrestricted condition of release is met."
                )
        if person.super_product_type == "transition_to_retirement_income_stream":
            warnings.append(
                f"{person.label}: TRIS rules are approximated with age-based minimum payments and a 10% maximum. "
                "This version does not yet model split accumulation/TRIS accounts or detailed pension-payment tax treatment."
            )
            if current_ages[person.label] < preservation_ages[person.label]:
                warnings.append(
                    f"{person.label}: TRIS drawings are assumed unavailable until preservation age is reached."
                )

    if inputs.include_age_pension:
        warnings.append(
            "Age Pension estimate uses the standard income and assets tests with deeming. "
            "Under Age Pension age, accumulation super is treated as exempt while account-based pension and TRIS settings are treated as assessable."
        )
    if cash_inheritance_event is not None and float(cash_inheritance_event.amount) > 0.0:
        warnings.append(
            "Cash inheritance is modelled as a one-off household reserve. It can fund later spending and is counted in Age Pension financial assets and deeming, but this version does not yet model inherited property, inherited shares, super death benefits, or CGT consequences."
        )
        if not inheritance_within_horizon:
            warnings.append(
                "The selected inheritance timing falls outside the current planning horizon, so it does not affect this projection."
            )
    warnings = _dedupe_warnings(warnings)

    balances = {person.label: float(person.current_super_balance) for person in people}
    carry_forward_unused_cap_state_by_person = {
        person.label: person.unused_concessional_cap_amounts for person in people
    }
    carry_forward_prior_balance_by_person = {
        person.label: person.carry_forward_previous_30_june_total_super_balance for person in people
    }
    retirement_balance_by_person: dict[str, float] = {}
    cash_reserve_balance = 0.0
    financial_asset_balance = float(inputs.retirement_financial_assets)
    inheritance_applied = False

    accumulation_rows: list[dict[str, object]] = []
    retirement_rows: list[dict[str, object]] = []
    age_pension_rows: list[dict[str, object]] = []

    first_cashflow_row: dict[str, object] | None = None
    first_shortfall_row: dict[str, object] | None = None
    first_eligible_age_pension_row: dict[str, object] | None = None
    first_retirement_balance_snapshot: float | None = None
    full_retirement_balance_snapshot: float | None = None
    first_cashflow_balance_snapshot: float | None = None
    first_inheritance_row: dict[str, object] | None = None

    for year_offset in range(final_year_offset + 1):
        calendar_year = int(inputs.as_of_date.year + year_offset)
        financial_year_start_year = current_financial_year_start_year + year_offset
        ages = {person.label: current_ages[person.label] + year_offset for person in people}
        start_balances = {label: float(balance) for label, balance in balances.items()}
        inheritance_received_this_year = 0.0

        if (
            cash_inheritance_event is not None
            and float(cash_inheritance_event.amount) > 0.0
            and not inheritance_applied
            and inheritance_trigger_offset is not None
            and year_offset == inheritance_trigger_offset
        ):
            inheritance_received_this_year = float(cash_inheritance_event.amount)
            cash_reserve_balance += inheritance_received_this_year
            inheritance_applied = True

        if year_offset == first_cashflow_year_offset:
            first_cashflow_balance_snapshot = sum(start_balances.values())
        if year_offset == first_retirement_year_offset:
            first_retirement_balance_snapshot = sum(start_balances.values())
        if year_offset == full_retirement_year_offset:
            full_retirement_balance_snapshot = sum(start_balances.values())

        for person in people:
            if year_offset == retirement_year_offsets[person.label] and person.label not in retirement_balance_by_person:
                retirement_balance_by_person[person.label] = start_balances[person.label]

        person_year_data: dict[str, dict[str, float | bool | str]] = {}
        household_start_super = sum(start_balances.values())
        household_start_super_assessable = 0.0
        household_start_super_exempt = 0.0
        total_work_and_other_income = 0.0
        total_taxable_income = 0.0
        total_personal_tax = 0.0
        total_net_after_tax_income = 0.0
        total_net_contributions = 0.0

        shared_household_income = _inflate(
            inputs.annual_other_income,
            year_offset,
            inputs.annual_other_income_growth,
        )
        shared_household_income_per_person = shared_household_income / len(people) if people else 0.0

        for person in people:
            age = ages[person.label]
            preservation_age = preservation_ages[person.label]
            retirement_offset = retirement_year_offsets[person.label]
            is_retired = year_offset >= retirement_offset
            can_draw = _can_draw_from_super(
                person,
                age=age,
                preservation_age=preservation_age,
                is_retired=is_retired,
            )
            assessable_for_age_pension = _is_super_assessable_for_age_pension(
                person,
                age=age,
                age_pension_age=age_pension_age,
            )

            start_balance = start_balances[person.label]

            if year_offset < retirement_offset:
                salary = _salary_for_year(person, year_offset)
                available_employment_income = _available_employment_income(person, year_offset)
                taxable_work_income = _taxable_work_income(person, year_offset)
                contribution_summary = calculate_super_contribution_summary(
                    annual_salary=salary,
                    employer_super_rate=float(person.employer_super_rate),
                    annual_salary_sacrifice=float(person.annual_salary_sacrifice),
                    annual_after_tax_contribution=float(person.annual_after_tax_contribution),
                    employer_super_annual_amount_override=person.employer_super_annual_amount_override,
                    prior_30_june_total_super_balance=carry_forward_prior_balance_by_person[person.label],
                    unused_concessional_cap_amounts=carry_forward_unused_cap_state_by_person[person.label],
                    financial_year_start_year=financial_year_start_year,
                    as_of_date=inputs.as_of_date,
                )
                net_contribution = float(contribution_summary["net_contribution"])
            else:
                available_employment_income = 0.0
                taxable_work_income = 0.0
                net_contribution = 0.0

            non_salary_income = _non_salary_income_for_year(
                person,
                year_offset,
                inputs.annual_other_income_growth,
            )
            gross_cash_income = available_employment_income + non_salary_income + shared_household_income_per_person
            taxable_income = taxable_work_income + non_salary_income + shared_household_income_per_person
            tax_result = calculate_personal_income_tax(
                taxable_income=taxable_income,
                as_of_date=inputs.as_of_date,
            )
            personal_tax = float(tax_result["total_tax"])
            net_after_tax_income = max(gross_cash_income - personal_tax, 0.0)

            balance_after_contributions = max(start_balance + net_contribution, 0.0)
            return_rate = (
                float(inputs.annual_return_post)
                if year_offset >= cashflow_start_year_offsets[person.label]
                else float(inputs.annual_return_pre)
            )
            balance_after_return = max(balance_after_contributions * (1.0 + return_rate), 0.0)

            if year_offset < retirement_offset:
                carry_forward_unused_cap_state_by_person[person.label] = contribution_summary[
                    "next_unused_concessional_cap_amounts"
                ]
                carry_forward_prior_balance_by_person[person.label] = balance_after_return

            person_year_data[person.label] = {
                "age": age,
                "retired": is_retired,
                "super_product": _super_product_label(person.super_product_type),
                "super_assessment_treatment": "Assessable" if assessable_for_age_pension else "Exempt",
                "start_balance": start_balance,
                "non_salary_income": non_salary_income,
                "gross_cash_income": gross_cash_income,
                "taxable_income": taxable_income,
                "personal_tax": personal_tax,
                "net_income_after_tax": net_after_tax_income,
                "net_contribution": net_contribution,
                "balance_after_return": balance_after_return,
                "can_draw": can_draw,
                "assessable_for_age_pension": assessable_for_age_pension,
            }

            if assessable_for_age_pension:
                household_start_super_assessable += start_balance
            else:
                household_start_super_exempt += start_balance
            total_work_and_other_income += gross_cash_income
            total_taxable_income += taxable_income
            total_personal_tax += personal_tax
            total_net_after_tax_income += net_after_tax_income
            total_net_contributions += net_contribution

        if year_offset < first_cashflow_year_offset:
            balances = {
                label: float(person_year_data[label]["balance_after_return"])
                for label in person_year_data
            }
            household_end_super = sum(balances.values())
            accumulation_row: dict[str, object] = {
                "Year": year_offset,
                "Calendar year": calendar_year,
                "Household phase": "Before first cashflow year",
                "Household start super ($)": _round_currency(household_start_super),
                "Household net contributions ($/yr)": _round_currency(total_net_contributions),
                "Inheritance received ($/yr)": _round_currency(inheritance_received_this_year),
                "Cash reserve balance ($)": _round_currency(cash_reserve_balance),
                "Household end super ($)": _round_currency(household_end_super),
                "Real household end super ($, today's dollars)": _round_currency(
                    household_end_super / ((1.0 + float(inputs.inflation_rate)) ** year_offset)
                ),
            }
            for person in people:
                person_data = person_year_data[person.label]
                accumulation_row[f"{person.label} age"] = int(person_data["age"])
                accumulation_row[f"{person.label} super product"] = person_data["super_product"]
                accumulation_row[f"{person.label} start super ($)"] = _round_currency(
                    float(person_data["start_balance"])
                )
                accumulation_row[f"{person.label} net contribution ($/yr)"] = _round_currency(
                    float(person_data["net_contribution"])
                )
                accumulation_row[f"{person.label} end super ($)"] = _round_currency(
                    float(balances[person.label])
                )
            accumulation_rows.append(accumulation_row)
            if first_inheritance_row is None and inheritance_received_this_year > 0.0:
                first_inheritance_row = accumulation_row
            continue

        if inputs.include_age_pension:
            age_pension_result = assess_age_pension(
                ages=[ages[person.label] for person in people],
                relationship_status=inputs.relationship_status,
                homeowner_status=inputs.homeowner_status,
                assessable_super_balance=household_start_super_assessable,
                retirement_financial_assets=financial_asset_balance + cash_reserve_balance,
                retirement_other_assessable_assets=float(inputs.retirement_other_assessable_assets),
                annual_other_assessable_income=total_taxable_income,
                as_of_date=inputs.as_of_date,
                years_from_today=year_offset,
                inflation_rate=float(inputs.inflation_rate),
            )
        else:
            age_pension_result = {
                "eligible": False,
                "eligible_person_count": 0,
                "annual_pension": 0.0,
                "fortnightly_pension": 0.0,
                "full_pension_annual": 0.0,
                "full_couple_pension_annual": (0.0 if inputs.relationship_status == "couple" else None),
                "is_full_pension": False,
                "is_full_couple_pension": False,
                "income_test_rate_fortnight": 0.0,
                "assets_test_rate_fortnight": 0.0,
                "deemed_income_annual": 0.0,
                "total_assessable_income_annual": _round_currency(total_taxable_income),
                "assessable_assets": _round_currency(
                    household_start_super_assessable
                    + financial_asset_balance
                    + cash_reserve_balance
                    + float(inputs.retirement_other_assessable_assets)
                ),
                "binding_test": "Age Pension not included",
            }

        annual_age_pension = float(age_pension_result["annual_pension"])
        spending_need = (
            _inflate(inputs.annual_retirement_spending, year_offset, inputs.inflation_rate)
            if year_offset >= first_retirement_year_offset
            else 0.0
        )
        spending_gap_before_reserve = _positive_currency_gap(
            spending_need - total_net_after_tax_income - annual_age_pension
        )
        cash_reserve_used = min(
            cash_reserve_balance,
            spending_gap_before_reserve,
        )
        draw_needed_after_income_and_reserve = _positive_currency_gap(
            spending_gap_before_reserve - cash_reserve_used,
        )
        financial_assets_used = 0.0
        if inputs.use_financial_assets_for_spending:
            financial_assets_used = min(financial_asset_balance, draw_needed_after_income_and_reserve)
        draw_needed_after_income_reserve_and_financial_assets = _positive_currency_gap(
            draw_needed_after_income_and_reserve - financial_assets_used,
        )

        minimum_draw_by_person: dict[str, float] = {}
        maximum_draw_by_person: dict[str, float] = {}
        for person in people:
            person_data = person_year_data[person.label]
            minimum_draw_by_person[person.label] = _minimum_required_super_draw(
                person,
                age=int(person_data["age"]),
                preservation_age=preservation_ages[person.label],
                is_retired=bool(person_data["retired"]),
                start_balance=float(person_data["start_balance"]),
                as_of_date=inputs.as_of_date,
            )
            maximum_draw_by_person[person.label] = _maximum_allowed_super_draw(
                person,
                age=int(person_data["age"]),
                preservation_age=preservation_ages[person.label],
                is_retired=bool(person_data["retired"]),
                start_balance=float(person_data["start_balance"]),
                after_return_balance=float(person_data["balance_after_return"]),
                tris_maximum_rate=tris_maximum_rate,
            )

        total_minimum_draw = sum(minimum_draw_by_person.values())
        draw_by_person = _allocate_additional_draw(
            target_total_draw=max(draw_needed_after_income_reserve_and_financial_assets, total_minimum_draw),
            minimum_draw_by_person=minimum_draw_by_person,
            maximum_draw_by_person=maximum_draw_by_person,
        )
        actual_super_draw = sum(draw_by_person.values())

        balances = {}
        for person in people:
            label = person.label
            person_year_data[label]["minimum_draw"] = minimum_draw_by_person[label]
            person_year_data[label]["maximum_draw"] = maximum_draw_by_person[label]
            person_year_data[label]["draw"] = draw_by_person[label]
            balances[label] = max(
                float(person_year_data[label]["balance_after_return"]) - float(draw_by_person[label]),
                0.0,
            )
            person_year_data[label]["end_balance"] = balances[label]

        financial_asset_balance = max(financial_asset_balance - financial_assets_used, 0.0)
        excess_pension_cash_to_reserve = _positive_currency_gap(
            actual_super_draw - draw_needed_after_income_reserve_and_financial_assets
        )
        cash_reserve_balance = max(cash_reserve_balance - cash_reserve_used, 0.0) + excess_pension_cash_to_reserve

        available_cash_for_spending = min(
            spending_need,
            total_net_after_tax_income
            + annual_age_pension
            + cash_reserve_used
            + financial_assets_used
            + actual_super_draw,
        )
        spending_shortfall = _positive_currency_gap(
            spending_need
            - total_net_after_tax_income
            - annual_age_pension
            - cash_reserve_used
            - financial_assets_used
            - actual_super_draw
        )
        household_end_super = sum(balances.values())

        retirement_row: dict[str, object] = {
            "Year": year_offset,
            "Calendar year": calendar_year,
            "Household phase": (
                "Transition year" if year_offset < full_retirement_year_offset else "All retired"
            ),
            "Household start super ($)": _round_currency(household_start_super),
            "Household assessable super for Age Pension ($)": _round_currency(
                household_start_super_assessable
            ),
            "Household exempt super for Age Pension ($)": _round_currency(household_start_super_exempt),
            "Inheritance received ($/yr)": _round_currency(inheritance_received_this_year),
            "Gross household work and other income ($/yr)": _round_currency(total_work_and_other_income),
            "Taxable household income estimate ($/yr)": _round_currency(total_taxable_income),
            "Estimated personal tax ($/yr)": _round_currency(total_personal_tax),
            "Net household income after tax ($/yr)": _round_currency(total_net_after_tax_income),
            "Age Pension ($/yr)": _round_currency(annual_age_pension),
            "Spending need ($/yr)": _round_currency(spending_need),
            "Cash reserve used ($/yr)": _round_currency(cash_reserve_used),
            "Financial assets outside super at start ($)": _round_currency(
                financial_asset_balance + financial_assets_used
            ),
            "Financial assets draw used ($/yr)": _round_currency(financial_assets_used),
            "Net household draw ($/yr)": _round_currency(draw_needed_after_income_reserve_and_financial_assets),
            "Minimum pension draw required ($/yr)": _round_currency(total_minimum_draw),
            "Actual super draw ($/yr)": _round_currency(actual_super_draw),
            "Excess pension cash to reserve ($/yr)": _round_currency(excess_pension_cash_to_reserve),
            "Available cash for spending ($/yr)": _round_currency(available_cash_for_spending),
            "Spending shortfall ($/yr)": _round_currency(spending_shortfall),
            "Cash reserve balance ($)": _round_currency(cash_reserve_balance),
            "Financial assets outside super at end ($)": _round_currency(financial_asset_balance),
            "Household end super ($)": _round_currency(household_end_super),
            "Real household end super ($, today's dollars)": _round_currency(
                household_end_super / ((1.0 + float(inputs.inflation_rate)) ** year_offset)
            ),
        }

        for person in people:
            person_data = person_year_data[person.label]
            retirement_row[f"{person.label} age"] = int(person_data["age"])
            retirement_row[f"{person.label} retired"] = bool(person_data["retired"])
            retirement_row[f"{person.label} super product"] = person_data["super_product"]
            retirement_row[f"{person.label} super assessment treatment"] = person_data[
                "super_assessment_treatment"
            ]
            retirement_row[f"{person.label} start super ($)"] = _round_currency(
                float(person_data["start_balance"])
            )
            retirement_row[f"{person.label} end super ($)"] = _round_currency(
                float(person_data["end_balance"])
            )
            retirement_row[f"{person.label} minimum pension draw ($/yr)"] = _round_currency(
                float(person_data["minimum_draw"])
            )
            retirement_row[f"{person.label} maximum draw allowed ($/yr)"] = _round_currency(
                float(person_data["maximum_draw"])
            )
            retirement_row[f"{person.label} draw ($/yr)"] = _round_currency(float(person_data["draw"]))
            retirement_row[f"{person.label} non-salary income ($/yr)"] = _round_currency(
                float(person_data["non_salary_income"])
            )
            retirement_row[f"{person.label} net income after tax ($/yr)"] = _round_currency(
                float(person_data["net_income_after_tax"])
            )

        retirement_rows.append(retirement_row)
        if first_inheritance_row is None and inheritance_received_this_year > 0.0:
            first_inheritance_row = retirement_row

        age_pension_row: dict[str, object] = {
            "Year": year_offset,
            "Calendar year": calendar_year,
            "Estimated Age Pension ($/yr)": _round_currency(annual_age_pension),
            "Full Age Pension benchmark ($/yr)": _round_currency(age_pension_result["full_pension_annual"]),
            "Full couple Age Pension benchmark ($/yr)": (
                _round_currency(age_pension_result["full_couple_pension_annual"])
                if age_pension_result["full_couple_pension_annual"] is not None
                else None
            ),
            "At full Age Pension": bool(age_pension_result["is_full_pension"]),
            "At full couple Age Pension": bool(age_pension_result["is_full_couple_pension"]),
            "Eligible people": int(age_pension_result["eligible_person_count"]),
            "Binding test": str(age_pension_result["binding_test"]),
            "Inheritance received ($/yr)": _round_currency(inheritance_received_this_year),
            "Deemed income ($/yr)": _round_currency(age_pension_result["deemed_income_annual"]),
            "Total assessable income ($/yr)": _round_currency(
                age_pension_result["total_assessable_income_annual"]
            ),
            "Assessable super ($)": _round_currency(household_start_super_assessable),
            "Exempt super ($)": _round_currency(household_start_super_exempt),
            "Cash reserve counted as financial asset ($)": _round_currency(cash_reserve_balance),
            "Base financial assets outside super ($)": _round_currency(
                financial_asset_balance + financial_assets_used
            ),
            "Financial assets draw used ($/yr)": _round_currency(financial_assets_used),
            "Financial assets outside super at end ($)": _round_currency(financial_asset_balance),
            "Other assessable assets ($)": _round_currency(inputs.retirement_other_assessable_assets),
            "Assessable assets ($)": _round_currency(age_pension_result["assessable_assets"]),
        }
        for person in people:
            person_data = person_year_data[person.label]
            age_pension_row[f"{person.label} age"] = int(person_data["age"])
            age_pension_row[f"{person.label} super product"] = person_data["super_product"]
            age_pension_row[f"{person.label} super assessment treatment"] = person_data[
                "super_assessment_treatment"
            ]
        age_pension_rows.append(age_pension_row)

        if first_cashflow_row is None:
            first_cashflow_row = retirement_row
        if first_shortfall_row is None and spending_shortfall > 0.0:
            first_shortfall_row = retirement_row
        if first_eligible_age_pension_row is None and annual_age_pension > 0.0:
            first_eligible_age_pension_row = age_pension_row

    final_household_super = sum(balances.values())
    final_total_resources = (
        final_household_super
        + financial_asset_balance
        + float(inputs.retirement_other_assessable_assets)
        + cash_reserve_balance
    )
    has_spending_shortfall = first_shortfall_row is not None
    funds_last_to_planning_age = not has_spending_shortfall
    is_on_track = funds_last_to_planning_age and final_total_resources + 1e-9 >= float(inputs.target_estate)

    primary_label = inputs.primary_person.label
    partner_label = inputs.partner_person.label if inputs.partner_person is not None else None

    first_shortfall_primary_age = (
        int(first_shortfall_row[f"{primary_label} age"]) if first_shortfall_row is not None else None
    )
    first_shortfall_partner_age = (
        int(first_shortfall_row[f"{partner_label} age"])
        if first_shortfall_row is not None and partner_label is not None
        else None
    )
    inheritance_trigger_calendar_year = (
        int(inputs.as_of_date.year + inheritance_trigger_offset)
        if inheritance_trigger_offset is not None and cash_inheritance_event is not None
        else None
    )
    inheritance_trigger_primary_age = (
        int(current_ages[primary_label] + inheritance_trigger_offset)
        if inheritance_trigger_offset is not None
        else None
    )
    inheritance_trigger_partner_age = (
        int(current_ages[partner_label] + inheritance_trigger_offset)
        if inheritance_trigger_offset is not None and partner_label is not None
        else None
    )

    summary = {
        "is_on_track": bool(is_on_track),
        "funds_last_to_planning_age": bool(funds_last_to_planning_age),
        "has_spending_shortfall": bool(has_spending_shortfall),
        "depletion_primary_age": first_shortfall_primary_age,
        "depletion_partner_age": first_shortfall_partner_age,
        "first_shortfall_primary_age": first_shortfall_primary_age,
        "first_shortfall_partner_age": first_shortfall_partner_age,
        "current_age_primary": int(current_ages[primary_label]),
        "current_age_partner": int(current_ages[partner_label]) if partner_label is not None else None,
        "preservation_age_primary": int(preservation_ages[primary_label]),
        "preservation_age_partner": (
            int(preservation_ages[partner_label]) if partner_label is not None else None
        ),
        "first_cashflow_age_primary": int(current_ages[primary_label] + first_cashflow_year_offset),
        "first_cashflow_age_partner": (
            int(current_ages[partner_label] + first_cashflow_year_offset)
            if partner_label is not None
            else None
        ),
        "first_retirement_age_primary": int(current_ages[primary_label] + first_retirement_year_offset),
        "first_retirement_age_partner": (
            int(current_ages[partner_label] + first_retirement_year_offset)
            if partner_label is not None
            else None
        ),
        "full_retirement_age_primary": int(current_ages[primary_label] + full_retirement_year_offset),
        "full_retirement_age_partner": (
            int(current_ages[partner_label] + full_retirement_year_offset)
            if partner_label is not None
            else None
        ),
        "age_pension_age": int(age_pension_age),
        "cashflow_start_year_offset": int(first_cashflow_year_offset),
        "first_retirement_year_offset": int(first_retirement_year_offset),
        "full_retirement_year_offset": int(full_retirement_year_offset),
        "retirement_balance": _round_currency(
            first_retirement_balance_snapshot if first_retirement_balance_snapshot is not None else 0.0
        ),
        "first_cashflow_balance": _round_currency(
            first_cashflow_balance_snapshot if first_cashflow_balance_snapshot is not None else 0.0
        ),
        "full_retirement_balance": _round_currency(
            full_retirement_balance_snapshot if full_retirement_balance_snapshot is not None else 0.0
        ),
        "retirement_balance_by_person": {
            label: _round_currency(value) for label, value in retirement_balance_by_person.items()
        },
        "first_eligible_age_pension": _round_currency(
            first_eligible_age_pension_row["Estimated Age Pension ($/yr)"]
            if first_eligible_age_pension_row is not None
            else 0.0
        ),
        "first_eligible_age_pension_binding_test": (
            str(first_eligible_age_pension_row["Binding test"])
            if first_eligible_age_pension_row is not None
            else None
        ),
        "first_eligible_age_pension_primary_age": (
            int(first_eligible_age_pension_row[f"{primary_label} age"])
            if first_eligible_age_pension_row is not None
            else None
        ),
        "first_eligible_age_pension_partner_age": (
            int(first_eligible_age_pension_row[f"{partner_label} age"])
            if first_eligible_age_pension_row is not None and partner_label is not None
            else None
        ),
        "first_year_spending_need": _round_currency(
            float(first_cashflow_row["Spending need ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_net_draw": _round_currency(
            float(first_cashflow_row["Net household draw ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_actual_super_draw": _round_currency(
            float(first_cashflow_row["Actual super draw ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_minimum_pension_draw": _round_currency(
            float(first_cashflow_row["Minimum pension draw required ($/yr)"])
            if first_cashflow_row is not None
            else 0.0
        ),
        "first_year_excess_pension_cash": _round_currency(
            float(first_cashflow_row["Excess pension cash to reserve ($/yr)"])
            if first_cashflow_row is not None
            else 0.0
        ),
        "first_year_cash_reserve": _round_currency(
            float(first_cashflow_row["Cash reserve balance ($)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_cash_reserve_used": _round_currency(
            float(first_cashflow_row["Cash reserve used ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_net_income_after_tax": _round_currency(
            float(first_cashflow_row["Net household income after tax ($/yr)"])
            if first_cashflow_row is not None
            else 0.0
        ),
        "first_year_financial_assets_draw": _round_currency(
            float(first_cashflow_row["Financial assets draw used ($/yr)"])
            if first_cashflow_row is not None
            else 0.0
        ),
        "first_year_estimated_personal_tax": _round_currency(
            float(first_cashflow_row["Estimated personal tax ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_shortfall": _round_currency(
            float(first_cashflow_row["Spending shortfall ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "first_year_age_pension": _round_currency(
            float(first_cashflow_row["Age Pension ($/yr)"]) if first_cashflow_row is not None else 0.0
        ),
        "final_household_super": _round_currency(final_household_super),
        "final_financial_assets_balance": _round_currency(financial_asset_balance),
        "final_cash_reserve_balance": _round_currency(cash_reserve_balance),
        "final_total_resources": _round_currency(final_total_resources),
        "estate_gap": _round_currency(max(float(inputs.target_estate) - final_total_resources, 0.0)),
        "inheritance_enabled": bool(
            cash_inheritance_event is not None and float(cash_inheritance_event.amount) > 0.0
        ),
        "inheritance_within_horizon": bool(inheritance_within_horizon),
        "inheritance_amount": _round_currency(
            float(cash_inheritance_event.amount) if cash_inheritance_event is not None else 0.0
        ),
        "inheritance_trigger_mode": (
            cash_inheritance_event.trigger_mode if cash_inheritance_event is not None else None
        ),
        "inheritance_trigger_person_label": (
            cash_inheritance_event.trigger_person_label if cash_inheritance_event is not None else None
        ),
        "inheritance_trigger_calendar_year": inheritance_trigger_calendar_year,
        "inheritance_trigger_primary_age": inheritance_trigger_primary_age,
        "inheritance_trigger_partner_age": inheritance_trigger_partner_age,
        "inheritance_received": bool(first_inheritance_row is not None),
        "inheritance_received_primary_age": (
            int(first_inheritance_row[f"{primary_label} age"]) if first_inheritance_row is not None else None
        ),
        "inheritance_received_partner_age": (
            int(first_inheritance_row[f"{partner_label} age"])
            if first_inheritance_row is not None and partner_label is not None
            else None
        ),
    }

    return RetirementProjection(
        accumulation_df=pd.DataFrame(accumulation_rows),
        retirement_df=pd.DataFrame(retirement_rows),
        age_pension_df=pd.DataFrame(age_pension_rows),
        summary=summary,
        warnings=warnings,
        rule_snapshot={
            "super": super_snapshot,
            "age_pension": age_pension_snapshot,
            "tax": tax_snapshot,
        },
    )


def _meets_target(projection: RetirementProjection) -> bool:
    return bool(projection.summary["is_on_track"])


def estimate_required_annual_salary_sacrifice(inputs: RetirementInputs) -> float | None:
    max_case_inputs = _build_household_salary_sacrifice_inputs(inputs, _MAX_SEARCH_SALARY_SACRIFICE)
    if not _meets_target(project_retirement(max_case_inputs)):
        return None

    lower = 0.0
    upper = _MAX_SEARCH_SALARY_SACRIFICE
    best = upper
    for _ in range(_SEARCH_STEPS):
        midpoint = (lower + upper) / 2.0
        midpoint_inputs = _build_household_salary_sacrifice_inputs(inputs, midpoint)
        if _meets_target(project_retirement(midpoint_inputs)):
            best = midpoint
            upper = midpoint
        else:
            lower = midpoint
    rounded_best = _round_currency_up(best)
    while rounded_best <= _MAX_SEARCH_SALARY_SACRIFICE and not _meets_target(
        project_retirement(_build_household_salary_sacrifice_inputs(inputs, rounded_best))
    ):
        rounded_best = _round_currency_up(rounded_best + 0.01)
    return _round_currency(rounded_best)


def estimate_required_annual_contribution(inputs: RetirementInputs) -> float | None:
    return estimate_required_annual_salary_sacrifice(inputs)


def estimate_max_sustainable_spending(inputs: RetirementInputs) -> float:
    if _meets_target(project_retirement(replace(inputs, annual_retirement_spending=_MAX_SEARCH_SPENDING))):
        return _round_currency(_MAX_SEARCH_SPENDING)

    lower = 0.0
    upper = _MAX_SEARCH_SPENDING
    best = 0.0
    for _ in range(_SEARCH_STEPS):
        midpoint = (lower + upper) / 2.0
        midpoint_inputs = replace(inputs, annual_retirement_spending=midpoint)
        if _meets_target(project_retirement(midpoint_inputs)):
            best = midpoint
            lower = midpoint
        else:
            upper = midpoint
    rounded_best = _round_currency_down(best)
    while rounded_best > 0.0 and not _meets_target(
        project_retirement(replace(inputs, annual_retirement_spending=rounded_best))
    ):
        rounded_best = _round_currency_down(rounded_best - 0.01)
    return _round_currency(rounded_best)
