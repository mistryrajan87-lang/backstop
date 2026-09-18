#!/usr/bin/env python3
"""Write the run's snapshot into docs/index.html.

Every number on the page used to arrive through `fetch("data/snapshot.json")`,
so the HTML source carried nothing but em-dashes. A crawler, a link unfurl, a
judge previewing rather than opening it, and a reader with scripting off all saw
the empty shell - and a 404 on the JSON produced the same dashes with the cards
hidden behind a banner.

This puts the snapshot *in* the file: as a JSON script block the page reads
first, and as static text in the hero, the reconcile line, the noscript box and
the social meta. data/snapshot.json stays exactly where it is, as the raw file
and as the fallback. One write, two copies, one reader.

Idempotent: every region is delimited by <!-- backstop:NAME:start/end --> and
replaced wholesale, so running twice is the same as running once.

It also rewrites the README's headline block from the same snapshot. That block was
hand-maintained until 18 Sep, by which point it was three runs behind the dashboard
it described - the exact failure this project is about.

    python scripts/inline_snapshot.py [docs_dir]     # default: docs
"""
from __future__ import annotations

import html
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

DOCS = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "docs")
PAGE = DOCS / "index.html"
SNAP = DOCS / "data" / "snapshot.json"


def region(text: str, name: str, body: str, where: object = None) -> str:
    """Replace everything between a marker pair. Missing markers are fatal -
    silently doing nothing is how a generator drifts out of its template."""
    start, end = f"<!-- backstop:{name}:start -->", f"<!-- backstop:{name}:end -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        raise SystemExit(f"marker pair backstop:{name} not found in {where or PAGE}")
    return text[: i + len(start)] + "\n" + body.rstrip() + "\n" + text[j:]


def set_text(text: str, el_id: str, value: str) -> str:
    """Replace the inner text of <... id="el_id">...</...>, first match only."""
    pat = re.compile(r'(id="' + re.escape(el_id) + r'"[^>]*>)(.*?)(</)', re.S)
    new, n = pat.subn(lambda m: m.group(1) + value + m.group(3), text, count=1)
    if n != 1:
        raise SystemExit(f"element #{el_id} not found in {PAGE}")
    return new


