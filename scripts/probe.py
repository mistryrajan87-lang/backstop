#!/usr/bin/env python3
"""
Endpoint shape discovery for the CoinMarketCap RWA (Real World Assets) API.

Run this ONCE before building anything real. It hits each RWA endpoint with a
small page size, prints the HTTP status, the credit cost reported in `status`,
and a structural outline of the response so we build against the actual schema
rather than a guessed one.

Usage:
    CMC_API_KEY=xxxx python3 scripts/probe.py
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests

BASE = "https://pro-api.coinmarketcap.com"
KEY = os.environ.get("CMC_API_KEY", "").strip()

if not KEY:
    sys.exit("CMC_API_KEY is not set. Export it and re-run.")

SESSION = requests.Session()
SESSION.headers.update({"X-CMC_PRO_API_KEY": KEY, "Accept": "application/json"})

# (label, path, params)
ENDPOINTS = [
    ("RWA ID map",        "/v5/real-world-assets/map",                {"limit": 5}),
    ("RWA info",          "/v5/real-world-assets/info",               {}),          # needs id/symbol; filled in later
    ("RWA assets list",   "/v5/real-world-assets/assets/list",        {"limit": 5}),
    ("RWA quotes latest", "/v5/real-world-assets/quotes/latest",      {}),          # needs id; filled in later
    ("Issuers list",      "/v5/real-world-assets/issuers/list",       {"limit": 5}),
    ("Single issuer",     "/v5/real-world-assets/issuers",            {}),          # needs id; filled in later
    ("Market pairs",      "/v5/real-world-assets/market-pairs/list",  {}),          # Growth tier - expect 403
]


def outline(node: Any, depth: int = 0, max_depth: int = 4) -> str:
    """Render a compact structural outline of a JSON value."""
    pad = "  " * depth
    if depth > max_depth:
        return f"{pad}..."
    if isinstance(node, dict):
        lines = []
        for k, v in list(node.items())[:40]:
            if isinstance(v, (dict, list)):
                lines.append(f"{pad}{k}:")
                lines.append(outline(v, depth + 1, max_depth))
            else:
                lines.append(f"{pad}{k}: {type(v).__name__} = {json.dumps(v)[:80]}")
        return "\n".join(lines)
    if isinstance(node, list):
        if not node:
            return f"{pad}[] (empty)"
        return f"{pad}[{len(node)} items] first:\n" + outline(node[0], depth + 1, max_depth)
    return f"{pad}{type(node).__name__} = {json.dumps(node)[:80]}"


def call(label: str, path: str, params: dict) -> dict | None:
    url = BASE + path
    print("\n" + "=" * 78)
    print(f"{label}   GET {path}   params={params}")
    print("=" * 78)
    try:
        r = SESSION.get(url, params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"  REQUEST FAILED: {exc}")
        return None

    print(f"  HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        print(f"  non-JSON body: {r.text[:400]}")
        return None

    status = body.get("status", {})
    print(f"  status.error_code={status.get('error_code')} "
          f"error_message={status.get('error_message')!r} "
          f"credit_count={status.get('credit_count')} "
          f"elapsed={status.get('elapsed')}ms")

    data = body.get("data")
    if data is None:
        print("  (no data key)")
        return body
    print("  --- data outline ---")
    print(outline(data, depth=1))
    return body


def main() -> None:
    results: dict[str, Any] = {}

    # 1. map first, so we can pull a real rwa_id for the id-scoped endpoints.
    body = call("RWA ID map", "/v5/real-world-assets/map", {"limit": 5})
    results["map"] = body

    sample_id = None
    if body and isinstance(body.get("data"), list) and body["data"]:
        sample_id = body["data"][0].get("rwa_id") or body["data"][0].get("id")
    elif body and isinstance(body.get("data"), dict):
        # some CMC endpoints wrap the list, e.g. {"assets": [...]} - dig one level
        for v in body["data"].values():
            if isinstance(v, list) and v:
                sample_id = v[0].get("rwa_id") or v[0].get("id")
                break
    print(f"\n>>> sample rwa_id discovered: {sample_id}")

    results["assets_list"] = call(
        "RWA assets list", "/v5/real-world-assets/assets/list", {"limit": 5}
    )
    results["issuers_list"] = call(
        "Issuers list", "/v5/real-world-assets/issuers/list", {"limit": 5}
    )

    # pull a real issuer_id for the single-issuer call
    issuer_id = None
    il = results.get("issuers_list") or {}
    d = il.get("data")
    if isinstance(d, list) and d:
        issuer_id = d[0].get("issuer_id") or d[0].get("id")
    elif isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list) and v:
                issuer_id = v[0].get("issuer_id") or v[0].get("id")
                break
    print(f"\n>>> sample issuer_id discovered: {issuer_id}")

    if sample_id is not None:
        results["info"] = call(
            "RWA info", "/v5/real-world-assets/info", {"id": sample_id}
        )
        results["quotes"] = call(
            "RWA quotes latest", "/v5/real-world-assets/quotes/latest", {"id": sample_id}
        )
        results["market_pairs"] = call(
            "Market pairs (expect 403 on Startup)",
            "/v5/real-world-assets/market-pairs/list",
            {"id": sample_id},
        )
    if issuer_id is not None:
        results["issuer"] = call(
            "Single issuer", "/v5/real-world-assets/issuers", {"id": issuer_id}
        )

    out = os.path.join(os.path.dirname(__file__), "..", "probe_output.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nRaw responses written to {os.path.abspath(out)}")


if __name__ == "__main__":
    main()
