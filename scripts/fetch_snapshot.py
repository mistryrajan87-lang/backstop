#!/usr/bin/env python3
"""
Backstop - issuer concentration in the tokenised real-world-asset market.

THE QUESTION
------------
CoinMarketCap's RWA map lists 7,811 rows, but only 790 of them carry tokens. Those
790 hold $7.42bn of reported cap and are what Backstop measures. A tokenised asset
is not one thing: "Gold" is a single row with a single market cap, and behind it
sit seven tokens minted by six different issuers - Tether mints two of them.
Backstop rebuilds the catalogue along that second axis and measures how
concentrated it is by value.

Scope, from the first live run, because the figures mislead without it:

  * The catalogue holds three asset classes - commodity, stock, etf. There is no
    tokenised-treasury class, so BUIDL, BENJI and OUSG are absent. This is
    tokenised gold plus equity and ETF wrappers, not the institutional RWA market.
  * Tokenised gold is 63% of it. The two largest issuers are large because they
    mint gold: Tether holds two tokens on one asset, Paxos one.
  * The issuer directory is complete for this join - no issuer appears on a token
    without being listed - but six entries carry no value at all and four never
    appear on a token. The residue is empty issuers, not missing ones.
  * 47% of tokens report market_cap as null, so every share is a share of the
    half that reports one.

None of those are assumed. Each is computed and published on every run.

WHAT IT MEASURES
----------------
Herfindahl-Hirschman Index over issuer shares of tokenised market cap, plus the
effective number of issuers (1 / sum of squared shares) - the count of
equal-sized issuers that would produce the same concentration. Reported overall
and within each asset class.

HHI here is concentration of CoinMarketCap-attributed tokenised market cap. It is
not a measure of issuance capacity, redemption liability or transfer-agent share.

The index itself is the inverse-Simpson index: a property of a share vector, used
for biodiversity and income distribution as readily as for markets, and valid on
any vector of shares. What this code deliberately does NOT do is attach the
1500/2500 merger-guideline bands to it. Those cut-points are defined for
substitutable products in a relevant market, and a CoinMarketCap issuer_name
spanning gold, tokenised equity and ETF wrappers is not that.

Earlier versions stamped each block with a "verdict" carrying those band names,
and then with a "shape" - top-heavy, uneven, broadly even - which was the same
two cut-points under new labels while this docstring claimed they were not
applied. Both are gone. No block carries a derived label of any kind: it carries
hhi, effective_n, top1/3/5, n and the full shares[] vector, and a reader who
wants a category can pick their own cut-point and say which one they used.

HOW THE ATTRIBUTION WORKS - and why the obvious way is wrong
------------------------------------------------------------
The obvious pipeline is: list the issuers, ask each one which tokens it issued,
look up those assets, and add up the assets' market caps. That is wrong, and
wrong in a way that inflates rather than merely blurs. Gold's $4.68bn tokenised
cap is split across PAXG, XAUT and five others from different issuers; crediting
the asset's cap to each issuer that touches it counts the same money several
times over, and the resulting concentration figures are meaningless.

`quotes/latest` avoids the problem entirely: each asset's `tokens[]` array
carries `issuer_id`, `issuer_name` and that token's OWN `market_cap`. Issuer
weight is therefore a direct sum over tokens, with no join and nothing counted
twice. The asset-level `tokenized_market_cap` is kept only as an independent
figure to reconcile against, and the discrepancy is published rather than hidden.

ENDPOINTS USED
--------------
All seven documented RWA endpoints, declared once in ENDPOINTS below - the single
source of truth for the README table, the dashboard, and the credit accounting.
`market-pairs/list` is Growth tier and is refused on this key; the refusal is
recorded from the live response rather than assumed from the documentation.

USAGE
-----
    CMC_API_KEY=xxxx python3 scripts/fetch_snapshot.py --out docs/data/snapshot.json

The key is read only from the environment, travels only in the
`X-CMC_PRO_API_KEY` header, is never placed in a URL, never logged, and never
written to the snapshot.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import requests

BASE = "https://pro-api.coinmarketcap.com"
UA = "backstop-rwa-issuer-concentration/1.0 (+https://github.com/mistryrajan87-lang/backstop)"

# Path, documented minimum plan tier, documented credit cost, and why Backstop
# calls it. Response shapes and parameter names below were confirmed against live
# responses by scripts/probe.ps1 - not taken from the reference.
ENDPOINTS: dict[str, dict[str, str]] = {
    "map": {
        "path": "/v5/real-world-assets/map",
        "tier": "Basic",
        "credits": "free",
        "why": "the canonical asset universe - rwa_id, asset class, and which assets have tokens at all",
    },
    "assets_list": {
        "path": "/v5/real-world-assets/assets/list",
        "tier": "Basic",
        "credits": "1 per call",
        "why": "asset-level tokenised market cap, used to reconcile the per-token totals against",
    },
    "quotes": {
        "path": "/v5/real-world-assets/quotes/latest",
        "tier": "Basic",
        "credits": "1 per call",
        "why": "the heart of it - tokens[] gives each token's own issuer and market cap; tradfi_markets[] says whether an underlying market exists",
    },
    "info": {
        "path": "/v5/real-world-assets/info",
        "tier": "Basic",
        "credits": "1 per call",
        "why": "descriptive metadata on the largest assets - industry, primary exchange, SEC CIK where the asset is an equity",
    },
    "issuers_list": {
        "path": "/v5/real-world-assets/issuers/list",
        "tier": "Basic",
        "credits": "1 per call",
        "why": "the issuer directory - name, website and declared token count for all 25",
    },
    "issuer": {
        "path": "/v5/real-world-assets/issuers",
        "tier": "Basic",
        "credits": "1 per call",
        "why": "each issuer's declared token count, cross-checked against what the token data actually attributes to them",
    },
    "market_pairs": {
        "path": "/v5/real-world-assets/market-pairs/list",
        "tier": "Growth",
        "credits": "1 per call",
        "why": "venue depth per asset; probed every run so the plan gate is recorded from evidence",
    },
    # Tickers a reader coming from an RWA league table will expect to find. They
    # are looked up in the map by symbol on every run, so the absence list is
    # evidence from this join rather than an assertion about the world.
    "crypto_info": {
        "path": "/v2/cryptocurrency/info",
        "tier": "Basic",
        "credits": "1 per 100 ids",
        "why": "the only route to a chain - no RWA endpoint carries one, so each token's crypto_id is taken across to the main cryptocurrency family for its platform",
    },
}

# Parameter names, confirmed live. `id` is rejected with error 4002 on every one
# of these - the RWA family uses its own names.
PARAM = {"rwa_id": "rwa_id", "issuer_id": "issuer_id"}

# Response envelopes: data is an object wrapping the list, not a bare list.
LIST_KEYS = ("rwa_assets", "issuers", "tokens")


def error_code(status: dict) -> int | str | None:
    """The real error code from a CMC status block, or None on success.

    CoinMarketCap is not consistent about the type. /v1/key/info returns the
    integer 0 on success; every /v5/real-world-assets endpoint returns the
    STRING "0". A plain truthiness test therefore passes the key check and
    rejects every data call, because "0" is truthy in Python - which is exactly
    what happened on the first production run: four calls, two credits, and an
    empty snapshot the publish gate refused.
    """
    code = status.get("error_code")
    if code is None or code == "":
        return None
    try:
        return None if int(code) == 0 else code
    except (TypeError, ValueError):
        return code          # unparseable is not success


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #

class CMC:
    """CMC client that tracks credit spend, honours the rate limit, retries on
    429, and records a refused call instead of raising."""

    def __init__(self, key: str, *, rate_limit_minute: int = 50, max_retries: int = 3) -> None:
        if not key:
            raise SystemExit("CMC_API_KEY is not set.")
        self.s = requests.Session()
        self.s.headers.update(
            {"X-CMC_PRO_API_KEY": key, "Accept": "application/json", "User-Agent": UA}
        )
        self.credits = 0
        self.calls = 0
        # Stay a shade under the ceiling rather than exactly on it.
        self.min_interval = 60.0 / max(1, rate_limit_minute - 4)
        self.max_retries = max_retries
        self._last_call = 0.0
        self.refused: list[dict[str, Any]] = []
        self.plan: dict[str, Any] = {}

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def get(self, path: str, **params: Any) -> Any | None:
        """Return the `data` payload, or None if the call was refused."""
        for attempt in range(self.max_retries):
            self._throttle()
            self.calls += 1
            try:
                r = self.s.get(BASE + path, params=params, timeout=60)
            except requests.RequestException as exc:
                self._record(path, params, f"transport: {exc}")
                return None
            finally:
                self._last_call = time.monotonic()

            if r.status_code == 429:
                back = 2 ** attempt * 5
                print(f"  ~ {path} rate limited, backing off {back}s", file=sys.stderr)
                time.sleep(back)
                continue

            try:
                body = r.json()
            except ValueError:
                self._record(path, params, f"non-JSON body (HTTP {r.status_code})")
                return None

            status = body.get("status") or {}
            self.credits += status.get("credit_count") or 0

            err = error_code(status)
            if err:
                self._record(path, params, f"{err}: {status.get('error_message')}")
                return None
            return body.get("data")

        self._record(path, params, "rate limited after retries")
        return None

    def read_plan(self) -> None:
        """Record the plan's credit and rate limits, and adopt the real rate
        limit rather than a guessed one."""
        data = self.get("/v1/key/info")
        if not isinstance(data, dict):
            return
        plan = data.get("plan") or {}
        usage = data.get("usage") or {}
        self.plan = {
            "credit_limit_monthly": plan.get("credit_limit_monthly"),
            "rate_limit_minute": plan.get("rate_limit_minute"),
            "credit_limit_monthly_reset_timestamp": plan.get("credit_limit_monthly_reset_timestamp"),
            "credits_used_this_month": (usage.get("current_month") or {}).get("credits_used"),
            "credits_left_this_month": (usage.get("current_month") or {}).get("credits_left"),
        }
        rl = plan.get("rate_limit_minute")
        if isinstance(rl, int) and rl > 4:
            self.min_interval = 60.0 / (rl - 4)
        print(f"    plan: {self.plan['credit_limit_monthly']} credits/month, "
              f"{rl} req/min, {self.plan['credits_left_this_month']} credits left")

    def _record(self, path: str, params: dict, reason: str) -> None:
        # Params are safe to record: the key travels in a header, never a param.
        short = {k: (str(v)[:60] + "…" if len(str(v)) > 60 else v) for k, v in params.items()}
        print(f"  ! {path} refused - {reason}", file=sys.stderr)
        self.refused.append({"path": path, "params": short, "reason": reason})


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #

def rows(payload: Any, *keys: str) -> list[dict]:
    """Pull the record list out of a CMC v5 envelope.

    `data` is an object like {"rwa_assets": [...], "total_size": n,
    "has_more": bool}. A bare list is accepted too, in case a future endpoint
    returns one.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for k in (keys or LIST_KEYS):
            v = payload.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def num(d: Any, *names: str) -> float:
    """First finite numeric value among `names`, 0.0 otherwise."""
    if not isinstance(d, dict):
        return 0.0
    for n in names:
        v = d.get(n)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and math.isfinite(v):
            return float(v)
        if isinstance(v, str):
            try:
                x = float(v)
            except ValueError:
                continue
            if math.isfinite(x):
                return x
    return 0.0


