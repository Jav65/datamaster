"""Fast deterministic checks exposed by the console's Tests tab."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from urllib.parse import urlparse

from app.change_manager import approve_change, detect_oss_v2_change
from app.contract_diff import diff_openapi, load_contract
from app.mock_gov import government_response
from app.onboarding import (
    OnboardingError,
    approve_onboarding,
    query_legacy_lms,
    scan_legacy_lms,
)
from app.policy import PERMIT_PURPOSE, PolicyDenied
from app.repository_scanner import (
    TEST_REPOSITORY_ROOT,
    ServiceDocumentation,
    connect_repository,
)
from app.resolver import resolve
from app.state_store import DEFAULT_STATE_DIR, PROJECT_ROOT, StateStore

PERMIT_FIELDS = [
    "person.nik",
    "business.nib",
    "business.company_name",
    "business.npwp",
    "business.company_deed",
    "bpbatam.land_record",
    "application.warehouse_evidence",
    "application.warehouse_plan",
    "application.logistics_purpose",
]
SUBJECT = {"name": "John Doe", "phone": "+62838292938"}


@contextmanager
def _isolated_store() -> Iterator[tuple[StateStore, Path]]:
    """Copy immutable templates so checks never alter the live judge state."""
    with tempfile.TemporaryDirectory(prefix="datamaster-checks-") as directory:
        root = Path(directory) / "state"
        root.mkdir()
        shutil.copyfile(DEFAULT_STATE_DIR / "initial_registry.json", root / "initial_registry.json")
        shutil.copyfile(DEFAULT_STATE_DIR / "initial_registry.json", root / "semantic_registry.json")
        shutil.copyfile(DEFAULT_STATE_DIR / "dependencies.json", root / "dependencies.json")
        (root / "proposals.json").write_text(
            json.dumps({"onboarding": [], "changes": []}, indent=2) + "\n"
        )
        (root / "demo.json").write_text(
            json.dumps({"oss_version": 1, "last_reset_by": "demo_checks"}, indent=2)
            + "\n"
        )
        yield StateStore(root), Path(directory) / "generated"


def run_demo_checks() -> dict:
    """Return judge-readable results without depending on the LLM or internet."""
    results: list[dict[str, object]] = []

    def check(name: str, assertion, evidence: str) -> None:
        try:
            passed = bool(assertion())
            results.append({"name": name, "passed": passed, "evidence": evidence})
        except Exception as exc:  # the result belongs in the UI, not a 500 response
            results.append({"name": name, "passed": False, "evidence": str(exc)})

    with _isolated_store() as (store, generated_root):
        def requester(method: str, url: str, params: dict):
            if method != "GET":
                return 405, {"error": "Method not allowed"}
            return government_response(urlparse(url).path, params, store=store)

        initial = resolve(
            SUBJECT, PERMIT_FIELDS, PERMIT_PURPOSE, store=store, requester=requester
        )

        check(
            "Permit retrieves repeated authoritative records",
            lambda: initial["data"]
            == {
                "application.warehouse_evidence": None,
                "application.warehouse_plan": None,
                "application.logistics_purpose": None,
                "person.nik": "2171012507890001",
                "business.nib": "1209260012345",
                "business.company_name": "PT Selat Niaga Makmur",
                "business.npwp": "09.254.294.3-217.000",
                "business.company_deed": "AHU-0045821.AH.01.01.2026",
                "bpbatam.land_record": "Kabil Industrial Estate Blok C-4 (2,400 m²)",
            },
            "NIK, NIB, NPWP, deed, company, and land values match deterministic fixtures.",
        )
        check(
            "Normal permit resolution never calls SATUSEHAT",
            lambda: "satusehat" not in initial["called_services"],
            f"Called: {', '.join(initial['called_services'])}",
        )

        executed: list[str] = []
        try:
            resolve(
                SUBJECT,
                ["health.blood_type"],
                PERMIT_PURPOSE,
                store=store,
                requester=lambda method, url, params: (
                    executed.append(url) or (500, {"error": "must not execute"})
                ),
            )
            health_blocked = False
        except PolicyDenied:
            health_blocked = not executed
        check(
            "Health request is rejected before execution",
            lambda: health_blocked,
            "Purpose policy denied health.blood_type; executed service count is zero.",
        )
        check(
            "New application documents remain manual",
            lambda: all(
                initial["provenance"][field]["status"] == "manual_required"
                for field in (
                    "application.warehouse_evidence",
                    "application.warehouse_plan",
                    "application.logistics_purpose",
                )
            ),
            "Warehouse evidence, plan, and requested activity are null/manual_required.",
        )

        onboarding = scan_legacy_lms(store=store)
        inactive_before = "legacy_lms" not in store.registry()["services"]
        try:
            query_legacy_lms("1209260012345", store=store)
            blocked_before = False
        except OnboardingError:
            blocked_before = True
        applied_onboarding = approve_onboarding(
            onboarding["id"], store=store, generated_root=generated_root
        )
        legacy_result = query_legacy_lms("1209260012345", store=store)
        check(
            "Legacy onboarding requires approval",
            lambda: (
                onboarding["scan"]["selected_operation"]["name"] == "find_land_record"
                and applied_onboarding["status"] == "applied"
                and inactive_before
                and blocked_before
                and legacy_result["data"]["bpbatam.legacy_land_record"]
                == "Kabil Industrial Estate Blok C-4 (2,400 m²)"
            ),
            "AST discovered find_land_record; activation and query succeeded only after approval.",
        )

        contracts = PROJECT_ROOT / "contracts"
        structural_diff = diff_openapi(
            load_contract(contracts / "oss-v1.openapi.json"),
            load_contract(contracts / "oss-v2.openapi.json"),
        )
        check(
            "OpenAPI diff detects the breaking OSS change",
            lambda: (
                structural_diff["removed_paths"][0]["path"] == "/oss/getNIB"
                and structural_diff["added_paths"][0]["path"]
                == "/oss/business-by-director"
                and "nib" in structural_diff["removed_response_fields"]
                and "business_identification_number"
                in structural_diff["added_response_fields"]
            ),
            "Removed /oss/getNIB + nib; added /oss/business-by-director + business_identification_number.",
        )

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            change = detect_oss_v2_change("deterministic_test", store=store)
        old_mapping_still_active = (
            store.registry()["concepts"]["business.nib"]["response_path"] == "nib"
        )
        check(
            "Semantic analyzer proposes the NIB mapping",
            lambda: any(
                item["concept"] == "business.nib"
                and item["new_field"] == "business_identification_number"
                for item in change["mappings"]
            ),
            f"Analysis mode: {change['analysis']['mode']}",
        )
        check(
            "Proposal does not apply itself",
            lambda: change["status"] == "pending" and old_mapping_still_active,
            "OSS mock moved to v2 while the approved registry remained on v1.",
        )

        approved = approve_change(change["id"], store=store, generated_root=generated_root)
        after = resolve(
            SUBJECT, PERMIT_FIELDS, PERMIT_PURPOSE, store=store, requester=requester
        )
        check(
            "Approval updates the adapter after contract tests pass",
            lambda: (
                approved["status"] == "applied"
                and all(test["passed"] for test in approved["tests"])
                and after["data"]["business.nib"] == "1209260012345"
            ),
            "Registry activated v2 and the canonical business.nib value still resolves.",
        )
        check(
            "Downstream permit request stays unchanged",
            lambda: after["data"].keys() == initial["data"].keys(),
            "The same canonical field list was used before and after OSS v2.",
        )
        check(
            "Every reused field has provenance",
            lambda: all(
                after["provenance"][field].get("source")
                and after["provenance"][field]["verified"]
                for field in PERMIT_FIELDS
                if not field.startswith("application.")
            ),
            "Identity, business, tax, legal, and land values name their authoritative source.",
        )

        undocumented_before = (
            store.registry()["services"]["bpbatam"].get("documentation_status")
            == "undocumented"
        )
        fixture_documentation = ServiceDocumentation.model_validate(
            {
                "name": "BP Batam",
                "hostname": "https://api.bpbatam.go.id",
                "description": "Business-permit validation service.",
                "apis": [
                    {
                        "method": method,
                        "endpoint": endpoint,
                        "description": description,
                        "source_files": ["src/bp_batam_api/main.py"],
                    }
                    for method, endpoint, description in (
                        ("GET", "/health", "Report service health."),
                        ("GET", "/api/v1/permits/{permit_id}", "Retrieve one permit."),
                        ("GET", "/api/v1/permit-validations", "List validations."),
                        ("POST", "/api/v1/permit-validations", "Create a validation."),
                    )
                ],
            }
        )
        repository_connection = connect_repository(
            "https://github.com/alexgeraldhandoko/bp-batam.git",
            store=store,
            repository_root=TEST_REPOSITORY_ROOT,
            analyzer=lambda _snapshot, _repository: (
                fixture_documentation,
                {"mode": "openai_structured_outputs", "model": "test-double"},
            ),
        )
        documented_service = store.registry()["services"][
            repository_connection["service_key"]
        ]
        check(
            "Repository scan documents BP Batam APIs",
            lambda: (
                undocumented_before
                and repository_connection["status"] == "connected"
                and documented_service["name"] == "BP Batam"
                and documented_service["operations"] == 4
                and documented_service["documentation_status"]
                == "generated_from_repository"
            ),
            "A schema-valid AI test double documented four routes and added BP Batam to the catalog.",
        )

    return {
        "results": results,
        "passed": sum(bool(result["passed"]) for result in results),
        "total": len(results),
        "isolated": True,
        "llm_required": False,
    }
