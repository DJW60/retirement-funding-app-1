from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from retirement_app.benchmark_pack import list_benchmark_cases, resolve_benchmark_case_inputs
from retirement_app.calculations import (
    estimate_max_sustainable_spending,
    estimate_required_annual_salary_sacrifice,
    project_retirement,
)
from retirement_app.formatting import format_age, format_currency, format_percentage
from retirement_app.models import CashInheritanceEvent, PersonProfile, RetirementInputs, UnusedConcessionalCapAmount
from retirement_app.rules_loader import get_rule_metadata
from retirement_app.superannuation import calculate_age, get_previous_carry_forward_financial_year_labels


st.set_page_config(
    page_title="Australian Retirement Funding Planner",
    layout="wide",
)


def _build_balance_chart_df(projection_df: pd.DataFrame) -> pd.DataFrame:
    chart_df = projection_df.copy()
    if chart_df.empty:
        return chart_df
    columns = [
        column
        for column in chart_df.columns
        if column in {
            "Household end super ($)",
            "Real household end super ($, today's dollars)",
            "Person 1 end super ($)",
            "Person 2 end super ($)",
        }
    ]
    return chart_df.set_index("Calendar year")[columns]


def _build_retirement_cashflow_chart_df(retirement_df: pd.DataFrame) -> pd.DataFrame:
    chart_df = retirement_df.copy()
    if chart_df.empty:
        return chart_df
    return chart_df.set_index("Calendar year")[
        [
            "Spending need ($/yr)",
            "Net household income after tax ($/yr)",
            "Age Pension ($/yr)",
            "Actual super draw ($/yr)",
            "Minimum pension draw required ($/yr)",
        ]
    ]


def _build_age_pension_chart(
    age_pension_df: pd.DataFrame,
    *,
    relationship_status: str,
) -> tuple[alt.Chart | None, pd.Series | None]:
    chart_df = age_pension_df.copy()
    if chart_df.empty:
        return None, None

    benchmark_column = (
        "Full couple Age Pension benchmark ($/yr)"
        if relationship_status == "couple"
        else "Full Age Pension benchmark ($/yr)"
    )
    status_column = "At full couple Age Pension" if relationship_status == "couple" else "At full Age Pension"
    benchmark_label = "Full couple pension benchmark" if relationship_status == "couple" else "Full pension benchmark"

    chart_source = pd.DataFrame(
        {
            "Calendar year": chart_df["Calendar year"],
            "Estimated Age Pension": chart_df["Estimated Age Pension ($/yr)"],
            benchmark_label: chart_df[benchmark_column],
        }
    )
    long_df = chart_source.melt(
        id_vars=["Calendar year"],
        var_name="Series",
        value_name="Amount ($/yr)",
    ).dropna(subset=["Amount ($/yr)"])

    series_order = ["Estimated Age Pension", benchmark_label]
    base = alt.Chart(long_df).encode(
        x=alt.X("Calendar year:Q", title="Calendar year", axis=alt.Axis(format="d")),
        y=alt.Y("Amount ($/yr):Q", title="Annual pension ($)", scale=alt.Scale(zero=True)),
        color=alt.Color(
            "Series:N",
            title=None,
            sort=series_order,
            scale=alt.Scale(
                domain=series_order,
                range=["#1d4ed8", "#b7791f"],
            ),
        ),
        strokeDash=alt.StrokeDash(
            "Series:N",
            title=None,
            sort=series_order,
            scale=alt.Scale(
                domain=series_order,
                range=[[1, 0], [8, 6]],
            ),
        ),
        tooltip=[
            alt.Tooltip("Calendar year:Q", format=".0f"),
            alt.Tooltip("Series:N"),
            alt.Tooltip("Amount ($/yr):Q", format=",.0f"),
        ],
    )
    chart: alt.Chart = base.mark_line(strokeWidth=3)

    full_rows = chart_df.loc[chart_df[status_column] == True]
    first_full_row = full_rows.iloc[0] if not full_rows.empty else None
    if first_full_row is not None:
        marker_df = pd.DataFrame(
            {
                "Calendar year": [first_full_row["Calendar year"]],
                "Amount ($/yr)": [first_full_row["Estimated Age Pension ($/yr)"]],
                "Label": [
                    "Full couple pension reached" if relationship_status == "couple" else "Full pension reached"
                ],
            }
        )
        rule = alt.Chart(marker_df).mark_rule(color="#2d6a4f", strokeDash=[6, 4]).encode(
            x=alt.X("Calendar year:Q")
        )
        point = alt.Chart(marker_df).mark_point(color="#2d6a4f", filled=True, size=110).encode(
            x=alt.X("Calendar year:Q"),
            y=alt.Y("Amount ($/yr):Q"),
        )
        text = alt.Chart(marker_df).mark_text(color="#2d6a4f", align="left", dx=6, dy=-10).encode(
            x=alt.X("Calendar year:Q"),
            y=alt.Y("Amount ($/yr):Q"),
            text="Label:N",
        )
        chart = alt.layer(chart, rule, point, text)

    return chart.properties(height=320), first_full_row


def _display_rule_card(title: str, entries: list[str]) -> None:
    st.markdown(f"#### {title}")
    for entry in entries:
        st.markdown(f"- {entry}")


def _format_age_pair(primary_age: int | None, partner_age: int | None) -> str:
    if primary_age is None:
        return "-"
    if partner_age is None:
        return format_age(primary_age)
    return f"Age {int(primary_age)} / Age {int(partner_age)}"


def _format_super_product_type(value: str) -> str:
    if value == "account_based_pension":
        return "Account-based pension"
    if value == "transition_to_retirement_income_stream":
        return "Transition to retirement income stream"
    return "Accumulation / no super pension being paid"


def _format_inheritance_timing(value: str) -> str:
    if value == "person_2_age":
        return "Person 2 age"
    if value == "calendar_year":
        return "Calendar year"
    return "Person 1 age"


def _set_state_default(key: str, value: object) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _state_value(key: str, default: object) -> object:
    _set_state_default(key, default)
    return st.session_state[key]


def _state_option_index(key: str, options: list[str], default: str) -> int:
    value = str(_state_value(key, default))
    return options.index(value) if value in options else options.index(default)