def main() -> None:
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    o = snap["overall"]
    counts = snap["counts"]
    recon = snap["coverage"]["reconciliation"]

    when = datetime.fromisoformat(snap["generated_at"]).astimezone(timezone.utc)
    stamp_short = when.strftime("%H:%M UTC")
    stamp_long = when.strftime("%a, %d %b %Y %H:%M:%S GMT")

    eff = f'{o["effective_n"]:.2f}'
    hhi = f'{o["hhi"]:,.1f}'
    total_bn = o["total"] / 1e9
    tokenised = f'{counts["assets_tokenised"]:,}'
    incomparable = recon.get("assets_priced_only_by_tokens", 0)

    page = PAGE.read_text(encoding="utf-8")

    # 1. the snapshot itself. "</" is escaped so the JSON cannot close its own
    #    script tag; JSON.parse reads \/ back as /.
    raw = json.dumps(snap, separators=(",", ":"), ensure_ascii=False)
    raw = raw.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    page = region(page, "snapdata",
                  f'<script type="application/json" id="snapdata">{raw}</script>')

    # 2. hero values, so the shell states the finding without running anything
    page = set_text(page, "hero-eff", eff)
    page = set_text(page, "hero-hhi", hhi)

    # 3. the reconcile line, on the first screen rather than only in Method.
    #    The incomparable six are a category, not a footnote.
    foot = (f'{tokenised} assets &middot; two endpoints &middot; '
            f'{incomparable} incomparable &middot; snapshot {html.escape(stamp_short)}')
    page = region(page, "herofoot", f'<p class="herofoot" id="herofoot">{foot}</p>')

    # 4. what the page says with scripting off
    ns = (
        '    <noscript>\n'
        '      <div class="nsbox">\n'
        f'        <b>{eff} effective issuers &middot; HHI {hhi}</b><br>\n'
        f'        {tokenised} of {counts["assets_mapped"]:,} catalogue rows carry a token, '
        f'worth ${total_bn:,.2f}bn between them, held by {o["n"]} issuers. '
        f'Snapshot {html.escape(stamp_long)}.<br>\n'
        '        &ldquo;Issuer&rdquo; is the label CoinMarketCap attaches to a token &mdash; '
        'not custody, jurisdiction, or who owes you the underlying.<br>\n'
        '        The cuts by chain and asset class need JavaScript; the raw figures are in\n'
        '        <a href="data/snapshot.json">data/snapshot.json</a>.\n'
        '      </div>\n'
        '    </noscript>'
    )
    page = region(page, "noscript", ns)

    # 5. social meta - the claim, not the stack
    desc = (f'Tokenised real-world assets on CoinMarketCap concentrate into {eff} effective '
            f'issuers (HHI {hhi}). Issuer here is a label, not a custodian.')
    title = "Backstop — issuer concentration in tokenised real-world assets"
    url = "https://mistryrajan87-lang.github.io/backstop/"
    meta = "\n".join([
        f'<meta property="og:type" content="website">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:title" content="{html.escape(title)}">',
        f'<meta property="og:description" content="{html.escape(desc)}">',
        f'<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{html.escape(title)}">',
        f'<meta name="twitter:description" content="{html.escape(desc)}">',
    ])
    page = region(page, "meta", meta)

    PAGE.write_text(page, encoding="utf-8", newline="")

    # The README quotes the same figures to a judge who may never open the page.
    readme = pathlib.Path("README.md")
    if readme.exists():
        c, o2 = counts, o
        cov = snap["coverage"]
        gold = next((a for a in snap.get("top_assets", []) if a["symbol"] == "GOLD"), None)
        gold_share = (gold["tokenized_market_cap"] / o2["total"]) if gold and o2["total"] else None
        chains = snap.get("chain_mix") or {}
        tf = snap.get("tradfi_reference") or {}
        venues = tf.get("venues") or []
        stamp_file = snap["generated_at"].replace("+00:00", "Z").replace(":", "-")
        body = f"""From the run of **{stamp_long}** — {snap['api']['calls']} calls, \
{snap['api']['credits']} credits. Every figure below is that one run, archived at
[`{stamp_file}.json`](docs/data/snapshots/{stamp_file}.json), and rewritten here by
`scripts/inline_snapshot.py` each time the pipeline runs.

> In CoinMarketCap's tokenised real-world-asset catalogue, **{tokenised} assets**
> carry tokens, holding **${total_bn:,.2f}bn** of reported market cap. **{o2['n']}**
> issuers hold all of it. The top five hold **{o2['top5']:.1%}**. Almost half the
> tokens — {cov['tokens_without_market_cap']:,} of {c['tokens_attributed']:,} — report
> no market cap at all. And every one of the {tf.get('total_listings', 0):,} off-chain
> listings the API returns is {venues[0]['venue'] if venues and 'venue' in venues[0] else (venues[0].get('name') if venues else 'one venue')}.

| | |
|---|---|
| Assets in `map` / carrying tokens | {c['assets_mapped']:,} / **{tokenised}** |
| Reported tokenised cap | **${total_bn:,.2f}bn** |
| Issuers: listed / seen on a token / carrying value | {c['issuers_in_directory']} / {c['issuers_seen']} / **{o2['n']}** |
| HHI over issuers, by value | **{hhi}** — {eff} effective issuers |
| Top 1 / 3 / 5 share | {o2['top1']:.1%} / {o2['top3']:.1%} / **{o2['top5']:.1%}** |
| Largest single asset | {gold['name'] if gold else '—'} — {gold_share:.1%} of the catalogue |
| Tokens reporting no market cap | **{cov['tokens_without_market_cap']:,} of {c['tokens_attributed']:,}** |
| Off-chain listings, and where | {tf.get('total_listings', 0):,} — **every one of them {venues[0]['venue'] if venues and 'venue' in venues[0] else (venues[0].get('name') if venues else 'one venue')}** |
| Chain concentration | {chains.get('leaders', [{}])[0].get('label', '—')} {chains.get('top1', 0):.1%}; {chains.get('effective_n', '—')} effective chains |"""
        txt = readme.read_text(encoding="utf-8")
        readme.write_text(region(txt, "readme", body, readme), encoding="utf-8", newline="")
        print(f"rewrote the headline block in {readme}")

    print(f"inlined {SNAP} into {PAGE}")
    print(f"  {eff} effective issuers - HHI {hhi} - {tokenised} assets - snapshot {stamp_short}")
    print(f"  page is now {len(page):,} characters")


if __name__ == "__main__":
    main()
