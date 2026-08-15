"""Focused tests for polling and automatically applied documentation changes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.repository_monitor import RepositoryMonitor
from app.repository_scanner import (
    TEST_REPOSITORY_ROOT,
    ServiceDocumentation,
    analyze_repository_with_openai,
    collect_repository_evidence,
    connect_repository,
    normalize_repository_url,
)
from app.state_store import StateStore

OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40
REPOSITORY_URL = "https://github.com/example/residential-api"


def _documentation(endpoint: str) -> ServiceDocumentation:
    return ServiceDocumentation.model_validate(
        {
            "name": "Residential API",
            "hostname": "https://api.example.test",
            "description": "Residential-community operations.",
            "apis": [
                {
                    "method": "GET",
                    "endpoint": endpoint,
                    "description": "List visitor passes.",
                    "source_files": ["src/bp_batam_api/main.py"],
                }
            ],
        }
    )


class RepositoryMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="repository-monitor-tests-")
        self.store = StateStore(Path(self.temporary.name) / "state")
        self.store.save_registry(
            {
                "revision": 1,
                "services": {
                    "residential_api": {
                        "name": "Residential API",
                        "hostname": "https://api.example.test",
                        "description": "Residential-community operations.",
                        "status": "active",
                        "contract": "AI-generated repository documentation",
                        "operations": 1,
                        "apis": [
                            {
                                "method": "GET",
                                "endpoint": "/visitors",
                                "description": "List visitor passes.",
                                "source_files": ["src/bp_batam_api/main.py"],
                            }
                        ],
                        "documentation_status": "generated_from_repository",
                        "source_repository": REPOSITORY_URL,
                        "repository_commit": OLD_COMMIT,
                        "concepts": ["visitor.pass"],
                        "dependents": ["resident_app"],
                    }
                },
                "concepts": {},
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_unchanged_commit_does_not_diff_or_scan(self) -> None:
        monitor = RepositoryMonitor(
            store=self.store,
            remote_commit_lookup=lambda _url: OLD_COMMIT,
            diff_inspector=lambda *_args: self.fail("unchanged commit must not be diffed"),
            scanner=lambda *_args, **_kwargs: self.fail("unchanged commit must not be scanned"),
        )

        status = monitor.check_once()

        self.assertEqual(status["repositories"][0]["status"], "unchanged")
        self.assertEqual(status["repositories"][0]["remote_commit"], OLD_COMMIT)

    def test_forced_inspection_scans_even_when_commit_is_unchanged(self) -> None:
        observed: dict = {}

        def scanner(repository_url: str, **kwargs):
            observed.update({"repository_url": repository_url, **kwargs})
            return {"registry_revision": 2}

        monitor = RepositoryMonitor(
            store=self.store,
            remote_commit_lookup=lambda _url: OLD_COMMIT,
            diff_inspector=lambda *_args: self.fail(
                "a forced inspection of the same commit does not need a Git diff"
            ),
            scanner=scanner,
        )

        status = monitor.check_once(force=True)

        self.assertEqual(observed["repository_url"], REPOSITORY_URL)
        self.assertEqual(observed["target_commit"], OLD_COMMIT)
        self.assertIs(observed["store"], self.store)
        self.assertEqual(observed["change_context"]["before_commit"], OLD_COMMIT)
        self.assertEqual(observed["change_context"]["after_commit"], OLD_COMMIT)
        self.assertIn("complete documentation inspection", observed["change_context"]["patch"])
        self.assertEqual(status["repositories"][0]["status"], "updated")
        self.assertEqual(
            status["repositories"][0]["inspection_reason"],
            "manual_forced_inspection",
        )

    def test_changed_commit_passes_diff_to_scanner_and_updates_status(self) -> None:
        observed: dict = {}
        change_context = {
            "before_commit": OLD_COMMIT,
            "after_commit": NEW_COMMIT,
            "changed_files": ["src/bp_batam_api/main.py"],
            "omitted_files": 0,
            "patch": "+@app.get('/visitor-passes')",
            "truncated": False,
        }

        def scanner(repository_url: str, **kwargs):
            observed.update({"repository_url": repository_url, **kwargs})
            registry = self.store.registry()
            registry["revision"] += 1
            registry["services"]["residential_api"]["repository_commit"] = NEW_COMMIT
            self.store.save_registry(registry)
            return {"registry_revision": registry["revision"]}

        monitor = RepositoryMonitor(
            store=self.store,
            remote_commit_lookup=lambda _url: NEW_COMMIT,
            diff_inspector=lambda _url, _before, _after: change_context,
            scanner=scanner,
        )

        status = monitor.check_once()

        self.assertEqual(observed["repository_url"], REPOSITORY_URL)
        self.assertEqual(observed["target_commit"], NEW_COMMIT)
        self.assertEqual(observed["change_context"], change_context)
        self.assertIs(observed["store"], self.store)
        self.assertEqual(status["repositories"][0]["status"], "updated")
        self.assertEqual(status["repositories"][0]["changed_files"], change_context["changed_files"])

    def test_openai_change_proposal_receives_previous_docs_and_bounded_diff(self) -> None:
        repository = normalize_repository_url(REPOSITORY_URL)
        snapshot = collect_repository_evidence(TEST_REPOSITORY_ROOT)
        proposed = _documentation("/visitor-passes")

        def openai_response(request: httpx.Request) -> httpx.Response:
            request_json = json.loads(request.content)
            self.assertIn("<repository_diff>", request_json["input"])
            self.assertIn("+@app.get('/visitor-passes')", request_json["input"])
            self.assertIn("Previous documentation", request_json["input"])
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": proposed.model_dump_json(),
                                }
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
                snapshot,
                repository,
                client=client,
                change_context={
                    "before_commit": OLD_COMMIT,
                    "after_commit": NEW_COMMIT,
                    "previous_documentation": {"apis": []},
                    "patch": "+@app.get('/visitor-passes')",
                },
            )

        self.assertEqual(documentation.apis[0].endpoint, "/visitor-passes")
        self.assertEqual(analysis["purpose"], "repository_documentation_change_proposal")

    def test_validated_proposal_is_applied_and_records_ui_summary(self) -> None:
        change_context = {
            "before_commit": OLD_COMMIT,
            "after_commit": NEW_COMMIT,
            "changed_files": ["src/bp_batam_api/main.py"],
            "omitted_files": 0,
            "patch": "+@app.get('/visitor-passes')",
            "truncated": False,
        }
        proposed = _documentation("/visitor-passes")

        result = connect_repository(
            REPOSITORY_URL,
            store=self.store,
            repository_root=TEST_REPOSITORY_ROOT,
            analyzer=lambda _snapshot, _repository: (
                proposed,
                {
                    "mode": "openai_structured_outputs",
                    "model": "test-double",
                    "purpose": "repository_documentation_change_proposal",
                },
            ),
            target_commit=NEW_COMMIT,
            change_context=change_context,
        )

        service = self.store.registry()["services"]["residential_api"]
        change = service["last_documentation_change"]
        self.assertEqual(result["status"], "updated")
        self.assertEqual(service["repository_commit"], NEW_COMMIT)
        self.assertEqual(service["concepts"], ["visitor.pass"])
        self.assertEqual(change["status"], "applied")
        self.assertEqual(
            change["added_operations"],
            [{"method": "GET", "endpoint": "/visitor-passes"}],
        )
        self.assertEqual(
            change["removed_operations"],
            [{"method": "GET", "endpoint": "/visitors"}],
        )


if __name__ == "__main__":
    unittest.main()