def _default_input_state(as_of_date: date) -> dict[str, object]:
    defaults: dict[str, object] = {
        "benchmark_case_selector": "",
        "projection_timing_mode": "anniversary",
        "retirement_drawdown_timing_mode": "monthly",
        "relationship_status": "couple",
        "homeowner_status": "homeowner",
        "birth_date_1": default_birth_date_person_1,
        "retirement_age_1": 67,
        "planning_age_1": 92,
        "current_super_balance_1": 120000.0,
        "annual_salary_1": 82000.0,
        "annual_non_salary_income_1": 0.0,
        "super_product_type_1": "accumulation",
        "annual_salary_growth_1": 3.0,
        "use_fixed_employer_contribution_1": True,
        "employer_super_rate_1": 12.0,
        "employer_super_annual_amount_override_1": 20962.0,
        "annual_salary_sacrifice_1": 27270.0,
        "annual_after_tax_contribution_1": 0.0,
        "person_1_carry_forward_enabled": True,
        "person_1_prior_total_super_balance": 120000.0,
        "birth_date_2": default_birth_date_person_2,
        "retirement_age_2": 64,
        "planning_age_2": 90,
        "current_super_balance_2": 440000.0,
        "annual_salary_2": 0.0,
        "annual_non_salary_income_2": 0.0,
        "super_product_type_2": "accumulation",
        "annual_salary_growth_2": 3.0,
        "employer_super_rate_2": 12.0,
        "annual_salary_sacrifice_2": 0.0,
        "annual_after_tax_contribution_2": 3000.0,
        "person_2_carry_forward_enabled": False,
        "person_2_prior_total_super_balance": 440000.0,
        "annual_retirement_spending": 85000.0,
        "annual_other_income": 0.0,
        "annual_other_income_growth": 2.5,
        "retirement_financial_assets": 100000.0,
        "use_financial_assets_for_spending": True,
        "retirement_other_assessable_assets": 70000.0,
        "include_cash_inheritance": False,
        "inheritance_amount": 150000.0,
        "inheritance_timing": "person_1_age",
        "inheritance_calendar_year": min(as_of_date.year + 10, as_of_date.year + 80),
        "person_1_inheritance_trigger_age": 67,
        "person_2_inheritance_trigger_age": 67,
        "target_estate": 0.0,
        "include_age_pension": True,
        "annual_return_pre": 7.0,
        "annual_return_post": 5.0,
        "inflation_rate": 2.5,
    }
    for year_label in get_previous_carry_forward_financial_year_labels(as_of_date):
        defaults[f"person_1_unused_cap_{year_label}"] = float(default_person_1_unused_concessional_cap_amounts.get(year_label, 0.0))
        defaults[f"person_2_unused_cap_{year_label}"] = 0.0
    return defaults


def _initialise_input_state(as_of_date: date) -> None:
    for key, value in _default_input_state(as_of_date).items():
        _set_state_default(key, value)


def _apply_benchmark_person_state(
    *,
    person_number: int,
    person_inputs: dict[str, object],
    as_of_date: date,
) -> None:
    suffix = str(person_number)
    st.session_state[f"birth_date_{suffix}"] = date.fromisoformat(str(person_inputs["birth_date"]))
    st.session_state[f"retirement_age_{suffix}"] = int(person_inputs["retirement_age"])
    st.session_state[f"planning_age_{suffix}"] = int(person_inputs["planning_age"])
    st.session_state[f"current_super_balance_{suffix}"] = float(person_inputs["current_super_balance"])
    st.session_state[f"annual_salary_{suffix}"] = float(person_inputs["annual_salary"])
    st.session_state[f"annual_non_salary_income_{suffix}"] = float(person_inputs.get("annual_non_salary_income", 0.0))
    st.session_state[f"super_product_type_{suffix}"] = str(person_inputs["super_product_type"])
    st.session_state[f"annual_salary_growth_{suffix}"] = float(person_inputs["annual_salary_growth"]) * 100.0
    st.session_state[f"annual_salary_sacrifice_{suffix}"] = float(person_inputs.get("annual_salary_sacrifice", 0.0))
    st.session_state[f"annual_after_tax_contribution_{suffix}"] = float(
        person_inputs.get("annual_after_tax_contribution", 0.0)
    )

    if person_number == 1:
        fixed_override = person_inputs.get("employer_super_annual_amount_override")
        st.session_state["use_fixed_employer_contribution_1"] = fixed_override is not None
        st.session_state["employer_super_annual_amount_override_1"] = float(fixed_override or 0.0)
        st.session_state["employer_super_rate_1"] = float(person_inputs.get("employer_super_rate", 0.0)) * 100.0
    else:
        st.session_state["employer_super_rate_2"] = float(person_inputs.get("employer_super_rate", 0.0)) * 100.0

    carry_forward_balance = person_inputs.get("carry_forward_previous_30_june_total_super_balance")
    unused_amounts = {
        str(entry["financial_year"]): float(entry["amount"])
        for entry in person_inputs.get("unused_concessional_cap_amounts", [])
    }
    st.session_state[f"person_{suffix}_carry_forward_enabled"] = (
        carry_forward_balance is not None or any(amount > 0.0 for amount in unused_amounts.values())
    )
    st.session_state[f"person_{suffix}_prior_total_super_balance"] = float(
        carry_forward_balance if carry_forward_balance is not None else person_inputs["current_super_balance"]
    )
    for year_label in get_previous_carry_forward_financial_year_labels(as_of_date):
        st.session_state[f"person_{suffix}_unused_cap_{year_label}"] = float(unused_amounts.get(year_label, 0.0))


def _apply_benchmark_case_to_state(case_id: str, *, as_of_date: date) -> None:
    inputs = resolve_benchmark_case_inputs(case_id)
    st.session_state["projection_timing_mode"] = str(inputs.get("projection_timing_mode", "anniversary"))
    st.session_state["retirement_drawdown_timing_mode"] = str(
        inputs.get("retirement_drawdown_timing_mode", "monthly")
    )
    st.session_state["relationship_status"] = str(inputs["relationship_status"])
    st.session_state["homeowner_status"] = str(inputs["homeowner_status"])

    _apply_benchmark_person_state(person_number=1, person_inputs=dict(inputs["primary_person"]), as_of_date=as_of_date)
    partner_person = inputs.get("partner_person")
    if isinstance(partner_person, dict):
        _apply_benchmark_person_state(person_number=2, person_inputs=partner_person, as_of_date=as_of_date)

    st.session_state["annual_retirement_spending"] = float(inputs.get("annual_retirement_spending", 0.0))
    st.session_state["annual_other_income"] = float(inputs.get("annual_other_income", 0.0))
    st.session_state["annual_other_income_growth"] = float(inputs.get("annual_other_income_growth", 0.0)) * 100.0
    st.session_state["retirement_financial_assets"] = float(inputs.get("retirement_financial_assets", 0.0))
    st.session_state["use_financial_assets_for_spending"] = bool(inputs.get("use_financial_assets_for_spending", False))
    st.session_state["retirement_other_assessable_assets"] = float(
        inputs.get("retirement_other_assessable_assets", 0.0)
    )
    st.session_state["include_cash_inheritance"] = False
    st.session_state["target_estate"] = float(inputs.get("target_estate", 0.0))
    st.session_state["include_age_pension"] = bool(inputs.get("include_age_pension", False))
    st.session_state["annual_return_pre"] = float(inputs.get("annual_return_pre", 0.0)) * 100.0
    st.session_state["annual_return_post"] = float(inputs.get("annual_return_post", 0.0)) * 100.0
    st.session_state["inflation_rate"] = float(inputs.get("inflation_rate", 0.0)) * 100.0


