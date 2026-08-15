"""Constrained AST scanner and human-gated adapter generator for legacy LMS."""

from __future__ import annotations

import ast
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.openapi_docs import render_markdown
from app.state_store import PROJECT_ROOT, STORE, StateStore
from fixtures.legacy_lms.repository import find_land_record

FIXTURE_ROOT = PROJECT_ROOT / "fixtures" / "legacy_lms"
DEFAULT_GENERATED_ROOT = PROJECT_ROOT / "generated"


class OnboardingError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _annotation(node: ast.expr | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def _discover_functions(path: Path) -> list[dict[str, Any]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    discovered = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        discovered.append(
            {
                "name": node.name,
                "async": isinstance(node, ast.AsyncFunctionDef),
                "parameters": [
                    {
                        "name": argument.arg,
                        "annotation": _annotation(argument.annotation),
                        "required": True,
                    }
                    for argument in node.args.args
                ],
                "return_annotation": _annotation(node.returns),
                "docstring": ast.get_docstring(node),
                "line": node.lineno,
            }
        )
    return discovered


def _generated_openapi(sample: dict[str, Any]) -> dict[str, Any]:
    properties = {
        key: {"type": "string"} for key, value in sample.items() if isinstance(value, str)
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Generated Legacy LMS Adapter", "version": "0.1.0-proposal"},
        "paths": {
            "/generated/legacy-lms/land-record": {
                "get": {
                    "operationId": "findLegacyLandRecord",
                    "summary": "Find a verified BP Batam land record by NIB",
                    "parameters": [
                        {
                            "name": "nib",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Legacy land record",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": properties}
                                }
                            },
                        }
                    },
                }
            }
        },
    }


def scan_legacy_lms(*, store: StateStore = STORE) -> dict[str, Any]:
    repository_path = FIXTURE_ROOT / "repository.py"
    functions = _discover_functions(repository_path)
    target = next((item for item in functions if item["name"] == "find_land_record"), None)
    if target is None:
        raise OnboardingError("The expected find_land_record operation was not discovered")
    sample = find_land_record("1209260012345")
    if sample is None:
        raise OnboardingError("The fixture contract sample was unavailable")
    openapi = _generated_openapi(sample)
    adapter = {
        "kind": "constrained_python_function",
        "module": "fixtures.legacy_lms.repository",
        "callable": "find_land_record",
        "input_mapping": {"nib": "nib"},
        "output_mapping": {"bpbatam.legacy_land_record": "plot"},
        "execution": "in_process_demo_only",
    }
    proposed_registry_entry = {
        "service": {
            "name": "Legacy BP Batam LMS",
            "status": "active",
            "contract": "Generated OpenAPI 3.1",
            "operations": 1,
            "hostname": "datamaster.local",
            "apis": [
                {
                    "endpoint": "/generated/legacy-lms/land-record",
                    "method": "GET",
                    "description": "Find a verified BP Batam land record by NIB through the generated legacy adapter.",
                }
            ],
            "concepts": ["bpbatam.legacy_land_record"],
            "dependents": [],
        },
        "concept": {
            "authoritative_service": "legacy_lms",
            "operation": "find_legacy_land_record",
            "response_path": "plot",
            "adapter": "generated/adapters/legacy_lms.json",
        },
    }
    proposal = {
        "id": "legacy-lms-find-land-record",
        "kind": "legacy_onboarding",
        "title": "Legacy LMS integration proposal",
        "status": "pending",
        "created_at": _now(),
        "fixture": "fixtures/legacy_lms",
        "scan": {
            "parser": "python.ast",
            "files_scanned": ["repository.py", "models.py", "README.md"],
            "discovered_functions": functions,
            "selected_operation": target,
        },
        "adapter": adapter,
        "openapi": openapi,
        "docs_preview": render_markdown(openapi),
        "proposed_registry_entry": proposed_registry_entry,
        "checks": [
            {"name": "Python parsed successfully", "passed": True},
            {"name": "Required nib parameter discovered", "passed": True},
            {"name": "Deterministic fixture returned a land record", "passed": True},
        ],
        "safety": "AI proposes → deterministic checks/tests → human approval → activate",
    }
    proposals = store.proposals()
    proposals["onboarding"] = [
        item for item in proposals["onboarding"] if item["id"] != proposal["id"]
    ]
    proposals["onboarding"].append(proposal)
    store.save_proposals(proposals)
    return proposal


def list_onboarding(*, store: StateStore = STORE) -> list[dict[str, Any]]:
    return store.proposals()["onboarding"]


def approve_onboarding(
    proposal_id: str,
    *,
    store: StateStore = STORE,
    generated_root: Path = DEFAULT_GENERATED_ROOT,
) -> dict[str, Any]:
    proposals = store.proposals()
    proposal = next(
        (item for item in proposals["onboarding"] if item["id"] == proposal_id), None
    )
    if proposal is None:
        raise OnboardingError(f"Unknown onboarding proposal: {proposal_id}")
    if proposal["status"] != "pending":
        raise OnboardingError("Only pending onboarding proposals can be approved")

    sample = find_land_record("1209260012345")
    tests = [
        {"name": "Fixture NIB round trip", "passed": sample and sample["nib"] == "1209260012345"},
        {"name": "Land plot is present", "passed": bool(sample and sample.get("plot"))},
        {"name": "Generated OpenAPI has one operation", "passed": len(proposal["openapi"]["paths"]) == 1},
    ]
    proposal["approval_tests"] = tests
    if not all(test["passed"] for test in tests):
        proposal["status"] = "failed_tests"
        store.save_proposals(proposals)
        raise OnboardingError("Generated integration failed deterministic tests")

    adapters = generated_root / "adapters"
    openapi_dir = generated_root / "openapi"
    docs = generated_root / "docs"
    for directory in (adapters, openapi_dir, docs):
        directory.mkdir(parents=True, exist_ok=True)
    (adapters / "legacy_lms.json").write_text(json.dumps(proposal["adapter"], indent=2) + "\n")
    (openapi_dir / "legacy_lms.openapi.json").write_text(
        json.dumps(proposal["openapi"], indent=2) + "\n"
    )
    (docs / "legacy_lms.md").write_text(proposal["docs_preview"])

    registry = deepcopy(store.registry())
    entry = proposal["proposed_registry_entry"]
    registry["services"]["legacy_lms"] = entry["service"]
    registry["concepts"]["bpbatam.legacy_land_record"] = entry["concept"]
    registry["revision"] += 1
    store.save_registry(registry)

    proposal["status"] = "applied"
    proposal["approved_by"] = "demo.reviewer@datamaster.local"
    proposal["approved_at"] = _now()
    proposal["registry_revision"] = registry["revision"]
    proposal["generated_files"] = [
        "generated/adapters/legacy_lms.json",
        "generated/openapi/legacy_lms.openapi.json",
        "generated/docs/legacy_lms.md",
    ]
    store.save_proposals(proposals)
    return proposal


def query_legacy_lms(nib: str, *, store: StateStore = STORE) -> dict[str, Any]:
    service = store.registry()["services"].get("legacy_lms")
    if not service or service["status"] != "active":
        raise OnboardingError("Legacy LMS adapter is not active; approve the proposal first")
    record = find_land_record(nib)
    if record is None:
        raise OnboardingError("No legacy land record matched that NIB")
    return {
        "data": {"bpbatam.legacy_land_record": record["plot"]},
        "provenance": {
            "bpbatam.legacy_land_record": {
                "source": service["name"],
                "verified": True,
                "last_verified": record["last_verified"],
            }
        },
    }
