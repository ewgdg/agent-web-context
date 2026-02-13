#!/usr/bin/env python3
"""
Fetch the service OpenAPI schema and split it into token-friendly files under:
  references/openapi_cache/

Outputs:
  - references/openapi_cache/meta.json
  - references/openapi_cache/index.json               (operation list)
  - references/openapi_cache/operations/*.json        (one file per operation)
  - references/openapi_cache/components/schemas/*.json (one file per component schema)

Usage:
  python3 refresh_openapi_cache.py
  python3 refresh_openapi_cache.py --base-url http://localhost:8000
  python3 refresh_openapi_cache.py --schema-url http://localhost:8000/openapi.json

Notes:
  - Uses ETag / If-None-Match when available to avoid re-downloading unchanged schemas.
  - Designed for *selective reading*: agents should read index.json, then only the
    specific operation file(s) they need (and referenced component schema files).
  - Does not write the full schema by default; pass --write-full to also write
    references/openapi_cache/openapi.json for debugging.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CACHE_FORMAT_VERSION = 1


@dataclass(frozen=True)
class OperationRef:
    operation_id: str | None
    method: str
    path: str
    summary: str | None
    description: str | None
    tags: list[str]
    filename: str


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _read_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _prune_json_files(directory: Path) -> None:
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        if path.is_file():
            path.unlink()


def _normalize_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _load_base_url_from_service_json(service_json_path: Path) -> str:
    payload = _read_json(service_json_path)
    if isinstance(payload, dict):
        base_url = str(payload.get("base_url", "")).strip()
        if base_url:
            return _normalize_base_url(base_url)
    return ""


def _sanitize_filename(name: str) -> str:
    # Keep it filesystem-safe and stable.
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or "operation"


def _compact_text(text: str | None, *, max_len: int) -> str | None:
    if not text:
        return None
    compacted = " ".join(text.split())
    if len(compacted) <= max_len:
        return compacted
    return compacted[: max(0, max_len - 3)] + "..."


def _operation_filename(method: str, path: str, operation_id: str | None) -> str:
    if operation_id:
        base = _sanitize_filename(operation_id)
    else:
        base = _sanitize_filename(f"{method.lower()}_{path}")
    if len(base) > 180:
        base = base[:180]
    return f"{base}.json"


def _dedupe_operation_filenames(ops: list[OperationRef]) -> list[OperationRef]:
    grouped: dict[str, list[OperationRef]] = {}
    for op in ops:
        grouped.setdefault(op.filename, []).append(op)

    out: list[OperationRef] = []
    used: set[str] = set()
    for filename, group in grouped.items():
        group_sorted = sorted(
            group,
            key=lambda o: (
                "" if o.operation_id is None else o.operation_id,
                o.method,
                o.path,
            ),
        )
        if len(group_sorted) == 1:
            op = group_sorted[0]
            out.append(op)
            used.add(op.filename)
            continue

        stem = filename[: -len(".json")] if filename.endswith(".json") else filename
        for idx, op in enumerate(group_sorted):
            if idx == 0 and op.filename not in used:
                out.append(op)
                used.add(op.filename)
                continue

            digest = hashlib.sha1(
                f"{op.method}|{op.path}|{op.operation_id or ''}".encode("utf-8"),
            ).hexdigest()[:8]
            candidate = f"{stem}_{digest}.json"
            collision_i = 2
            while candidate in used:
                candidate = f"{stem}_{digest}_{collision_i}.json"
                collision_i += 1

            out.append(
                OperationRef(
                    operation_id=op.operation_id,
                    method=op.method,
                    path=op.path,
                    summary=op.summary,
                    description=op.description,
                    tags=op.tags,
                    filename=candidate,
                )
            )
            used.add(candidate)

    return out


def _http_get_openapi_json(
    schema_url: str,
    *,
    timeout_s: float,
    etag: str | None,
) -> tuple[dict[str, Any], dict[str, str]]:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "agent-web-context-skill/refresh_openapi_cache",
    }
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(schema_url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            raw = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise SystemExit(f"Schema JSON must be an object: {schema_url}")
        return parsed, resp_headers
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise
        raise SystemExit(
            f"Failed to fetch schema from {schema_url}: HTTP {e.code}"
        ) from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Failed to fetch schema from {schema_url}: {e}") from e
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"Schema response was not valid JSON: {schema_url}: {e}"
        ) from e


def _iter_operations(
    schema: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any], dict[str, Any]]]:
    """
    Yield (path, method, op, path_item) for each operation in schema paths.
    """
    paths = schema.get("paths") if isinstance(schema.get("paths"), dict) else {}
    out: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if not isinstance(op, dict):
                continue
            if method.lower() not in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "options",
                "head",
            }:
                continue
            out.append((str(path), method.upper(), op, path_item))
    return out


def _compact_index(schema: dict[str, Any]) -> tuple[dict[str, Any], list[OperationRef]]:
    info = schema.get("info") if isinstance(schema.get("info"), dict) else {}
    title = info.get("title")
    version = info.get("version")

    ops: list[OperationRef] = []
    for path, method, op, _path_item in _iter_operations(schema):
        operation_id = op.get("operationId")
        summary = op.get("summary")
        description = op.get("description")
        tags = op.get("tags") if isinstance(op.get("tags"), list) else []
        filename = _operation_filename(
            method, path, operation_id if isinstance(operation_id, str) else None
        )
        ops.append(
            OperationRef(
                operation_id=operation_id if isinstance(operation_id, str) else None,
                method=method,
                path=path,
                summary=summary if isinstance(summary, str) else None,
                description=description if isinstance(description, str) else None,
                tags=[str(t) for t in tags if t is not None],
                filename=filename,
            )
        )

    ops_unique = _dedupe_operation_filenames(ops)
    ops_sorted = sorted(
        ops_unique,
        key=lambda o: (
            "" if o.operation_id is None else o.operation_id,
            o.method,
            o.path,
        ),
    )

    index = {
        "format_version": CACHE_FORMAT_VERSION,
        "fetched_at": _iso_now(),
        "info": {"title": title, "version": version},
        "operations": [
            {
                "id": o.operation_id,
                "m": o.method,
                "p": o.path,
                "s": _compact_text(o.summary or o.description, max_len=160),
                "file": f"operations/{o.filename}",
                **({"tags": o.tags} if o.tags else {}),
            }
            for o in ops_sorted
        ],
    }
    return index, ops_sorted


def _write_split_files(
    *,
    cache_dir: Path,
    schema: dict[str, Any],
    ops: list[OperationRef],
    base_url: str,
    schema_url: str,
    etag: str | None,
    last_modified: str | None,
    write_full_schema: bool,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    operations_dir = cache_dir / "operations"
    schemas_dir = cache_dir / "components" / "schemas"
    operations_dir.mkdir(parents=True, exist_ok=True)
    schemas_dir.mkdir(parents=True, exist_ok=True)
    # Prevent stale generated artifacts when operations/components are removed or renamed.
    _prune_json_files(operations_dir)
    _prune_json_files(schemas_dir)

    if write_full_schema:
        _write_json(cache_dir / "openapi.json", schema)

    index, _ = _compact_index(schema)
    index["base_url"] = base_url
    index["schema_url"] = schema_url
    _write_json(cache_dir / "index.json", index)

    meta = {
        "format_version": CACHE_FORMAT_VERSION,
        "checked_at": _iso_now(),
        "base_url": base_url,
        "schema_url": schema_url,
        "etag": etag,
        "last_modified": last_modified,
        "operation_count": len(index.get("operations", [])),
        "components_schema_count": 0,
    }

    # One file per operation (include path-level parameters too).
    path_items = schema.get("paths") if isinstance(schema.get("paths"), dict) else {}
    for op_ref in ops:
        path_item = path_items.get(op_ref.path)
        if not isinstance(path_item, dict):
            continue
        op_obj = path_item.get(op_ref.method.lower())
        if not isinstance(op_obj, dict):
            continue

        combined_parameters: list[object] = []
        if isinstance(path_item.get("parameters"), list):
            combined_parameters.extend(path_item["parameters"])
        if isinstance(op_obj.get("parameters"), list):
            combined_parameters.extend(op_obj["parameters"])

        op_payload = {
            "id": op_ref.operation_id,
            "m": op_ref.method,
            "p": op_ref.path,
            "s": _compact_text(op_ref.summary, max_len=200),
            "d": _compact_text(op_ref.description, max_len=400),
            "tags": op_ref.tags,
            "parameters": combined_parameters,
            "requestBody": op_obj.get("requestBody"),
            "responses": op_obj.get("responses"),
            "security": op_obj.get("security"),
            "deprecated": op_obj.get("deprecated"),
        }
        _write_json(operations_dir / op_ref.filename, op_payload)

    # Split component schemas for selective reading.
    components = (
        schema.get("components") if isinstance(schema.get("components"), dict) else {}
    )
    schemas = (
        components.get("schemas") if isinstance(components.get("schemas"), dict) else {}
    )
    if isinstance(schemas, dict):
        for name, schema_obj in schemas.items():
            if not isinstance(name, str):
                continue
            _write_json(schemas_dir / f"{_sanitize_filename(name)}.json", schema_obj)
        meta["components_schema_count"] = len(schemas)

    _write_json(cache_dir / "meta.json", meta)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--base-url",
        default=None,
        help="Service base URL (e.g., http://localhost:8000). Defaults to references/service.json base_url.",
    )
    p.add_argument(
        "--schema-url",
        default=None,
        help="Explicit OpenAPI schema URL (overrides --base-url).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10).",
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Remove the existing cache directory before writing.",
    )
    p.add_argument(
        "--write-full",
        action="store_true",
        help="Also write references/openapi_cache/openapi.json (full schema) for debugging.",
    )
    args = p.parse_args(argv[1:])

    here = Path(__file__).resolve().parent
    references_dir = here.parent / "references"
    service_json_path = references_dir / "service.json"
    cache_dir = references_dir / "openapi_cache"

    base_url = _normalize_base_url(args.base_url) if args.base_url else ""
    if not base_url:
        base_url = _load_base_url_from_service_json(service_json_path)

    schema_url = str(args.schema_url).strip() if args.schema_url else ""
    if not schema_url:
        if not base_url:
            raise SystemExit(
                "Missing base_url. Set references/service.json base_url, or pass --base-url."
            )
        schema_url = f"{base_url}/openapi.json"

    previous_meta = _read_json(cache_dir / "meta.json")
    etag: str | None = None
    if isinstance(previous_meta, dict):
        prev_etag = previous_meta.get("etag")
        if isinstance(prev_etag, str) and prev_etag.strip():
            etag = prev_etag.strip()

    try:
        schema, headers = _http_get_openapi_json(
            schema_url, timeout_s=float(args.timeout), etag=etag
        )
    except urllib.error.HTTPError as e:
        if e.code != 304:
            raise
        if not (cache_dir / "index.json").exists():
            raise SystemExit(
                f"Schema not modified (ETag matched), but no cache exists at {cache_dir}"
            ) from e
        meta = previous_meta if isinstance(previous_meta, dict) else {}
        meta = {
            **meta,
            "checked_at": _iso_now(),
            "base_url": base_url,
            "schema_url": schema_url,
        }
        _write_json(cache_dir / "meta.json", meta)
        print(f"[ok] schema unchanged (304); cache is up to date: {cache_dir}")
        return 0

    new_etag = headers.get("etag")
    last_modified = headers.get("last-modified")

    index, ops = _compact_index(schema)
    if args.clean and cache_dir.exists():
        shutil.rmtree(cache_dir)
    _write_split_files(
        cache_dir=cache_dir,
        schema=schema,
        ops=ops,
        base_url=base_url,
        schema_url=schema_url,
        etag=new_etag,
        last_modified=last_modified,
        write_full_schema=bool(args.write_full),
    )

    print(f"[ok] wrote cache: {cache_dir}")
    print(f"[ok] operations: {len(index.get('operations', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