def _build_carry_forward_input_section(
    *,
    person_label: str,
    default_total_super_balance: float,
    as_of_date: date,
    key_prefix: str,
    default_enabled: bool = False,
    default_unused_cap_amounts: dict[str, float] | None = None,
) -> tuple[float | None, tuple[UnusedConcessionalCapAmount, ...]]:
    available_year_labels = get_previous_carry_forward_financial_year_labels(as_of_date)
    default_unused_cap_amounts = default_unused_cap_amounts or {}

    with st.expander(
        f"{person_label} carry-forward concessional caps",
        expanded=bool(_state_value(f"{key_prefix}_carry_forward_enabled", default_enabled)),
    ):
        enabled = st.checkbox(
            f"Use carry-forward concessional cap inputs for {person_label}",
            value=bool(_state_value(f"{key_prefix}_carry_forward_enabled", default_enabled)),
            key=f"{key_prefix}_carry_forward_enabled",
            help=(
                "Enter the previous 30 June total super balance and any unused concessional cap amounts "
                "you want the planner to apply. Oldest amounts are used first."
            ),
        )
        if not enabled:
            st.caption(
                "Leave this off to use the standard concessional cap only. Turn it on only when you want the "
                "planner to use your prior 30 June total super balance and unused cap history."
            )
            return None, ()

        prior_total_super_balance = st.number_input(
            f"{person_label} total super balance at previous 30 June ($)",
            min_value=0.0,
            value=float(_state_value(f"{key_prefix}_prior_total_super_balance", default_total_super_balance)),
            step=1000.0,
            format="%.0f",
            key=f"{key_prefix}_prior_total_super_balance",
            help=(
                "Use the actual total super balance at the previous 30 June across all relevant interests. "
                "Under current rules, carry-forward amounts are only applied if this was below $500,000."
            ),
        )

        unused_cap_amounts: list[UnusedConcessionalCapAmount] = []
        if available_year_labels:
            st.caption(
                "Enter unused concessional cap amounts for the available carry-forward years: "
                + ", ".join(available_year_labels)
                + "."
            )
        for year_label in available_year_labels:
            amount = st.number_input(
                f"{person_label} unused concessional cap from {year_label} ($)",
                min_value=0.0,
                value=float(_state_value(f"{key_prefix}_unused_cap_{year_label}", default_unused_cap_amounts.get(year_label, 0.0))),
                step=1000.0,
                format="%.0f",
                key=f"{key_prefix}_unused_cap_{year_label}",
            )
            if float(amount) > 0.0:
                unused_cap_amounts.append(
                    UnusedConcessionalCapAmount(financial_year=year_label, amount=float(amount))
                )

        total_unused_cap = sum(float(entry.amount) for entry in unused_cap_amounts)
        st.caption(
            f"Entered carry-forward amount: {format_currency(total_unused_cap)}. "
            "The planner applies these amounts oldest first if the prior 30 June balance test is met."
        )
        return float(prior_total_super_balance), tuple(unused_cap_amounts)


today = date.today()
default_birth_date_person_1 = date(1960, 11, 30)
default_birth_date_person_2 = date(1964, 1, 30)
default_person_1_unused_concessional_cap_amounts = {
    "2020-21": 18232.0,
    "2021-22": 19875.0,
    "2022-23": 13647.0,
    "2023-24": 300.0,
    "2024-25": 0.0,
}
_initialise_input_state(today)
benchmark_cases = list_benchmark_cases()

st.title("Australian Retirement Funding Planner")
st.caption(
    "Moneysmart-style retirement planning starter with Australian super settings, "
    "Centrelink Age Pension means testing, separate-person couple mode, staggered-retirement cashflow, and a simple personal tax layer."
)

