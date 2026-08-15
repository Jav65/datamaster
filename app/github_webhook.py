"""Small GitHub-App-style webhook boundary for the offline demo."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any


class WebhookError(ValueError):
    """Raised when a webhook cannot be trusted or does not describe our demo event."""


def verify_signature(body: bytes, signature: str | None) -> None:
    """Verify GitHub's ``sha256=...`` signature when a secret is configured."""
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        return
    if not signature or not signature.startswith("sha256="):
        raise WebhookError("Missing GitHub webhook signature")
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookError("Invalid GitHub webhook signature")


def parse_oss_contract_event(body: bytes, event_name: str | None) -> dict[str, Any]:
    """Accept a narrow push/pull-request payload representing the OSS v2 merge."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WebhookError("Webhook body must be valid JSON") from exc

    if event_name not in {"push", "pull_request"}:
        raise WebhookError("Only GitHub push and pull_request events are supported")

    repository = payload.get("repository", {}).get("full_name")
    if repository != "demo-government/oss-api":
        raise WebhookError("Webhook repository is not the configured OSS contract source")

    if event_name == "pull_request":
        action = payload.get("action")
        merged = payload.get("pull_request", {}).get("merged") is True
        if action != "closed" or not merged:
            raise WebhookError("Pull request event was not a completed merge")

    commit = (
        payload.get("after")
        or payload.get("pull_request", {}).get("merge_commit_sha")
        or "demo-oss-v2"
    )
    return {"repository": repository, "commit": commit, "event": event_name}
