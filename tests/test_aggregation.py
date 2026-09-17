#!/usr/bin/env python3
"""
Aggregation tests for Backstop.

The CoinMarketCap API is unreachable from the build environment, so the
attribution logic and the concentration maths are verified against fixtures with
hand-calculable answers. The fixtures use the REAL response envelope, confirmed
live by scripts/probe.ps1: `data` is an object wrapping the record list under
`rwa_assets` / `issuers` / `tokens`, `quotes` is a list of currency entries
rather than an object, and the id parameters are `rwa_id` and `issuer_id`.

Test 1 is the one that matters. It encodes the mistake this pipeline exists to
avoid: crediting an asset's whole market cap to every issuer that mints a token
for it. Gold alone is $4.68bn across seven issuers, so that error does not blur
the answer, it multiplies it.

Run:  python3 tests/test_aggregation.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from fetch_snapshot import (  # noqa: E402
    CMC, ENDPOINTS, PARAM, asset_cap, build_snapshot, collect, concentration_block,
    error_code, usd_quote,
)

FAILURES: list[str] = []


def check(label, condition, detail=""):
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def approx(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# fixture builders - real shapes
# --------------------------------------------------------------------------- #

def asset(rwa_id, symbol, cls, cap, *, has_tokens=True, flattened=False, **extra):
    """An asset record as assets/list and map return them."""
    a = {"name": f"{symbol} asset", "symbol": symbol, "slug": symbol.lower(),
         "rwa_id": rwa_id, "asset_type": cls, "rwa_rank": rwa_id,
         "has_tokens": has_tokens}
    if flattened:
        # Some endpoints repeat the priced fields at the top level.
        a["tokenized_market_cap"] = cap
        a["tokenized_volume_24h"] = cap * 0.01
    else:
        a["quotes"] = [{"symbol": "USD", "crypto_id": 2781,
                        "tokenized_market_cap": cap,
                        "tokenized_volume_24h": cap * 0.01}]
    a.update(extra)
    return a


def token(issuer_id, issuer_name, symbol, cap, *, crypto_id=1):
    """A token as quotes/latest returns them. `cap` may be None - roughly a fifth
    of live tokens report market_cap as null."""
    return {"symbol": symbol, "name": f"{symbol} token", "price": 1.0,
            "crypto_id": crypto_id, "issuer_id": issuer_id,
            "issuer_name": issuer_name, "market_cap": cap,
            "volume_24h": (cap or 0) * 0.02}


def venue(name, ticker="X"):
    """A tradfi_markets entry: an exchange listing, not the asset's primary market."""
    return {"exchange": {"slug": name.lower(), "name": name, "exchange_id": 1},
            "ticker": ticker, "market_url": f"https://{name.lower()}.invalid/{ticker}"}


def issuer(issuer_id, name, num_tokens=1):
    return {"name": name, "website": f"https://{name.lower().replace(' ', '')}.invalid",
            "logo": None, "issuer_id": issuer_id, "num_tokens": num_tokens}


class FakeCMC(CMC):
    """CMC stand-in serving fixtures in the real envelope. No network."""

    def __init__(self, assets, issuers, tokens_by_asset, tradfi=None, *,
                 page_size=250, platforms=None, missing_ids=()):
        self.credits = 0
        self.calls = 0
        self.refused = []
        self.plan = {}
        self.min_interval = 0.0
        self.max_retries = 1
        self._last_call = 0.0
        self._assets = assets
        self._issuers = issuers
        self._tokens = tokens_by_asset or {}
        self._tradfi = tradfi or {}
        self._platforms = platforms or {}
        self._missing = set(missing_ids)   # ids CMC does not know, omitted from the reply
        self._page = page_size
        self.chain_calls = 0
        self.quote_calls = 0
        self.quote_batch_sizes: list[int] = []

    def read_plan(self):
        self.plan = {"credit_limit_monthly": 15000, "rate_limit_minute": 50,
                     "credits_left_this_month": 15000}

    def _envelope(self, key, items, start, limit):
        window = items[start - 1: start - 1 + limit]
        return {key: window, "total_size": len(items),
                "has_more": (start - 1 + limit) < len(items)}

    def get(self, path, **params):
        self.calls += 1
        self.credits += 1
        start = int(params.get("start", 1))
        limit = int(params.get("limit", self._page))

        if path in (ENDPOINTS["map"]["path"], ENDPOINTS["assets_list"]["path"]):
            return self._envelope("rwa_assets", self._assets, start, limit)

        if path == ENDPOINTS["issuers_list"]["path"]:
            return self._envelope("issuers", self._issuers, start, limit)

        if path == ENDPOINTS["quotes"]["path"]:
            wanted = [x for x in str(params.get("rwa_id", "")).split(",") if x]
            self.quote_calls += 1
            self.quote_batch_sizes.append(len(wanted))
            out = []
            for a in self._assets:
                rid = str(a["rwa_id"])
                if rid not in wanted:
                    continue
                rec = dict(a)
                rec["tokens"] = self._tokens.get(rid, [])
                tf = self._tradfi.get(rid, 0)
                rec["tradfi_markets"] = ([venue(*v) if isinstance(v, tuple) else venue(v)
                                          for v in tf] if isinstance(tf, list)
                                         else [venue("Binance")] * tf)
                out.append(rec)
            return {"rwa_assets": out, "total_size": len(out), "has_more": False}

        if path == ENDPOINTS["issuer"]["path"]:
            iid = str(params.get("issuer_id"))
            rec = next((i for i in self._issuers if str(i["issuer_id"]) == iid), None)
            if rec is None:
                return None
            return {"name": rec["name"], "website": rec["website"], "tokens": [],
                    "issuer_id": iid, "num_tokens": rec["num_tokens"],
                    "total_size": rec["num_tokens"], "has_more": True}

        if path == ENDPOINTS["info"]["path"]:
            wanted = [x for x in str(params.get("rwa_id", "")).split(",") if x]
            return {"rwa_assets": [
                {"rwa_id": int(r), "name": "x", "industry": "Mining",
                 "primary_exchange": "NYSE", "cik": "0000000001",
                 "about": {"website": "https://example.invalid"}}
                for r in wanted]}

        if path == ENDPOINTS["market_pairs"]["path"]:
            self._record(path, params,
                         "1006: Your API Key subscription plan doesn't support this endpoint.")
            return None

        if path == ENDPOINTS["crypto_info"]["path"]:
            self.chain_calls += 1
            wanted = [x for x in str(params.get("id", "")).split(",") if x]
            out = {}
            for cid in wanted:
                if cid in self._missing:
                    continue          # CMC omits unknown ids rather than erroring
                plat = self._platforms.get(cid)
                rec = {"id": int(cid), "symbol": f"C{cid}", "name": f"Coin {cid}"}
                if plat:
                    rec["platform"] = {"id": "1", "name": plat, "symbol": plat[:3].upper()}
                out[cid] = rec
            return out

        if path == "/v1/key/info":
            return {"plan": {}, "usage": {}}

        raise AssertionError(f"unexpected path {path}")


