"""Dependency-aware contract-change proposals and human-gated activation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.contract_diff import diff_openapi, load_contract
from app.mock_gov import government_response
from app.openapi_docs import render_markdown
from app.semantic_analysis import propose_semantic_mappings
from app.state_store import PROJECT_ROOT, STORE, StateStore

CONTRACT_ROOT = PROJECT_ROOT / "contracts"
DEFAULT_GENERATED_ROOT = PROJECT_ROOT / "generated"


class ChangeError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _affected_consumers(concepts: list[str], store: StateStore) -> list[dict[str, str]]:
    affected = []
    for consumer_id, consumer in store.dependencies()["consumers"].items():
        if set(concepts) & set(consumer["uses"]):
            affected.append({"id": consumer_id, "name": consumer["name"]})
    return affected


def detect_oss_v2_change(
    source: str,
    *,
    store: StateStore = STORE,
) -> dict[str, Any]:
    """Switch the mock upstream and create, but never apply, a review proposal."""
    if store.registry()["services"]["oss"].get("adapter_version") == 2:
        raise ChangeError("OSS v2 is already approved and active; reset the demo to replay the merge")
    old_contract = load_contract(CONTRACT_ROOT / "oss-v1.openapi.json")
    new_contract = load_contract(CONTRACT_ROOT / "oss-v2.openapi.json")
    structural_diff = diff_openapi(old_contract, new_contract)
    registry = store.registry()
    affected_concepts = [
        concept
        for concept, config in registry["concepts"].items()
        if config.get("authoritative_service") == "oss"
        and config.get("response_path") in structural_diff["removed_response_fields"]
    ]
    analysis = propose_semantic_mappings(
        structural_diff["removed_response_fields"],
        structural_diff["added_response_fields"],
    )
    concept_by_old_field = {
        registry["concepts"][concept]["response_path"]: concept for concept in affected_concepts
    }
    mappings = [
        {**mapping, "concept": concept_by_old_field.get(mapping["old_field"])}
        for mapping in analysis["mappings"]
        if mapping["old_field"] in concept_by_old_field
    ]

    proposal = {
        "id": "oss-demo-oss-v2",
        "kind": "contract_change",
        "service": "oss",
        "title": "OSS contract change detected",
        "commit": "demo-oss-v2",
        "source": source,
        "status": "pending",
        "detected_at": _now(),
        "diff": structural_diff,
        "affected_concepts": affected_concepts,
        "affected_consumers": _affected_consumers(affected_concepts, store),
        "analysis": {key: value for key, value in analysis.items() if key != "mappings"},
        "mappings": mappings,
        "pipeline": [
            {"name": "OpenAPI structural diff", "status": "passed", "actor": "deterministic"},
            {"name": "Dependency graph lookup", "status": "passed", "actor": "deterministic"},
            {"name": "Semantic mapping proposal", "status": "passed", "actor": analysis["mode"]},
            {"name": "Human approval", "status": "waiting", "actor": "reviewer"},
        ],
    }

    proposals = store.proposals()
    proposals["changes"] = [item for item in proposals["changes"] if item["id"] != proposal["id"]]
    proposals["changes"].append(proposal)
    store.save_proposals(proposals)
    demo = store.demo()
    demo["oss_version"] = 2
    demo["last_change_event"] = {"source": source, "commit": "demo-oss-v2"}
    store.save_demo(demo)
    return proposal


def list_changes(*, store: StateStore = STORE) -> list[dict[str, Any]]:
    return store.proposals()["changes"]


def _find(proposals: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    proposal = next((item for item in proposals["changes"] if item["id"] == proposal_id), None)
    if proposal is None:
        raise ChangeError(f"Unknown change proposal: {proposal_id}")
    return proposal


def edit_mapping(
    proposal_id: str,
    concept: str,
    new_field: str,
    *,
    store: StateStore = STORE,
) -> dict[str, Any]:
    proposals = store.proposals()
    proposal = _find(proposals, proposal_id)
    if proposal["status"] != "pending":
        raise ChangeError("Only pending proposals can be edited")
    mapping = next((item for item in proposal["mappings"] if item["concept"] == concept), None)
    if mapping is None:
        raise ChangeError(f"Proposal has no mapping for {concept}")
    mapping["new_field"] = new_field
    mapping["edited_by"] = "demo.reviewer@datamaster.local"
    mapping["confidence"] = None
    store.save_proposals(proposals)
    return proposal


def _candidate_registry(proposal: dict[str, Any], store: StateStore) -> dict[str, Any]:
    registry = deepcopy(store.registry())
    for mapping in proposal["mappings"]:
        concept = mapping.get("concept")
        if concept:
            registry["concepts"][concept]["endpoint"] = "/oss/business-by-director"
            registry["concepts"][concept]["response_path"] = mapping["new_field"]
    return registry


def _contract_tests(candidate: dict[str, Any], store: StateStore) -> list[dict[str, Any]]:
    status, body = government_response(
        "/oss/business-by-director", {"nik": "2171012507890001"}, store=store
    )
    tests = [{"name": "OSS v2 endpoint responds", "passed": status == 200}]
    expected = {
        "business.nib": "1209260012345",
        "business.company_name": "PT Selat Niaga Makmur",
    }
    for concept, value in expected.items():
        response_path = candidate["concepts"][concept]["response_path"]
        tests.append(
            {
                "name": f"{concept} maps through {response_path}",
                "passed": body.get(response_path) == value,
            }
        )
    return tests


def approve_change(
    proposal_id: str,
    *,
    store: StateStore = STORE,
    generated_root: Path = DEFAULT_GENERATED_ROOT,
) -> dict[str, Any]:
    proposals = store.proposals()
    proposal = _find(proposals, proposal_id)
    if proposal["status"] != "pending":
        raise ChangeError("Only pending proposals can be approved")
    candidate = _candidate_registry(proposal, store)
    tests = _contract_tests(candidate, store)
    proposal["tests"] = tests
    if not all(test["passed"] for test in tests):
        proposal["status"] = "failed_tests"
        proposal["pipeline"][-1]["status"] = "failed_tests"
        store.save_proposals(proposals)
        raise ChangeError("Candidate mapping failed deterministic contract tests")

    generated_docs = generated_root / "docs"
    generated_docs.mkdir(parents=True, exist_ok=True)
    contract = load_contract(CONTRACT_ROOT / "oss-v2.openapi.json")
    (generated_docs / "oss.md").write_text(render_markdown(contract))

    candidate["revision"] += 1
    candidate["services"]["oss"]["adapter_version"] = 2
    candidate["services"]["oss"]["status"] = "active"
    candidate["services"]["oss"]["apis"] = [
        {
            "endpoint": "/oss/business-by-director",
            "method": "GET",
            "description": (
                "Retrieve a director's registered business, including its NIB, "
                "legal name, activity codes, and risk classification."
            ),
        }
    ]
    store.save_registry(candidate)

    proposal["status"] = "applied"
    proposal["approved_at"] = _now()
    proposal["approved_by"] = "demo.reviewer@datamaster.local"
    proposal["registry_revision"] = candidate["revision"]
    proposal["generated_docs"] = "generated/docs/oss.md"
    proposal["pipeline"][-1]["status"] = "approved"
    proposal["pipeline"].extend(
        [
            {"name": "Contract tests", "status": "passed", "actor": "deterministic"},
            {"name": "Registry activation", "status": "passed", "actor": "approval gate"},
            {"name": "Documentation regeneration", "status": "passed", "actor": "OpenAPI renderer"},
        ]
    )
    store.save_proposals(proposals)
    return proposal


def reject_change(proposal_id: str, *, store: StateStore = STORE) -> dict[str, Any]:
    proposals = store.proposals()
    proposal = _find(proposals, proposal_id)
    if proposal["status"] != "pending":
        raise ChangeError("Only pending proposals can be rejected")
    proposal["status"] = "rejected"
    proposal["rejected_at"] = _now()
    proposal["rejected_by"] = "demo.reviewer@datamaster.local"
    store.save_proposals(proposals)
    return proposal
