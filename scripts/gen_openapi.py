#!/usr/bin/env python3
"""
Generate docs/api/openapi.json from the FastAPI app.

Determinism is the whole point. This file is committed, and CI fails a PR
when the committed copy differs from a fresh generation. Any run-to-run
instability — key order, timestamps, a floating dependency version — would
turn that check into permanent false failures nobody can fix. Hence
sort_keys, and exact pins on fastapi/pydantic in requirements.txt.

Usage:
    python scripts/gen_openapi.py              # write the spec
    python scripts/gen_openapi.py --check      # exit 1 if stale (what CI runs)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Importing app.main must not require ANTHROPIC_API_KEY — agent.py validates
# the key lazily in require_api_key() precisely so this runs in CI.

from app.main import app  # noqa: E402

OUT = ROOT / "docs" / "api" / "openapi.json"

BANNER = (
    "GENERATED FILE — do not edit by hand. Produced by scripts/gen_openapi.py "
    "from the route definitions in app/main.py."
)


def render() -> str:
    spec = app.openapi()
    spec["info"]["x-generated-by"] = BANNER
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify without writing")
    args = ap.parse_args()

    fresh = render()

    if args.check:
        if not OUT.exists():
            print(f"error: {OUT.relative_to(ROOT)} is missing.")
            print("       Run: python scripts/gen_openapi.py")
            return 1
        if OUT.read_text() != fresh:
            print(f"error: {OUT.relative_to(ROOT)} is out of date.")
            print("       Run: python scripts/gen_openapi.py  — then commit the result.")
            return 1
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(fresh)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(json.loads(fresh)['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