def text(d: Any, *names: str, default: str = "") -> str:
    if not isinstance(d, dict):
        return default
    for n in names:
        v = d.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def ident(d: Any, *names: str) -> str | None:
    if not isinstance(d, dict):
        return None
    for n in names:
        v = d.get(n)
        if v not in (None, "", []):
            return str(v)
    return None


def usd_quote(asset: dict) -> dict:
    """The USD entry from an asset's `quotes` list.

    `quotes` is a LIST of {"symbol": "USD", ...}, not an object keyed by
    currency - the shape most of the rest of the CMC API uses. Getting this
    wrong reads every price as zero.
    """
    q = asset.get("quotes")
    if isinstance(q, list):
        for entry in q:
            if isinstance(entry, dict) and str(entry.get("symbol", "")).upper() == "USD":
                return entry
    return {}


def asset_cap(asset: dict) -> float:
    """Asset-level tokenised market cap, from the USD quote or the flattened
    top-level copy, whichever is present."""
    return (num(usd_quote(asset), "tokenized_market_cap")
            or num(asset, "tokenized_market_cap"))


def asset_vol(asset: dict) -> float:
    return (num(usd_quote(asset), "tokenized_volume_24h")
            or num(asset, "tokenized_volume_24h"))


# --------------------------------------------------------------------------- #
# concentration maths
# --------------------------------------------------------------------------- #

