# Benchmark Pack

This project uses a small external benchmark pack to validate the parts of the planner that should line up with official public calculators.

As of March 24, 2026, the benchmark pack uses:

- Moneysmart Superannuation Calculator for pre-retirement accumulation and contribution-change cases
- Moneysmart Account-based Pension Calculator for simple retirement drawdown cases
- Moneysmart Retirement Planner for simple couple retirement cases
- Services Australia Age Pension rules as the primary benchmark for the first-year means test case

## Purpose

The benchmark pack is designed to catch regressions in:

- accumulation balance growth
- contribution handling
- simple account-based pension drawdown
- simple couple-mode retirement income
- first-year Age Pension means-test outcomes

It is not designed to validate:

- TRIS behavior
- staggered-retirement transition years
- inheritance or windfall modelling
- non-super financial-asset drawdown strategy
- fixed employer contribution overrides
- carry-forward concessional cap history beyond the entered test values
- complex tax edge cases

## Source Limits That Shape The Pack

These source-tool assumptions determine what is safe to benchmark.

- Moneysmart Superannuation Calculator:
  - works for accumulation accounts only
  - shows results in today's dollars
  - uses age as at June 30 of the current year
  - shows balances at the 1 July after the age shown
- Moneysmart Account-based Pension Calculator:
  - excludes Age Pension and other investments
  - shows results in today's dollars
  - shows balances and income for the financial year starting on 1 July after the age shown
- Moneysmart Retirement Planner:
  - assumes super is converted to an account-based pension at retirement
  - shows Age Pension for couples only once both people are retired
  - does not take income needs into account before both members of a couple are retired
  - shows results in today's dollars
  - shows balances and income at the 1 July after the age shown
  - shows retirement income before tax, although super and Age Pension income are usually tax free after age 60

## Global Benchmark Rules

Use these rules for every benchmark capture unless a case explicitly says otherwise.

1. Freeze the app rules snapshot to `2026-03-24`.
2. Capture source-tool outputs on the same day and record the source URL used.
3. Use birth dates on `1 January` where possible, so the app's current-age calculation stays aligned with Moneysmart's "age as of June 30 this year" convention.
4. Turn off features that Moneysmart does not model:
   - inheritance / windfall
   - TRIS
   - outside-super financial-asset drawdown
   - fixed employer contribution override
   - carry-forward concessional caps
5. Use simple benchmark overrides in the source tools and in the app unless the case says otherwise:
   - CPI inflation `0%`
   - wage inflation / salary growth `0%`
   - admin fees `0`
   - contribution fees `0`
   - adviser fees `0`
   - insurance fees `0`
   - custom investment return `6.0%` for accumulation benchmarks
   - custom pension return `6.0%` for pension benchmarks
6. Compare only the fields listed in the case definition.
7. If a case fails tolerance, first check assumption alignment before changing the app logic.

## Pass And Fail Rules

- A benchmark passes only if every listed comparison field is within tolerance.
- For Moneysmart Superannuation Calculator and Account-based Pension Calculator cases, treat differences above tolerance as likely maths or timing issues in the app.
- For Moneysmart Retirement Planner couple cases, treat differences above tolerance as "investigate" rather than immediate proof of a bug, because the source tool has stronger simplifications around couple timing and retirement-phase assumptions.
- For the Services Australia first-year Age Pension benchmark, treat differences above tolerance as a likely rules or calculation defect unless a source-assumption mismatch is documented.

## Case Summary

| Case ID | Source | Main Purpose | Primary App Fields |
| --- | --- | --- | --- |
| `single_accumulation_baseline` | Moneysmart Superannuation Calculator | Validate simple accumulation balance growth | `summary.first_cashflow_balance` |
| `single_accumulation_contribution_change` | Moneysmart Superannuation Calculator | Validate contribution-change impact | `summary.first_cashflow_balance` and delta vs baseline |
| `single_account_based_pension_drawdown` | Moneysmart Account-based Pension Calculator | Validate simple pension drawdown behavior | first-row draw, FY-aligned later balance, planning-horizon balance |
| `couple_same_retirement_date_simple` | Moneysmart Retirement Planner | Validate simple couple retirement income when both are already retired | first-row draw plus pension, first cashflow balance |
| `couple_age_pension_means_test` | Services Australia rules plus Moneysmart Retirement Planner | Validate first-year Age Pension means test and broad couple parity | first eligible Age Pension, binding test, first-row pension income |

