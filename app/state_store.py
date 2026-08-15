"""Small JSON-backed state store for the self-contained hackathon demo."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = PROJECT_ROOT / "state"


class StateStore:
    """Persist review state without introducing a database for a local demo."""

    def __init__(self, root: Path = DEFAULT_STATE_DIR) -> None:
        self.root = root
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _read(self, name: str) -> dict[str, Any]:
        with self._lock:
            return json.loads((self.root / name).read_text())

    def _write(self, name: str, value: dict[str, Any]) -> None:
        with self._lock:
            destination = self.root / name
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text(json.dumps(value, indent=2) + "\n")
            temporary.replace(destination)

    def registry(self) -> dict[str, Any]:
        return self._read("semantic_registry.json")

    def save_registry(self, value: dict[str, Any]) -> None:
        self._write("semantic_registry.json", value)

    def dependencies(self) -> dict[str, Any]:
        return self._read("dependencies.json")

    def proposals(self) -> dict[str, Any]:
        return self._read("proposals.json")

    def save_proposals(self, value: dict[str, Any]) -> None:
        self._write("proposals.json", value)

    def demo(self) -> dict[str, Any]:
        return self._read("demo.json")

    def save_demo(self, value: dict[str, Any]) -> None:
        self._write("demo.json", value)

    def reset(self, actor: str = "demo_operator") -> None:
        """Restore only DataMaster-owned demo files to a known initial state."""
        template_root = DEFAULT_STATE_DIR
        if self.root != template_root:
            shutil.copyfile(
                template_root / "initial_registry.json",
                self.root / "initial_registry.json",
            )
            shutil.copyfile(
                template_root / "dependencies.json",
                self.root / "dependencies.json",
            )
        shutil.copyfile(
            self.root / "initial_registry.json",
            self.root / "semantic_registry.json",
        )
        self.save_proposals({"onboarding": [], "changes": []})
        self.save_demo({"oss_version": 1, "last_reset_by": actor})
        if self.root == DEFAULT_STATE_DIR:
            for relative_path in (
                "generated/adapters/legacy_lms.json",
                "generated/openapi/legacy_lms.openapi.json",
                "generated/docs/legacy_lms.md",
                "generated/docs/oss.md",
            ):
                (PROJECT_ROOT / relative_path).unlink(missing_ok=True)


STORE = StateStore()