COMMODITY_CLASS = "commodity"   # the asset_class the ex-commodity scope removes


def concentration_block(weights: dict[str, float], labels: dict[str, str] | None = None) -> dict:
    """
    HHI and top-N concentration over a {key: value} weighting.

    HHI is the sum of squared percentage shares, 0-10000. effective_n is
    1 / sum(share^2): the number of equal-sized participants that would give the
    same HHI, which is the more intuitive reading of the same number.
    """
    positive = {k: v for k, v in weights.items() if v > 0}
    total = sum(positive.values())
    if total <= 0:
        return {"total": 0.0, "hhi": None, "effective_n": None,
                "top1": None, "top3": None, "top5": None, "n": 0, "leaders": []}

    shares = sorted((v / total for v in positive.values()), reverse=True)
    sum_sq = sum(s * s for s in shares)
    hhi = sum_sq * 10000.0

    # No derived label is computed here, deliberately. The field that used to sit
    # at this point was called "verdict", carried the 2010 US Horizontal Merger
    # Guidelines band names, and was renamed "shape" - top-heavy / uneven /
    # broadly even - when those names were withdrawn. The rename changed the
    # words and kept the rule: the cut-points stayed at 2500 and 1500, so the
    # snapshot asserted a category from merger thresholds in the same file whose
    # method block said those thresholds were deliberately not applied. The
    # honest fix is not a third set of names. effective_n is the one-number
    # reading of the index, shares[] is the whole vector, and any cut-point a
    # reader wants is theirs to choose and to state.
    ranked = sorted(positive.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "total": total,
        "hhi": round(hhi, 1),
        "effective_n": round(1.0 / sum_sq, 2),
        "top1": round(sum(shares[:1]), 4),
        "top3": round(sum(shares[:3]), 4),
        "top5": round(sum(shares[:5]), 4),
        "n": len(positive),
        # The whole share vector, largest first. leaders[] stops at 12 for the
        # charts, so it cannot be used to recompute anything: dropping the top
        # issuer from a 15-issuer market and renormalising over 11 of the
        # remaining 14 would quietly invent a different market. Six decimals is
        # enough to reproduce hhi to a tenth.
        # Nine places, not six: the smallest issuer in the live book holds a
        # share of 0.000000148, which six places flattens to a literal 0.0 and
        # makes the vector look padded with an empty participant.
        "shares": [round(x, 9) for x in shares],
        "leaders": [
            {"key": k, "label": (labels or {}).get(k, k),
             "value": v, "share": round(v / total, 4)}
            for k, v in ranked[:12]
        ],
    }


# --------------------------------------------------------------------------- #
# collection
# --------------------------------------------------------------------------- #

def page_all(api: CMC, key: str, *, limit: int, cap: int, **extra: Any) -> list[dict]:
    """Page a v5 list endpoint on start/limit until has_more goes false."""
    path = ENDPOINTS[key]["path"]
    out: list[dict] = []
    start = 1
    while len(out) < cap:
        data = api.get(path, start=start, limit=limit, **extra)
        chunk = rows(data)
        if not chunk:
            break
        out.extend(chunk)
        more = isinstance(data, dict) and data.get("has_more")
        if not more or len(chunk) < limit:
            break
        start += len(chunk)
    return out[:cap]