with st.sidebar:
    st.markdown("### Benchmark presets")
    benchmark_case_options = [""] + [case["id"] for case in benchmark_cases]
    selected_benchmark_case = st.selectbox(
        "Load benchmark case",
        options=benchmark_case_options,
        index=_state_option_index("benchmark_case_selector", benchmark_case_options, ""),
        key="benchmark_case_selector",
        format_func=lambda value: (
            "None"
            if value == ""
            else next(
                (
                    f"{case['id']} - {case['purpose']}"
                    for case in benchmark_cases
                    if case["id"] == value
                ),
                value,
            )
        ),
        help="Loads an exact benchmark case from tests/benchmark_pack.json into the sidebar inputs.",
    )
    if st.button("Apply benchmark case", use_container_width=True):
        if selected_benchmark_case:
            _apply_benchmark_case_to_state(selected_benchmark_case, as_of_date=today)
            st.rerun()
    st.caption(
        "This is useful for validating the planner against the benchmark cases without re-entering every field manually."
    )
    projection_timing_options = ["anniversary", "moneysmart_july_1"]
    projection_timing_mode = st.selectbox(
        "Projection timing",
        options=projection_timing_options,
        index=_state_option_index("projection_timing_mode", projection_timing_options, "anniversary"),
        key="projection_timing_mode",
        format_func=lambda value: (
            "Planner anniversary timing"
            if value == "anniversary"
            else "Moneysmart 1 July benchmark timing"
        ),
        help=(
            "Standard mode runs yearly steps from today's date. "
            "Moneysmart benchmark mode rolls the opening balance to the next 1 July and then uses 1 July timing "
            "with mid-year contribution growth for pre-retirement benchmark comparisons."
        ),
    )
    if projection_timing_mode == "moneysmart_july_1":
        st.caption(
            "Benchmark timing mode is intended for external comparison work. "
            "Leave it off for your normal planning scenarios unless you specifically want the Moneysmart-style timing convention."
        )
    retirement_drawdown_timing_options = ["monthly", "midyear_annual", "year_end_annual"]
    retirement_drawdown_timing_mode = st.selectbox(
        "Retirement draw timing",
        options=retirement_drawdown_timing_options,
        index=_state_option_index("retirement_drawdown_timing_mode", retirement_drawdown_timing_options, "monthly"),
        key="retirement_drawdown_timing_mode",
        format_func=lambda value: (
            "Monthly through year"
            if value == "monthly"
            else ("Mid-year annual benchmark" if value == "midyear_annual" else "End-of-year annual simplified")
        ),
        help=(
            "Controls how super pension drawdowns are spread through each retirement year. "
            "Monthly is the more realistic personal-planning default. Mid-year annual is useful when benchmarking "
            "against calculators that assume income and outgoings land mid-year."
        ),
    )

    st.markdown("### Household profile")
    relationship_options = ["single", "couple"]
    relationship_status = st.radio(
        "Household mode",
        options=relationship_options,
        index=_state_option_index("relationship_status", relationship_options, "couple"),
        key="relationship_status",
        format_func=lambda value: "Single" if value == "single" else "Couple",
        horizontal=True,
    )
    homeowner_options = ["homeowner", "non_homeowner"]
    homeowner_status = st.radio(
        "Homeowner status for Age Pension test",
        options=homeowner_options,
        index=_state_option_index("homeowner_status", homeowner_options, "homeowner"),
        key="homeowner_status",
        format_func=lambda value: "Homeowner" if value == "homeowner" else "Non-homeowner",
        horizontal=True,
    )

    st.markdown("### Person 1")
    birth_date_1 = st.date_input(
        "Person 1 date of birth",
        value=_state_value("birth_date_1", default_birth_date_person_1),
        min_value=date(1940, 1, 1),
        max_value=today,
        key="birth_date_1",
        help="Person 1 is the reference person for planning summaries.",
    )
    current_age_1 = calculate_age(birth_date_1, today)
    st.caption(f"Person 1 current age: {current_age_1}")
    retirement_age_1 = st.number_input(
        "Person 1 retirement age",
        min_value=max(int(current_age_1), 18),
        max_value=95,
        value=max(int(_state_value("retirement_age_1", 67)), max(int(current_age_1), 18)),
        step=1,
        key="retirement_age_1",
    )
    planning_age_1 = st.number_input(
        "Person 1 planning age",
        min_value=max(int(retirement_age_1) + 1, 19),
        max_value=110,
        value=max(int(_state_value("planning_age_1", 92)), max(int(retirement_age_1) + 1, 19)),
        step=1,
        key="planning_age_1",
    )
    current_super_balance_1 = st.number_input(
        "Person 1 current super balance ($)",
        min_value=0.0,
        value=float(_state_value("current_super_balance_1", 120000.0)),
        step=10000.0,
        format="%.0f",
        key="current_super_balance_1",
    )
    annual_salary_1 = st.number_input(
        "Person 1 current salary ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_salary_1", 82000.0)),
        step=5000.0,
        format="%.0f",
        key="annual_salary_1",
    )
    annual_non_salary_income_1 = st.number_input(
        "Person 1 gross taxable non-salary income ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_non_salary_income_1", 0.0)),
        step=2500.0,
        format="%.0f",
        key="annual_non_salary_income_1",
        help="Enter gross taxable income only. Do not enter after-tax cashflow here. If the income comes from financial assets outside super entered below, usually leave this at 0 unless you want to model a separate taxable income stream.",
    )
    product_options = ["accumulation", "account_based_pension", "transition_to_retirement_income_stream"]
    super_product_type_1 = st.selectbox(
        "Person 1 super product / access mode",
        options=product_options,
        index=_state_option_index("super_product_type_1", product_options, "accumulation"),
        key="super_product_type_1",
        format_func=_format_super_product_type,
        help="This controls both pension drawdown rules and, before Age Pension age, whether Centrelink treats the super as exempt accumulation or an assessable income stream.",
    )
    annual_salary_growth_1 = st.slider(
        "Person 1 salary growth (%/yr)",
        min_value=0.0,
        max_value=8.0,
        value=float(_state_value("annual_salary_growth_1", 3.0)),
        step=0.25,
        key="annual_salary_growth_1",
    )
    use_fixed_employer_contribution_1 = st.checkbox(
        "Use fixed employer contribution for Person 1",
        value=bool(_state_value("use_fixed_employer_contribution_1", True)),
        key="use_fixed_employer_contribution_1",
        help=(
            "If selected, this annual dollar amount fully overrides the percentage-based employer super contribution "
            "for Person 1."
        ),
    )
    employer_super_rate_1 = st.slider(
        "Person 1 employer super rate (% of salary)",
        min_value=0.0,
        max_value=20.0,
        value=float(_state_value("employer_super_rate_1", 12.0)),
        step=0.25,
        disabled=use_fixed_employer_contribution_1,
        key="employer_super_rate_1",
    )
    employer_super_annual_amount_override_1 = None
    if use_fixed_employer_contribution_1:
        employer_super_annual_amount_override_1 = st.number_input(
            "Person 1 fixed employer contribution ($/yr)",
            min_value=0.0,
            value=float(_state_value("employer_super_annual_amount_override_1", 20962.0)),
            step=1000.0,
            format="%.0f",
            key="employer_super_annual_amount_override_1",
            help="This is the full employer contribution used for Person 1. No extra SG-style percentage amount is added on top.",
        )
        st.caption(
            "The fixed annual amount is now authoritative for Person 1. The percentage-based employer super rate "
            "is disabled and contributes $0 while this option is selected."
        )
    annual_salary_sacrifice_1 = st.number_input(
        "Person 1 before-tax contribution ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_salary_sacrifice_1", 27270.0)),
        step=1000.0,
        format="%.0f",
        key="annual_salary_sacrifice_1",
    )
    annual_after_tax_contribution_1 = st.number_input(
        "Person 1 after-tax contribution ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_after_tax_contribution_1", 0.0)),
        step=1000.0,
        format="%.0f",
        key="annual_after_tax_contribution_1",
    )
    (
        carry_forward_previous_30_june_total_super_balance_1,
        unused_concessional_cap_amounts_1,
    ) = _build_carry_forward_input_section(
        person_label="Person 1",
        default_total_super_balance=float(current_super_balance_1),
        as_of_date=today,
        key_prefix="person_1",
        default_enabled=True,
        default_unused_cap_amounts=default_person_1_unused_concessional_cap_amounts,
    )

    partner_person: PersonProfile | None = None
    carry_forward_previous_30_june_total_super_balance_2: float | None = None
    unused_concessional_cap_amounts_2: tuple[UnusedConcessionalCapAmount, ...] = ()
    if relationship_status == "couple":
        st.markdown("### Person 2")
        birth_date_2 = st.date_input(
            "Person 2 date of birth",
            value=_state_value("birth_date_2", default_birth_date_person_2),
            min_value=date(1940, 1, 1),
            max_value=today,
            key="birth_date_2",
        )
        current_age_2 = calculate_age(birth_date_2, today)
        st.caption(f"Person 2 current age: {current_age_2}")
        retirement_age_2 = st.number_input(
            "Person 2 retirement age",
            min_value=max(int(current_age_2), 18),
            max_value=95,
            value=max(int(_state_value("retirement_age_2", 64)), max(int(current_age_2), 18)),
            step=1,
            key="retirement_age_2",
        )
        planning_age_2 = st.number_input(
            "Person 2 planning age",
            min_value=max(int(retirement_age_2) + 1, 19),
            max_value=110,
            value=max(int(_state_value("planning_age_2", 90)), max(int(retirement_age_2) + 1, 19)),
            step=1,
            key="planning_age_2",
        )
        current_super_balance_2 = st.number_input(
            "Person 2 current super balance ($)",
            min_value=0.0,
            value=float(_state_value("current_super_balance_2", 440000.0)),
            step=10000.0,
            format="%.0f",
            key="current_super_balance_2",
        )
        annual_salary_2 = st.number_input(
            "Person 2 current salary ($/yr)",
            min_value=0.0,
            value=float(_state_value("annual_salary_2", 0.0)),
            step=5000.0,
            format="%.0f",
            key="annual_salary_2",
        )
        annual_non_salary_income_2 = st.number_input(
            "Person 2 gross taxable non-salary income ($/yr)",
            min_value=0.0,
            value=float(_state_value("annual_non_salary_income_2", 0.0)),
            step=2500.0,
            format="%.0f",
            key="annual_non_salary_income_2",
            help="Enter gross taxable income only. Do not enter after-tax cashflow here. If the income comes from financial assets outside super entered below, usually leave this at 0 unless you want to model a separate taxable income stream.",
        )
        super_product_type_2 = st.selectbox(
            "Person 2 super product / access mode",
            options=product_options,
            index=_state_option_index("super_product_type_2", product_options, "accumulation"),
            key="super_product_type_2",
            format_func=_format_super_product_type,
            help="This controls both pension drawdown rules and, before Age Pension age, whether Centrelink treats the super as exempt accumulation or an assessable income stream.",
        )
        annual_salary_growth_2 = st.slider(
            "Person 2 salary growth (%/yr)",
            min_value=0.0,
            max_value=8.0,
            value=float(_state_value("annual_salary_growth_2", 3.0)),
            step=0.25,
            key="annual_salary_growth_2",
        )
        employer_super_rate_2 = st.slider(
            "Person 2 employer super rate (% of salary)",
            min_value=0.0,
            max_value=20.0,
            value=float(_state_value("employer_super_rate_2", 12.0)),
            step=0.25,
            key="employer_super_rate_2",
        )
        annual_salary_sacrifice_2 = st.number_input(
            "Person 2 before-tax contribution ($/yr)",
            min_value=0.0,
            value=float(_state_value("annual_salary_sacrifice_2", 0.0)),
            step=1000.0,
            format="%.0f",
            key="annual_salary_sacrifice_2",
        )
        annual_after_tax_contribution_2 = st.number_input(
            "Person 2 after-tax contribution ($/yr)",
            min_value=0.0,
            value=float(_state_value("annual_after_tax_contribution_2", 3000.0)),
            step=1000.0,
            format="%.0f",
            key="annual_after_tax_contribution_2",
        )
        (
            carry_forward_previous_30_june_total_super_balance_2,
            unused_concessional_cap_amounts_2,
        ) = _build_carry_forward_input_section(
            person_label="Person 2",
            default_total_super_balance=float(current_super_balance_2),
            as_of_date=today,
            key_prefix="person_2",
        )

        partner_person = PersonProfile(
            label="Person 2",
            birth_date=birth_date_2,
            retirement_age=int(retirement_age_2),
            planning_age=int(planning_age_2),
            current_super_balance=float(current_super_balance_2),
            annual_salary=float(annual_salary_2),
            annual_non_salary_income=float(annual_non_salary_income_2),
            super_product_type=super_product_type_2,
            annual_salary_growth=float(annual_salary_growth_2) / 100.0,
            employer_super_rate=float(employer_super_rate_2) / 100.0,
            annual_salary_sacrifice=float(annual_salary_sacrifice_2),
            annual_after_tax_contribution=float(annual_after_tax_contribution_2),
            carry_forward_previous_30_june_total_super_balance=carry_forward_previous_30_june_total_super_balance_2,
            unused_concessional_cap_amounts=unused_concessional_cap_amounts_2,
        )

    st.markdown("### Household retirement funding")
    annual_retirement_spending = st.number_input(
        "Household retirement spending target ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_retirement_spending", 85000.0)),
        step=2500.0,
        format="%.0f",
        key="annual_retirement_spending",
        help="This version applies the spending target from the first selected retirement year, not only after both people have fully retired.",
    )
    annual_other_income = st.number_input(
        "Shared household gross taxable income before Age Pension ($/yr)",
        min_value=0.0,
        value=float(_state_value("annual_other_income", 0.0)),
        step=2500.0,
        format="%.0f",
        key="annual_other_income",
        help="Enter gross taxable household income only. Do not enter after-tax cashflow here. If the income comes from financial assets outside super entered below, usually leave this at 0 unless you want to model a separate taxable income stream.",
    )
    annual_other_income_growth = st.slider(
        "Other income growth (%/yr)",
        min_value=0.0,
        max_value=8.0,
        value=float(_state_value("annual_other_income_growth", 2.5)),
        step=0.25,
        key="annual_other_income_growth",
        help="Applied to both person-level non-salary income and shared household other income.",
    )
    retirement_financial_assets = st.number_input(
        "Financial assets outside super at retirement ($)",
        min_value=0.0,
        value=float(_state_value("retirement_financial_assets", 100000.0)),
        step=5000.0,
        format="%.0f",
        key="retirement_financial_assets",
        help="Examples: cash, term deposits, shares, ETFs, managed funds, or offset cash held outside super. The Age Pension estimate uses deeming on this balance.",
    )
    use_financial_assets_for_spending = st.checkbox(
        "Use financial assets outside super to help fund retirement spending",
        value=bool(_state_value("use_financial_assets_for_spending", True)),
        key="use_financial_assets_for_spending",
        help="If selected, this balance is drawn down before extra super draw. The Age Pension estimate still applies deeming to the remaining balance. This does not separately model actual dividend or interest yield from the same assets.",
    )
    retirement_other_assessable_assets = st.number_input(
        "Other assessable assets at retirement ($)",
        min_value=0.0,
        value=float(_state_value("retirement_other_assessable_assets", 70000.0)),
        step=5000.0,
        format="%.0f",
        key="retirement_other_assessable_assets",
    )
    st.markdown("### Inheritance / windfall")
    include_cash_inheritance = st.checkbox(
        "Include one-off cash inheritance or windfall",
        value=bool(_state_value("include_cash_inheritance", False)),
        key="include_cash_inheritance",
    )
    cash_inheritance_event: CashInheritanceEvent | None = None
    if include_cash_inheritance:
        inheritance_amount = st.number_input(
            "Cash inheritance amount ($)",
            min_value=0.0,
            value=float(_state_value("inheritance_amount", 150000.0)),
            step=5000.0,
            format="%.0f",
            key="inheritance_amount",
        )
        inheritance_timing_options = ["person_1_age", "calendar_year"]
        if relationship_status == "couple":
            inheritance_timing_options.insert(1, "person_2_age")
        inheritance_timing = st.selectbox(
            "Inheritance timing basis",
            options=inheritance_timing_options,
            index=_state_option_index("inheritance_timing", inheritance_timing_options, "person_1_age"),
            key="inheritance_timing",
            format_func=_format_inheritance_timing,
            help="This first version models one household cash inheritance only.",
        )
        if inheritance_timing == "calendar_year":
            inheritance_calendar_year = st.number_input(
                "Expected inheritance calendar year",
                min_value=today.year,
                max_value=today.year + 80,
                value=int(_state_value("inheritance_calendar_year", min(today.year + 10, today.year + 80))),
                step=1,
                key="inheritance_calendar_year",
            )
            cash_inheritance_event = CashInheritanceEvent(
                amount=float(inheritance_amount),
                trigger_mode="calendar_year",
                trigger_calendar_year=int(inheritance_calendar_year),
            )
        else:
            inheritance_person_label = "Person 1" if inheritance_timing == "person_1_age" else "Person 2"
            inheritance_current_age = current_age_1 if inheritance_person_label == "Person 1" else current_age_2
            inheritance_trigger_age = st.number_input(
                f"{inheritance_person_label} age at inheritance",
                min_value=max(int(inheritance_current_age), 18),
                max_value=110,
                value=max(
                    int(
                        _state_value(
                            "person_1_inheritance_trigger_age"
                            if inheritance_person_label == "Person 1"
                            else "person_2_inheritance_trigger_age",
                            67,
                        )
                    ),
                    max(int(inheritance_current_age), 18),
                ),
                step=1,
                key=(
                    "person_1_inheritance_trigger_age"
                    if inheritance_person_label == "Person 1"
                    else "person_2_inheritance_trigger_age"
                ),
            )
            cash_inheritance_event = CashInheritanceEvent(
                amount=float(inheritance_amount),
                trigger_mode="person_age",
                trigger_person_label=inheritance_person_label,
                trigger_age=int(inheritance_trigger_age),
            )
        st.caption(
            "This stage treats the inheritance as household cash added to the reserve bucket. "
            "It can be spent later and is counted in Age Pension financial assets and deeming, but it does not yet model inherited property, shares, or super death benefits."
        )
    target_estate = st.number_input(
        "Desired estate at plan end ($)",
        min_value=0.0,
        value=float(_state_value("target_estate", 0.0)),
        step=10000.0,
        format="%.0f",
        key="target_estate",
    )
    include_age_pension = st.checkbox(
        "Include Centrelink Age Pension estimate",
        value=bool(_state_value("include_age_pension", True)),
        key="include_age_pension",
    )

    st.markdown("### Return assumptions")
    annual_return_pre = st.slider(
        "Pre-retirement net return (%/yr)",
        min_value=0.0,
        max_value=12.0,
        value=float(_state_value("annual_return_pre", 7.0)),
        step=0.25,
        key="annual_return_pre",
    )
    annual_return_post = st.slider(
        "Retirement-phase net return (%/yr)",
        min_value=0.0,
        max_value=12.0,
        value=float(_state_value("annual_return_post", 5.0)),
        step=0.25,
        key="annual_return_post",
    )
    inflation_rate = st.slider(
        "Inflation (%/yr)",
        min_value=0.0,
        max_value=8.0,
        value=float(_state_value("inflation_rate", 2.5)),
        step=0.25,
        key="inflation_rate",
    )

