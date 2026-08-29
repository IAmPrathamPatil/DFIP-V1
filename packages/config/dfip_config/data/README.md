# Configuration snapshots (P2)

JSON in this folder is the **New Logic / grouping configuration**, extracted
read-only from the supplied production workbooks and the client Daily Report
pivot cache.

It is not raw Web Engage fact data. The 12 `.xlsx` evidence files remain
unmodified at the repository root and are gitignored.

| File | Contents |
|---|---|
| `campaign_labels_v1.json` | New Logic A:M, Apr–Jul 2025, 3905 rows |
| `campaign_labels_v2.json` | New Logic A:M, Aug–Oct 2025, 4093 rows |
| `templates_v1.json` … `v4.json` | New Logic Q:R (header `Rate`), four vintages |
| `label_groups_v1.json` | Filter Logic 1_2 membership (43 items) plus `captions` (Group1–Group7 display names) |
| `manifest.json` | Version ids and names |

Campaign names are stored **exactly**, including leading spaces. Duplicates are
retained. `row_order` is 1-based sheet order.
