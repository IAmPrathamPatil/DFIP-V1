# Contributing to DFIP

DFIP is a working production system. Changes should preserve existing behaviour
unless the change is explicitly about behaviour.

## Before you start

Read [`documentation/V1_MASTER_DOCUMENTATION.md`](documentation/V1_MASTER_DOCUMENTATION.md).
It indexes the engineering record and states which document wins when two
disagree. Where the documentation and the code disagree, the code is correct and
the documentation is a bug.

For system context, read [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Local setup

```bash
git clone https://github.com/IAmPrathamPatil/DFIP-V1.git
cd DFIP-V1

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Requires Python 3.11 or later. Then create a local environment file:

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

`.env` is gitignored. Leave secret fields empty. No database is required for
development — with `DATABASE_URL` unset the API uses in-memory stores.

Run the two processes:

```bash
python -m dfip_api                 # API on 127.0.0.1:8000
python -m dfip_web                 # web origin on 127.0.0.1:3000
```

Full local procedures, including the one-click demo scripts and authentication
setup, are in
[`documentation/LOCAL_TESTING_RUNBOOK.md`](documentation/LOCAL_TESTING_RUNBOOK.md).

## Tests

```bash
# default suite: in-memory stores, no database needed
python -m pytest

# PostgreSQL-marked tests: require a DISPOSABLE PostgreSQL 16 database
python -m pytest -m postgres
```

> **Warning.** The PostgreSQL-marked tests drop and recreate the `public` schema.
> Never point `DFIP_TEST_DATABASE_URL` at a database holding data you care about.

To start a disposable local PostgreSQL:

```bash
docker compose --profile v2-db up -d dfip_db      # host port 5433
export DFIP_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5433/dfip_test
```

## Lint and format

```bash
python -m ruff check packages tests
python -m ruff format --check packages tests
```

Keep both clean for the files you touch. `ruff format` without `--check` rewrites
files in place — scope it to your own files rather than running it across the
repository, which would also rewrite files that are already failing `--check` for
unrelated reasons.

## Known baseline failures

**This repository does not currently have a clean lint or test baseline.** There
are pre-existing findings and failures on `master` that are unrelated to any
current work, and they are measured and described in
[`documentation/PROJECT_STATUS.md`](documentation/PROJECT_STATUS.md).

Practical consequences:

- A red CI run does not automatically mean your change broke something.
- To attribute a failure, compare against the base revision rather than assuming.
- Do not "fix" unrelated baseline failures as part of a feature change. Keep
  changes scoped, and call out what you did and did not touch in the pull request.

## Pull requests

- One logical change per pull request.
- Describe what changed, why, and how it was verified.
- State explicitly whether behaviour is unchanged.
- Note any documentation that needs updating alongside the code.
- If you change a data contract, say whether a migration is required. Do not add a
  down migration; this project does not have them.
- If you touch workbook generation, say which artifact you verified and whether it
  was opened in Excel.

## Data and secrets

Never commit, and never include in an issue or pull request:

- client workbooks, raw exports, or any published data
- `.env` files, credentials, tokens, API keys, or connection strings
- backup dumps, manifests containing client data, or source-archive copies
- `*.csv`, `*.parquet`, `*.pkl`, or generated extracts

The committed environment template must never contain a real value. Client
evidence files are read-only forensic material — do not edit, rename, or move
them.

Policy and rationale: [`documentation/DATA_HANDLING.md`](documentation/DATA_HANDLING.md).

## Reporting a security issue

Do not open a public issue. See [`SECURITY.md`](SECURITY.md).