primary_person = PersonProfile(
    label="Person 1",
    birth_date=birth_date_1,
    retirement_age=int(retirement_age_1),
    planning_age=int(planning_age_1),
    current_super_balance=float(current_super_balance_1),
    annual_salary=float(annual_salary_1),
    annual_non_salary_income=float(annual_non_salary_income_1),
    super_product_type=super_product_type_1,
    annual_salary_growth=float(annual_salary_growth_1) / 100.0,
    employer_super_rate=(0.0 if use_fixed_employer_contribution_1 else float(employer_super_rate_1) / 100.0),
    annual_salary_sacrifice=float(annual_salary_sacrifice_1),
    annual_after_tax_contribution=float(annual_after_tax_contribution_1),
    employer_super_annual_amount_override=employer_super_annual_amount_override_1,
    carry_forward_previous_30_june_total_super_balance=carry_forward_previous_30_june_total_super_balance_1,
    unused_concessional_cap_amounts=unused_concessional_cap_amounts_1,
)

inputs = RetirementInputs(
    relationship_status=relationship_status,
    homeowner_status=homeowner_status,
    primary_person=primary_person,
    partner_person=partner_person,
    annual_retirement_spending=float(annual_retirement_spending),
    annual_other_income=float(annual_other_income),
    annual_other_income_growth=float(annual_other_income_growth) / 100.0,
    retirement_financial_assets=float(retirement_financial_assets),
    retirement_other_assessable_assets=float(retirement_other_assessable_assets),
    include_age_pension=bool(include_age_pension),
    annual_return_pre=float(annual_return_pre) / 100.0,
    annual_return_post=float(annual_return_post) / 100.0,
    inflation_rate=float(inflation_rate) / 100.0,
    projection_timing_mode=str(projection_timing_mode),
    retirement_drawdown_timing_mode=str(retirement_drawdown_timing_mode),
    use_financial_assets_for_spending=bool(use_financial_assets_for_spending),
    cash_inheritance_event=cash_inheritance_event,
    target_estate=float(target_estate),
    as_of_date=today,
)

