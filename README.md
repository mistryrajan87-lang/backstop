# Backstop

**How few issuers is CoinMarketCap's tokenised-asset catalogue made of?**

Built for the [Build with CMC: API Hackathon](https://dorahacks.io/hackathon/coinmarketcap-api-202609/detail) · **Real World Assets** track.

Live dashboard: **https://mistryrajan87-lang.github.io/backstop/**

---

## What it found

<!-- backstop:readme:start -->
From the run of **Sat, 19 Sep 2026 16:57:04 GMT** — 107 calls, 73 credits. Every figure below is that one run, archived at
[`2026-09-19T16-57-04Z.json`](docs/data/snapshots/2026-09-19T16-57-04Z.json), and rewritten here by
`scripts/inline_snapshot.py` each time the pipeline runs.

> In CoinMarketCap's tokenised real-world-asset catalogue, **791 assets**
> carry tokens, holding **$7.51bn** of reported market cap. **15**
> issuers hold all of it. The top five hold **94.1%**. Almost half the
> tokens — 670 of 1,435 — report
> no market cap at all. And every one of the 778 off-chain
> listings the API returns is Binance.

| | |
|---|---|
| Assets in `map` / carrying tokens | 7,811 / **791** |
| Reported tokenised cap | **$7.51bn** |
| Issuers: listed / seen on a token / carrying value | 25 / 21 / **15** |
| HHI over issuers, by value | **2,328.7** — 4.29 effective issuers |
| Top 1 / 3 / 5 share | 36.5% / 73.7% / **94.1%** |
| Largest single asset | Gold — 62.7% of the catalogue |
| Tokens reporting no market cap | **670 of 1,435** |
| Off-chain listings, and where | 778 — **every one of them Binance** |
| Chain concentration | Ethereum 74.2%; 1.74 effective chains |
<!-- backstop:readme:end -->

## What this catalogue is, and what it is not

This matters more than the headline, because the headline is quotable and wrong
without it.

**It is not the RWA market as usually discussed.** CoinMarketCap's RWA catalogue
contains exactly three asset classes: `commodity`, `stock` and `etf`. There is **no
tokenised-treasury class at all** — no BUIDL, no BENJI, no OUSG. The institutional
T-bill funds that dominate most RWA league tables are simply absent. What is here
is tokenised gold plus equity and ETF wrappers.

**The concentration is largely one asset.** Tokenised gold is about **63%** of
the whole catalogue. Tether's ~$2.7bn is two tokens on that one asset; Paxos's
~$1.9bn is one. So "two issuers are 62%" is, underneath, "tokenised gold is most of this
catalogue, and two firms mint most of the gold". Both statements are true; only
the second is informative.

**The directory is complete, and padded.** `issuers_outside_directory` is **0** —
every issuer appearing on a token is in the directory. But six entries declare
tokens and carry no value whatever (`NA (Derivatives)`, Dinari Assets, Kinesis
Assets, XAGx, Token, Superstate Assets), and four more never appear on a token at
all. The residue is not *missing* issuers. It is *empty* ones.

**Every share is a share of the half that reports a cap.** Almost half the tokens
return `market_cap: null` — 669 of 1,428. 625 of those belong to one issuer, Backed
Assets, whose book is computed from the minority of its tokens that report anything.

**The reconciliation gap is resolved, and it was a data-quality finding.** The
first runs showed per-token caps summing to ~$106m more than CoinMarketCap's own
asset-level figures — a ratio of 1.0144 — while every asset in the top 25
reconciled at 1.0000. Splitting the comparison found the cause: **six assets whose
tokens report a market cap while the asset-level endpoint prices them at zero.**

| | asset-level | sum of its tokens |
|---|---|---|
| EWG — iShares MSCI Germany ETF | $0 | **$56,880,853** |
| NLR — VanEck Uranium and Nuclear ETF | $0 | **$47,813,996** |
| VOO — Vanguard S&P 500 ETF | $0 | $816,527 |
| VUG — Vanguard Growth ETF | $0 | $275,907 |
| RCAT — Red Cat Holdings | $0 | $105,879 |
| ECHO — EchoStar | $0 | $31 |

Those six account for **$105.9m** of the $106m. They cannot be reconciled — one
side of the comparison is missing — so they are excluded from the ratio and
reported as their own category, while their value stays in the market total
because it is real. Over the assets both endpoints price, the ratio rounds to **1.0000**,
and exactly one asset is more than 1% out (a pre-IPO wrapper, by $120k).

That is the single most reusable thing here for anyone else building on these
endpoints: `assets/list` and `quotes/latest` do not always agree that an asset has
a price, and the disagreement is worth about 1.4% of the catalogue.

**"All listings are Binance" is about a field, not about price discovery.**
`tradfi_markets[]` returns exchange listings. Every one it returned
names Binance. This is reported as a count rather than as a concentration index,
deliberately: an HHI over a single-entry field can only return 10,000, which is
arithmetic wearing the clothes of a finding. And it says nothing about where these
assets are actually priced — only what one API field returns.

**Some names are simply not here, and the near-misses are instructive.** Every run
looks up the tickers a reader arriving from an RWA league table would expect, by
symbol, in CoinMarketCap's own RWA map. Nine are not in it at all: **BUIDL, BENJI,
USYC, OUSG, USDY, TBILL, JTRSY, USTBL, FOBXX**.

Three do match a row — and none of them is the product you would assume:

| Ticker | What is actually in the map | State |
|---|---|---|
| `USTB` | Victory Portfolios II VictoryShares Short-Term Bond ETF | no tokens, carries nothing |
| `JAAA` | Janus Henderson AAA CLO ETF | tokens, carries **$65,242** |
| `BOXX` | Alpha Architect 1-3 Month Box ETF | no tokens, carries nothing |

All three are conventional ETFs that happen to share a ticker with something an
RWA reader might be thinking of — not the tokenised treasury funds themselves. So
symbol matching finds them, and a careless lookup would report the treasury market
as "partly present" when it is not present at all. The run reports three states
separately — absent from the map, present but carrying nothing, present with value
— because collapsing them would manufacture exactly that error.

**A single snapshot is a photograph.** The scheduled job runs daily and appends one
line per run to `docs/data/history.jsonl` — total cap, HHI, top-1 and top-5 share,
the largest asset's share, null-cap token count and the reconciliation ratio. The
series starts from the first run, so it answers "is this catalogue changing" only
going forward.

## Who this is for

The single useful thing Backstop does is stop someone reading CoinMarketCap's RWA
catalogue as though it were the whole tokenised-asset market. That sounds modest.
It is the entire benefit, and it is worth more than another metric.

If you are about to cite this dataset, the page bounds five claims for you before
you publish them:

- **The size.** The map looks like ~7,800 assets. The live book is **under 800** and
  about **$7.5bn**. Anything that opens "thousands of tokenised RWAs" is describing
  the filing cabinet, not the market.
- **The concentration.** Tether and Paxos at 62% is not "two firms captured RWAs".
  It is "this catalogue is 63% tokenised gold, and those two mint the gold."
- **The directory.** 25 rows, 21 that appear on a token, **15** that carry value,
  six that declare tokens worth nothing. Counting issuers from `issuers/list` gives
  you a padded number.
- **The venue field.** Every off-chain listing the API returns is Binance. `tradfi_markets[]`
  returns exchange listings, so do not read NYSE-style price discovery into it.
- **The weighting.** Almost half the tokens report no market cap, 625 of them from
  one issuer. Any league table built on token *count* crowns the wrong name.

**Three things it is not**, stated on the dashboard as well as here, because people
land on a concentration chart and reach for it as a risk tool:

- **Not a safety check.** Safe would mean reserves audited, the contract sound,
  redemption possible. CoinMarketCap supplies a name, a ticker and a market-cap
  field — no attestations, no custody, no redemption terms. Adding a concentration
  index to that does not add safety.
- **Not a map of who pays you back.** That is a legal question: which entity, in
  which jurisdiction, under which document. The API's "issuer" is a label on a
  token; for a fund the sponsor, the tokenisation agent and the fund vehicle are
  three different answers, and this data distinguishes none of them.
- **Not investment advice, and not a buy list.** One vendor's catalogue,
  gold-heavy, missing the large treasury products, with almost half its tokens
  unpriced. Not a portfolio view, and it should not be read as one.

Answering any of those would need a different project entirely — issuer legal
documents, attestations, redemption timelines, and sources well beyond
CoinMarketCap.

And a single snapshot is a photograph, not a trend: the scheduled job commits one
a day, so the series starts from the first run, not before it.

The honest pitch: *if you use CoinMarketCap to talk about tokenised real-world
assets, this shows what that database actually contains. Use it to bound a claim.
Do not use it as a map of on-chain Treasuries.*

## The question

Every tokenised real-world asset was minted by somebody, and CoinMarketCap's
`issuers/list` directory lists 25 names. Nothing in a per-asset view tells you how
the catalogue's value distributes across them. Backstop rebuilds it along the
issuer axis and measures the concentration by value: HHI, the effective number of
issuers, and top-1/3/5 share — overall, within each chain, and within each asset
class.

### What "issuer" means here

The API's issuer record carries a name, a website and a token roster. It carries no
custody, no jurisdiction and no legal entity, so it cannot say who is *liable* for
a token — BlackRock, Securitize and the fund vehicle are three different answers to
"who is behind BUIDL" and this data distinguishes none of them. Read every figure
as concentration of **attributed issuance**, nothing more.

## Why the obvious way is wrong

A tokenised asset is not one thing. Ask the API for Gold:

```bash
curl -H "X-CMC_PRO_API_KEY: $CMC_API_KEY" \
  "https://pro-api.coinmarketcap.com/v5/real-world-assets/quotes/latest?rwa_id=1&convert=USD"
```

```jsonc
{
  "status": { "error_code": "0", "elapsed": 4, "credit_count": 1 },
  "data": {
    "rwa_assets": [{
      "name": "Gold", "symbol": "GOLD", "rwa_id": 1, "asset_type": "commodity",
      "tokenized_market_cap": 4678747810.83764,
      "tokens": [
        { "symbol": "PAXG",  "issuer_name": "Paxos",           "issuer_id": "68904c24abae9b5b9fb35815", "market_cap": 1885314998.73 },
        { "symbol": "XAUt",  "issuer_name": "Tether Holdings", "issuer_id": "68904e9cabae9b5b9fb358ac", "market_cap": 2703534700.30 },
        { "symbol": "XAUM",  "issuer_name": "Matrixdock",      "issuer_id": "68905a7babae9b5b9fb35a8d", "market_cap":   47621379.03 },
        { "symbol": "CGO",   "issuer_name": "Comtech Gold",    "issuer_id": "68904cceabae9b5b9fb35839", "market_cap":   19506423.40 },
        { "symbol": "XAUT0", "issuer_name": "Tether Holdings", "issuer_id": "68904e9cabae9b5b9fb358ac", "market_cap":   17049487.71 },
        { "symbol": "VNXAU", "issuer_name": "VNX",             "issuer_id": "68905b43abae9b5b9fb35a95", "market_cap":    5804718.39 },
        { "symbol": "XAU",   "issuer_name": "NA (Derivatives)","issuer_id": "695e11f774b54210f3b95dc3", "market_cap":          0.00 }
      ],
      "tradfi_markets": []
    }]
  }
}
```

*(Response trimmed to the fields Backstop reads. It is a real one — the
`elapsed` and `credit_count` are as returned.)*

Seven tokens, **six** distinct issuers, one asset. The natural pipeline — list the
issuers, ask each which tokens it issued, add up those assets' market caps — would
credit Gold's whole **$4.68 bn** to each of the six. The market would appear nearly
six times larger than it is and the concentration figures would be meaningless.

Backstop sums **each token's own `market_cap`, grouped by `issuer_id`** — both
fields read from the same token object, so the weights come from one source and are
not the product of a join rule between the quote path and the issuer path. (Those
two *can* disagree; Backstop publishes the disagreement as declared-versus-
attributed token counts per issuer rather than resolving it silently.) Nothing is
counted twice, and two tokens from the same issuer (XAUt and XAUT0) roll up
correctly — Tether Holdings, not Paxos, is the largest gold issuer at 58% of the
asset. The per-token total comes to `4,678,831,707.56` against CoinMarketCap's own
asset-level `4,678,747,810.84`: a ratio of **1.0000**. The dashboard publishes that
reconciliation ratio every run, so if the two ever diverge the reader sees it
rather than being handed a confident wrong number.

## What it measures

| | |
|---|---|
| **HHI** | sum of squared percentage issuer shares **by tokenised market cap in USD** — never by token count, which would let a bucket of 239 worthless wrappers outweigh a $2.7bn issuer. Issuers carrying no value are excluded by construction, so the three zero-token directory rows and the `NA (Derivatives)` bucket cannot move the number. 0–10,000. The index is inverse-Simpson, a property of any share vector, used for biodiversity and income distribution as readily as for markets. The 1,500 and 2,500 marks used in merger analysis are deliberately **not** applied here: those cut-points are defined for substitutable products in a relevant market, and a CoinMarketCap issuer label spanning gold, tokenised equity and ETF wrappers is not that. Each block instead carries a `shape` — top-heavy, uneven or broadly even — which describes the distribution and claims nothing about competition. Either way this measures concentration of **attributed issuance**, not of liability or redemption capacity. |
| **Effective number of issuers** | `1 / Σ(share²)` — the count of equal-sized issuers that would produce the same HHI. The same number, in units a person can hold in their head. |
| **Top 1 / 3 / 5 share** | the blunt version of the same question. |
| **Off-chain quotation** | `quotes/latest` returns `tradfi_markets[]` beside `tokens[]`: the non-token venues quoting the asset, each with an exchange, a ticker and a market URL. Despite the field name these are exchange listings, not the asset's primary market — a tokenised equity's entry points at a crypto exchange's stock product, not at the exchange the share itself trades on. Backstop reports how many assets are quoted off-chain **and how those listings split by venue**, because on the live data one venue carries almost all of them. |
| **Chain** | no RWA endpoint carries one. Each token's `crypto_id` is taken across to `/v2/cryptocurrency/info` for its `platform`, which is the only route to a per-chain view. |

Weights are tokenised market cap in USD. Every figure is reported per asset class
as well as overall, because a class served by one issuer scores 10,000 by
definition and that only means something next to the issuer count.

## CoinMarketCap endpoints used

All seven documented Real World Assets endpoints. The table is generated from the
`ENDPOINTS` dict in `scripts/fetch_snapshot.py`, which is also what the pipeline
calls and what the dashboard renders — it cannot drift from the code.

| Endpoint | Tier | Credits | Why Backstop calls it |
|---|---|---|---|
| `GET /v5/real-world-assets/map` | Basic | free | the canonical asset universe — `rwa_id`, asset class, and which assets have tokens at all |
| `GET /v5/real-world-assets/assets/list` | Basic | 1/call | asset-level tokenised market cap, to reconcile the per-token totals against |
| `GET /v5/real-world-assets/quotes/latest` | Basic | 1/call | the heart of it — `tokens[]` gives each token's own issuer and market cap; `tradfi_markets[]` says whether an underlying market exists |
| `GET /v5/real-world-assets/info` | Basic | 1/call | descriptive metadata on the largest assets — industry, primary exchange, SEC CIK where the asset is an equity |
| `GET /v5/real-world-assets/issuers/list` | Basic | 1/call | the issuer directory — name, website and declared token count for all 25 |
| `GET /v5/real-world-assets/issuers` | Basic | 1/call | each issuer's declared token count, cross-checked against what the token data actually attributes to it |
| `GET /v5/real-world-assets/market-pairs/list` | **Growth** | 1/call | venue depth; probed every run so the plan gate is recorded from evidence, not assumed |

Plus two outside the RWA family:

| Endpoint | Tier | Credits | Why Backstop calls it |
|---|---|---|---|
| `GET /v2/cryptocurrency/info` | Basic | 1/100 ids | the only route to a chain — each token's `crypto_id` carried across for its `platform` |
| `GET /v1/key/info` | Basic | free | plan limits, so the run adapts its own throttle and publishes what it spent |

Auth is the `X-CMC_PRO_API_KEY` header on every call. The key is never placed in a
URL, never logged, and never written into the snapshot.

## What the API made possible, and where it got in the way

**What it made possible.** The `tokens[]` array inside `quotes/latest` is the whole
project. It carries `issuer_id`, `issuer_name` and a per-token `market_cap`, which
means the issuer→value attribution needs no fuzzy matching, no name normalisation
and no join at all. Nothing else I know of publishes tokenised-asset market caps
broken out by the party that minted them. A full refresh — the whole 7,811-row map,
every asset that carries tokens, the issuer directory, the chain
lookup and the metadata — measured **73 credits and 107 calls**, a few minutes end
to end.

**Where it got in the way.**

1. **The response shapes are barely documented.** The reference covers parameters
   thoroughly and response structure hardly at all, so everything below had to be
   discovered by probing a live key (`scripts/probe.ps1` does exactly that and
   saves every raw response). Building against the documentation alone would have
   produced a pipeline that ran clean and returned zeros.
2. **`id` is rejected across the whole RWA family.** These endpoints want `rwa_id`
   and `issuer_id`. The error is `4002 Missing required parameter`, which is
   misleading — the parameter is present, it is simply named differently from the
   rest of the CMC API. That cost real time.
3. **`quotes` is a list, not an object.** Everywhere else in the CMC API a priced
   payload is `quote: {"USD": {...}}`. Here it is `quotes: [{"symbol": "USD", ...}]`.
   Code carried over from the v1/v2 endpoints reads every price as zero and says
   nothing about it.
4. **There is no chain or platform field anywhere in the RWA family.** You cannot
   ask "which chain is this token on" from these endpoints at all. The way through
   is each token's `crypto_id`, carried over to `/v2/cryptocurrency/info`, which
   does return a proper `platform` — PAXG comes back as Ethereum with its contract
   address. It works, and it is one of the more interesting things in the build,
   but it is a cross-family join the RWA documentation never mentions.
5. **`tradfi_markets` is not the underlying's market**, whatever the name suggests.
   Nvidia's entry is `{exchange: Binance, ticker: NVDA, market_url: …/stocks/EQ_NVDA}` —
   Binance's tokenised-stock product, not the NYSE. Across the whole catalogue,
   nearly every asset carries an off-chain listing and **every one of them is
   Binance**. (An early 100-asset probe said 97 of 100; the catalogue-wide figure
   replaced it and this line did not, for a while.) Taken at face
   value the field would support a claim about price discoverability that the data
   does not make; read correctly it reveals a second concentration, at the venue
   layer, which is a better finding than the one I expected.
6. **Almost half the tokens report `market_cap: null`** — 669 of 1,428, and 625 of
   those belong to a single issuer. They are real tokens with no reported value,
   so every share Backstop
   publishes is a share of the value that *is* reported. Treating null as zero is
   arithmetically harmless and editorially misleading, so the count is published
   as its own line, overall and per issuer.
7. **The two endpoints disagree about how big the catalogue is.** `map` says 7,811,
   `assets/list` says 7,942. Neither is documented as a subset of the other, and
   neither is the number that matters: fewer than **800** rows carry tokens at all. A
   write-up that quotes either total as "the tokenised market" is out by an order
   of magnitude — which is the mistake this README made until the first live run
   corrected it.
8. **`num_tokens` in the issuer directory doesn't agree with the token data.**
   Bitget Assets and Coinbase are both listed with `num_tokens: 0`. Backed Assets
   declares 1,176. Backstop reports the declared count alongside the count it
   actually attributes rather than picking one and hoping.
   Three of the 25 declare zero. One entry, `NA (Derivatives)`, declares 239 tokens
   of which every single one is worth nothing — an aggregate placeholder rather
   than an issuer, so Backstop flags it instead of letting it pad the counts.
9. **`/issuers` paginates its tokens.** `limit=250` is honoured and `start=` works,
   but Backed Assets alone has 1,176 tokens, so a naive loop is five calls for one
   issuer. Backstop doesn't need that path for its weights, which is the point of
   taking the attribution from `quotes/latest` instead.
10. **Registering for the hackathon did not grant the Startup tier.** The key reports
   `credit_limit_monthly: 15000`, not 300,000, and `market-pairs/list` returns
   `1006 — your API Key subscription plan doesn't support this endpoint`. The
   pipeline was redesigned around the tighter budget; that pressure is what
   surfaced the per-token attribution in the first place, so it made the entry
   better rather than worse.

## How it runs

No server. A GitHub Actions cron runs the pipeline where the API is reachable,
commits `docs/data/snapshot.json`, and GitHub Pages serves a static page that reads
it. The key lives only as an encrypted repository secret.

```
.github/workflows/refresh.yml   cron + manual; tests, builds, sanity-checks, commits
scripts/
  fetch_snapshot.py             the pipeline
  probe.ps1  probe.py           endpoint discovery — dumps raw responses
tests/
  test_aggregation.py           hand-calculated checks, no network
  make_fixture.py               synthetic snapshot for dashboard work
docs/
  index.html                    the dashboard
  data/snapshot.json            written by the workflow
```

Locally:

```bash
pip install -r requirements.txt
python tests/test_aggregation.py                 # no key needed
export CMC_API_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
python scripts/fetch_snapshot.py --out docs/data/snapshot.json
python -m http.server -d docs 8000               # then open localhost:8000
```

## On correctness

The numbers are the product, so the arithmetic is tested rather than trusted.
`tests/test_aggregation.py` runs its checks against fixtures whose answers are
worked out by hand in the comments — including the Gold case above in miniature,
where a three-issuer asset must total 200 and not the 460 that per-asset counting
would produce. It needs no network and runs in the workflow before a single credit
is spent.

The pipeline also publishes what it could *not* account for: tokens reporting no
market cap, tokens with no issuer, issuers that appear in token data but not in the
directory, aggregate entities that declare tokens worth nothing, value sitting on
tokens whose chain could not be resolved, the declared-versus-attributed token count
per issuer, and the reconciliation ratio between the per-token and asset-level totals. A partial run says so on the page. The workflow
refuses to publish a snapshot flagged synthetic, one with no issuer carrying value,
or one whose top-1/3/5 shares are not monotonic.

---

Backstop is an independent hackathon entry. It is not affiliated with or endorsed
by CoinMarketCap, and nothing in it is investment advice.