## Detailed Case Definitions

### 1. `single_accumulation_baseline`

Use this to validate pre-retirement accumulation without retirement-income complexity.

- App scenario:
  - relationship status: `single`
  - homeowner status: `homeowner`
  - projection timing mode: `Moneysmart 1 July benchmark timing`
  - Person 1 birth date: `1975-01-01`
  - Person 1 retirement age: `67`
  - Person 1 planning age: `92`
  - Person 1 current super: `$250,000`
  - Person 1 salary: `$90,000`
  - Person 1 employer rate: `12%`
  - Person 1 before-tax contribution: `$0`
  - Person 1 after-tax contribution: `$0`
  - Person 1 product: `accumulation`
  - annual return pre-retirement: `6.0%`
  - inflation: `0%`
  - include Age Pension: `No`
- Compare:
  - Moneysmart estimated super balance at retirement
  - app `projection.summary["first_cashflow_balance"]`
- Tolerance:
  - `Â±$250`

### 2. `single_accumulation_contribution_change`

Use this to validate how added concessional and non-concessional contributions change the retirement balance.

- Baseline case: `single_accumulation_baseline`
- App overrides:
  - Person 1 before-tax contribution: `$10,000`
  - Person 1 after-tax contribution: `$5,000`
- Compare:
  - absolute retirement balance
  - change in retirement balance versus `single_accumulation_baseline`
- Tolerance:
  - absolute balance: `Â±$250`
  - delta versus baseline: `Â±$150`

### 3. `single_account_based_pension_drawdown`

Use this to validate clean retirement-phase drawdown with no Age Pension or outside assets.

- App scenario:
  - relationship status: `single`
  - homeowner status: `homeowner`
  - projection timing mode: `Planner anniversary timing`
  - retirement draw timing: `Mid-year annual benchmark`
  - Person 1 birth date: `1959-01-01`
  - Person 1 retirement age: `67`
  - Person 1 planning age: `95`
  - Person 1 current super: `$600,000`
  - Person 1 salary: `$0`
  - Person 1 product: `account_based_pension`
  - retirement spending target: `$35,000`
  - include Age Pension: `No`
  - retirement return: `6.0%`
  - inflation: `0%`
- Moneysmart alignment note:
  - use the `Alternative super pension` path, not the current-super-income path
  - Moneysmart asks for age as at `30 June` and anchors results from `1 July`, so the age labels are financial-year labels rather than birthday labels
  - for this benchmark, Moneysmart age `78` aligns with the app `Calendar year` `2036` row, and Moneysmart age `96` aligns with the app `Calendar year` `2054` planning-horizon row
- Compare:
  - first retirement-year income from super
  - app `Calendar year` `2036` end balance versus Moneysmart age `78`
  - app planning-horizon `Calendar year` `2054` end balance versus Moneysmart age `96`
  - whether the pension still has a positive balance at the planning horizon
- App fields:
  - `projection.retirement_df.iloc[0]["Actual super draw ($/yr)"]`
  - `projection.retirement_df where "Calendar year" == 2036 -> "Household end super ($)"`
  - `projection.retirement_df where "Calendar year" == 2054 -> "Household end super ($)"`
  - `projection.summary["funds_last_to_planning_age"]`
- Captured source values:
  - first-year draw: `$35,000`
  - Moneysmart age `78`: `$596,471`
  - Moneysmart age `96`: `$330,047`
  - lasts to planning horizon: `True`
- Tolerance:
  - first-year draw: `+/-$250`
  - 2036 aligned balance: `+/-$250`
  - planning-horizon aligned balance: `+/-$250`
  - lasts to planning horizon: exact match

### 4. `couple_same_retirement_date_simple`

Use this only for a simple couple case where both people retire together. This keeps the Moneysmart Retirement Planner within the area it models best.

