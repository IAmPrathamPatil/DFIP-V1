# Data handling

The supplied Web Engage workbooks contain client campaign data, including PII (`Created By`) and message content.

## Do

- Keep local copies for forensic comparison.
- Leave the supplied `.xlsx` files unmodified.
- Place any new local drops in `source/` (gitignored).
- P3 reads workbooks with openpyxl `read_only=True` and never saves them. The P4
  workbook reconciliation test opens them the same way and asserts the file
  mtime is unchanged afterwards.
- The P5 API must not return `storage_uri`, local filesystem paths, secrets,
  environment values, or Python tracebacks.
- Original uploaded `.xlsx` files are stored in private object storage under
  `{client_id}/{source_file_id}/{sha256}.xlsx`. Keep the bucket/directory
  private. Server-side credentials only. P13F keeps source archive, staging,
  facts, QA, and publications until whole-company operator purge. Processing
  success, QA, publish, and company deactivation do not delete source bytes.
- The P6 SPA stores the issued access JWT in `sessionStorage` after password
  sign-in. Do not put `DFIP_AUTH_SECRET`, `DATABASE_URL`, `DFIP_DEV_AUTH_TOKEN`,
  `DFIP_BOOTSTRAP_TOKEN`, or service-role keys in the frontend.
- Production-grade processes read secrets from the platform/container
  environment only. Do not rely on a repository-local `.env`. Do not log
  Authorization, Cookie, passwords, or `DATABASE_URL`.
- P13C backup artifacts (`postgres/dfip.dump`, `source-archive/`,
  `manifest.json`) are confidential client data. Restrict access. Do not
  serve them over HTTP or commit them. Manifests contain no secrets. The
  dump contains client rows and `app_user.password_hash`. Restore JWT /
  bootstrap / database passwords separately through the P13A environment.

## Do not

- `git add` production/evidence `.xlsx`, `.xls`, `.csv`, or extracts (`.pkl`, `.parquet`).
  The V2-X client workbook `excel/Client_Report.xlsx` is the only tracked workbook
  (empty BearerToken; not production campaign extracts).
- Commit `.env`, credential files, or backup dumps/archives.
- Upload source workbooks to a public bucket.
- Copy source workbooks into `excel/` — that folder holds the V2-X client
  workbook and `PublishedFacts.m` only.
- Move or rename the supplied files to "clean up" the repository.

P2 configuration snapshots in `packages/config/dfip_config/data/*.json` are
versioned New Logic / grouping tables, not raw Web Engage fact extracts. They
are source-controlled on purpose. Do not replace them with CSV dumps of the
workbooks.

## Gitignore coverage

`.gitignore` excludes at least:

- `*.xlsx` except `excel/Client_Report.xlsx`
- `source/` (except this folder's sibling README)
- `raw/`, `data/raw/`, `uploads/`, `generated/`
- `.env`, `.env.*` except `.env.example`
- `.venv/`, `__pycache__/`, caches, logs, local databases

## Nested Git warning

This project previously sat inside a git work tree rooted at the user home directory (`C:\Users\PRATHAM`). DFIP now has its own `.git` in this folder. Do not `git add` from the home directory. Confirm with `git rev-parse --show-toplevel` that the toplevel is `.../DFIP-V1` before any commit.
