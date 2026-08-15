"""Deterministic acceptance tests for the complete 4–5 minute judge path."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import httpx

from app.change_manager import approve_change, detect_oss_v2_change
from app.contract_diff import diff_openapi, load_contract
from app.demo_resident_records import register_demo_resident
from app.github_webhook import parse_oss_contract_event, verify_signature
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
    RepositoryScanError,
    analyze_repository_with_openai,
    collect_repository_evidence,
    connect_repository,
    normalize_repository_url,
)
from app.resolver import ResolutionError, resolve
from app.state_store import DEFAULT_STATE_DIR, PROJECT_ROOT, StateStore

SUBJECT = {"name": "John Doe", "phone": "+62838292938"}
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


class DataMasterDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="datamaster-tests-")
        self.root = Path(self.temporary.name) / "state"
        self.root.mkdir()
        shutil.copyfile(
            DEFAULT_STATE_DIR / "initial_registry.json", self.root / "initial_registry.json"
        )
        shutil.copyfile(
            DEFAULT_STATE_DIR / "initial_registry.json", self.root / "semantic_registry.json"
        )
        shutil.copyfile(
            DEFAULT_STATE_DIR / "dependencies.json", self.root / "dependencies.json"
        )
        (self.root / "proposals.json").write_text(
            json.dumps({"onboarding": [], "changes": []}, indent=2) + "\n"
        )
        (self.root / "demo.json").write_text(
            json.dumps({"oss_version": 1, "last_reset_by": "unittest"}, indent=2) + "\n"
        )
        self.store = StateStore(self.root)
        self.generated = Path(self.temporary.name) / "generated"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def requester(self, method: str, url: str, params: dict):
        self.assertEqual(method, "GET")
        return government_response(urlparse(url).path, params, store=self.store)

    def initial_resolution(self):
        return resolve(
            SUBJECT,
            PERMIT_FIELDS,
            PERMIT_PURPOSE,
            store=self.store,
            requester=self.requester,
        )

    def pending_change(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            return detect_oss_v2_change("unittest", store=self.store)

    def test_01_permit_retrieves_repeated_records(self):
        result = self.initial_resolution()
        self.assertEqual(result["data"]["person.nik"], "2171012507890001")
        self.assertEqual(result["data"]["business.nib"], "1209260012345")
        self.assertEqual(result["data"]["business.npwp"], "09.254.294.3-217.000")
        self.assertEqual(
            result["data"]["business.company_deed"], "AHU-0045821.AH.01.01.2026"
        )
        self.assertEqual(
            result["data"]["bpbatam.land_record"],
            "Kabil Industrial Estate Blok C-4 (2,400 m²)",
        )

    def test_02_health_is_forbidden_before_any_call(self):
        executed = []

        def forbidden_requester(method, url, params):
            executed.append((method, url, params))
            return 500, {"error": "must not run"}

        with self.assertRaises(PolicyDenied):
            resolve(
                SUBJECT,
                ["health.blood_type"],
                PERMIT_PURPOSE,
                store=self.store,
                requester=forbidden_requester,
            )
        self.assertEqual(executed, [])
        self.assertNotIn("satusehat", self.initial_resolution()["called_services"])

    def test_03_new_application_documents_stay_manual(self):
        result = self.initial_resolution()
        for concept in (
            "application.warehouse_evidence",
            "application.warehouse_plan",
            "application.logistics_purpose",
        ):
            self.assertIsNone(result["data"][concept])
            self.assertEqual(result["provenance"][concept]["status"], "manual_required")

    def test_04_legacy_onboarding_is_human_gated(self):
        proposal = scan_legacy_lms(store=self.store)
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(
            proposal["scan"]["selected_operation"]["name"], "find_land_record"
        )
        self.assertNotIn("legacy_lms", self.store.registry()["services"])
        with self.assertRaises(OnboardingError):
            query_legacy_lms("1209260012345", store=self.store)

        approved = approve_onboarding(
            proposal["id"], store=self.store, generated_root=self.generated
        )
        self.assertEqual(approved["status"], "applied")
        self.assertEqual(
            query_legacy_lms("1209260012345", store=self.store)["data"][
                "bpbatam.legacy_land_record"
            ],
            "Kabil Industrial Estate Blok C-4 (2,400 m²)",
        )

    def test_05_openapi_diff_is_deterministic(self):
        contract_root = PROJECT_ROOT / "contracts"
        result = diff_openapi(
            load_contract(contract_root / "oss-v1.openapi.json"),
            load_contract(contract_root / "oss-v2.openapi.json"),
        )
        self.assertIn({"method": "GET", "path": "/oss/getNIB"}, result["removed_paths"])
        self.assertIn(
            {"method": "GET", "path": "/oss/business-by-director"},
            result["added_paths"],
        )
        self.assertIn("nib", result["removed_response_fields"])
        self.assertIn("business_identification_number", result["added_response_fields"])

    def test_06_semantic_fallback_proposes_nib_mapping(self):
        proposal = self.pending_change()
        mapping = next(
            item for item in proposal["mappings"] if item["concept"] == "business.nib"
        )
        self.assertEqual(mapping["new_field"], "business_identification_number")
        self.assertEqual(proposal["analysis"]["mode"], "deterministic_fallback")

    def test_07_change_is_never_applied_automatically(self):
        proposal = self.pending_change()
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(self.store.demo()["oss_version"], 2)
        self.assertEqual(
            self.store.registry()["concepts"]["business.nib"]["response_path"], "nib"
        )
        with self.assertRaises(ResolutionError):
            self.initial_resolution()

    def test_08_approval_updates_adapter_only_after_tests(self):
        proposal = self.pending_change()
        approved = approve_change(
            proposal["id"], store=self.store, generated_root=self.generated
        )
        self.assertEqual(approved["status"], "applied")
        self.assertTrue(all(item["passed"] for item in approved["tests"]))
        self.assertEqual(self.initial_resolution()["data"]["business.nib"], "1209260012345")
        self.assertTrue((self.generated / "docs" / "oss.md").exists())

    def test_09_downstream_request_is_unchanged_across_versions(self):
        before = self.initial_resolution()
        proposal = self.pending_change()
        approve_change(proposal["id"], store=self.store, generated_root=self.generated)
        after = self.initial_resolution()
        self.assertEqual(list(before["data"]), list(after["data"]))
        self.assertEqual(before["data"]["business.nib"], after["data"]["business.nib"])

    def test_10_reused_fields_have_provenance(self):
        result = self.initial_resolution()
        for concept in PERMIT_FIELDS:
            if concept.startswith("application."):
                continue
            self.assertTrue(result["provenance"][concept]["source"])
            self.assertTrue(result["provenance"][concept]["verified"])

    def test_11_github_signature_and_event_are_verified(self):
        body = json.dumps(
            {
                "after": "demo-oss-v2",
                "repository": {"full_name": "demo-government/oss-api"},
            }
        ).encode()
        secret = "unit-test-secret"
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        with patch.dict("os.environ", {"GITHUB_WEBHOOK_SECRET": secret}):
            verify_signature(body, signature)
        event = parse_oss_contract_event(body, "push")
        self.assertEqual(event["commit"], "demo-oss-v2")

    def test_12_repository_connection_documents_bp_batam_apis(self):
        self.assertEqual(
            self.store.registry()["services"]["bpbatam"]["documentation_status"],
            "undocumented",
        )
        repository = normalize_repository_url(
            "https://github.com/alexgeraldhandoko/bp-batam.git"
        )
        snapshot = collect_repository_evidence(TEST_REPOSITORY_ROOT)
        documented = {
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
                    ("GET", "/api/v1/permit-validations", "List permit validations."),
                    ("POST", "/api/v1/permit-validations", "Create a permit validation."),
                )
            ],
        }

        def openai_response(request: httpx.Request) -> httpx.Response:
            request_json = json.loads(request.content)
            self.assertEqual(request_json["text"]["format"]["type"], "json_schema")
            self.assertTrue(request_json["text"]["format"]["strict"])
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps(documented)}
                            ],
                        }
                    ],
                },
            )

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "unit-test-key"}),
            httpx.Client(transport=httpx.MockTransport(openai_response)) as client,
        ):
            documentation, analysis = analyze_repository_with_openai(
                snapshot, repository, client=client
            )

        result = connect_repository(
            repository.canonical_url,
            store=self.store,
            repository_root=TEST_REPOSITORY_ROOT,
            analyzer=lambda _snapshot, _repository: (documentation, analysis),
        )
        service = self.store.registry()["services"][result["service_key"]]
        self.assertEqual(result["status"], "connected")
        self.assertEqual(service["name"], "BP Batam")
        self.assertEqual(service["documentation_status"], "generated_from_repository")
        self.assertEqual(service["operations"], 4)
        self.assertIn(
            {"method": "POST", "endpoint": "/api/v1/permit-validations"},
            [
                {"method": item["method"], "endpoint": item["endpoint"]}
                for item in service["apis"]
            ],
        )
        self.assertEqual(service["analysis_mode"], "openai_structured_outputs")
        self.assertEqual(service["source_repository"], repository.canonical_url)
        self.assertEqual(
            self.store.registry()["concepts"]["bpbatam.land_record"]["authoritative_service"],
            "bpbatam",
        )
        with self.assertRaises(RepositoryScanError):
            normalize_repository_url("https://example.com/owner/repository")

    def test_13_disdukcapil_registration_unlocks_permit_sections_a_and_b(self):
        subject = {"name": "Ayu Lestari", "phone": "+6281212345678"}
        fields = [
            "person.nik",
            "person.date_of_birth",
            "person.registered_address",
            "person.email",
            "business.npwp",
            "business.company_name",
            "business.nib",
            "business.company_deed",
            "business.sk_kemenkumham",
            "business.notary",
            "business.risk_level",
            "business.kbli",
        ]

        with self.assertRaises(ResolutionError):
            resolve(
                subject,
                fields,
                PERMIT_PURPOSE,
                store=self.store,
                requester=self.requester,
            )

        register_demo_resident(
            {
                "name": subject["name"],
                "phone": subject["phone"],
                "nik": "2171024504900004",
                "dob": "1990-04-05",
                "registered_address": "Jl. Raja Haji Fisabilillah No. 18, Batam Center",
                "email": "ayu.lestari@samudramaju.co.id",
                "npwp": "73.456.789.0-217.000",
                "company_name": "PT Samudra Maju Logistik",
                "nib": "1308260098765",
                "deed_number": "AHU-0067421.AH.01.01.2026",
                "sk_kemenkumham": "AHU-0067421.AH.01.01.TAHUN 2026",
                "notary": "Dewi Anggraini, S.H., M.Kn. (Batam)",
                "risk_level": "Medium-Low",
                "kbli": ["52101 — Warehousing", "52291 — Freight forwarding"],
            },
            store=self.store,
        )

        result = resolve(
            subject,
            fields,
            PERMIT_PURPOSE,
            store=self.store,
            requester=self.requester,
        )

        self.assertEqual(result["called_services"], ["dukcapil", "oss", "djp", "ahu"])
        self.assertEqual(result["data"]["person.nik"], "2171024504900004")
        self.assertEqual(result["data"]["person.email"], "ayu.lestari@samudramaju.co.id")
        self.assertEqual(result["data"]["business.nib"], "1308260098765")
        self.assertEqual(
            result["data"]["business.kbli"],
            ["52101 — Warehousing", "52291 — Freight forwarding"],
        )

        self.store.reset("unittest")
        with self.assertRaises(ResolutionError):
            resolve(
                subject,
                fields,
                PERMIT_PURPOSE,
                store=self.store,
                requester=self.requester,
            )


if __name__ == "__main__":
    unittest.main()