def run(api, **kw):
    kw.setdefault("max_assets", 9000)
    kw.setdefault("quote_batch", 100)
    kw.setdefault("max_quote_calls", 120)
    kw.setdefault("info_sample", 25)
    kw.setdefault("chain_lookup", True)
    kw.setdefault("max_chain_calls", 40)
    return build_snapshot(collect(api, **kw), api)


# --------------------------------------------------------------------------- #
# 1. THE REGRESSION TEST. Per-token attribution, not per-asset.
#
#    GOLD  cap 100 -> Alpha 50, Bravo 30, Charlie 20
#    TBILL cap  60 -> Alpha 40, Delta  20
#    STOCK cap  40 -> Bravo 40
#
#    Correct issuer totals: Alpha 90, Bravo 70, Charlie 20, Delta 20 = 200.
#    The per-asset mistake would give Alpha 160, Bravo 140, Charlie 100,
#    Delta 60 = 460 - more than twice the real market.
#
#    shares      .45   .35   .10   .10
#    sum of sq   .2025+.1225+.01+.01 = .345
#    HHI         3450.0     effective_n 1/.345 = 2.8986
#    top1/3/5    .45 / .90 / 1.00
# --------------------------------------------------------------------------- #
def test_per_token_attribution():
    print("\n[1] per-token attribution - the asset's cap is never counted twice")
    assets = [asset(1, "GOLD", "commodity", 100.0),
              asset(2, "TBILL", "treasury", 60.0),
              asset(3, "STOCK", "equity", 40.0)]
    issuers = [issuer("a1", "Alpha", 2), issuer("b2", "Bravo", 2),
               issuer("c3", "Charlie", 1), issuer("d4", "Delta", 1)]
    tokens = {
        "1": [token("a1", "Alpha", "AGLD", 50.0), token("b2", "Bravo", "BGLD", 30.0),
              token("c3", "Charlie", "CGLD", 20.0)],
        "2": [token("a1", "Alpha", "ATB", 40.0), token("d4", "Delta", "DTB", 20.0)],
        "3": [token("b2", "Bravo", "BST", 40.0)],
    }
    snap = run(FakeCMC(assets, issuers, tokens))
    o = snap["overall"]
    rows = {r["name"]: r for r in snap["issuers"]}

    check("market totals 200, not the 460 per-asset counting would give",
          approx(o["total"], 200.0), f"got {o['total']}")
    check("Alpha totals 90 across two assets", approx(rows["Alpha"]["market_cap"], 90.0),
          f"got {rows['Alpha']['market_cap']}")
    check("Bravo totals 70", approx(rows["Bravo"]["market_cap"], 70.0),
          f"got {rows['Bravo']['market_cap']}")
    check("HHI is 3450.0", approx(o["hhi"], 3450.0, 0.05), f"got {o['hhi']}")
    check("effective_n is 2.90", approx(o["effective_n"], 2.90, 0.005), f"got {o['effective_n']}")
    check("verdict is highly concentrated", o["verdict"] == "highly concentrated", o["verdict"])
    check("top1 is 0.45", approx(o["top1"], 0.45, 1e-9), f"got {o['top1']}")
    check("top3 is 0.90", approx(o["top3"], 0.90, 1e-9), f"got {o['top3']}")
    check("top5 is 1.00", approx(o["top5"], 1.00, 1e-9), f"got {o['top5']}")
    check("four issuers carry value", o["n"] == 4, f"got {o['n']}")
    check("Alpha is the leader", o["leaders"][0]["label"] == "Alpha", o["leaders"][0]["label"])

    # asset classes: commodity {50,30,20}=100 -> HHI 3800; treasury {40,20}=60 ->
    # shares 2/3,1/3 -> 5555.6; equity single issuer -> 10000
    cls = snap["by_asset_class"]
    check("commodity HHI is 3800", approx(cls["commodity"]["hhi"], 3800.0, 0.05),
          f"got {cls['commodity']['hhi']}")
    check("treasury HHI is 5555.6", approx(cls["treasury"]["hhi"], 5555.6, 0.1),
          f"got {cls['treasury']['hhi']}")
    check("single-issuer equity class is a 10000 monopoly",
          approx(cls["equity"]["hhi"], 10000.0, 0.05), f"got {cls['equity']['hhi']}")
    check("asset classes ordered by size, commodity first",
          list(cls.keys())[0] == "commodity", list(cls.keys()))

    # the top-assets breakdown is the illustration of the whole point
    gold = next(a for a in snap["top_assets"] if a["symbol"] == "GOLD")
    check("GOLD is shown as split across 3 issuers", gold["issuers"] == 3, f"got {gold['issuers']}")
    check("GOLD's top issuer holds 50%", approx(gold["top_issuer_share"], 0.5, 1e-9),
          f"got {gold['top_issuer_share']}")
    check("reconciliation ratio is 1.0",
          approx(snap["coverage"]["reconciliation"]["ratio"], 1.0, 1e-9),
          f"got {snap['coverage']['reconciliation']['ratio']}")
    check("Growth-tier refusal recorded, not raised", len(snap["api"]["refused"]) == 1,
          f"got {snap['api']['refused']}")
    check("all seven RWA endpoints plus the cross-family one are declared",
          len(snap["endpoints"]) == 8, f"got {len(snap['endpoints'])}")
    check("info enrichment reached the top assets",
          gold["industry"] == "Mining", f"got {gold['industry']!r}")


