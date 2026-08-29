# packages/config

- P0: `dfip_config.settings` — environment loader, no secrets required.
- P5 adds API prefix and authentication-mode fields to the same `Settings`
  class. It does not introduce a second settings framework.
- P2: versioned New Logic snapshots under `dfip_config/data/`, plus
  `store.py` / `resolve.py` / `rate_cards.py`.
- V2-C: publisher Logic/Labels workbook parse (`workbook.py`) and in-memory
  catalog overlay (`catalog.py`). Active uploaded versions replace packaged
  JSON for that client during bind/transform. Drafts are never used.

Resolvers reproduce Excel VLOOKUP first-match-wins. They do not ingest files,
compute Total Cost, or run QA.
