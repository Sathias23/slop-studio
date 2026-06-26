"""Offline contract test: cloud.py vs. the pinned Comfy Cloud OpenAPI snapshot.

Runs ``scripts/check_cloud_contract.py``'s checker against the vendored
``tests/contract/openapi-cloud.yaml``. Deterministic and network-free, so it
belongs in normal CI. The *live* drift check runs weekly via
``.github/workflows/cloud-contract.yml``.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO_ROOT / "tests" / "contract" / "openapi-cloud.yaml"


def _load_checker():
    """Import scripts/check_cloud_contract.py (not an installed package)."""
    spec_path = REPO_ROOT / "scripts" / "check_cloud_contract.py"
    spec = importlib.util.spec_from_file_location("check_cloud_contract", spec_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # required before exec for dataclass annotation resolution
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load_checker()


@pytest.fixture(scope="module")
def snapshot_spec():
    with SNAPSHOT.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_snapshot_exists():
    assert SNAPSHOT.is_file(), "vendored OpenAPI snapshot is missing"


def test_cloud_py_matches_pinned_contract(checker, snapshot_spec):
    """Every endpoint/field cloud.py depends on is present in the pinned spec."""
    results = checker.check_contract(snapshot_spec)
    failures = [r for r in results if r.status == "fail"]
    assert not failures, "contract breaches:\n" + "\n".join(f"  - {r.name}: {r.detail}" for r in failures)


def test_history_migrated_off_deprecated_v2(checker, snapshot_spec):
    """The endpoint history() now uses (/api/jobs/{job_id}) is not deprecated...

    ...while the old /api/history_v2 path it migrated away from still is — the
    drift this whole check exists to catch.
    """
    paths = snapshot_spec["paths"]
    assert paths["/api/jobs/{job_id}"]["get"].get("deprecated") is not True
    assert paths["/api/history_v2/{prompt_id}"]["get"].get("deprecated") is True


def test_checker_flags_a_removed_endpoint(checker, snapshot_spec):
    """Sanity-check the checker itself: drop a used endpoint -> a fail appears."""
    broken = {**snapshot_spec, "paths": {k: v for k, v in snapshot_spec["paths"].items() if k != "/api/jobs/{job_id}"}}
    results = checker.check_contract(broken)
    assert any(r.status == "fail" and "/api/jobs/{job_id}" in r.name for r in results)