projection = project_retirement(inputs)
required_salary_sacrifice = estimate_required_annual_salary_sacrifice(inputs)
max_sustainable_spending = estimate_max_sustainable_spending(inputs)
summary = projection.summary

if summary["is_on_track"]:
    st.success(
        "This plan is currently on track to cover the selected planning horizon and estate target under the chosen assumptions."
    )
elif summary["has_spending_shortfall"]:
    st.warning(
        "This plan hits a spending shortfall during the retirement transition under the current assumptions. "
        f"The first shortfall appears around {_format_age_pair(summary['first_shortfall_primary_age'], summary['first_shortfall_partner_age'])}."
    )
elif summary["funds_last_to_planning_age"]:
    st.info(
        "This plan appears to last through the selected planning horizon, but it falls short of the desired estate target."
    )
else:
    st.warning(
        "This plan runs out before the selected planning horizon under the current assumptions. "
        f"Projected depletion is around {_format_age_pair(summary['depletion_primary_age'], summary['depletion_partner_age'])}."
    )

for warning in projection.warnings:
    st.warning(warning)

if summary["inheritance_enabled"]:
    if summary["inheritance_within_horizon"]:
        st.info(
            f"One-off cash inheritance of {format_currency(summary['inheritance_amount'])} is assumed around "
            f"{_format_age_pair(summary['inheritance_trigger_primary_age'], summary['inheritance_trigger_partner_age'])} "
            f"(calendar year {summary['inheritance_trigger_calendar_year']}). "
            "This first version treats it as household cash added to the reserve bucket."
        )
    else:
        st.info(
            f"One-off cash inheritance of {format_currency(summary['inheritance_amount'])} is configured for "
            f"calendar year {summary['inheritance_trigger_calendar_year']}, which is outside the current planning horizon."
        )

current_total_salary_sacrifice = float(primary_person.annual_salary_sacrifice) + float(
    partner_person.annual_salary_sacrifice if partner_person is not None else 0.0
)
needed_delta = None
if required_salary_sacrifice is not None:
    needed_delta = required_salary_sacrifice - current_total_salary_sacrifice