# --------------------------------------------------------------------------- #
# 2. Reconciliation must surface a shortfall rather than absorb it.
# --------------------------------------------------------------------------- #
def test_reconciliation_gap():
    print("\n[2] token caps that do not add up to the asset cap are reported")
    assets = [asset(1, "GOLD", "commodity", 100.0)]      # asset says 100
    issuers = [issuer("a1", "Alpha")]
    tokens = {"1": [token("a1", "Alpha", "AGLD", 70.0)]}  # tokens only cover 70
    snap = run(FakeCMC(assets, issuers, tokens))
    r = snap["coverage"]["reconciliation"]
    check("ratio is 0.7", approx(r["ratio"], 0.7, 1e-9), f"got {r['ratio']}")
    check("difference is -30", approx(r["difference"], -30.0, 1e-6), f"got {r['difference']}")
    check("concentration uses the attributed 70, not the asset's 100",
          approx(snap["overall"]["total"], 70.0), f"got {snap['overall']['total']}")


# --------------------------------------------------------------------------- #
# 3. Tokens with no issuer, and issuers absent from the directory.
# --------------------------------------------------------------------------- #
def test_unattributed_and_off_directory():
    print("\n[3] unattributed tokens and issuers missing from the directory")
    assets = [asset(1, "GOLD", "commodity", 100.0)]
    issuers = [issuer("a1", "Alpha")]
    tokens = {"1": [
        token("a1", "Alpha", "AGLD", 60.0),
        token("z9", "Zulu", "ZGLD", 30.0),          # real value, not in the directory
        {"symbol": "ORPHAN", "name": "Orphan", "market_cap": 10.0, "volume_24h": 0.0},
    ]}
    snap = run(FakeCMC(assets, issuers, tokens))
    c = snap["coverage"]
    rows = {r["name"]: r for r in snap["issuers"]}

    check("orphan token counted as unattributed", c["tokens_without_issuer"] == 1,
          f"got {c['tokens_without_issuer']}")
    check("its 10 of value is reported, not silently dropped",
          approx(c["value_without_issuer"], 10.0), f"got {c['value_without_issuer']}")
    check("orphan value excluded from the concentration total",
          approx(snap["overall"]["total"], 90.0), f"got {snap['overall']['total']}")
    check("off-directory issuer flagged", c["issuers_outside_directory"] == 1,
          f"got {c['issuers_outside_directory']}")
    check("off-directory issuer still counted - the value is real",
          approx(rows["Zulu"]["market_cap"], 30.0), f"got {rows.get('Zulu')}")
    check("Zulu marked as outside the directory", rows["Zulu"]["in_directory"] is False)
    check("Alpha marked as inside it", rows["Alpha"]["in_directory"] is True)
    check("Alpha carries its website from the directory",
          rows["Alpha"]["site"].startswith("https://"), rows["Alpha"]["site"])


# --------------------------------------------------------------------------- #
# 4. The quotes envelope: `quotes` is a LIST, not an object keyed by currency.
# --------------------------------------------------------------------------- #
def test_quote_envelope():
    print("\n[4] USD quote read from a list, and from the flattened copy")
    listed = asset(1, "GOLD", "commodity", 123.0)
    flat = asset(2, "TBILL", "treasury", 456.0, flattened=True)
    check("USD entry found in the quotes list", approx(asset_cap(listed), 123.0),
          f"got {asset_cap(listed)}")
    check("flattened top-level cap read too", approx(asset_cap(flat), 456.0),
          f"got {asset_cap(flat)}")
    check("a non-USD-only quotes list yields 0, not a wrong currency",
          asset_cap({"quotes": [{"symbol": "EUR", "tokenized_market_cap": 999.0}]}) == 0.0)
    check("usd_quote returns {} when quotes is an object, not a list",
          usd_quote({"quotes": {"USD": {"tokenized_market_cap": 1.0}}}) == {})

    both = run(FakeCMC([listed, flat], [issuer("a1", "Alpha")],
                       {"1": [token("a1", "Alpha", "A", 100.0)],
                        "2": [token("a1", "Alpha", "B", 400.0)]}))
    rec = both["coverage"]["reconciliation"]
    check("both envelope styles reconcile together",
          approx(rec["sum_of_asset_caps"], 579.0), f"got {rec['sum_of_asset_caps']}")