def collect(api: CMC, *, max_assets: int, quote_batch: int, max_quote_calls: int,
            info_sample: int, chain_lookup: bool = True,
            max_chain_calls: int = 40) -> dict:
    print("0/6 plan and limits ...")
    api.read_plan()

    print("1/6 asset universe from the free id map ...")
    universe = page_all(api, "map", limit=250, cap=max_assets)
    tokenised = [a for a in universe if a.get("has_tokens")]
    print(f"    {len(universe)} assets mapped, {len(tokenised)} of them tokenised")

    print("2/6 issuer directory ...")
    issuers = page_all(api, "issuers_list", limit=250, cap=500)
    print(f"    {len(issuers)} issuers")

    print("3/6 asset-level market caps, for reconciliation ...")
    priced = page_all(api, "assets_list", limit=250, cap=max_assets, convert="USD")
    cap_by_id = {ident(a, "rwa_id"): asset_cap(a) for a in priced if ident(a, "rwa_id")}
    print(f"    {len(cap_by_id)} assets priced")

    print("4/6 per-token issuer attribution ...")
    ids = [ident(a, "rwa_id") for a in tokenised]
    ids = [i for i in ids if i]
    batches = [ids[i:i + quote_batch] for i in range(0, len(ids), quote_batch)]
    if len(batches) > max_quote_calls:
        print(f"    capping at {max_quote_calls} of {len(batches)} batches "
              f"to stay inside the credit budget", file=sys.stderr)
        batches = batches[:max_quote_calls]

    links: list[dict] = []
    null_caps = 0
    tradfi: dict[str, int] = {}
    tradfi_venues: dict[str, int] = defaultdict(int)
    quoted_caps: dict[str, float] = {}
    assets_seen: dict[str, dict] = {}
    for n_batch, batch in enumerate(batches, 1):
        data = api.get(ENDPOINTS["quotes"]["path"],
                       **{PARAM["rwa_id"]: ",".join(batch)}, convert="USD")
        for asset in rows(data):
            rid = ident(asset, "rwa_id")
            if not rid:
                continue
            assets_seen[rid] = asset
            quoted_caps[rid] = asset_cap(asset)
            tf = asset.get("tradfi_markets") if isinstance(asset.get("tradfi_markets"), list) else []
            tradfi[rid] = len(tf)
            for venue in tf:
                ex = venue.get("exchange") if isinstance(venue, dict) else None
                name = text(ex, "name") or text(venue, "exchange") or "unnamed venue"
                tradfi_venues[name] += 1
            for tok in rows(asset, "tokens"):
                iid = ident(tok, "issuer_id")
                if tok.get("market_cap") is None:
                    null_caps += 1
                links.append({
                    "rwa_id": rid,
                    "asset_name": text(asset, "name"),
                    "asset_symbol": text(asset, "symbol"),
                    "asset_class": text(asset, "asset_type", default="unclassified"),
                    "issuer_id": iid,
                    "issuer_name": text(tok, "issuer_name", default="unattributed"),
                    "token_symbol": text(tok, "symbol"),
                    "token_name": text(tok, "name"),
                    "crypto_id": ident(tok, "crypto_id"),
                    "market_cap": num(tok, "market_cap"),
                    "has_market_cap": tok.get("market_cap") is not None,
                    "volume_24h": num(tok, "volume_24h"),
                })
        if n_batch % 10 == 0 or n_batch == len(batches):
            print(f"    batch {n_batch}/{len(batches)}: {len(links)} tokens, "
                  f"{api.credits} credits")

    # No RWA endpoint carries a chain. Each token does carry a crypto_id, so the
    # platform is fetched from the main cryptocurrency family - a genuine
    # cross-family join, and the only way to answer "which chain is this on".
    # Only tokens that actually carry value are looked up: an unpriced token
    # cannot move a concentration figure, and credits are finite.
    print("5/7 chain lookup via crypto_id ...")
    chain_of: dict[str, str] = {}
    # The same call that yields the chain also carries the contract address and
    # the issuer's own links. Backstop makes no reserve claim - it cannot, from
    # these endpoints - but the address is the hook any future reserve check
    # would start from, and discarding a field already paid for is wasteful.
    token_ref: dict[str, dict] = {}
    unmatched_ids: list[str] = []
    if chain_lookup:
        want = sorted({l["crypto_id"] for l in links
                       if l["crypto_id"] and l["market_cap"] > 0})
        cbatches = [want[i:i + 100] for i in range(0, len(want), 100)]
        if len(cbatches) > max_chain_calls:
            print(f"    capping at {max_chain_calls} of {len(cbatches)} batches",
                  file=sys.stderr)
            cbatches = cbatches[:max_chain_calls]
        asked: set[str] = set()
        for batch in cbatches:
            asked.update(batch)
            data = api.get(ENDPOINTS["crypto_info"]["path"], id=",".join(batch))
            if not isinstance(data, dict):
                continue
            for cid, rec in data.items():
                if not isinstance(rec, dict):
                    continue
                plat = rec.get("platform") if isinstance(rec.get("platform"), dict) else {}
                name = text(plat, "name")
                # A coin with no platform is its own chain, not an unknown.
                chain_of[str(cid)] = name or (text(rec, "name") or "unknown")
                urls = rec.get("urls") if isinstance(rec.get("urls"), dict) else {}
                first = lambda k: (urls.get(k) or [None])[0] if isinstance(urls.get(k), list) else None
                token_ref[str(cid)] = {
                    "symbol": text(rec, "symbol"),
                    "chain": chain_of[str(cid)],
                    "contract": text(plat, "token_address"),
                    "website": first("website"),
                    "explorer": first("explorer"),
                    "docs": first("technical_doc"),
                }
        unmatched_ids = sorted(asked - set(chain_of))
        print(f"    {len(chain_of)} of {len(want)} valued tokens placed on a chain"
              + (f"; {len(unmatched_ids)} crypto_ids not returned" if unmatched_ids else ""))

    print("6/7 issuer cross-check ...")
    declared: dict[str, int] = {}
    for iss in issuers:
        iid = ident(iss, "issuer_id")
        if not iid:
            continue
        detail = api.get(ENDPOINTS["issuer"]["path"], **{PARAM["issuer_id"]: iid}, limit=1)
        if isinstance(detail, dict):
            declared[iid] = int(num(detail, "num_tokens", "total_size"))

    # Which of the names a reader will expect are simply not in this catalogue.
    expect = ["BUIDL", "BENJI", "USYC", "OUSG", "USTB", "JAAA", "USDY", "TBILL",
              "JTRSY", "USTBL", "BOXX", "FOBXX"]
    by_symbol = {}
    for a in universe:
        sym = text(a, "symbol").upper()
        if sym:
            by_symbol.setdefault(sym, a)
    absent = [t for t in expect if t not in by_symbol]
    present = []
    for t in expect:
        a = by_symbol.get(t)
        if not a:
            continue
        rid = ident(a, "rwa_id")
        present.append({
            "symbol": t,
            "name": text(a, "name"),
            "asset_type": text(a, "asset_type"),
            "has_tokens": bool(a.get("has_tokens")),
        })
    print(f"    of {len(expect)} expected treasury/credit tickers, "
          f"{len(present)} in the map, {len(absent)} not in it")

    print("7/7 descriptive metadata on the largest assets ...")
    by_cap = sorted(quoted_caps.items(), key=lambda kv: kv[1], reverse=True)[:info_sample]
    info: dict[str, dict] = {}
    if by_cap:
        data = api.get(ENDPOINTS["info"]["path"],
                       **{PARAM["rwa_id"]: ",".join(k for k, _ in by_cap)})
        for rec in rows(data):
            rid = ident(rec, "rwa_id")
            if rid:
                about = rec.get("about") if isinstance(rec.get("about"), dict) else {}
                info[rid] = {
                    "industry": text(rec, "industry"),
                    "primary_exchange": text(rec, "primary_exchange"),
                    "cik": text(rec, "cik"),
                    "founded": text(rec, "founded"),
                    "website": text(rec, "website") or text(about, "website"),
                }
    print(f"    {len(info)} assets enriched")

    # Growth-tier probe, so the plan gate is reported from evidence.
    mp = None
    if ids:
        res = api.get(ENDPOINTS["market_pairs"]["path"], **{PARAM["rwa_id"]: ids[0]})
        mp = {"available": res is not None, "sample_rwa_id": ids[0],
              "rows": len(rows(res)) if res is not None else 0}

    return {"universe": universe, "tokenised": tokenised, "issuers": issuers,
            "links": links, "tradfi": tradfi, "tradfi_venues": dict(tradfi_venues),
            "null_caps": null_caps, "chain_of": chain_of,
            "unmatched_crypto_ids": unmatched_ids, "token_ref": token_ref,
            "cap_by_id": cap_by_id,
            "expected_absent": absent, "expected_present": present,
            "quoted_caps": quoted_caps, "assets_seen": assets_seen,
            "declared": declared, "info": info, "market_pairs_probe": mp,
            "batches_run": len(batches), "batches_total":
                max(1, (len(ids) + quote_batch - 1) // quote_batch) if ids else 0}


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #

def build_snapshot(raw: dict, api: CMC) -> dict:
    links: list[dict] = raw["links"]
    directory = {ident(i, "issuer_id"): i for i in raw["issuers"] if ident(i, "issuer_id")}

    chain_of: dict[str, str] = raw.get("chain_of") or {}
    labels: dict[str, str] = {}
    tokens_per_asset: dict[str, int] = defaultdict(int)
    by_issuer: dict[str, float] = defaultdict(float)
    class_issuer: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Rebuilt from the tokens themselves rather than added up from the stock and
    # ETF blocks. Those happen to sum to the same figure today because the
    # catalogue has exactly three classes; the day CoinMarketCap adds a fourth,
    # summing the ones you thought of drops it silently, and this does not.
    ex_commodity_issuer: dict[str, float] = defaultdict(float)
    chain_issuer: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    chain_total: dict[str, float] = defaultdict(float)
    asset_issuer: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    meta: dict[str, dict] = {}
    unattributed = 0.0
    unattributed_tokens = 0
    unplaced_value = 0.0
    off_directory: set[str] = set()

    for l in links:
        tokens_per_asset[l["rwa_id"]] += 1
        iid, cap = l["issuer_id"], l["market_cap"]
        if not iid:
            unattributed += cap
            unattributed_tokens += 1
            continue
        if iid not in directory:
            off_directory.add(iid)
        labels[iid] = l["issuer_name"]
        by_issuer[iid] += cap
        class_issuer[l["asset_class"]][iid] += cap
        if l["asset_class"] != COMMODITY_CLASS:
            ex_commodity_issuer[iid] += cap
        asset_issuer[l["rwa_id"]][iid] += cap

        chain = chain_of.get(l["crypto_id"] or "")
        if chain and cap > 0:
            chain_issuer[chain][iid] += cap
            chain_total[chain] += cap
        elif cap > 0:
            unplaced_value += cap

        m = meta.setdefault(iid, {
            "issuer_id": iid, "name": l["issuer_name"],
            "site": text(directory.get(iid), "website"),
            "in_directory": iid in directory,
            "tokens": 0, "tokens_without_cap": 0, "assets": set(),
            "market_cap": 0.0, "volume_24h": 0.0,
            "classes": set(), "chains": set(), "assets_with_tradfi_market": 0,
        })
        m["tokens"] += 1
        if not l.get("has_market_cap", True):
            m["tokens_without_cap"] += 1
        if chain:
            m["chains"].add(chain)
        m["market_cap"] += cap
        m["volume_24h"] += l["volume_24h"]
        m["classes"].add(l["asset_class"])
        m["assets"].add(l["rwa_id"])

    for iid, m in meta.items():
        m["assets_with_tradfi_market"] = sum(
            1 for rid in m["assets"] if raw["tradfi"].get(rid, 0) > 0)
        dec = raw["declared"].get(iid)
        m["declared_tokens"] = dec
        m["attributed_tokens"] = m["tokens"]
        m["assets"] = len(m["assets"])
        m["classes"] = sorted(m["classes"])
        m["chains"] = sorted(m["chains"])
        # An entity that declares tokens but carries no value at all is an
        # aggregate placeholder, not an issuer. Flag it rather than let it pad
        # the counts.
        m["placeholder"] = m["market_cap"] <= 0 and m["tokens"] > 0

    issuer_rows = sorted(meta.values(), key=lambda r: r["market_cap"], reverse=True)

    # Reconciliation: the per-token caps we summed, against the asset-level cap
    # the API reports for the same assets. A large gap means the token list does
    # not account for the whole asset, and the reader is entitled to know.
    summed_by_asset: dict[str, float] = defaultdict(float)
    for l in links:
        summed_by_asset[l["rwa_id"]] += l["market_cap"]
    recon_assets = [rid for rid in summed_by_asset if rid in raw["quoted_caps"]]

    # Two different situations get conflated if this is done in one pass:
    #
    #   a) assets both endpoints price - these can be compared, and a ratio means
    #      something;
    #   b) assets whose tokens report a market cap while the asset-level figure is
    #      zero or absent - these cannot be compared at all.
    #
    # Adding (b) into one ratio silently inflates it: the token side contributes
    # value and the asset side contributes nothing. On the first runs that pushed
    # the headline ratio to 1.0144 while every large asset reconciled at 1.0000,
    # and a per-asset check that skipped cap <= 0 could not see why. So (b) is
    # counted and reported as its own category, and the ratio is computed only
    # over (a).
    comparable = [rid for rid in recon_assets if raw["quoted_caps"][rid] > 0]
    token_only = [rid for rid in recon_assets if raw["quoted_caps"][rid] <= 0
                  and summed_by_asset[rid] > 0]

    token_total = sum(summed_by_asset[r] for r in comparable)
    asset_total = sum(raw["quoted_caps"][r] for r in comparable)
    token_only_value = sum(summed_by_asset[r] for r in token_only)

    def describe(rid: str) -> dict:
        a = raw["assets_seen"].get(rid, {})
        return {"rwa_id": rid, "symbol": text(a, "symbol"), "name": text(a, "name"),
                "asset_cap": raw["quoted_caps"].get(rid, 0.0),
                "token_sum": summed_by_asset[rid]}

    # An aggregate ratio of 1.0 can still hide one asset reconciling perfectly and
    # another being badly wrong, so every comparable asset is checked on its own.
    outliers = []
    for rid in comparable:
        ratio = summed_by_asset[rid] / raw["quoted_caps"][rid]
        if abs(ratio - 1.0) > 0.01:
            outliers.append({**describe(rid), "ratio": round(ratio, 4)})
    outliers.sort(key=lambda o: -abs(o["ratio"] - 1.0))

    token_only_rows = sorted((describe(r) for r in token_only),
                             key=lambda o: -o["token_sum"])

    # The largest assets, and how their value splits across issuers - the single
    # clearest illustration of why per-asset attribution would have been wrong.
    top_assets = []
    for rid, cap in sorted(raw["quoted_caps"].items(), key=lambda kv: kv[1], reverse=True)[:25]:
        a = raw["assets_seen"].get(rid, {})
        split = sorted(asset_issuer.get(rid, {}).items(), key=lambda kv: kv[1], reverse=True)
        enrich = raw["info"].get(rid, {})
        top_assets.append({
            "rwa_id": rid,
            "name": text(a, "name"),
            "symbol": text(a, "symbol"),
            "asset_class": text(a, "asset_type", default="unclassified"),
            "tokenized_market_cap": cap,
            "tokens": tokens_per_asset.get(rid, 0),
            "issuers": len(split),
            "tradfi_markets": raw["tradfi"].get(rid, 0),
            "industry": enrich.get("industry", ""),
            "primary_exchange": enrich.get("primary_exchange", ""),
            "cik": enrich.get("cik", ""),
            # Largest BY VALUE, same rule as asset_index. An asset can be priced
            # while every token representing it reports no market cap: it has
            # issuers, but none of them leads it, and naming the first of several
            # zeroes credits whoever happens to sort first.
            "top_issuer": labels.get(split[0][0], "") if split and split[0][1] > 0 else "",
            "top_issuer_share": (round(split[0][1] / cap, 4)
                                 if split and split[0][1] > 0 and cap > 0 else None),
            "split": [{"issuer": labels.get(k, k), "market_cap": v,
                       "share": round(v / cap, 4) if cap > 0 else None}
                      for k, v in split[:8]],
        })

    # Every tokenised asset, not only the largest 25. The page inlines the whole
    # snapshot into one self-contained file, so this block is columnar: `fields`
    # names the columns once and each asset is a row. Written as 791 objects with
    # full keys it is ~119KB; written this way it is ~47KB, and the `fields`
    # line keeps it readable on its own terms rather than making a row a mystery.
    #
    # top_issuer is an INDEX into issuers[] rather than a name. The names are
    # already in that list, and repeating 791 of them would cost more than the
    # rest of this block; -1 means no issuer attributes any value to this asset,
    # which is a real state - an asset can carry tokens that all report no cap.
    #
    # The set is the union of "has a quoted cap" and "has at least one token",
    # not just the first. They coincide today at 791; the day they do not, an
    # asset with tokens and no price is exactly the one worth seeing.
    issuer_pos = {r["issuer_id"]: i for i, r in enumerate(issuer_rows)}
    asset_ids = sorted(set(raw["quoted_caps"]) | set(tokens_per_asset),
                       key=lambda rid: (-raw["quoted_caps"].get(rid, 0.0), rid))
    index_rows = []
    for rid in asset_ids:
        a = raw["assets_seen"].get(rid, {})
        split = sorted(asset_issuer.get(rid, {}).items(), key=lambda kv: kv[1], reverse=True)
        index_rows.append([
            text(a, "symbol"),
            text(a, "name"),
            text(a, "asset_type", default="unclassified"),
            round(raw["quoted_caps"].get(rid, 0.0), 2),
            tokens_per_asset.get(rid, 0),
            len(split),
            # Largest BY VALUE. An asset can carry tokens that all report no
            # market cap, and then no issuer leads it: sorting zeroes and taking
            # the first would name one on the strength of dictionary order. The
            # issuer COUNT beside it still says how many mint it.
            (issuer_pos.get(split[0][0], -1) if split and split[0][1] > 0 else -1),
            raw["tradfi"].get(rid, 0),
        ])

    # Value attributed to each symbol, so a present-but-empty ticker can be told
    # apart from a present-and-real one.
    by_issuer_asset: dict[str, float] = defaultdict(float)
    for l in links:
        sym = (l.get("asset_symbol") or "").upper()
        if sym:
            by_issuer_asset[sym] += l["market_cap"]

    checked = len(raw["tradfi"])
    with_tradfi = sum(1 for v in raw["tradfi"].values() if v > 0)
    venues = sorted((raw.get("tradfi_venues") or {}).items(), key=lambda kv: -kv[1])
    venue_total = sum(v for _, v in venues)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {
            "metric": "Herfindahl-Hirschman Index over issuer shares of tokenised market cap",
            "scale": "0-10000, the sum of squared percentage shares. Read as a "
                     "description of this share vector. The 1500 and 2500 marks used "
                     "in merger analysis are deliberately NOT applied: these are "
                     "CoinMarketCap issuer labels, not firms shown to compete in a "
                     "defined market. No block carries a derived band, verdict or "
                     "shape label - earlier versions did, computed from exactly those "
                     "two cut-points, which contradicted this sentence",
            "effective_n": "1 / sum of squared shares - the number of equal-sized "
                           "issuers that would produce the same HHI",
            "weight": "each token's own market cap in USD, from quotes/latest tokens[], "
                      "summed by issuer - never the asset's cap, which several issuers share",
            "ex_commodity": f"the ex_commodity scope is rebuilt from every token whose "
                            f"asset_class is not '{COMMODITY_CLASS}', not added up from the "
                            "stock and ETF blocks - those two agree with it only while "
                            "those are the only other classes in the catalogue",
            "shares": "every block carries shares[]: the full list of participant shares, "
                      "largest first, so a reader can recompute the index or ask what it "
                      "would be without the largest holders",
            "asset_mix": "the one block NOT weighted by the token sum. asset_mix is over "
                          "assets, weighted by each asset's own market cap from quotes/latest, "
                          "so its denominator omits the assets the asset-level endpoint prices "
                          "at zero while their tokens report a value "
                          "(coverage.reconciliation.value_priced_only_by_tokens is exactly that "
                          "gap). Its shares are therefore NOT comparable with the issuer, chain "
                          "or class blocks, which all use the token sum: an asset's share of "
                          "asset_mix and its share of overall.total are different denominators "
                          "and neither is wrong",
        },
        "counts": {
            "assets_mapped": len(raw["universe"]),
            "assets_tokenised": len(raw["tokenised"]),
            "assets_quoted": len(raw["quoted_caps"]),
            "issuers_in_directory": len(directory),
            # by_issuer keys every issuer seen, including those summing to zero.
            # The count that matters is the one the concentration is measured over.
            "issuers_seen": len(by_issuer),
            "issuers_with_value": sum(1 for v in by_issuer.values() if v > 0),
            # Every token seen on a quoted asset, whether or not it carries an
            # issuer_id - the loop above appends to `links` before it checks.
            # So this is the denominator for "tokens reporting no market cap",
            # and it is NOT the number of tokens attributed to an issuer; that
            # is this minus coverage.tokens_without_issuer. The name is kept
            # for the archived snapshots that already use it; the page labels it
            # by what it counts.
            "tokens_attributed": len(links),
        },
        "coverage": {
            "quote_batches_run": raw["batches_run"],
            "quote_batches_total": raw["batches_total"],
            "assets_fully_quoted": raw["batches_run"] >= raw["batches_total"],
            "tokens_without_issuer": unattributed_tokens,
            "value_without_issuer": unattributed,
            "tokens_without_market_cap": raw.get("null_caps", 0),
            "issuers_outside_directory": len(off_directory),
            "chains_resolved": len(chain_issuer),
            "value_without_a_chain": unplaced_value,
            "placeholder_issuers": sum(1 for m in meta.values() if m["placeholder"]),
            "reconciliation": {
                # `comparable`, not `recon_assets`. The note below says the ratio
                # covers only the assets both endpoints price, and `comparable`
                # is the only set that satisfies it: a POSITIVE asset-level cap
                # on both sides.
                #
                # The first version of this comment said the count should have
                # been 785 - 791 minus the six token-only assets. That was wrong
                # by arithmetic I did not check. 499 of the 791 have no
                # asset-level cap at all; only six of those are token-only and
                # the other 493 are zero on both sides. The published label said
                # 791 about a ratio computed over 292, and the fix moves it by
                # 499, not by 6. Written down because getting the denominator
                # wrong is the exact failure this field exists to report.
                "assets_compared": len(comparable),
                "sum_of_token_caps": token_total,
                "sum_of_asset_caps": asset_total,
                "difference": token_total - asset_total,
                "ratio": round(token_total / asset_total, 4) if asset_total > 0 else None,
                "assets_compared_note": "the ratio covers only assets both endpoints price",
                "assets_off_by_over_1pct": len(outliers),
                "worst_assets": outliers[:15],
                "assets_priced_only_by_tokens": len(token_only),
                "value_priced_only_by_tokens": token_only_value,
                "token_only_examples": token_only_rows[:15],
            },
            "unmatched_crypto_ids": len(raw.get("unmatched_crypto_ids") or []),
            "null_caps_by_issuer": sorted(
                ({"issuer": m["name"], "tokens_without_cap": m["tokens_without_cap"]}
                 for m in meta.values() if m["tokens_without_cap"]),
                key=lambda r: -r["tokens_without_cap"])[:10],
            "note": "Issuer weights are summed from each token's own market cap. A "
                    "material share of tokens report market_cap as null; they are "
                    "counted here but carry no value and cannot move a concentration "
                    "figure, so every share below is a share of the value that IS "
                    "reported. The reconciliation compares the per-token total against "
                    "the asset-level tokenised market cap CoinMarketCap gives for the "
                    "same assets; a ratio far from 1.0 means the token lists do not "
                    "account for the whole of those assets.",
        },
        "plan": api.plan,
        "overall": concentration_block(dict(by_issuer), labels),
        "by_chain": {
            chain: concentration_block(dict(w), labels)
            for chain, w in sorted(chain_issuer.items(),
                                   key=lambda kv: -chain_total[kv[0]])
        },
        "chain_mix": concentration_block(dict(chain_total)),
        "asset_mix": concentration_block(
            {rid: cap for rid, cap in raw["quoted_caps"].items() if cap > 0},
            {rid: (text(raw["assets_seen"].get(rid, {}), "symbol") or rid)
             for rid in raw["quoted_caps"]},
        ),
        "ex_commodity": concentration_block(dict(ex_commodity_issuer), labels),
        "by_asset_class": {
            cls: concentration_block(dict(w), labels)
            for cls, w in sorted(class_issuer.items(),
                                 key=lambda kv: -sum(kv[1].values()))
        },
        "issuers": issuer_rows,
        "top_assets": top_assets,
        "asset_index": {
            "note": "every asset in the catalogue that is tokenised or priced, "
                    "largest first. top_assets above carries the same assets in "
                    "full for the largest 25; this is the whole list, in columns.",
            "fields": ["symbol", "name", "asset_class", "tokenised_market_cap",
                       "tokens", "issuers", "top_issuer", "tradfi_markets"],
            "top_issuer": "an index into issuers[] above: the issuer holding the "
                          "largest attributed value. -1 where no issuer attributes "
                          "any value - an asset whose tokens all report no market "
                          "cap has issuers but no leader",
            "tokenised_market_cap": "the asset-level figure from quotes/latest, "
                                    "NOT the sum of its tokens - the two disagree, "
                                    "and coverage.reconciliation is where that gap "
                                    "is reported",
            "rows": index_rows,
        },
        "tradfi_reference": {
            "assets_checked": checked,
            "assets_with_tradfi_market": with_tradfi,
            "venues": [{"name": k, "listings": v,
                        "share": round(v / venue_total, 4) if venue_total else None}
                       for k, v in venues],
            "distinct_venues": len(venues),
            "total_listings": venue_total,
            "venue_note": "Reported as a count, deliberately. When the field returns "
                          "one venue, a concentration index over it is arithmetic "
                          "rather than a finding - it can only be 10,000. The number "
                          "worth quoting is how many listings came back and how many "
                          "distinct venues they name.",
            "note": "quotes/latest returns tradfi_markets[] beside tokens[]: the "
                    "non-token venues that quote the asset, each with an exchange, a "
                    "ticker and a market URL. These are exchange listings rather than "
                    "the asset's primary market - a tokenised equity's entry points at "
                    "a crypto exchange's stock product, not at the exchange the share "
                    "itself trades on. Read it as 'is this asset quoted anywhere off "
                    "chain', and read the venue split beside it: if one venue supplies "
                    "nearly all of them, that is a second concentration sitting "
                    "underneath the issuer one.",
        },
        "not_in_this_catalogue": {
            "absent": raw.get("expected_absent") or [],
            "present": [
                {**p, "attributed_value": by_issuer_asset.get(p["symbol"].upper(), 0.0)}
                for p in (raw.get("expected_present") or [])
            ],
            "note": "Tickers a reader arriving from an RWA league table would expect, "
                    "looked up by symbol in CoinMarketCap's own RWA map on this run. "
                    "Absent means absent from this catalogue - not from the world, and "
                    "not a judgement on the product. A ticker can also be present in "
                    "the map and still carry nothing, which is its own kind of absence "
                    "and is reported separately.",
        },
        # Contract addresses and issuer links for the tokens carrying the most
        # value. Backstop makes NO reserve or backing claim - the RWA endpoints
        # carry no attestation, no custody and no redemption terms, and a
        # concentration index over them does not become one. This is only the
        # starting point a reserve check would need: what is on chain, and where
        # the issuer publishes.
        "token_references": sorted(
            ({**(raw.get("token_ref") or {}).get(l["crypto_id"] or "", {}),
              "issuer": l["issuer_name"], "market_cap": l["market_cap"]}
             for l in links
             if l["crypto_id"] and (raw.get("token_ref") or {}).get(l["crypto_id"])
             and l["market_cap"] > 0),
            key=lambda r: -r["market_cap"])[:25],
        "reserves_note": "Backstop does not and cannot verify reserves. These "
                         "endpoints publish no attestation, no custodian and no "
                         "redemption terms. What is listed here is the contract "
                         "address and the issuer's own published links, which is "
                         "where such a check would have to begin - against the "
                         "issuer's reports, not against this API.",
        "market_pairs_probe": raw["market_pairs_probe"],
        "endpoints": ENDPOINTS,
        "api": {"calls": api.calls, "credits": api.credits, "refused": api.refused},
    }


def append_history(path: str, snap: dict) -> None:
    """Append one compact line per run.

    A single snapshot is a photograph. The scheduled job runs daily, so keeping a
    handful of numbers per run costs nothing and turns the page into something
    that can answer "is this catalogue changing" rather than only "what is it
    now". One line is rewritten rather than appended if a run already exists for
    the same day, so re-running does not double-count.
    """
    o, c = snap["overall"], snap["coverage"]
    rec = c.get("reconciliation") or {}
    top_asset = (snap.get("top_assets") or [{}])[0]
    total = o.get("total") or 0.0
    row = {
        "date": snap["generated_at"][:10],
        "generated_at": snap["generated_at"],
        "total_cap": round(total, 2),
        "assets_tokenised": snap["counts"].get("assets_tokenised"),
        "issuers_with_value": o.get("n"),
        "hhi": o.get("hhi"),
        "effective_n": o.get("effective_n"),
        "top1": o.get("top1"),
        "top5": o.get("top5"),
        "largest_asset": top_asset.get("symbol"),
        "largest_asset_share": (round(top_asset.get("tokenized_market_cap", 0) / total, 4)
                                if total > 0 else None),
        "tokens_without_cap": c.get("tokens_without_market_cap"),
        "reconciliation_ratio": rec.get("ratio"),
        "credits": (snap.get("api") or {}).get("credits"),
    }
    existing: list[dict] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.append(json.loads(line))
                except ValueError:
                    continue
    existing = [r for r in existing if r.get("date") != row["date"]]
    existing.append(row)
    existing.sort(key=lambda r: r.get("generated_at") or "")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in existing:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"  history: {len(existing)} run(s) in {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the Backstop snapshot.")
    ap.add_argument("--out", default="docs/data/snapshot.json")
    ap.add_argument("--max-assets", type=int, default=9000)
    ap.add_argument("--quote-batch", type=int, default=100,
                    help="rwa_ids per quotes/latest call")
    ap.add_argument("--max-quote-calls", type=int, default=120,
                    help="hard ceiling on quotes/latest calls, to protect the credit budget")
    ap.add_argument("--info-sample", type=int, default=25,
                    help="largest assets to enrich with info metadata")
    ap.add_argument("--no-chains", action="store_true",
                    help="skip the crypto_id -> platform lookup that builds the chain view")
    ap.add_argument("--max-chain-calls", type=int, default=40,
                    help="ceiling on /v2/cryptocurrency/info calls")
    ap.add_argument("--history", default="docs/data/history.jsonl",
                    help="append-only series, one line per run; '' to skip")
    args = ap.parse_args()

    api = CMC(os.environ.get("CMC_API_KEY", "").strip())
    raw = collect(api, max_assets=args.max_assets, quote_batch=args.quote_batch,
                  max_quote_calls=args.max_quote_calls, info_sample=args.info_sample,
                  chain_lookup=not args.no_chains, max_chain_calls=args.max_chain_calls)
    snapshot = build_snapshot(raw, api)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=1)

    if args.history:
        append_history(args.history, snapshot)

    o, c = snapshot["overall"], snapshot["coverage"]
    print(f"\nwrote {args.out}")
    if o["hhi"] is None:
        print("  NO VALUE ATTRIBUTED - the snapshot has no concentration figures.")
    else:
        print(f"  {o['n']} issuers with value   HHI {o['hhi']}")
        print(f"  effective issuers: {o['effective_n']}")
        print(f"  top 1 / 3 / 5 share: {o['top1']:.1%} / {o['top3']:.1%} / {o['top5']:.1%}")
    r = c["reconciliation"]
    print(f"  token caps vs asset caps: {r['ratio']} over {r['assets_compared']} assets")
    print(f"  tokens with no market cap: {c['tokens_without_market_cap']}")
    print(f"  chains resolved: {c['chains_resolved']}")
    print(f"  {api.calls} calls, {api.credits} credits")
    if api.refused:
        print(f"  {len(api.refused)} call(s) refused - see snapshot.api.refused")


if __name__ == "__main__":
    main()