m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Current ages",
    _format_age_pair(summary["current_age_primary"], summary["current_age_partner"]),
)
m2.metric(
    "Preservation ages",
    _format_age_pair(summary["preservation_age_primary"], summary["preservation_age_partner"]),
)
m3.metric(
    "First cashflow ages",
    _format_age_pair(summary["first_cashflow_age_primary"], summary["first_cashflow_age_partner"]),
)
m4.metric(
    "All retired ages",
    _format_age_pair(summary["full_retirement_age_primary"], summary["full_retirement_age_partner"]),
)

m5, m6, m7, m8 = st.columns(4)
m5.metric("Age Pension age", format_age(summary["age_pension_age"]))
m6.metric("Household super at first cashflow", format_currency(summary["first_cashflow_balance"]))
m7.metric("Household super when all retired", format_currency(summary["full_retirement_balance"]))
m8.metric("First eligible Age Pension", format_currency(summary["first_eligible_age_pension"]))

m9, m10, m11, m12 = st.columns(4)
m9.metric("Max sustainable spending", format_currency(max_sustainable_spending))
m10.metric(
    "Needed household before-tax contribution",
    (format_currency(required_salary_sacrifice) if required_salary_sacrifice is not None else "Above search range"),
    (
        ("On track" if abs(float(needed_delta or 0.0)) < 1.0 else format_currency(needed_delta))
        if required_salary_sacrifice is not None
        else None
    ),
)
m11.metric("First-year minimum pension draw", format_currency(summary["first_year_minimum_pension_draw"]))
m12.metric("First-year cash reserve", format_currency(summary["first_year_cash_reserve"]))

st.caption(
    "This version supports separate ages, salaries, super balances, and retirement ages for two people. "
    "Cashflow can start before selected retirement if a TRIS is chosen and preservation age is reached, and any ongoing work income is converted to an after-tax estimate using resident tax brackets, LITO, a simplified SAPTO layer, and Medicare levy low-income reductions. "
    "Person-level and shared non-salary income inputs are treated as gross taxable income, and each person can now be modelled as accumulation, account-based pension, or TRIS with age-based minimum pension rules and a simple cash reserve for excess mandatory drawdowns. "
    "Financial assets outside super can also be used to help fund retirement spending, while the Age Pension estimate continues to apply deeming to the remaining balance. "
    "A one-off cash inheritance or windfall can also be added to that reserve. "
    "Carry-forward concessional caps can now be entered using the prior 30 June total super balance and unused cap history, but the cap check is still approximate because the projection runs in annual steps rather than exact payroll or financial-year timing. "
    "It does not yet model SAPTO spouse-transfer rules, Medicare levy exemptions, HELP, MLS, first-year pension pro-rating, detailed pension-payment tax, bring-forward rules, inherited property or shares, super death benefits, or a separate taxable yield model for those outside-super financial assets."
)

full_balance_df = pd.concat(
    [projection.accumulation_df, projection.retirement_df],
    ignore_index=True,
)
full_balance_df = full_balance_df.drop_duplicates(subset=["Year"], keep="last")

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["Plan summary", "Balance path", "Retirement cashflow", "Age Pension means test", "Rules and tables"]
)

with tab1:
    st.markdown("#### Plain-English summary")
    st.markdown(
        f"Household cashflow from retirement income starts at **{_format_age_pair(summary['first_cashflow_age_primary'], summary['first_cashflow_age_partner'])}**, "
        f"with projected combined super of **{format_currency(summary['first_cashflow_balance'])}** at that point."
    )
    st.markdown(
        f"Both people are retired by **{_format_age_pair(summary['full_retirement_age_primary'], summary['full_retirement_age_partner'])}**, "
        f"when projected combined super is **{format_currency(summary['full_retirement_balance'])}**."
    )
    if summary["first_cashflow_age_primary"] != summary["first_retirement_age_primary"] or (
        summary["first_cashflow_age_partner"] != summary["first_retirement_age_partner"]
    ):
        st.markdown(
            "Cashflow starts earlier than the selected retirement age for at least one person because a transition to retirement income stream is available before full retirement."
        )
    if partner_person is None:
        st.markdown(
            f"Person 1 is projected to have **{format_currency(summary['retirement_balance_by_person']['Person 1'])}** at retirement."
        )
    else:
        st.markdown(
            f"Person 1 is projected to have **{format_currency(summary['retirement_balance_by_person']['Person 1'])}** and "
            f"Person 2 **{format_currency(summary['retirement_balance_by_person']['Person 2'])}** at their own retirement points."
        )

    age_pension_line = (
        f"Estimated first eligible Age Pension is {format_currency(summary['first_eligible_age_pension'])} per year"
        if inputs.include_age_pension
        else "Age Pension is not included in this scenario"
    )
    binding_test = summary.get("first_eligible_age_pension_binding_test")
    if binding_test:
        eligible_ages = _format_age_pair(
            summary["first_eligible_age_pension_primary_age"],
            summary["first_eligible_age_pension_partner_age"],
        )
        age_pension_line = (
            f"{age_pension_line}, first appearing around {eligible_ages}, with the {binding_test.lower()} setting the result."
        )
    else:
        age_pension_line = f"{age_pension_line}."
    st.markdown(age_pension_line)
    if summary["inheritance_enabled"]:
        if summary["inheritance_within_horizon"]:
            st.markdown(
                f"A one-off cash inheritance of **{format_currency(summary['inheritance_amount'])}** is assumed around "
                f"**{_format_age_pair(summary['inheritance_trigger_primary_age'], summary['inheritance_trigger_partner_age'])}** "
                f"(calendar year **{summary['inheritance_trigger_calendar_year']}**)."
            )
        else:
            st.markdown(
                f"A one-off cash inheritance of **{format_currency(summary['inheritance_amount'])}** is configured, "
                "but it falls outside the current planning horizon and is not included in the projected cashflow."
            )
    st.markdown(
        f"The model estimates a first cashflow-year spending gap of **{format_currency(summary['first_year_net_draw'])}**, "
        f"first-year net non-pension income after estimated tax of **{format_currency(summary['first_year_net_income_after_tax'])}**, "
        f"first-year draw from financial assets outside super of **{format_currency(summary['first_year_financial_assets_draw'])}**, "
        f"first-year estimated personal tax of **{format_currency(summary['first_year_estimated_personal_tax'])}**, "
        f"a minimum pension draw of **{format_currency(summary['first_year_minimum_pension_draw'])}**, "
        f"an actual super draw of **{format_currency(summary['first_year_actual_super_draw'])}**, "
        f"and a closing cash reserve of **{format_currency(summary['first_year_cash_reserve'])}**."
    )
    st.markdown(
        f"The first cashflow-year Age Pension is **{format_currency(summary['first_year_age_pension'])}**, "
        f"the first-year transition shortfall is **{format_currency(summary['first_year_shortfall'])}**, "
        f"and the maximum sustainable household spending level is **{format_currency(max_sustainable_spending)}** under the chosen assumptions."
    )

    st.markdown("#### Rule snapshot used today")
    super_snapshot = projection.rule_snapshot["super"]
    pension_snapshot = projection.rule_snapshot["age_pension"]
    tax_snapshot = projection.rule_snapshot["tax"]
    c1, c2, c3 = st.columns(3)
    with c1:
        _display_rule_card(
            "Super settings",
            [
                f"Financial year: {super_snapshot['financial_year']}",
                f"SG rate: {format_percentage(super_snapshot['super_guarantee_rate'])}",
                f"Concessional cap: {format_currency(super_snapshot['concessional_cap'])}",
                f"Carry-forward TSB limit: {format_currency(super_snapshot['carry_forward_balance_limit'])}",
                f"Non-concessional cap: {format_currency(super_snapshot['non_concessional_cap'])}",
                f"Contributions tax: {format_percentage(super_snapshot['contributions_tax_rate'])}",
                f"Transfer balance cap: {format_currency(super_snapshot['general_transfer_balance_cap'])}",
                f"Account-based pension minimum under 65: {format_percentage(super_snapshot['minimum_pension_rate_under_65'])}",
                f"TRIS maximum: {format_percentage(super_snapshot['tris_maximum_rate'])}",
            ],
        )
    with c2:
        _display_rule_card(
            "Age Pension settings",
            [
                f"Single maximum rate: {format_currency(pension_snapshot['single_max_rate_fortnight'])} per fortnight",
                f"Couple combined maximum rate: {format_currency(pension_snapshot['couple_combined_max_rate_fortnight'])} per fortnight",
                f"Single income free area: {format_currency(pension_snapshot['single_income_free_area_fortnight'])} per fortnight",
                f"Single homeowner full-pension assets threshold: {format_currency(pension_snapshot['single_homeowner_assets_full_pension'])}",
                f"Single deeming threshold: {format_currency(pension_snapshot['single_deeming_threshold'])}",
            ],
        )
    with c3:
        _display_rule_card(
            "Tax settings",
            [
                f"Financial year: {tax_snapshot['financial_year']}",
                f"Tax-free threshold: {format_currency(tax_snapshot['tax_free_threshold'])}",
                f"Third bracket starts: {format_currency(tax_snapshot['third_bracket_threshold'])}",
                f"Top bracket starts: {format_currency(tax_snapshot['top_bracket_threshold'])}",
                f"LITO max offset: {format_currency(tax_snapshot['lito_max_offset'])}",
                f"SAPTO max offset (single): {format_currency(tax_snapshot['sapto_single_max_offset'])}",
                f"SAPTO max offset (couple each): {format_currency(tax_snapshot['sapto_couple_each_max_offset'])}",
                f"Medicare levy rate: {format_percentage(tax_snapshot['medicare_levy_rate'])}",
                f"Medicare low-income threshold (single): {format_currency(tax_snapshot['medicare_single_lower_threshold'])}",
                f"Medicare low-income threshold (family): {format_currency(tax_snapshot['medicare_family_lower_threshold'])}",
            ],
        )