# --------------------------------------------------------------------------- #
# 5. Batching and the credit ceiling.
# --------------------------------------------------------------------------- #
def test_batching_and_ceiling():
    print("\n[5] quotes/latest batching and the credit ceiling")
    assets = [asset(i, f"A{i}", "commodity", 10.0) for i in range(1, 251)]
    issuers = [issuer("a1", "Alpha")]
    tokens = {str(i): [token("a1", "Alpha", f"T{i}", 10.0)] for i in range(1, 251)}

    api = FakeCMC(assets, issuers, tokens)
    snap = run(api, quote_batch=100)
    check("250 assets batched into 3 quotes calls", api.quote_calls == 3,
          f"got {api.quote_calls}")
    check("batch sizes are 100, 100, 50", api.quote_batch_sizes == [100, 100, 50],
          f"got {api.quote_batch_sizes}")
    check("all batches reported as run", snap["coverage"]["assets_fully_quoted"] is True)
    check("total is 250 x 10", approx(snap["overall"]["total"], 2500.0),
          f"got {snap['overall']['total']}")

    api2 = FakeCMC(assets, issuers, tokens)
    snap2 = run(api2, quote_batch=100, max_quote_calls=2)
    check("ceiling stops the third batch", api2.quote_calls == 2, f"got {api2.quote_calls}")
    check("partial coverage is declared, not hidden",
          snap2["coverage"]["assets_fully_quoted"] is False)
    check("partial run reports 2 of 3 batches",
          (snap2["coverage"]["quote_batches_run"], snap2["coverage"]["quote_batches_total"]) == (2, 3),
          f"got {snap2['coverage']['quote_batches_run']}/{snap2['coverage']['quote_batches_total']}")

    # Assets flagged has_tokens: false must never be quoted - that is wasted credit.
    quiet = [asset(1, "A", "commodity", 5.0, has_tokens=False),
             asset(2, "B", "commodity", 5.0)]
    api3 = FakeCMC(quiet, issuers, {"2": [token("a1", "Alpha", "T", 5.0)]})
    run(api3)
    check("untokenised assets are not quoted", api3.quote_batch_sizes == [1],
          f"got {api3.quote_batch_sizes}")


# --------------------------------------------------------------------------- #
# 6. tradfi overlay, and issuer cross-check against the declared token count.
# --------------------------------------------------------------------------- #
def test_tradfi_and_crosscheck():
    print("\n[6] tradfi reference overlay and the declared-token cross-check")
    assets = [asset(1, "GOLD", "commodity", 70.0), asset(2, "OPAQUE", "private credit", 30.0)]
    issuers = [issuer("a1", "Alpha", num_tokens=9), issuer("b2", "Bravo", num_tokens=1)]
    tokens = {"1": [token("a1", "Alpha", "AGLD", 70.0)],
              "2": [token("b2", "Bravo", "BOP", 30.0)]}
    snap = run(FakeCMC(assets, issuers, tokens, tradfi={"1": 2, "2": 0}))
    tf = snap["tradfi_reference"]
    rows = {r["name"]: r for r in snap["issuers"]}

    check("both assets checked", tf["assets_checked"] == 2, f"got {tf['assets_checked']}")
    check("one has a traditional market", tf["assets_with_tradfi_market"] == 1,
          f"got {tf['assets_with_tradfi_market']}")
    check("Alpha's asset has an underlying market",
          rows["Alpha"]["assets_with_tradfi_market"] == 1)
    check("Bravo's does not", rows["Bravo"]["assets_with_tradfi_market"] == 0)
    check("Alpha declares 9 tokens but only 1 is attributed",
          (rows["Alpha"]["declared_tokens"], rows["Alpha"]["attributed_tokens"]) == (9, 1),
          f"got {rows['Alpha']['declared_tokens']}/{rows['Alpha']['attributed_tokens']}")


# --------------------------------------------------------------------------- #
# 7. Degenerate input must not manufacture certainty.
# --------------------------------------------------------------------------- #
def test_degenerate():
    print("\n[7] empty and zero-value inputs")
    snap = run(FakeCMC([], [], {}))
    check("empty run yields no HHI rather than 0.0", snap["overall"]["hhi"] is None,
          f"got {snap['overall']['hhi']}")
    check("empty run reports the no-data verdict", snap["overall"]["verdict"] == "no data")
    check("empty run has no reconciliation ratio",
          snap["coverage"]["reconciliation"]["ratio"] is None)

    zero = run(FakeCMC([asset(1, "Z", "other", 0.0)], [issuer("a1", "Alpha")],
                       {"1": [token("a1", "Alpha", "ZT", 0.0)]}))
    check("zero-value issuer excluded from concentration", zero["overall"]["n"] == 0,
          f"got {zero['overall']['n']}")
    check("zero-value token still counted as attributed",
          zero["counts"]["tokens_attributed"] == 1)

    b = concentration_block({"only": 5.0})
    check("a single participant scores 10000", approx(b["hhi"], 10000.0, 1e-6), f"got {b['hhi']}")
    check("and an effective count of 1", approx(b["effective_n"], 1.0, 1e-6),
          f"got {b['effective_n']}")
    b2 = concentration_block({f"i{i}": 1.0 for i in range(10)})
    check("ten equal participants score 1000", approx(b2["hhi"], 1000.0, 1e-6), f"got {b2['hhi']}")
    check("and an effective count of 10", approx(b2["effective_n"], 10.0, 1e-6),
          f"got {b2['effective_n']}")
    check("ten equal participants read as unconcentrated", b2["verdict"] == "unconcentrated")


