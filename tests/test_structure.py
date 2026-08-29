"""Structural checks that P0 files exist and Docker/CI YAML parse."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_required_foundation_files_exist() -> None:
    required = [
        ".gitignore",
        ".env.example",
        "README.md",
        "pyproject.toml",
        "Dockerfile",
        "docker-compose.yml",
        ".github/workflows/ci.yml",
        "packages/core/dfip_core/__init__.py",
        "packages/db/dfip_db/__init__.py",
        "packages/shared/dfip_shared/__init__.py",
        "packages/config/dfip_config/settings.py",
        "documentation/STATUS.md",
        "documentation/DATA_HANDLING.md",
        "documentation/SCHEMA.md",
        "source/README.md",
        "supabase/migrations/20260823000001_p1_extensions.sql",
        "supabase/migrations/20260823000002_p1_tables.sql",
        "supabase/migrations/20260823000003_p1_indexes.sql",
        "supabase/migrations/20260823000004_p1_seed.sql",
        "supabase/migrations/20260823000005_p2_config_versions.sql",
        "supabase/migrations/20260823000006_p2_campaign_v1.sql",
        "supabase/migrations/20260823000007_p2_campaign_v2.sql",
        "supabase/migrations/20260823000008_p2_templates.sql",
        "supabase/migrations/20260823000009_p2_client_kpis.sql",
        "packages/config/dfip_config/resolve.py",
        "packages/config/dfip_config/store.py",
        "packages/config/dfip_config/data/manifest.json",
        "packages/config/dfip_config/data/campaign_labels_v1.json",
        "packages/config/dfip_config/data/campaign_labels_v2.json",
        "packages/config/dfip_config/data/templates_v4.json",
        "packages/config/dfip_config/data/label_groups_v1.json",
        "supabase/migrations/20260823000010_p3_batch_ingest_metadata.sql",
        "packages/core/dfip_core/ingest/pipeline.py",
        "packages/core/dfip_core/ingest/reader.py",
        "packages/core/dfip_core/ingest/store.py",
        "packages/core/dfip_core/ingest/headers.py",
        "packages/core/dfip_core/transform/engine.py",
        "packages/core/dfip_core/transform/extract.py",
        "packages/core/dfip_core/transform/labels.py",
        "packages/core/dfip_core/transform/cost.py",
        "packages/core/dfip_core/transform/derive.py",
        "packages/core/dfip_core/transform/fact.py",
        "packages/core/dfip_core/transform/store.py",
        "packages/core/dfip_core/transform/reconcile.py",
        "tests/test_p4_transform.py",
        "tests/test_p4_workbook_reconciliation.py",
        "packages/api/dfip_api/app.py",
        "packages/api/dfip_api/auth.py",
        "packages/api/dfip_api/routes.py",
        "tests/test_p5_api.py",
        "packages/web/dfip_web/app.py",
        "apps/web/static/index.html",
        "apps/web/static/js/api-client.js",
        "tests/test_p6_web.py",
        "tests/test_daily_report.py",
        "tests/test_p8_uat.py",
        "tests/test_p9_authz.py",
        "tests/test_p10_release.py",
        "excel/PublishedFacts.m",
        "excel/Client_Report.xlsx",
        "documentation/PUBLICATION.md",
        "documentation/DAILY_REPORT.md",
        "packages/web/dfip_web/daily_report.py",
        "packages/api/dfip_api/publication_store.py",
        "packages/api/dfip_api/publication_routes.py",
    ]
    missing = [path for path in required if not (ROOT / path).exists()]
    assert missing == []


def test_docker_compose_parses() -> None:
    data = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "services" in data
    assert "app" in data["services"]
    assert "postgres" not in data["services"]
    assert "supabase" not in data["services"]


def test_github_actions_workflow_parses() -> None:
    data = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert data["name"] == "DFIP CI"
    jobs = data["jobs"]
    assert "verify" in jobs
    steps = [step.get("name") or step.get("uses") for step in jobs["verify"]["steps"]]
    assert any(step and "Checkout" in str(step) for step in steps)
    assert any(step and "Install" in str(step) for step in steps)
    assert any(step and "Lint" in str(step) for step in steps)
    assert any(step and "Format" in str(step) for step in steps)
    assert any(step and "Test" in str(step) for step in steps)
