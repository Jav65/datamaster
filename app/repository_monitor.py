"""In-process polling for documentation generated from public repositories.

The monitor is deliberately small and single-process for the local DataMaster
prototype. It checks public GitHub commit SHAs every twenty seconds, serializes
scans, and never executes repository code.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.repository_scanner import (
    connect_repository,
    inspect_repository_diff,
    remote_repository_commit,
)
from app.state_store import STORE, StateStore

DEFAULT_MONITOR_INTERVAL_SECONDS = 20

RemoteCommitLookup = Callable[[str], str]
DiffInspector = Callable[[str, str, str], dict[str, Any]]
RepositoryScanner = Callable[..., dict[str, Any]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class RepositoryMonitor:
    """Own one non-overlapping polling loop for all repository-backed services."""

    def __init__(
        self,
        *,
        store: StateStore = STORE,
        interval_seconds: int = DEFAULT_MONITOR_INTERVAL_SECONDS,
        remote_commit_lookup: RemoteCommitLookup = remote_repository_commit,
        diff_inspector: DiffInspector = inspect_repository_diff,
        scanner: RepositoryScanner = connect_repository,
    ) -> None:
        if interval_seconds < 1:
            raise ValueError("Repository monitor interval must be at least one second")
        self.store = store
        self.interval_seconds = interval_seconds
        self._remote_commit_lookup = remote_commit_lookup
        self._diff_inspector = diff_inspector
        self._scanner = scanner
        self._state_lock = threading.RLock()
        self._check_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._enabled = False
        self._checking = False
        self._last_checked_at: str | None = None
        self._next_check_at: str | None = None
        self._force_requested = False
        self._repositories: dict[str, dict[str, Any]] = {}

    def start(self) -> dict[str, Any]:
        """Start the always-on polling thread if it is not already running."""
        with self._state_lock:
            self._enabled = True
            self._wake_event.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="datamaster-repository-monitor",
                    daemon=True,
                )
                self._thread.start()
        return self.status()

    def request_inspection(self) -> dict[str, Any]:
        """Queue one forced inspection, even when repository SHAs are unchanged."""

        with self._state_lock:
            self._force_requested = True
            self._enabled = True
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run,
                    name="datamaster-repository-monitor",
                    daemon=True,
                )
                self._thread.start()
            self._wake_event.set()
        return self.status()

    def close(self) -> None:
        """Stop future checks when the FastAPI process shuts down."""

        with self._state_lock:
            self._enabled = False
            self._next_check_at = None
            thread = self._thread
            self._wake_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _run(self) -> None:
        while True:
            with self._state_lock:
                if not self._enabled:
                    return
                force = self._force_requested
                self._force_requested = False
                if force:
                    self._wake_event.clear()
            self.check_once(force=force)
            with self._state_lock:
                if not self._enabled:
                    return
                self._next_check_at = _timestamp(
                    _now() + timedelta(seconds=self.interval_seconds)
                )
            if self._wake_event.wait(self.interval_seconds):
                with self._state_lock:
                    if not self._enabled:
                        return
                    self._wake_event.clear()

    def _targets(self) -> list[tuple[str, dict[str, Any]]]:
        registry = self.store.registry()
        return [
            (service_key, service)
            for service_key, service in registry["services"].items()
            if service.get("documentation_status") == "generated_from_repository"
            and service.get("source_repository")
        ]

    def _record(self, key: str, **values: Any) -> None:
        with self._state_lock:
            current = self._repositories.get(key, {})
            self._repositories[key] = {**current, **values}

    def check_once(self, *, force: bool = False) -> dict[str, Any]:
        """Check every connected repository once; concurrent calls reuse current status."""

        if not self._check_lock.acquire(blocking=False):
            return self.status()
        try:
            with self._state_lock:
                self._checking = True
                self._next_check_at = None
            targets = self._targets()
            target_keys = {service_key for service_key, _service in targets}
            with self._state_lock:
                self._repositories = {
                    key: value
                    for key, value in self._repositories.items()
                    if key in target_keys
                }

            for service_key, service in targets:
                repository_url = service["source_repository"]
                tracked_commit = service.get("repository_commit")
                checked_at = _timestamp()
                self._record(
                    service_key,
                    service_key=service_key,
                    name=service.get("name", service_key),
                    repository=repository_url,
                    tracked_commit=tracked_commit,
                    status="checking",
                    error=None,
                )
                try:
                    remote_commit = self._remote_commit_lookup(repository_url)
                    if tracked_commit == remote_commit and not force:
                        self._record(
                            service_key,
                            status="unchanged",
                            inspection_reason="scheduled_sha_check",
                            remote_commit=remote_commit,
                            checked_at=checked_at,
                        )
                        continue

                    if force and tracked_commit == remote_commit:
                        change_context = {
                            "before_commit": tracked_commit,
                            "after_commit": remote_commit,
                            "changed_files": [],
                            "omitted_files": 0,
                            "patch": (
                                "A user requested a complete documentation inspection. The "
                                "repository commit SHA is unchanged, so inspect the complete "
                                "current repository snapshot and previous documentation."
                            ),
                            "truncated": False,
                        }
                    elif tracked_commit:
                        change_context = self._diff_inspector(
                            repository_url, tracked_commit, remote_commit
                        )
                    else:
                        change_context = {
                            "before_commit": None,
                            "after_commit": remote_commit,
                            "changed_files": [],
                            "omitted_files": 0,
                            "patch": (
                                "No previous commit was recorded. Inspect the complete current "
                                "repository snapshot as the proposed documentation source."
                            ),
                            "truncated": False,
                        }
                    result = self._scanner(
                        repository_url,
                        store=self.store,
                        target_commit=remote_commit,
                        change_context=change_context,
                    )
                    self._record(
                        service_key,
                        status="updated",
                        inspection_reason=(
                            "manual_forced_inspection" if force else "commit_sha_changed"
                        ),
                        tracked_commit=remote_commit,
                        remote_commit=remote_commit,
                        checked_at=checked_at,
                        updated_at=_timestamp(),
                        changed_files=change_context.get("changed_files", []),
                        registry_revision=result.get("registry_revision"),
                    )
                except Exception as exc:
                    self._record(
                        service_key,
                        status="error",
                        checked_at=checked_at,
                        error=str(exc)[:500],
                    )

            with self._state_lock:
                self._last_checked_at = _timestamp()
        finally:
            with self._state_lock:
                self._checking = False
            self._check_lock.release()
        return self.status()

    def status(self) -> dict[str, Any]:
        targets = self._targets()
        target_keys = {service_key for service_key, _service in targets}
        with self._state_lock:
            return {
                "enabled": self._enabled,
                "checking": self._checking,
                "interval_seconds": self.interval_seconds,
                "force_requested": self._force_requested,
                "monitored_repository_count": len(targets),
                "last_checked_at": self._last_checked_at,
                "next_check_at": self._next_check_at,
                "repositories": [
                    dict(self._repositories[key])
                    for key in sorted(self._repositories)
                    if key in target_keys
                ],
            }


REPOSITORY_MONITOR = RepositoryMonitor()
