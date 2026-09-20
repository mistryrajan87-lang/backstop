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

    # 3. the plain-English line. Judges read a sentence before they read an index,
    #    and "4.29" says nothing to a reader who has never met an HHI. Every figure
    #    in it is read from the snapshot here, at build time: typing "94.1%" into the
    #    HTML would be the same class of bug as a hard-coded date in a test, in a
    #    more visible place. Top-5 moved 94.11% -> 94.09% in a single day.
    top_n = min(5, o["n"] or 0)
    lead = (f'<b>{o["top5"]:.1%}</b> of priced tokenised value sits with '
            f'{top_n} issuer label{"" if top_n == 1 else "s"}')
    biggest = (snap.get("top_assets") or [{}])[0]
    b_name = biggest.get("name") or biggest.get("symbol")
    b_cap = biggest.get("tokenized_market_cap")
    if b_name and b_cap and o["total"]:
        lead += (f', and <b>{html.escape(str(b_name))}</b> alone is '
                 f'{b_cap / o["total"]:.1%} of it')
    lead += ". The figures are re-measured and archived every morning."
    # The decomposition, generated. A 58/40 block and a 32/28/27 block average to
    # 4.29, which is the shape of neither, and that is the honest sentence about
    # this catalogue. Neither cut is called a market: they are share vectors.
    def top(block, k):
        out = []
        for l in (block or {}).get("leaders", [])[:k]:
            if l.get("label") and l.get("share"):
                out.append(f'{html.escape(str(l["label"]))} {l["share"]:.0%}')
        return " / ".join(out)
    com = top((snap.get("by_asset_class") or {}).get("commodity"), 2)
    rest = top(snap.get("ex_commodity"), 3)
    parts = []
    if com and rest:
        parts.append(f'Underneath it sit two different share vectors \u2014 <b>{com}</b> across '
                     f'commodities, <b>{rest}</b> across everything else. The blended index is '
                     'the shape of neither.')
    # The band refusal stays in the OPEN paragraph. The page folds .qual behind a
    # disclosure, and this sentence is the reason the band label was deleted at
    # all - the one claim this project refuses to make. It does not get folded
    # away behind any summary. The two-vector decomposition can.
    band = ('<span class="bandnote">The index is inverse-Simpson, a description of this '
            'share vector. The 1,500 and 2,500 marks used in merger analysis are '
            'deliberately not applied: these are CoinMarketCap issuer labels, not firms '
            'shown to compete.</span>')
    qual = ('<span class="qual">' + " ".join(parts) + '</span>') if parts else ''
    page = region(page, "punchline",
                  f'<p class="punchline" id="punchline">{lead}{band}{qual}</p>')

    # 4. the reconcile line, on the first screen rather than only in Method.
    #    The incomparable six are a category, not a footnote.
    foot = (f'{tokenised} assets &middot; two endpoints &middot; '
            f'{incomparable} incomparable &middot; snapshot {html.escape(stamp_short)}')
    page = region(page, "herofoot", f'<p class="herofoot" id="herofoot">{foot}</p>')

    # 5. the daily series, inlined like everything else. The page never fetches
    #    it, so the delta strip works from the file alone - and a run that has no
    #    yesterday simply writes one row and the strip hides itself.
    hist_path = DOCS / "data" / "history.jsonl"
    rows: list[dict] = []
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue          # a corrupt line must not cost us the series
    rows = rows[-30:]
    hraw = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    hraw = hraw.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    page = region(page, "history",
                  f'<script type=\"application/json\" id=\"histdata\">{hraw}</script>')

    # 6. what the page says with scripting off
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

    # 7. social meta - the claim, not the stack
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
        # Two sentences in the prose quoted these counts by hand. They read "669 of
        # 1,428" while the generated block three screens above said 670 of 1,435,
        # from the same run - a README contradicting itself about precisely the
        # drift this project exists to catch. Correcting the digits would have put
        # them back on the same rot, so they are generated too, and the share is
        # generated with them rather than left as a hand-written "almost half".
        worst = max(snap.get("issuers") or [{}],
                    key=lambda i: i.get("tokens_without_cap") or 0, default={})
        n_null, n_tok = cov["tokens_without_market_cap"], c["tokens_attributed"]
        nulls = (f"**{n_null:,} of {n_tok:,}** tokens"
                 + (f" ({n_null / n_tok:.0%} of them)" if n_tok else "")
                 + " return `market_cap: null`.")
        if worst.get("tokens_without_cap"):
            nulls += (f" {worst['tokens_without_cap']:,} of those belong to one issuer, "
                      f"{worst.get('name') or worst.get('label')}, whose book is computed "
                      f"from the minority of its tokens that report anything.")

        # "exactly one asset is more than 1% out (a pre-IPO wrapper, by $120k)" was
        # typed by hand and was true of an early run. By 19 Sep the run reported
        # assets_off_by_over_1pct = 0 with an empty worst_assets, so the sentence
        # was false in the file that argues against exactly this. Generated now.
        off = recon.get("assets_off_by_over_1pct") or 0
        if recon.get("ratio") is None:
            recon_txt = "The two endpoints priced nothing in common this run."
        else:
            recon_txt = (f"Over the assets both endpoints price, the ratio rounds to "
                         f"**{recon['ratio']:.4f}**")
            if off == 0:
                recon_txt += ", and no asset is more than 1% out."
            else:
                worst = (recon.get("worst_assets") or [{}])[0]
                recon_txt += (f", and {off} {'asset is' if off == 1 else 'assets are'} "
                              f"more than 1% out")
                if worst.get("symbol"):
                    recon_txt += f" (worst: {worst['symbol']} at {worst.get('ratio')})"
                recon_txt += "."

        # The near-miss tickers. This was a hand-typed sentence and a hand-typed
        # table: "Nine are not in it", "Three do match", and JAAA "carries
        # $65,242" - which the snapshot had at $91,247 and the page's own
        # generated prose printed as $91K. Three typed figures in the file the
        # page points at for method, nine lines below a block this script
        # rewrites every run. Generated now, so it moves with the map.
        WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven",
                 "eight", "nine", "ten", "eleven", "twelve"]
        def count_word(n: int) -> str:
            return WORDS[n] if n < len(WORDS) else f"{n:,}"

        nic = snap.get("not_in_this_catalogue") or {}
        absent = [a for a in (nic.get("absent") or []) if a]
        present = [x if isinstance(x, dict) else {"symbol": x}
                   for x in (nic.get("present") or [])]
        nm = []
        if absent:
            nm.append(
                f"{count_word(len(absent)).capitalize()} "
                f"{'is' if len(absent) == 1 else 'are'} not in it at all: "
                + "**" + ", ".join(absent) + "**.")
        if present:
            nm.append("")
            nm.append(
                f"{count_word(len(present)).capitalize()} "
                f"{'does' if len(present) == 1 else 'do'} match a row - and "
                f"{'it is not' if len(present) == 1 else 'none of them is'} "
                "the product you would assume:")
            nm.append("")
            nm.append("| Ticker | What is actually in the map | State |")
            nm.append("|---|---|---|")
            for x in present:
                val = float(x.get("attributed_value") or 0.0)
                tok = "tokens" if x.get("has_tokens") else "no tokens"
                state = f"{tok}, carries **${val:,.0f}**" if val > 0 else f"{tok}, carries nothing"
                nm.append(f"| `{x.get('symbol','')}` | {x.get('name','')} | {state} |")
        txt = readme.read_text(encoding="utf-8")
        txt = region(txt, "notinmap", "\n".join(nm), readme)
        txt = region(txt, "readme", body, readme)
        txt = region(txt, "recon", recon_txt, readme)
        txt = region(txt, "nullcaps", nulls, readme)
        txt = region(txt, "nullcaps2", "   " + nulls, readme)
        readme.write_text(txt, encoding="utf-8", newline="")
        print(f"rewrote the headline block in {readme}")

    print(f"inlined {SNAP} into {PAGE}")
    print(f"  {eff} effective issuers - HHI {hhi} - {tokenised} assets - snapshot {stamp_short}")
    print(f"  page is now {len(page):,} characters")


if __name__ == "__main__":
    main()
