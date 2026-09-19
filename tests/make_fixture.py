#!/usr/bin/env python3
"""
Generate a SYNTHETIC snapshot with the same shape as a real run, for building and
reviewing the dashboard while the live API is unreachable from this environment.

It drives the real collect() and build_snapshot() through the test harness's
FakeCMC, so the fixture cannot drift from the pipeline's actual output.

Every issuer name is obviously invented and the snapshot carries
"synthetic": true, which the dashboard renders as a warning banner and the
refresh workflow refuses to publish. Nothing here is market data.

Run:  python3 tests/make_fixture.py
Out:  tests/fixtures/snapshot.sample.json
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from test_aggregation import FakeCMC, asset, issuer, token  # noqa: E402
from fetch_snapshot import build_snapshot, collect  # noqa: E402

RNG = random.Random(20260930)          # the submission deadline, for a stable fixture

# The live probe reported 25 issuers and 7,942 assets, ~7,811 of them mapped.
# The fixture keeps those proportions but a smaller asset count, so a regenerate
# is quick.
N_ISSUERS = 25
N_ASSETS = 600

# The live data is dominated by `stock`, with `commodity` a distant second.
CLASSES = [("stock", 72), ("commodity", 9), ("tokenised treasury", 7),
           ("private credit", 5), ("real estate", 3), ("money market fund", 2),
           ("corporate bond", 1), ("unclassified", 1)]

CHAINS = [("Ethereum", 46), ("Solana", 18), ("Base", 12), ("Polygon", 9),
          ("Arbitrum", 8), ("Avalanche", 7)]

# Live sampling: ~97% of assets carry a tradfi_markets entry and every one of
# them was Binance. The fixture keeps that shape, with a token presence from two
# others so the venue table is not a single row.
VENUES = [("Binance", 94), ("Bybit", 4), ("OKX", 2)]

# Roughly a fifth of live tokens report market_cap as null.
NULL_CAP_RATE = 0.20


def main() -> None:
    names = [f"Synthetic Issuer {i:02d}" for i in range(1, N_ISSUERS + 1)]
    ids = [f"issuer{i:02d}" for i in range(1, N_ISSUERS + 1)]

    # Issuer sizes follow a Zipf curve with multiplicative jitter: a few very
    # large issuers and a long thin tail. The exponent puts the fixture in the
    # middle of the scale rather than at the degenerate single-issuer end
    # a raw Pareto draw gives - the dashboard has to stay readable across the
    # whole range of the scale.
    weights = [(1.0 / (i ** 1.25)) * RNG.uniform(0.75, 1.3) for i in range(1, N_ISSUERS + 1)]
    total_w = sum(weights)
    market = 41_000_000_000.0                       # plausible order of magnitude, not a claim
    issuer_budget = {ids[i]: market * (w / total_w) for i, w in enumerate(weights)}

    classes = [c for c, _ in CLASSES]
    cweights = [w for _, w in CLASSES]

    assets, tokens_by_asset, tradfi = [], {}, {}
    platforms: dict[str, str] = {}
    remaining = dict(issuer_budget)
    next_crypto_id = 1000

    chains = [c for c, _ in CHAINS]
    chain_w = [w for _, w in CHAINS]
    venues = [v for v, _ in VENUES]
    venue_w = [w for _, w in VENUES]

    for rwa_id in range(1, N_ASSETS + 1):
        cls = RNG.choices(classes, weights=cweights)[0]
        # Most assets are issued by one party; the largest are shared, which is
        # the case the per-token attribution exists for.
        n_iss = 1 if RNG.random() < 0.72 else RNG.randint(2, 5)
        picks = RNG.sample(range(N_ISSUERS), n_iss)
        toks, cap = [], 0.0
        for j, idx in enumerate(picks):
            iid = ids[idx]
            share = RNG.uniform(0.004, 0.06) if remaining[iid] > 0 else 0.0
            v = max(0.0, remaining[iid] * share)
            cid = next_crypto_id
            next_crypto_id += 1
            # A fifth of tokens report no market cap at all. They still exist and
            # are still counted; they just carry no value.
            if RNG.random() < NULL_CAP_RATE:
                toks.append(token(iid, names[idx], f"SYN{rwa_id:03d}{chr(65 + j)}",
                                  None, crypto_id=cid))
            else:
                remaining[iid] -= v
                cap += v
                toks.append(token(iid, names[idx], f"SYN{rwa_id:03d}{chr(65 + j)}",
                                  round(v, 2), crypto_id=cid))
            # A handful of tokens are native assets with no platform, and a
            # handful are ids the info endpoint does not know.
            # A tokenised RWA is, by definition, a token on a chain. A missing
            # platform is rare and means a native asset; keep the rate low so the
            # fixture is not dominated by one-token pseudo-chains.
            if RNG.random() > 0.005:
                platforms[str(cid)] = RNG.choices(chains, weights=chain_w)[0]
        has_tokens = cap > 0
        assets.append(asset(rwa_id, f"SYN{rwa_id:03d}", cls, round(cap, 2),
                            has_tokens=has_tokens))
        if has_tokens:
            tokens_by_asset[str(rwa_id)] = toks
        # Almost every asset carries a listing, and almost all of them at one venue.
        if RNG.random() < 0.95:
            tradfi[str(rwa_id)] = [RNG.choices(venues, weights=venue_w)[0]
                                   for _ in range(RNG.randint(1, 2))]
        else:
            tradfi[str(rwa_id)] = []

    issuers = [issuer(ids[i], names[i],
                      num_tokens=sum(1 for t in tokens_by_asset.values()
                                     for x in t if x["issuer_id"] == ids[i]) + RNG.randint(0, 3))
               for i in range(N_ISSUERS)]

    # One aggregate placeholder, as the live directory has: declares tokens,
    # every one of them worth nothing.
    issuers.append(issuer("placeholder", "NA (Derivatives)", num_tokens=239))
    for rwa_id in list(tokens_by_asset)[:40]:
        cid = next_crypto_id
        next_crypto_id += 1
        platforms[str(cid)] = "Ethereum"
        tokens_by_asset[rwa_id].append(
            token("placeholder", "NA (Derivatives)", f"NA{rwa_id}", 0.0, crypto_id=cid))

    api = FakeCMC(assets, issuers, tokens_by_asset, tradfi, platforms=platforms)
    snap = build_snapshot(collect(api, max_assets=9000, quote_batch=100,
                                  max_quote_calls=120, info_sample=25), api)

    snap["synthetic"] = True
    snap["synthetic_note"] = (
        "SYNTHETIC FIXTURE - generated by tests/make_fixture.py to develop and "
        "review the dashboard while pro-api.coinmarketcap.com was unreachable from "
        "the build environment. Issuer names are invented. No figure here is market "
        "data and none of it came from the CoinMarketCap API."
    )
    snap["plan"] = {"credit_limit_monthly": 15000, "rate_limit_minute": 50,
                    "credits_left_this_month": 14900}

    out = os.path.join(os.path.dirname(__file__), "fixtures", "snapshot.sample.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=1)

    o, c = snap["overall"], snap["coverage"]
    print(f"wrote {out}")
    print(f"  {o['n']} issuers, HHI {o['hhi']}, effective {o['effective_n']}")
    print(f"  top1 {o['top1']:.1%}  top3 {o['top3']:.1%}  top5 {o['top5']:.1%}")
    print(f"  {snap['counts']['tokens_attributed']} tokens over "
          f"{snap['counts']['assets_quoted']} assets, {len(snap['by_asset_class'])} classes")
    print(f"  reconciliation ratio {c['reconciliation']['ratio']}")
    print(f"  multi-issuer assets in the top 25: "
          f"{sum(1 for a in snap['top_assets'] if a['issuers'] > 1)}")
    print(f"  tokens with no market cap: {c['tokens_without_market_cap']}")
    print(f"  chains {len(snap['by_chain'])}, venues {len(snap['tradfi_reference']['venues'])}, "
          f"placeholder issuers {c['placeholder_issuers']}")


if __name__ == "__main__":
    main()