# --------------------------------------------------------------------------- #
# 8. Null market caps. Roughly a fifth of live tokens report market_cap: null.
#    Treating them as zero is arithmetically harmless and editorially dishonest -
#    they have to be counted and published.
# --------------------------------------------------------------------------- #
def test_null_market_caps():
    print("\n[8] tokens reporting a null market cap are counted, not silently zeroed")
    assets = [asset(1, "NVDA", "stock", 100.0)]
    issuers = [issuer("a1", "Alpha"), issuer("b2", "Bravo")]
    tokens = {"1": [token("a1", "Alpha", "NVDAX", 60.0),
                    token("b2", "Bravo", "NVDA.D", None),     # null, as Dinari's is
                    token("b2", "Bravo", "NVDAon", 40.0)]}
    snap = run(FakeCMC(assets, issuers, tokens))
    c = snap["coverage"]
    rows = {r["name"]: r for r in snap["issuers"]}

    check("the null-cap token is counted", c["tokens_without_market_cap"] == 1,
          f"got {c['tokens_without_market_cap']}")
    check("all three tokens still counted as attributed",
          snap["counts"]["tokens_attributed"] == 3)
    check("a null contributes nothing to the total",
          approx(snap["overall"]["total"], 100.0), f"got {snap['overall']['total']}")
    check("Bravo's total is 40, not 40-plus-a-guess",
          approx(rows["Bravo"]["market_cap"], 40.0), f"got {rows['Bravo']['market_cap']}")
    check("Bravo's null-cap token counted against it",
          rows["Bravo"]["tokens_without_cap"] == 1, f"got {rows['Bravo']['tokens_without_cap']}")
    check("Alpha has none", rows["Alpha"]["tokens_without_cap"] == 0)


# --------------------------------------------------------------------------- #
# 9. Chains come from a cross-family join. No RWA endpoint carries one.
# --------------------------------------------------------------------------- #
def test_chain_join():
    print("\n[9] chain view built from crypto_id via /v2/cryptocurrency/info")
    assets = [asset(1, "GOLD", "commodity", 100.0), asset(2, "NVDA", "stock", 130.0)]
    issuers = [issuer("a1", "Alpha"), issuer("b2", "Bravo")]
    tokens = {
        "1": [token("a1", "Alpha", "PAXG", 60.0, crypto_id=10),
              token("b2", "Bravo", "XAUt", 40.0, crypto_id=11)],
        "2": [token("a1", "Alpha", "NVDAX", 80.0, crypto_id=12),
              token("b2", "Bravo", "NVDAon", 20.0, crypto_id=13),   # native coin
              token("b2", "Bravo", "NVDA.x", 30.0, crypto_id=14)],  # unknown to CMC
    }
    platforms = {"10": "Ethereum", "11": "Ethereum", "12": "Solana"}
    api = FakeCMC(assets, issuers, tokens, platforms=platforms, missing_ids={"14"})
    snap = run(api)
    ch = snap["by_chain"]

    check("one batched chain lookup, not one per token", api.chain_calls == 1,
          f"got {api.chain_calls}")
    check("Ethereum and Solana both resolved", set(ch) >= {"Ethereum", "Solana"}, str(set(ch)))
    check("Ethereum totals 100", approx(ch["Ethereum"]["total"], 100.0),
          f"got {ch['Ethereum']['total']}")
    check("Ethereum holds two issuers", ch["Ethereum"]["n"] == 2, f"got {ch['Ethereum']['n']}")
    check("Ethereum HHI is 5200 (60/40 split)", approx(ch["Ethereum"]["hhi"], 5200.0, 0.05),
          f"got {ch['Ethereum']['hhi']}")
    check("Solana is a single-issuer chain", ch["Solana"]["n"] == 1, f"got {ch['Solana']['n']}")
    # A coin with no `platform` is not an error: it is a native asset, and its
    # chain is itself. Filing it as "unknown" would be wrong.
    check("a coin with no platform becomes its own chain", "Coin 13" in ch, str(set(ch)))
    check("chains ordered by size, Ethereum first", list(ch)[0] == "Ethereum", list(ch))
    check("chain_mix measures across chains, not within one",
          snap["chain_mix"]["n"] == 3, f"got {snap['chain_mix']['n']}")
    # 100 / 80 / 20 across three chains -> shares .5 / .4 / .1 -> HHI 4200
    check("chain_mix HHI is 4200", approx(snap["chain_mix"]["hhi"], 4200.0, 0.05),
          f"got {snap['chain_mix']['hhi']}")
    check("an id CMC does not know leaves its value unplaced, and says so",
          approx(snap["coverage"]["value_without_a_chain"], 30.0),
          f"got {snap['coverage']['value_without_a_chain']}")
    check("unplaced value is still in the whole-market total",
          approx(snap["overall"]["total"], 230.0), f"got {snap['overall']['total']}")
    check("chains_resolved counts chains, not tokens",
          snap["coverage"]["chains_resolved"] == 3,
          f"got {snap['coverage']['chains_resolved']}")
    check("issuers carry the chains they were seen on",
          {r["name"]: r["chains"] for r in snap["issuers"]}["Alpha"] == ["Ethereum", "Solana"],
          str({r["name"]: r["chains"] for r in snap["issuers"]}))

    off = run(FakeCMC(assets, issuers, tokens, platforms=platforms), chain_lookup=False)
    check("--no-chains skips the lookup entirely", off["by_chain"] == {}, str(off["by_chain"]))
    check("and the whole-market figures are unaffected",
          approx(off["overall"]["total"], snap["overall"]["total"]))