- App scenario:
  - relationship status: `couple`
  - homeowner status: `homeowner`
  - projection timing mode: `Moneysmart 1 July benchmark timing`
  - retirement draw timing: `Monthly through year`
  - Person 1 birth date: `1959-01-01`
  - Person 1 retirement age: `67`
  - Person 1 planning age: `92`
  - Person 1 current super: `$400,000`
  - Person 2 birth date: `1959-01-01`
  - Person 2 retirement age: `67`
  - Person 2 planning age: `92`
  - Person 2 current super: `$300,000`
  - both salaries: `$0`
  - both products: `account_based_pension`
  - retirement spending target: `$50,000`
  - include Age Pension: `No`
  - retirement return: `6.0%`
  - inflation: `0%`
- Moneysmart alignment note:
  - the Retirement Planner asks for age now and retirement age, so use `66 now` and `67 retirement age` for both people
  - Moneysmart reports chart values on a `1 July` financial-year anchor, so the displayed age `67` balance is the right comparison point for the app's July-benchmark first-cashflow balance
  - the cleanest formal comparisons are Moneysmart `Average estimated income` and the displayed super balance at age `67`
  - the raw `$700,000` retirement balance is still a good reasonableness check, but it is an input/start value rather than the displayed benchmark output we are testing here
- Compare:
  - Moneysmart `Average estimated income`
  - Moneysmart displayed super balance at age `67`
- App fields:
  - `estimate_max_sustainable_spending(inputs)`
  - `projection.summary["first_cashflow_balance"]`
- Captured source values:
  - average estimated income: `$52,919`
  - displayed balance at age `67`: `$711,128`
- Tolerance:
  - average estimated income: `+/-$250`
  - displayed age-67 balance: `+/-$250`

### 5. `couple_age_pension_means_test`

Use this as a dual benchmark:

- Services Australia rules are the primary source for the first-year Age Pension amount and binding test.
- Moneysmart Retirement Planner is a secondary reasonableness check for simple couple retirement income after both are retired.

- App scenario:
  - relationship status: `couple`
  - homeowner status: `homeowner`
  - Person 1 birth date: `1958-01-01`
  - Person 2 birth date: `1958-01-01`
  - Person 1 retirement age: `68`
  - Person 2 retirement age: `68`
  - Person 1 planning age: `92`
  - Person 2 planning age: `92`
  - Person 1 current super: `$150,000`
  - Person 2 current super: `$100,000`
  - Person 1 salary: `$0`
  - Person 2 salary: `$0`
  - Person 1 product: `account_based_pension`
  - Person 2 product: `account_based_pension`
  - household retirement spending target: `$40,000`
  - shared household other income: `$0`
  - financial assets outside super: `$0`
  - other assessable assets: `$0`
  - include Age Pension: `Yes`
  - retirement return: `0%`
  - inflation: `0%`
- Moneysmart alignment note:
  - timing settings do not materially change this case in the app because returns are `0%` and both members are already age-eligible
  - for the Retirement Planner, use `67 now` and `68 retirement age` for both people
  - use Services Australia as the source of truth for the pension rate, then use Moneysmart for the combined-income reasonableness check
- Compare:
  - first eligible annual Age Pension
  - first-year binding test
  - whether the first year is at full couple Age Pension
  - first retirement-year combined income from super and Age Pension
- App fields:
  - `projection.summary["first_eligible_age_pension"]`
  - `projection.summary["first_eligible_age_pension_binding_test"]`
  - `projection.age_pension_df.iloc[0]["At full couple Age Pension"]`
  - `projection.retirement_df.iloc[0]["Actual super draw ($/yr)"] + projection.retirement_df.iloc[0]["Age Pension ($/yr)"]`
- Captured source values:
  - first eligible Age Pension: `$47,070.40`
  - binding test: `Tests produce similar result`
  - full couple pension flag: `True`
  - first-year combined income: `$59,750`
- Tolerance:
  - first eligible Age Pension: `+/-$50`
  - binding test: exact match
  - full couple pension flag: exact match
  - first-year combined income: `+/-$250`
