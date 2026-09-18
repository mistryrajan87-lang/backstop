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


def region(text: str, name: str, body: str) -> str:
    """Replace everything between a marker pair. Missing markers are fatal -
    silently doing nothing is how a generator drifts out of its template."""
    start, end = f"<!-- backstop:{name}:start -->", f"<!-- backstop:{name}:end -->"
    i, j = text.find(start), text.find(end)
    if i < 0 or j < 0 or j < i:
        raise SystemExit(f"marker pair backstop:{name} not found in {PAGE}")
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
    print(f"inlined {SNAP} into {PAGE}")
    print(f"  {eff} effective issuers - HHI {hhi} - {tokenised} assets - snapshot {stamp_short}")
    print(f"  page is now {len(page):,} characters")


if __name__ == "__main__":
    main()
