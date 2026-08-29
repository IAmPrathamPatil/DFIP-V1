"""Comparator decode: PivotCache sharedItems values may contain '/'."""

from __future__ import annotations

import re
from xml.sax.saxutils import unescape


def parse_shared_items(block: str) -> list:
    out = []
    for match in re.finditer(r"<(s|n|d|m)\b([^>]*)/>", block):
        tag = match.group(1)
        attrs = match.group(2)
        if tag == "m":
            out.append(None)
            continue
        raw = re.search(r'v="([^"]*)"', attrs)
        value = unescape(raw.group(1), {"&quot;": '"'}) if raw else None
        if tag == "d" and value:
            out.append(value[:10])
        else:
            out.append(value)
    return out


def parse_shared_items_broken(block: str) -> list:
    """Pre-P10-root-cause parser. `[^/]*` drops items whose v= contains '/'."""
    out = []
    for match in re.finditer(r"<(s|n|d|m)\b([^/]*)/>", block):
        tag = match.group(1)
        attrs = match.group(2)
        if tag == "m":
            out.append(None)
            continue
        raw = re.search(r'v="([^"]*)"', attrs)
        out.append(unescape(raw.group(1), {"&quot;": '"'}) if raw else None)
    return out


SAMPLE = (
    '<s v="Email One off"/>'
    '<s v="TAMC_OW/OC_Apr\'21 to Mar\'22"/>'
    '<s v="Job Allocated Notification"/>'
    '<n v="0"/>'
    "<m/>"
)


def test_shared_items_keep_values_containing_slash() -> None:
    items = parse_shared_items(SAMPLE)
    assert items == [
        "Email One off",
        "TAMC_OW/OC_Apr'21 to Mar'22",
        "Job Allocated Notification",
        "0",
        None,
    ]


def test_broken_slash_parser_drops_items_with_slash() -> None:
    broken = parse_shared_items_broken(SAMPLE)
    good = parse_shared_items(SAMPLE)
    assert len(broken) < len(good)
    assert "TAMC_OW/OC_Apr'21 to Mar'22" not in broken
    assert good[1] == "TAMC_OW/OC_Apr'21 to Mar'22"
