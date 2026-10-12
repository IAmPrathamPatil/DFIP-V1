## What changed

<!-- One or two sentences. What does this PR do? -->

## Why

<!-- The problem being solved, or the issue it closes. Link the issue. -->

## Behaviour

- [ ] No behaviour change — implementation detail, refactor, or documentation only
- [ ] Behaviour change — describe precisely what a user or operator will observe differently

<!-- If behaviour changed, state explicitly what a caller can now rely on that they
     could not before, and what they can no longer do. -->

## Data and migrations

- [ ] No schema change
- [ ] Migration included — a new file under `supabase/migrations/`

<!-- This project has additive migrations only. Do not add a down migration. -->

## How this was verified

<!-- List the commands you actually ran and their results. Do not claim a clean
     baseline: this repository has pre-existing lint and test failures documented
     in documentation/PROJECT_STATUS.md. -->

```

```

## Things a reviewer should check carefully

<!-- Name the risky part: authorization, tenant scoping, a query plan, workbook
     output, a concurrency assumption. -->

## Documentation

<!-- Did behaviour, configuration, or an operational procedure change? Which
     document needs updating to match? -->

- [ ] No documentation change needed
- [ ] Documentation updated (list the files)

---

Before submitting, confirm:

- [ ] `python -m ruff check packages tests` and `python -m ruff format --check packages tests` are clean for the files I touched
- [ ] `python -m pytest` run, with results stated above
- [ ] `pytest -m postgres` run if the change touches database or RLS behaviour, against a **disposable** database only
- [ ] No client data, credentials, `.env`, backup dumps, or generated extracts staged
- [ ] Unrelated pre-existing failures were left alone and called out rather than silently fixed