# --------------------------------------------------------------------------- #
# 10. tradfi venues, and placeholder issuers that declare tokens worth nothing.
# --------------------------------------------------------------------------- #
def test_venues_and_placeholders():
    print("\n[10] venue concentration and placeholder issuers")
    assets = [asset(1, "NVDA", "stock", 100.0), asset(2, "TSLA", "stock", 50.0)]
    issuers = [issuer("a1", "Alpha"), issuer("na", "NA (Derivatives)", num_tokens=239)]
    tokens = {"1": [token("a1", "Alpha", "NVDAX", 100.0),
                    token("na", "NA (Derivatives)", "NVDA", 0.0)],
              "2": [token("a1", "Alpha", "TSLAX", 50.0)]}
    # Every listing on one venue - which is what the live data shows.
    tf = {"1": ["Binance", "Binance"], "2": ["Binance"]}
    snap = run(FakeCMC(assets, issuers, tokens, tradfi=tf))
    t = snap["tradfi_reference"]
    rows = {r["name"]: r for r in snap["issuers"]}

    check("venues are named, not just counted",
          t["venues"] and t["venues"][0]["name"] == "Binance", str(t["venues"]))
    check("three listings counted across two assets",
          t["venues"][0]["listings"] == 3, str(t["venues"]))
    check("a single venue scores 10000 on venue concentration",
          approx(t["venue_concentration"]["hhi"], 10000.0, 0.05),
          f"got {t['venue_concentration']['hhi']}")
    check("both assets have a listing", t["assets_with_tradfi_market"] == 2)
    check("the zero-value entity is flagged as a placeholder",
          rows["NA (Derivatives)"]["placeholder"] is True)
    check("and a real issuer is not", rows["Alpha"]["placeholder"] is False)
    check("placeholders counted in coverage",
          snap["coverage"]["placeholder_issuers"] == 1,
          f"got {snap['coverage']['placeholder_issuers']}")
    check("a placeholder cannot move the concentration figure",
          snap["overall"]["n"] == 1, f"got {snap['overall']['n']}")


