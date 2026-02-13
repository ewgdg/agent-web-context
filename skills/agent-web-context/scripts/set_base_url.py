#!/usr/bin/env python3
"""
Persist the agent-web-context service base URL into references/service.json.

Usage:
  python3 set_base_url.py http://localhost:8000
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print(__doc__.strip())
        return 0

    base_url = argv[1].strip().rstrip("/")
    if not base_url:
        raise SystemExit("base_url must be non-empty")

    here = Path(__file__).resolve().parent
    references_dir = here.parent / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    service_path = references_dir / "service.json"
    payload = {}
    if service_path.exists():
        payload = _read_json(service_path) or {}
    if not isinstance(payload, dict):
        payload = {}

    payload["base_url"] = base_url
    payload.setdefault("example", "http://localhost:8000")
    payload.setdefault(
        "notes", "Set base_url to the root URL where /openapi.json is reachable."
    )

    service_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[ok] wrote {service_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