with tab2:
    st.markdown("#### Super balance path")
    st.line_chart(_build_balance_chart_df(full_balance_df), use_container_width=True)
    st.caption(
        "The chart uses calendar year so different ages can still be viewed together. Person-level lines are shown when available."
    )

with tab3:
    st.markdown("#### Retirement cashflow")
    st.line_chart(_build_retirement_cashflow_chart_df(projection.retirement_df), use_container_width=True)
    st.caption(
        "The chart starts at the first cashflow year. Actual super draw can be higher than the spending gap when minimum pension rules apply, with excess cash parked in the reserve balance."
    )

with tab4:
    st.markdown("#### Age Pension estimate")
    age_pension_chart, first_full_pension_row = _build_age_pension_chart(
        projection.age_pension_df,
        relationship_status=inputs.relationship_status,
    )
    if age_pension_chart is not None:
        st.altair_chart(age_pension_chart, use_container_width=True)
    st.dataframe(projection.age_pension_df, use_container_width=True, hide_index=True)
    if first_full_pension_row is not None:
        st.caption(
            f"First {'full couple Age Pension' if inputs.relationship_status == 'couple' else 'full Age Pension'} "
            f"appears around {_format_age_pair(first_full_pension_row['Person 1 age'], first_full_pension_row.get('Person 2 age'))} "
            f"in calendar year {int(first_full_pension_row['Calendar year'])}."
        )
    else:
        st.caption(
            f"This projection does not reach {'full couple Age Pension' if inputs.relationship_status == 'couple' else 'full Age Pension'} "
            "under the current assumptions."
        )
    st.caption(
        "Under Age Pension age, accumulation super is shown as exempt, while account-based pension and TRIS settings are shown as assessed. "
        "This is still a standard-rule estimate and does not yet cover Work Bonus, Rent Assistance, transitional rates, or more complex income-stream cases."
    )

with tab5:
    st.markdown("#### Projection tables")
    st.markdown("##### Before first cashflow year")
    st.dataframe(projection.accumulation_df, use_container_width=True, hide_index=True)
    st.markdown("##### Transition and retirement years")
    st.dataframe(projection.retirement_df, use_container_width=True, hide_index=True)

    st.markdown("#### Official source mapping")
    st.markdown(
        f"- [{get_rule_metadata('super_rules.json')['name']}]({get_rule_metadata('super_rules.json')['source_url']})"
    )
    st.markdown(
        f"- [{get_rule_metadata('age_pension_rates.json')['name']}]({get_rule_metadata('age_pension_rates.json')['source_url']})"
    )
    st.markdown(
        f"- [{get_rule_metadata('age_pension_income_test.json')['name']}]({get_rule_metadata('age_pension_income_test.json')['source_url']})"
    )
    st.markdown(
        f"- [{get_rule_metadata('age_pension_assets_test.json')['name']}]({get_rule_metadata('age_pension_assets_test.json')['source_url']})"
    )
    st.markdown(
        f"- [{get_rule_metadata('deeming_rates.json')['name']}]({get_rule_metadata('deeming_rates.json')['source_url']})"
    )
    st.markdown(
        f"- [{get_rule_metadata('personal_income_tax_rules.json')['name']}]({get_rule_metadata('personal_income_tax_rules.json')['source_url']})"
    )
    st.markdown(
        "- [ATO beneficiary of a deceased estate](https://www.ato.gov.au/individuals-and-families/deceased-estates/if-you-are-a-beneficiary-of-a-deceased-estate)"
    )
    st.markdown(
        "- [Services Australia financial investments and deeming](https://www.servicesaustralia.gov.au/financial-investments)"
    )

    with st.expander("Suggested next build steps", expanded=False):
        st.markdown(
            "1. Add SAPTO, Medicare levy reductions, HELP, and Medicare levy surcharge to improve after-tax cashflow realism.\n"
            "2. Add compare-mode scenarios so different inheritance timings and amounts can be tested side by side.\n"
            "3. Split Age Pension treatment further for under-age income streams and more special-case Centrelink rules.\n"
            "4. Add bring-forward non-concessional rules, downsizer contributions, and more exact contribution timing.\n"
            "5. Add scenario save/load and downloadable reports."
        )