# --------------------------------------------------------------------------- #
# 11. The client, against real response envelopes.
#
#     This is the test whose absence cost a production run. Every other test
#     replaces CMC.get() wholesale, so the client's own status handling was never
#     exercised: /v1/key/info returns the INTEGER 0 on success and every
#     /v5/real-world-assets endpoint returns the STRING "0". `if
#     status.get("error_code")` therefore passed the plan check and rejected all
#     seven data endpoints, because "0" is truthy. Four calls, two credits, and an
#     empty snapshot that the publish gate caught.
# --------------------------------------------------------------------------- #
class StubResponse:
    def __init__(self, payload, status_code=200, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class StubSession:
    """Stands in for requests.Session, so the REAL CMC.get() runs."""

    def __init__(self, script):
        self.script = list(script)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.script.pop(0)


def client_with(script):
    api = CMC("dummy-key", rate_limit_minute=6000)   # no real throttling in tests
    stub = StubSession(script)
    stub.headers.update(api.s.headers)               # keep the auth header the client set
    api.s = stub
    return api


def test_client_envelopes():
    print("\n[11] the client against real CMC status envelopes")

    check("integer 0 reads as success", error_code({"error_code": 0}) is None)
    check("STRING \"0\" also reads as success — the bug that broke the first run",
          error_code({"error_code": "0"}) is None, repr(error_code({"error_code": "0"})))
    check("a real code survives as an error", error_code({"error_code": 1006}) == 1006)
    check("a real code as a string survives too", error_code({"error_code": "1006"}) == "1006")
    check("a missing code is success", error_code({}) is None)
    check("an unparseable code is not waved through",
          error_code({"error_code": "nope"}) == "nope")

    # v5 success: string "0", data under an envelope key
    api = client_with([StubResponse(
        {"status": {"error_code": "0", "error_message": "", "credit_count": 1},
         "data": {"rwa_assets": [{"rwa_id": 1, "symbol": "GOLD"}],
                  "total_size": 1, "has_more": False}})])
    data = api.get(ENDPOINTS["map"]["path"], limit=250)
    check("a v5 call with error_code \"0\" returns its data",
          isinstance(data, dict) and len(data.get("rwa_assets", [])) == 1, repr(data))
    check("and is not recorded as refused", api.refused == [], str(api.refused))
    check("its credit is counted", api.credits == 1, f"got {api.credits}")

    # v1 success: integer 0
    api2 = client_with([StubResponse(
        {"status": {"error_code": 0, "error_message": None, "credit_count": 0},
         "data": {"plan": {"credit_limit_monthly": 15000, "rate_limit_minute": 50},
                  "usage": {"current_month": {"credits_used": 0, "credits_left": 15000}}}})])
    api2.read_plan()
    check("key/info's integer 0 still reads as success",
          api2.plan.get("credit_limit_monthly") == 15000, str(api2.plan))
    check("and the real rate limit is adopted",
          abs(api2.min_interval - 60.0 / 46) < 1e-9, f"got {api2.min_interval}")

    # plan gate
    api3 = client_with([StubResponse(
        {"status": {"error_code": 1006,
                    "error_message": "Your API Key subscription plan doesn't support this endpoint.",
                    "credit_count": 0}}, status_code=403)])
    out = api3.get(ENDPOINTS["market_pairs"]["path"], **{PARAM["rwa_id"]: "1"})
    check("a plan-gated call returns None", out is None)
    check("and is recorded with its reason", len(api3.refused) == 1 and
          "1006" in api3.refused[0]["reason"], str(api3.refused))

    # 429 then success
    api4 = client_with([
        StubResponse(None, status_code=429),
        StubResponse({"status": {"error_code": "0", "credit_count": 1},
                      "data": {"issuers": [{"issuer_id": "a1", "name": "Alpha"}],
                               "total_size": 1, "has_more": False}}),
    ])
    api4.max_retries = 3
    import fetch_snapshot as fs
    real_sleep, fs.time.sleep = fs.time.sleep, lambda *_: None
    try:
        d4 = api4.get(ENDPOINTS["issuers_list"]["path"], limit=250)
    finally:
        fs.time.sleep = real_sleep
    check("a 429 is retried rather than recorded as a failure",
          isinstance(d4, dict) and len(d4.get("issuers", [])) == 1, repr(d4))
    check("and the retry is not counted as a refusal", api4.refused == [], str(api4.refused))

    # non-JSON
    api5 = client_with([StubResponse(None, status_code=502, text="<html>bad gateway</html>")])
    check("a non-JSON body returns None", api5.get("/v5/real-world-assets/map") is None)
    check("and is recorded", len(api5.refused) == 1 and "non-JSON" in api5.refused[0]["reason"],
          str(api5.refused))

    # the key must never reach a URL or a recorded param
    api6 = client_with([StubResponse({"status": {"error_code": "0"}, "data": {}})])
    api6.get(ENDPOINTS["quotes"]["path"], **{PARAM["rwa_id"]: "1,2,3"})
    url, params = api6.s.calls[0]
    check("the key is not in the URL", "dummy-key" not in url, url)
    check("the key is not in the params", "dummy-key" not in json.dumps(params), str(params))
    check("the key travels as a header",
          api6.s.headers.get("X-CMC_PRO_API_KEY") == "dummy-key", str(api6.s.headers))


# --------------------------------------------------------------------------- #
# 12. End-to-end through the REAL client, with realistic envelopes.
#
#     The seam is where this broke: client and aggregation were each correct and
#     the join between them was not. So drive collect() through CMC.get() with
#     nothing stubbed but the HTTP call itself.
# --------------------------------------------------------------------------- #
def test_end_to_end_through_real_client():
    print("\n[12] collect() end to end through the real client")

    def env(key, items, total=None, more=False, credit=1):
        return StubResponse({"status": {"error_code": "0", "error_message": "", "credit_count": credit},
                             "data": {key: items, "total_size": total if total is not None else len(items),
                                      "has_more": more}})

    assets = [asset(1, "GOLD", "commodity", 100.0), asset(2, "NVDA", "stock", 60.0)]
    tokens1 = [token("a1", "Alpha", "PAXG", 70.0, crypto_id=10),
               token("b2", "Bravo", "XAUt", 30.0, crypto_id=11)]
    tokens2 = [token("a1", "Alpha", "NVDAX", 60.0, crypto_id=12)]

    quotes_payload = StubResponse({
        "status": {"error_code": "0", "credit_count": 1},
        "data": {"rwa_assets": [
            {**assets[0], "tokens": tokens1, "tradfi_markets": []},
            {**assets[1], "tokens": tokens2,
             "tradfi_markets": [venue("Binance", "NVDA")]},
        ], "total_size": 2, "has_more": False}})

    script = [
        StubResponse({"status": {"error_code": 0, "credit_count": 0},
                      "data": {"plan": {"credit_limit_monthly": 15000, "rate_limit_minute": 50},
                               "usage": {"current_month": {"credits_left": 15000}}}}),  # key/info
        env("rwa_assets", assets, credit=0),        # map
        env("issuers", [issuer("a1", "Alpha", 2), issuer("b2", "Bravo", 1)]),
        env("rwa_assets", assets),                  # assets/list
        quotes_payload,                             # quotes/latest
        StubResponse({"status": {"error_code": "0", "credit_count": 1},
                      "data": {"10": {"id": 10, "symbol": "PAXG",
                                      "platform": {"name": "Ethereum"}},
                               "11": {"id": 11, "symbol": "XAUt",
                                      "platform": {"name": "Ethereum"}},
                               "12": {"id": 12, "symbol": "NVDAX",
                                      "platform": {"name": "Solana"}}}}),   # crypto info
        env("tokens", [], total=2),                 # issuer detail a1
        env("tokens", [], total=1),                 # issuer detail b2
        env("rwa_assets", [{"rwa_id": 1, "industry": "Mining"}]),           # info
        StubResponse({"status": {"error_code": 1006,
                                 "error_message": "plan doesn't support this endpoint"}}),  # market-pairs
    ]

    api = client_with(script)
    import fetch_snapshot as fs
    real_sleep, fs.time.sleep = fs.time.sleep, lambda *_: None
    try:
        snap = build_snapshot(collect(api, max_assets=9000, quote_batch=100,
                                      max_quote_calls=120, info_sample=25), api)
    finally:
        fs.time.sleep = real_sleep

    o = snap["overall"]
    check("the run attributes value rather than producing an empty snapshot",
          o["n"] > 0, f"got {o['n']} issuers with value")
    check("both issuers land", o["n"] == 2, f"got {o['n']}")
    check("total is 160", approx(o["total"], 160.0), f"got {o['total']}")
    # Alpha 70+60=130, Bravo 30 -> shares .8125 / .1875
    #   .8125^2 + .1875^2 = .66015625 + .03515625 = .6953125 -> HHI 6953.1
    check("HHI is 6953.1", approx(o["hhi"], 6953.1, 0.1), f"got {o['hhi']}")
    check("no list endpoint was recorded as refused",
          [r for r in api.refused if "market-pairs" not in r["path"]] == [],
          str(api.refused))
    check("only the plan-gated endpoint is refused", len(api.refused) == 1, str(api.refused))
    check("chains resolved through the real client", len(snap["by_chain"]) == 2,
          str(list(snap["by_chain"])))
    check("the Binance listing is picked up",
          snap["tradfi_reference"]["venues"] and
          snap["tradfi_reference"]["venues"][0]["name"] == "Binance",
          str(snap["tradfi_reference"]["venues"]))
    check("credits are accumulated from the envelopes", api.credits >= 7, f"got {api.credits}")


# --------------------------------------------------------------------------- #
# 13. The residuals the README leans on, each pinned by a fixture.
#
#     An outside reviewer's fair challenge: if the caveats are only prose, they
#     are not caveats. Each one below is a claim the write-up makes, with a
#     fixture that makes it false if the code stops reporting it.
# --------------------------------------------------------------------------- #
def test_residuals_are_measured_not_asserted():
    print("\n[13] every residual the write-up claims, pinned to a fixture")

    # A clean aggregate hiding one broken asset: 100 short by 30, 100 over by 30.
    assets = [asset(1, "FINE", "stock", 100.0), asset(2, "BROKEN", "stock", 100.0)]
    issuers = [issuer("a1", "Alpha"), issuer("b2", "Bravo")]
    tokens = {"1": [token("a1", "Alpha", "F1", 100.0, crypto_id=10)],
              "2": [token("b2", "Bravo", "B1", 130.0, crypto_id=11)]}
    snap = run(FakeCMC(assets, issuers, tokens, platforms={"10": "Ethereum", "11": "Ethereum"}))
    rec = snap["coverage"]["reconciliation"]
    check("aggregate ratio looks healthy at 1.15", approx(rec["ratio"], 1.15, 1e-4),
          f"got {rec['ratio']}")
    check("but the per-asset check still names the broken one",
          rec["assets_off_by_over_1pct"] == 1, f"got {rec['assets_off_by_over_1pct']}")
    check("and names it by symbol",
          rec["worst_assets"] and rec["worst_assets"][0]["symbol"] == "BROKEN",
          str(rec["worst_assets"]))
    check("the healthy asset is not flagged",
          all(w["symbol"] != "FINE" for w in rec["worst_assets"]), str(rec["worst_assets"]))

    # crypto_ids the cryptocurrency endpoint never returns
    api = FakeCMC(assets, issuers, tokens, platforms={"10": "Ethereum"}, missing_ids={"11"})
    snap2 = run(api)
    check("an id the chain endpoint omits is counted",
          snap2["coverage"]["unmatched_crypto_ids"] == 1,
          f"got {snap2['coverage']['unmatched_crypto_ids']}")
    check("and its value is reported as unplaced",
          snap2["coverage"]["value_without_a_chain"] > 0,
          f"got {snap2['coverage']['value_without_a_chain']}")

    # null caps attributed to the issuer that holds them
    assets3 = [asset(1, "A", "stock", 50.0)]
    tokens3 = {"1": [token("a1", "Alpha", "T1", 50.0),
                     token("a1", "Alpha", "T2", None),
                     token("a1", "Alpha", "T3", None),
                     token("b2", "Bravo", "T4", None)]}
    snap3 = run(FakeCMC(assets3, issuers, tokens3))
    by = {r["issuer"]: r["tokens_without_cap"] for r in snap3["coverage"]["null_caps_by_issuer"]}
    check("null caps are attributed to the issuer holding them",
          by.get("Alpha") == 2, str(snap3["coverage"]["null_caps_by_issuer"]))
    check("and the ranking is ordered, worst first",
          snap3["coverage"]["null_caps_by_issuer"][0]["issuer"] == "Alpha",
          str(snap3["coverage"]["null_caps_by_issuer"]))

    # concentration across assets, not only across issuers - a market can look
    # diverse by issuer while being one asset wearing several coats
    assets4 = [asset(1, "BIG", "commodity", 900.0), asset(2, "SMALL", "stock", 100.0)]
    tokens4 = {"1": [token("a1", "Alpha", "X", 450.0), token("b2", "Bravo", "Y", 450.0)],
               "2": [token("a1", "Alpha", "Z", 100.0)]}
    snap4 = run(FakeCMC(assets4, issuers, tokens4))
    am, iss = snap4["asset_mix"], snap4["overall"]
    # assets .9/.1 -> HHI 8200 ; issuers 550/450 -> .55/.45 -> 5050
    check("asset_mix measures concentration across assets",
          approx(am["hhi"], 8200.0, 0.1), f"got {am['hhi']}")
    check("and is not the issuer figure wearing a different label",
          approx(iss["hhi"], 5050.0, 0.1) and am["hhi"] != iss["hhi"],
          f"asset {am['hhi']} vs issuer {iss['hhi']}")
    check("the dominant asset is named", am["leaders"][0]["label"] == "BIG",
          str(am["leaders"][:2]))


if __name__ == "__main__":
    test_per_token_attribution()
    test_reconciliation_gap()
    test_unattributed_and_off_directory()
    test_quote_envelope()
    test_batching_and_ceiling()
    test_tradfi_and_crosscheck()
    test_degenerate()
    test_null_market_caps()
    test_chain_join()
    test_venues_and_placeholders()
    test_client_envelopes()
    test_end_to_end_through_real_client()
    test_residuals_are_measured_not_asserted()

    print("\n" + "-" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")
