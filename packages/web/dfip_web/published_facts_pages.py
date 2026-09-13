"""PublishedFacts.m paging contract (page size 250000 until total is consumed).

Mirrors excel/PublishedFacts.m. Power Query Number.RoundDown((Total - 1) / PageLimit)
is integer division for a non-negative total. This is not an HTTP client.
"""

from __future__ import annotations

PUBLISHED_FACTS_PAGE_LIMIT = 250000
PUBLISHED_FACTS_RELATIVE_PATH = "/api/v1/publications/history/facts"
PUBLISHED_FACTS_CSV_RELATIVE_PATH = "/api/v1/publications/history/facts.csv"


def published_facts_page_offsets(
    total: int, page_limit: int = PUBLISHED_FACTS_PAGE_LIMIT
) -> tuple[int, ...]:
    """Offsets FetchPage uses when Total > 0.

    PageIndexes = {0..Number.RoundDown((Total - 1) / PageLimit)}.
    Offset = index * PageLimit. Empty total yields no pages.
    """
    if total <= 0:
        return ()
    last_index = (total - 1) // page_limit
    return tuple(index * page_limit for index in range(last_index + 1))


def published_facts_combine(
    items: list[object], page_limit: int = PUBLISHED_FACTS_PAGE_LIMIT
) -> list[object]:
    """Same combine rule as PublishedFacts.m CombinedItems."""
    total = len(items)
    if total == 0:
        return []
    if total <= page_limit:
        return list(items)
    combined: list[object] = []
    for offset in published_facts_page_offsets(total, page_limit):
        combined.extend(items[offset : offset + page_limit])
    return combined


def published_facts_last_page_size(total: int, page_limit: int = PUBLISHED_FACTS_PAGE_LIMIT) -> int:
    if total <= 0:
        return 0
    remainder = total % page_limit
    return page_limit if remainder == 0 else remainder
