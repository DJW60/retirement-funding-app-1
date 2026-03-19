# Australian Retirement Funding Planner

Starter Streamlit app for modelling retirement funding with an Australia-first structure and a separate-person household model.

## What this version does

- Projects super balance growth for `Person 1` and `Person 2` separately.
- Supports different ages, super balances, salaries, contribution levels, retirement ages, and planning ages.
- Supports person-level non-salary income as well as shared household income.
- Lets each person choose `Accumulation`, `Account-based pension`, or `TRIS` treatment.
- Starts household cashflow from the earlier of first selected retirement or TRIS access, and carries ongoing work income through the transition years.
- Applies a simple personal tax estimate using resident tax brackets, LITO, and a flat Medicare levy.
- Applies age-based minimum pension drawdowns for account-based pensions and a simple 10% cap for TRIS.
- Parks excess mandatory pension withdrawals in a household cash reserve that then feeds later cashflow and Age Pension deeming.
- Supports one one-off cash inheritance or windfall event, timed by calendar year or by a selected person's age.
- Separates employer super, before-tax contributions, after-tax contributions, and concessional contributions tax.
- Estimates retirement drawdown for the household through the chosen planning horizon.
- Estimates a standard-rule Centrelink Age Pension using income test, assets test, and deeming rules.
- Uses a rules-driven structure so official thresholds live in JSON rather than being hardcoded through the app.

## What this version does not yet do

- Carry-forward concessional caps
- Bring-forward non-concessional rules
- Downsizer contributions
- First-year pension-payment pro-rating
- Detailed tax treatment of pension payments and tax-free/taxable components
- User-controlled drawdown of non-super assets
- Inherited property, shares, or super death benefits
- Compare-mode inheritance / windfall scenarios
- Rent Assistance, Work Bonus, transitional pension rates, or special Centrelink cases
- Monte Carlo / sequence-of-returns modelling

## Project structure

```text
retirement-funding-app/
  app.py
  requirements.txt
  runtime.txt
  .streamlit/
    config.toml
  retirement_app/
    __init__.py
    age_pension.py
    calculations.py
    formatting.py
    models.py
    personal_tax.py
    rules_loader.py
    superannuation.py
    rules/
      age_pension_assets_test.json
      age_pension_income_test.json
      age_pension_rates.json
      deeming_rates.json
      personal_income_tax_rules.json
      super_rules.json
```

## Design approach

- `models.py` defines household inputs and per-person profiles.
- `rules/*.json` stores official rule snapshots and effective dates.
- `rules_loader.py` selects the correct rule set for the chosen date.
- `superannuation.py` handles preservation age, SG, contribution tax, and cap warnings.
- `age_pension.py` handles deeming plus the standard income and assets tests.
- `calculations.py` runs separate-person super accumulation plus household retirement drawdown, including pension-product rules and a simple reserve bucket for excess mandatory withdrawals.
- `app.py` stays focused on user inputs and presentation.

## Current couple-mode simplifications

- Ongoing work income in the transition years is converted into a simple after-tax estimate using current resident tax rules.
- Current tax treatment does not yet model SAPTO, Medicare levy reductions, HELP, or Medicare levy surcharge.
- Person-level non-salary income uses the shared `other income growth` assumption.
- Work Bonus is not modelled yet.
- For mixed-age couples, under-Age-Pension-age accumulation super is treated as exempt from the Age Pension means test until that partner reaches Age Pension age.
- Under-Age-Pension-age account-based pension and TRIS settings are treated as assessable, but the app does not yet model more detailed income-stream variations.
- Excess mandatory pension withdrawals are stored in a simple household cash reserve rather than being forced into separate spending or reinvestment choices.
- One-off cash inheritances are also stored in that same reserve bucket rather than being allocated across different asset classes.
- The app does not yet actively draw down non-super financial assets before super.
- The inheritance feature does not yet model asset-specific tax outcomes, estate administration timing, or CGT on later disposal of inherited assets.

## Official source mapping

- Moneysmart retirement planner:
  - https://moneysmart.gov.au/retirement-income-sources/retirement-planner
- ATO super contribution caps:
  - https://www.ato.gov.au/tax-rates-and-codes/key-superannuation-rates-and-thresholds/contributions-caps
- Services Australia Age Pension rates:
  - https://www.servicesaustralia.gov.au/how-much-age-pension-you-can-get?context=22526
- Services Australia Age Pension income test:
  - https://www.servicesaustralia.gov.au/income-test-for-age-pension?context=22526
- Services Australia Age Pension assets test:
  - https://www.servicesaustralia.gov.au/assets-test-for-age-pension?context=22526
- Services Australia deeming:
  - https://www.servicesaustralia.gov.au/deeming
- ATO resident tax rates:
  - https://www.ato.gov.au/tax-rates-and-codes/tax-rates-australian-residents?%2Ftax-rates-australian-residents=
- ATO income stream pensions:
  - https://www.ato.gov.au/individuals-and-families/super-for-individuals-and-families/super/withdrawing-and-using-your-super/retirement-income/account-based-pensions
- ATO transition to retirement income streams:
  - https://www.ato.gov.au/individuals-and-families/super-for-individuals-and-families/super/withdrawing-and-using-your-super/transition-to-retirement-income-streams
- ATO beneficiaries of deceased estates:
  - https://www.ato.gov.au/individuals-and-families/deceased-estates/if-you-are-a-beneficiary-of-a-deceased-estate
- Services Australia financial investments:
  - https://www.servicesaustralia.gov.au/financial-investments

## Run locally

```bash
cd retirement-funding-app
streamlit run app.py
```

## Suggested next steps

1. Add SAPTO, Medicare levy reductions, HELP, and Medicare levy surcharge.
2. Add compare-mode inheritance and windfall scenarios.
3. Add richer Centrelink treatment for under-age income streams and more special-case pension rules.
4. Add carry-forward concessional caps, bring-forward rules, and downsizer contributions.
5. Add scenario save/load and downloadable reports.
