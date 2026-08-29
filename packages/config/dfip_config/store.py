"""Load versioned New Logic / grouping configuration from packaged JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

CAMPAIGN_OUTPUT_FIELDS: tuple[str, ...] = (
    "filter_logic_1",
    "filter_logic_2",
    "amc_status_filter_logic_3",
    "amc_device_category_filter_logic_4",
    "amc_product_cat_filter_logic_5",
    "manual_or_automated",
)


@dataclass(frozen=True)
class ConfigSnapshot:
    version_label: str
    source_filename: str | None
    effective_from: str | None
    effective_to: str | None
    rows: tuple[dict[str, Any], ...]


def _load_json(name: str) -> dict[str, Any]:
    path = DATA_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot(filename: str) -> ConfigSnapshot:
    raw = _load_json(filename)
    return ConfigSnapshot(
        version_label=raw["version_label"],
        source_filename=raw.get("source_filename"),
        effective_from=raw.get("effective_from"),
        effective_to=raw.get("effective_to"),
        rows=tuple(raw["rows"]),
    )


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    return _load_json("manifest.json")


@lru_cache(maxsize=None)
def load_campaign_version(version_label: str) -> ConfigSnapshot:
    mapping = {"campaign-v1": "campaign_labels_v1.json", "campaign-v2": "campaign_labels_v2.json"}
    if version_label not in mapping:
        raise KeyError(f"unknown campaign version: {version_label}")
    return _snapshot(mapping[version_label])


@lru_cache(maxsize=None)
def load_template_version(version_label: str) -> ConfigSnapshot:
    mapping = {
        "template-v1": "templates_v1.json",
        "template-v2": "templates_v2.json",
        "template-v3": "templates_v3.json",
        "template-v4": "templates_v4.json",
    }
    if version_label not in mapping:
        raise KeyError(f"unknown template version: {version_label}")
    return _snapshot(mapping[version_label])


@lru_cache(maxsize=None)
def load_label_group_version(version_label: str = "fl1-group-v1") -> ConfigSnapshot:
    if version_label != "fl1-group-v1":
        raise KeyError(f"unknown label group version: {version_label}")
    return _snapshot("label_groups_v1.json")


@lru_cache(maxsize=None)
def load_label_group_captions(
    version_label: str = "fl1-group-v1",
) -> tuple[tuple[str, str], ...]:
    """Internal Filter Logic 1_2 key → client display caption.

    Membership rows are unchanged. Unknown / leftover group keys pass through
    as themselves at resolve time.
    """
    if version_label != "fl1-group-v1":
        raise KeyError(f"unknown label group version: {version_label}")
    raw = _load_json("label_groups_v1.json")
    captions = tuple(
        (str(item["group_name"]), str(item["display_name"]))
        for item in raw.get("captions", ())
        if item.get("group_name") and item.get("display_name")
    )
    return captions


def campaign_versions() -> tuple[str, ...]:
    return tuple(load_manifest()["campaign_versions"])


def template_versions() -> tuple[str, ...]:
    return tuple(load_manifest()["template_versions"])
