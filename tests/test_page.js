#!/usr/bin/env node
/* Page smoke tests: does the DOM say what the JSON says?
 *
 * tests/test_aggregation.py checks what the pipeline computes. Nothing checked
 * what the page rendered, which is why three bugs shipped: [object Object] in the
 * scope card, a table footnote clipped mid-sentence, and a Tokens column that
 * published the issuer count. All three were found by a human looking at pixels.
 *
 * The assertions here are deliberately written against the JSON rather than
 * against today's numbers. "Gold shows 7" rots the morning gold stops being the
 * largest asset. "Every rendered row matches its snapshot row" does not.
 *
 *   node tests/test_page.js [docsDir]      (default: docs/)
 *
 * Browser: uses PW_CHANNEL (default "chrome") so CI can drive the runner's
 * preinstalled Chrome via playwright-core, with no browser download. Set
 * PW_EXECUTABLE to point at a specific binary instead.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");

const DOCS = path.resolve(process.argv[2] || "docs");
const SNAP = path.join(DOCS, "data", "snapshot.json");

let pass = 0;
const failures = [];
function check(label, ok, detail = "") {
  if (ok) { pass++; console.log(`  PASS  ${label}`); }
  else { failures.push(label); console.log(`  FAIL  ${label}${detail ? "  " + detail : ""}`); }
}

const MIME = { ".html": "text/html", ".json": "application/json", ".jsonl": "text/plain",
               ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };

function serve(dir) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      const rel = decodeURIComponent(req.url.split("?")[0]).replace(/^\/+/, "") || "index.html";
      const file = path.join(dir, rel);
      if (!file.startsWith(dir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404).end("not found"); return;
      }
      res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
      fs.createReadStream(file).pipe(res);
    });
    srv.listen(0, "127.0.0.1", () => resolve({ srv, port: srv.address().port }));
  });
}

(async () => {
  let chromium;
  try { ({ chromium } = require("playwright")); }
  catch { ({ chromium } = require("playwright-core")); }

  if (!fs.existsSync(SNAP)) { console.error(`no snapshot at ${SNAP}`); process.exit(2); }
  const snap = JSON.parse(fs.readFileSync(SNAP, "utf8"));

  const { srv, port } = await serve(DOCS);
  const launch = process.env.PW_EXECUTABLE
    ? { executablePath: process.env.PW_EXECUTABLE }
    : { channel: process.env.PW_CHANNEL || "chrome" };
  const browser = await chromium.launch(launch);
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "light" });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    // The browser requests /favicon.ico on its own. The console text for that 404
    // is generic ("Failed to load resource..."), so the URL has to be read from
    // the message location or the filter silently matches nothing - which is how
    // this check failed on a page that was fine.
    const url = (m.location() && m.location().url) || "";
    if (/favicon/i.test(url) || /favicon/i.test(m.text())) return;
    consoleErrors.push("console: " + m.text() + (url ? "  <" + url + ">" : ""));
  });

  console.log(`\npage smoke tests — ${DOCS}\n${"-".repeat(60)}`);
  await page.goto(`http://127.0.0.1:${port}/index.html`, { waitUntil: "networkidle" });
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(300);

  // 1. nothing threw
  check("the page renders with no console or page errors",
        consoleErrors.length === 0, consoleErrors.slice(0, 3).join(" | "));

  // 2. no rendering tells. [object Object] shipped once; undefined/NaN are the
  //    same failure wearing a different coat.
  const bodyText = await page.evaluate(() => document.body.innerText);
  for (const tell of ["[object Object]", "undefined", "NaN"]) {
    const hit = bodyText.includes(tell);
    let where = "";
    if (hit) {
      const i = bodyText.indexOf(tell);
      where = "…" + bodyText.slice(Math.max(0, i - 50), i + tell.length + 30).replace(/\s+/g, " ") + "…";
    }
    check(`no "${tell}" anywhere in the rendered page`, !hit, where);
  }

  // 3. the page is showing the snapshot it shipped with
  const stamp = await page.evaluate(() => {
    const kv = document.getElementById("methodkv");
    if (!kv) return null;
    const t = kv.innerText.split("\n").map((s) => s.trim()).filter(Boolean);
    const i = t.findIndex((s) => /snapshot generated/i.test(s));
    return i >= 0 ? t[i + 1] : null;
  });
  const stampMs = stamp ? Date.parse(stamp) : NaN;
  /* The method card's three token rows have to add up. "Tokens attributed to an
     issuer 1,435" sat four rows above "Tokens with no issuer 5" while the issuer
     rows summed to 1,430 - counts.tokens_attributed is every token SEEN, issuer
     or not, and the label claimed otherwise. Read the rendered rows and do the
     arithmetic rather than trusting either label. */
  const kvPairs = await page.evaluate(() => {
    const kv = document.getElementById("methodkv");
    if (!kv) return null;
    const out = {};
    kv.querySelectorAll("div").forEach((d) => {
      const k = d.querySelector(".k"), v = d.querySelector(".v");
      if (k && v) out[k.textContent.trim()] = v.textContent.trim();
    });
    return out;
  });
  /* The FIRST number in the cell, not every digit in it: "Tokens with no issuer"
     renders as "5 ($0)" and stripping all non-digits reads that as 50. */
  const kvNum = (label) => {
    const raw = kvPairs && kvPairs[label];
    if (raw == null) return NaN;
    const m = String(raw).match(/-?[\d,]*\d/);
    return m ? Number(m[0].replace(/,/g, "")) : NaN;
  };
  const seen = (snap.counts || {}).tokens_attributed;
  const noIssuer = (snap.coverage || {}).tokens_without_issuer;
  const issuerSum = (snap.issuers || []).reduce((a, i) => a + (i.tokens || 0), 0);
  check("the method card's token rows add up to each other and to the snapshot",
        kvPairs != null && kvNum("Tokens matched to an asset") === seen
          && kvNum("Tokens with no issuer") === noIssuer
          && kvNum("Of those, carrying an issuer") === seen - noIssuer,
        JSON.stringify({ matched: kvNum("Tokens matched to an asset"),
                         withIssuer: kvNum("Of those, carrying an issuer"),
                         noIssuer: kvNum("Tokens with no issuer"), seen, snapNoIssuer: noIssuer }));
  check("and the count carrying an issuer is what the issuer rows actually sum to",
        seen - noIssuer === issuerSum,
        `${seen} seen - ${noIssuer} unattributed = ${seen - noIssuer}, issuer rows sum to ${issuerSum}`);
  check("no row on the method card claims the seen-token count is attributed",
        kvPairs != null && !Object.keys(kvPairs).some((k) =>
          /attributed to an issuer/i.test(k) && kvNum(k) === seen),
        JSON.stringify(Object.keys(kvPairs || {}).filter((k) => /attributed/i.test(k))));

  check("the method card's timestamp is the snapshot's own generated_at",
        Number.isFinite(stampMs) && stampMs === Date.parse(snap.generated_at),
        `page shows ${JSON.stringify(stamp)}, json says ${snap.generated_at}`);

  // 4. THE assertion: every rendered asset row equals its snapshot row.
  //    Matched by symbol, never by position - the largest asset can change.
  const rendered = await page.evaluate(() => {
    const head = [...document.querySelectorAll("#assettable thead th")].map((t) => t.textContent.trim());
    const col = (n) => head.indexOf(n);
    return [...document.querySelectorAll("#assettable tbody tr")].map((r) => {
      const c = [...r.querySelectorAll("td")].map((x) => x.textContent.trim());
      return { first: c[col("Asset")] || "", tokens: c[col("Tokens")], issuers: c[col("Issuers")] };
    });
  });
  const num = (s) => (s == null ? NaN : Number(String(s).replace(/[^\d.-]/g, "")));
  let rowsOk = 0, rowFail = [];
  for (const a of snap.top_assets || []) {
    const row = rendered.find((r) => new RegExp(`\\b${a.symbol.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(r.first));
    if (!row) { rowFail.push(`${a.symbol}: not rendered`); continue; }
    if (num(row.tokens) !== a.tokens) { rowFail.push(`${a.symbol}: tokens ${row.tokens} vs json ${a.tokens}`); continue; }
    if (num(row.issuers) !== a.issuers) { rowFail.push(`${a.symbol}: issuers ${row.issuers} vs json ${a.issuers}`); continue; }
    rowsOk++;
  }
  check(`every asset row's Tokens and Issuers match the JSON (${rowsOk}/${(snap.top_assets || []).length})`,
        rowFail.length === 0, rowFail.slice(0, 3).join(" | "));

  // 5. tokens and issuers are genuinely different fields. If they are equal on
  //    every row, either the data is a remarkable coincidence or the bug is back.
  const differ = (snap.top_assets || []).filter((a) => a.tokens !== a.issuers).length;
  check("tokens and issuers differ on at least one asset (the bug made them always equal)",
        differ > 0, `${differ} of ${(snap.top_assets || []).length} rows differ`);

  // 6. the hero is the snapshot's own headline
  const heroTxt = await page.evaluate(() => document.body.innerText);
  check("the hero's effective-issuer figure appears on the page",
        heroTxt.includes(String(snap.overall.effective_n)), `looking for ${snap.overall.effective_n}`);

  // 7. Table notes are readable. One was clipped mid-sentence for weeks because it
  //    was a <caption>, which inherits the table's width and is cropped by the
  //    scroll box. Three assertions, not one: the structural cause, a count so the
  //    check cannot pass over an empty set, and the symptom itself. The first
  //    draft of this test had only the third, and a reverted fix sailed through it.
  const notes = await page.evaluate(() => ({
    count: document.querySelectorAll(".tablenote").length,
    tables: document.querySelectorAll(".tablewrap table").length,
    insideScrollBox: [...document.querySelectorAll(".tablewrap caption")].map(
      (c) => (c.closest("table") || {}).id || "?"),
    clipped: [...document.querySelectorAll(".tablenote")].filter(
      (n) => n.scrollWidth > n.clientWidth + 1).map((n) => n.id),
  }));
  check("no table note is rendered inside the scrolling box as a <caption>",
        notes.insideScrollBox.length === 0, notes.insideScrollBox.join(", "));
  check(`table notes exist to be checked (${notes.count} notes / ${notes.tables} tables)`,
        notes.count > 0 && notes.count >= notes.tables - 1,
        `${notes.count} notes for ${notes.tables} tables - an empty set passes every check below`);
  check("no table note is clipped by its container",
        notes.clipped.length === 0, notes.clipped.join(", "));

  // 8. The what-if must be a read of the snapshot, not an edit of it. If it
  //     renormalises in place, every chart after it quietly shows a market with
  //     the largest issuer deleted - and nothing on the page would say so.
  //     A snapshot without shares[] hides the control by design, so the clicks
  //     below would hang rather than fail. Assert the vector is there - a missing
  //     one is a pipeline regression, not a reason to quietly skip - and gate the
  //     interaction on the control actually being visible.
  check("the snapshot carries a share vector for every participant",
        Array.isArray(snap.overall.shares) && snap.overall.shares.length === snap.overall.n,
        `shares=${(snap.overall.shares || []).length} vs n=${snap.overall.n}`);
  const shockBtns = await page.$$(".shock button");
  const shockShown = await page.evaluate(() => {
    const r = document.querySelector(".shock");
    return !!r && r.offsetParent !== null;
  });
  check("the what-if control is on the page", shockBtns.length >= 2, `${shockBtns.length} buttons`);
  if (!shockShown) console.log("  SKIP  what-if interaction - the control is hidden for this snapshot");
  if (shockBtns.length >= 2 && shockShown) {
    const before = await page.evaluate(() => JSON.stringify(SNAP.overall));
    await page.click('.shock button[data-drop="1"]');
    await page.waitForTimeout(150);
    await page.click('.shock button[data-drop="3"]');
    await page.waitForTimeout(150);
    const after = await page.evaluate(() => JSON.stringify(SNAP.overall));
    check("the what-if leaves the snapshot object untouched", before === after,
          "SNAP.overall changed after dropping issuers");

    const shown = await page.evaluate(() => document.getElementById("shockout").innerText);
    const recomputed = await page.evaluate(() => {
      const sh = SNAP.overall.shares.slice(3);
      const t = sh.reduce((a, v) => a + v, 0);
      const ss = sh.reduce((a, v) => a + (v / t) * (v / t), 0);
      return (ss * 10000).toFixed(1);
    });
    check("the what-if's HHI is the one the shares imply",
          shown.replace(/,/g, "").includes(recomputed), `expected ${recomputed} in "${shown}"`);
    await page.click('.shock button[data-drop="0"]');
    await page.waitForTimeout(150);
  }

  // 9. The two coverage lanes must not borrow each other's denominator. A hatched
  //    segment on the value lane would claim we know how many dollars are missing.
  //    We do not: an unpriced token is a count, and its value is unknown, not zero.
  const lanes = await page.evaluate(() => {
    const seg = (id, cls) => document.querySelectorAll(`#${id} .${cls}`).length;
    const txt = (id) => (document.getElementById(id) || {}).innerText || "";
    return {
      valueUnpriced: seg("lane-value", "seg-unpriced"),
      valuePriced: seg("lane-value", "seg-priced"),
      countUnpriced: seg("lane-count", "seg-unpriced"),
      valueText: txt("lane-value"),
      countText: txt("lane-count"),
    };
  });
  check("the value lane draws no unpriced segment", lanes.valueUnpriced === 0,
        `${lanes.valueUnpriced} hatched segments on the value lane`);
  check("the value lane is drawn at all", lanes.valuePriced === 1, `${lanes.valuePriced} segments`);
  check("the count lane does draw one", lanes.countUnpriced === 1,
        `${lanes.countUnpriced} hatched segments on the count lane`);
  check("the count lane states the unpriced token count from the JSON",
        lanes.countText.replace(/,/g, "").includes(String(snap.coverage.tokens_without_market_cap)),
        `looking for ${snap.coverage.tokens_without_market_cap}`);
  check("the value lane says an unpriced token is not a missing dollar",
        /unknown rather than zero/i.test(lanes.valueText), lanes.valueText.slice(0, 80));

  // 10. The delta strip is arithmetic on the archive, so it must agree with the
  //     archive. It also has to disappear rather than invent a comparison when
  //     there is only one run on record - the state this page was in on its
  //     first morning.
  const delta = await page.evaluate(() => {
    const el = document.getElementById("histdata");
    let rows = [];
    try { rows = JSON.parse((el && el.textContent) || "[]"); } catch (e) { rows = []; }
    const box = document.getElementById("deltastrip");
    return { rows: rows.length, hidden: !box || box.hidden,
             text: (box && box.innerText) || "",
             last: rows[rows.length - 1] || null, prev: rows[rows.length - 2] || null };
  });
  check("the page carries the run history inline", delta.rows >= 1, `${delta.rows} rows`);
  if (delta.rows < 2) {
    check("with fewer than two runs the delta strip hides itself", delta.hidden,
          "it rendered a comparison with nothing to compare against");
  } else {
    check("the delta strip is shown once there are two runs", !delta.hidden, "still hidden");
    check("it names both dates it is comparing",
          delta.text.includes(delta.prev.date) && delta.text.includes(delta.last.date),
          `${delta.prev.date} -> ${delta.last.date}`);
    const shownHHI = delta.text.replace(/,/g, "").includes(String(delta.last.hhi));
    check("the HHI it shows is the latest row\'s own figure", shownHHI,
          `looking for ${delta.last.hhi}`);
    const move = Math.abs(delta.last.hhi - delta.prev.hhi).toFixed(1);
    check("the move it shows is the difference between the two rows",
          delta.text.replace(/,/g, "").includes(move), `looking for ${move}`);
    const dir = delta.last.hhi > delta.prev.hhi ? "↑" : "↓";
    check("the arrow points the way the index actually moved",
          delta.text.includes(dir), `expected ${dir}`);
  }

  // 11. No derived concentration label, anywhere. A chip under the HHI used to
  //     read "top-heavy"; the three names it could take were the 2,500 and 1,500
  //     merger-guideline cut-points relabelled, and the meter drew those two
  //     marks as unlabelled ticks. Deleting the words is only real if nothing
  //     renders them and nothing renders the marks, so this asserts the absence.
  //     Re-adding either one fails here rather than passing quietly.
  const nolabel = await page.evaluate(() => {
    const t = document.body.innerText;
    const bandWords = ["top-heavy", "broadly even", "moderately concentrated",
                       "highly concentrated", "unconcentrated"];
    return {
      words: bandWords.filter((w) => t.toLowerCase().includes(w)),
      chips: document.querySelectorAll(".chip, #hhichip, .band-crit").length,
      ticks: document.querySelectorAll(".meter .thr").length,
      heads: [...document.querySelectorAll("table thead th")]
              .map((h) => h.textContent.trim()).filter((h) => /^concentration$/i.test(h)),
      slot: !!document.getElementById("hero-hhisub"),
      heroText: (document.querySelectorAll(".heroblock")[1] || { innerText: "" }).innerText,
    };
  });
  check("no band name is rendered anywhere on the page", nolabel.words.length === 0,
        nolabel.words.join(", "));
  check("no status chip or crit-banded element is rendered", nolabel.chips === 0,
        `${nolabel.chips} found`);
  check("the HHI meter draws no threshold marks", nolabel.ticks === 0,
        `${nolabel.ticks} ticks`);
  check("no table carries a Concentration verdict column", nolabel.heads.length === 0,
        nolabel.heads.join(", "));

  //     Nothing replaced it. The first attempt was a reference point - what an
  //     equal split of the issuers present would score, 10,000/n - and it failed
  //     on its own terms: n is a baseline this code picks (15 -> 667, without the
  //     issuer holding $1,112 -> 714, above 1% only -> 1,429), and on a
  //     single-issuer chain it states that 10,000 equals 10,000. So the slot is
  //     gone, and this checks it stays gone rather than growing a new anchor.
  check("there is no element under the HHI to write a verdict into", !nolabel.slot,
        "#hero-hhisub is back");
  const evenScore = (10000 / snap.overall.n)
    .toLocaleString("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  check("the HHI hero block renders its label and its number and nothing else",
        nolabel.heroText.split("\n").filter((l) => l.trim()).length === 2,
        JSON.stringify(nolabel.heroText));
  check("no equal-split reference score is printed beside the index",
        !nolabel.heroText.replace(/,/g, "").includes(evenScore.replace(/,/g, "")),
        `found ${evenScore}`);

  // 11b. Reading order. The first complete card on this page used to be a
  //      disclaimer: the caveats card sat above the finding, so a reader met
  //      "Not a safety check" before meeting a single number. The caveats are
  //      unchanged and still present - they sit below the headline now, and
  //      this asserts they stay there.
  const order = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("section.card")].map(
      (s) => s.id || (s.querySelector("h2") || {}).id || "");
    return {
      ids,
      headline: ids.indexOf("h-headline"),
      caveats: ids.indexOf("scopecard"),
      issuerDef: document.getElementById("scopecard").innerText
                   .includes("only whose name is on it"),
    };
  });
  check("the finding is rendered before the caveats, not after them",
        order.headline > -1 && order.caveats > -1 && order.headline < order.caveats,
        `headline at ${order.headline}, caveats at ${order.caveats}: ${order.ids.join(" > ")}`);
  check("the issuer definition survived the move into the caveats card",
        order.issuerDef, "the 'whose name is on it' sentence is gone");

  // 11c. The punchline's qualifying tail - two share vectors, the
  //      inverse-Simpson note, the merger-marks disclaimer - is correct and is
  //      a wall of text directly under the one sentence a reader actually
  //      reads. It is folded behind a summary, not cut: still in the DOM,
  //      still reachable, closed by default.
  const qual = await page.evaluate(() => {
    /* the tail starts inside #punchline and is moved out into the
       disclosure beside it, so it is found by class, not by ancestor. */
    const q = document.querySelector(".qual");
    const d = q && q.closest("details.qualwrap");
    return {
      present: !!q,
      folded: !!d,
      openByDefault: d ? d.hasAttribute("open") : null,
      /* innerText is layout-dependent and empty for a hidden node, so the
         text is read with textContent and visibility asked for separately. */
      visible: q ? (typeof q.checkVisibility === "function"
                     ? q.checkVisibility() : q.offsetParent !== null) : null,
      keepsItsText: q ? q.textContent.includes("shape of neither") : null,
    };
  });
  check("the qualifying tail is folded behind a disclosure and closed by default",
        qual.present && qual.folded && qual.openByDefault === false
          && qual.visible === false && qual.keepsItsText === true,
        JSON.stringify(qual));

  // 11d. The issuer lookup. Everything it shows was already on the page, in a
  //      collapsed table in the tenth section, twelve columns wide. These check
  //      that it is now reachable by typing a name, and that the panel agrees
  //      with the snapshot rather than with the rendered cells.
  const target = (snap.issuers || []).find(
    (r) => r.declared_tokens != null && r.declared_tokens !== r.attributed_tokens)
    || (snap.issuers || [])[0];

  /* Null-safe on purpose: if the lookup markup is ever removed these must
     report a clean FAIL, not throw and abort every check after them. */
  const lookupPresent = await page.evaluate(() =>
    !!document.getElementById("issuercard") && !!document.getElementById("issuerfind"));
  check("the issuer lookup is on the page", lookupPresent, "#issuerfind / #issuercard missing");

  const hiddenFirst = await page.evaluate(() => {
    const c = document.getElementById("issuercard");
    return c ? c.hidden : null;
  });
  check("no issuer panel is shown until one is asked for", hiddenFirst === true,
        String(hiddenFirst));

  if (lookupPresent) await page.fill("#issuerfind", target.name.split(" ")[0]);
  await page.waitForTimeout(200);
  const found = await page.evaluate(() => {
    const body = document.querySelector("#issuertable tbody");
    const card = document.getElementById("issuercard");
    const det = document.getElementById("issuerdetails");
    const nt = document.getElementById("findnote");
    return {
      shownCount: body ? [...body.rows].filter((r) => !r.hidden).length : -1,
      detailsOpen: det ? det.open : null,
      cardHidden: card ? card.hidden : null,
      cardText: card ? card.innerText.replace(/\s+/g, " ") : "",
      note: nt ? nt.textContent : null,
    };
  });
  check("typing an issuer name filters the table and opens it",
        found.shownCount >= 1 && found.shownCount < (snap.issuers || []).length
          && found.detailsOpen === true,
        JSON.stringify({ shown: found.shownCount, open: found.detailsOpen }));
  check("the matching issuer's panel is shown",
        found.cardHidden === false && found.cardText.includes(target.name),
        found.cardText.slice(0, 120));

  // The number that was hardest to reach on the old page: what an issuer
  // declares through the issuers endpoint against what the catalogue can
  // actually attribute to it.
  if (target.declared_tokens !== target.attributed_tokens) {
    const dec = target.declared_tokens.toLocaleString("en-GB");
    const att = target.attributed_tokens.toLocaleString("en-GB");
    check("the panel states the declared-versus-attributed gap from the JSON",
          found.cardText.includes(dec) && found.cardText.includes(att),
          `want ${att} of ${dec} in: ${found.cardText.slice(0, 200)}`);
  }

  if (lookupPresent) await page.fill("#issuerfind", "");
  await page.waitForTimeout(150);
  const cleared = await page.evaluate(() => {
    const nt = document.getElementById("findnote");
    return {
      all: [...document.querySelectorAll("#issuertable tbody tr")].every((r) => !r.hidden),
      note: nt ? nt.textContent : null,
    };
  });
  check("clearing the search restores every row", cleared.all && cleared.note === "");

  // 11e. The Lorenz curve used to be drawn once over the whole catalogue, and
  //      the caption admitted it ignored the scope selector. Every block in
  //      the snapshot carries its own complete `shares` vector, so the curve is
  //      exact for any scope. Asserted against the JSON's own n, not a number.
  const chainKeys = Object.keys(snap.by_chain || {});
  const pick = chainKeys
    .map((k) => ({ k, n: (snap.by_chain[k] || {}).n || 0 }))
    .filter((c) => c.n >= 2 && c.n !== (snap.overall || {}).n)
    .sort((a, b) => b.n - a.n)[0];

  check("the Lorenz caption no longer disclaims the scope selector",
        !(await page.evaluate(() =>
          (document.getElementById("lorenzcap") || {}).textContent || "")).includes("does not follow"));

  if (pick) {
    await page.selectOption("#scope", `chain:${pick.k}`);
    await page.waitForTimeout(350);
    const scoped = await page.evaluate(() => ({
      desc: (document.getElementById("lorenzdesc") || {}).textContent || "",
      cap: (document.getElementById("lorenzcap") || {}).textContent || "",
      pts: document.querySelectorAll("#chart-lorenz path, #chart-lorenz polyline").length,
      url: location.search,
    }));
    /* The exact phrase, not a bare digit: "over 15 issuers ... top issuer 36.5%"
       contains "6" and passed this check on a page where the curve never moved. */
    check("the curve is redrawn for the selected scope, over that scope's issuers",
          scoped.desc.includes(`over ${pick.n} issuers`) && scoped.pts > 0,
          `want "over ${pick.n} issuers" in: ${scoped.desc}`);
    check("the caption names the scope it is showing",
          scoped.cap.length > 0 && !scoped.cap.includes("whole catalogue"),
          scoped.cap);
    check("selecting a scope puts it in the URL",
          scoped.url.includes("scope=") && decodeURIComponent(scoped.url).includes(pick.k),
          scoped.url);

    // 11f. And the URL brings it back.
    await page.goto(`http://127.0.0.1:${port}/index.html?scope=chain:${encodeURIComponent(pick.k)}`);
    await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
    await page.waitForTimeout(300);
    const deep = await page.evaluate(() => ({
      sel: (document.getElementById("scope") || {}).value,
      desc: (document.getElementById("lorenzdesc") || {}).textContent || "",
    }));
    check("a ?scope= link opens on that scope",
          deep.sel === `chain:${pick.k}` && deep.desc.includes(`over ${pick.n} issuers`),
          JSON.stringify(deep));

    // A scope this snapshot does not have must not leave an empty page.
    await page.goto(`http://127.0.0.1:${port}/index.html?scope=chain:NotAChainThatExists`);
    await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
    const bad = await page.evaluate(() => ({
      sel: (document.getElementById("scope") || {}).value,
      eff: (document.getElementById("hero-eff") || {}).textContent,
    }));
    check("an unknown ?scope= falls back to the whole catalogue",
          bad.sel === "all" && bad.eff !== "—", JSON.stringify(bad));

    await page.goto(`http://127.0.0.1:${port}/index.html`);
    await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
    await page.waitForTimeout(250);
  }

  // 11g. The run history. This is the only block on the page that cannot be
  //      recomputed from the API: the RWA endpoints are all "latest", and
  //      issuer attribution exists only in the live call. So it must be honest
  //      about how little of it there is.
  const histRows = JSON.parse(
    fs.readFileSync(path.join(DOCS, "data", "history.jsonl"), "utf8")
      .split("\n").filter(Boolean).map((l) => l).join(",").replace(/^/, "[") + "]");

  const hist = await page.evaluate(() => ({
    inline: JSON.parse(document.getElementById("histdata").textContent).length,
    cap: (document.getElementById("histcap") || {}).textContent || "",
    paths: document.querySelectorAll("#chart-history path").length,
    tableRows: document.querySelectorAll("#histtable tbody tr").length,
  }));
  check("the history card renders one table row per recorded day",
        hist.tableRows === histRows.length && hist.inline === histRows.length,
        `${hist.tableRows} rows vs ${histRows.length} in history.jsonl`);
  check("the caption states how many days it is drawn from",
        hist.cap.includes(String(histRows.length)) && /\bdays?\b/.test(hist.cap),
        hist.cap);

  // history.jsonl is keyed by date: a second run on the same day replaces that
  // day's row rather than adding one. So a point is a DAY, and calling it a run
  // overstates nothing about the data but understates the work and misnames the
  // axis - there were 19 archived runs behind three points on 20 Sep, and every
  // string on this card said "run". The page may describe the series only in
  // days; "run" is allowed where the sentence is about a run (the day's last
  // one, the per-run archive), which is why this looks for the counting
  // phrases rather than banning the word.
  const histDates = new Set(histRows.map((r) => r.date));
  check("history.jsonl really is one row per day, which is what the card now claims",
        histDates.size === histRows.length,
        `${histRows.length} rows over ${histDates.size} distinct dates`);
  const histText = await page.evaluate(() => {
    const card = document.getElementById("h-history").closest("section");
    const strip = document.getElementById("deltastrip");
    return ((card ? card.innerText : "") + " " + (strip && !strip.hidden ? strip.innerText : ""))
      .replace(/\s+/g, " ");
  });
  const runCounting = [/\d[\d,]* runs?\b/, /per run\b/, /every run\b/,
                       /runs? recorded/, /once a run\b/, /run before this one/];
  const offenders = runCounting.filter((re) => re.test(histText)).map(String);
  check("and neither the history card nor the delta strip counts its points as runs",
        offenders.length === 0, offenders.join(" "));

  /* The delta heading asserts an adjacency. Check it against the dates rather
     than against the string it happens to contain today: with a gap in the
     series "the day before" is false in the same way "the run before this one"
     was. */
  const deltaHead = await page.evaluate(() => {
    const b = document.getElementById("deltastrip");
    if (!b || b.hidden) return null;
    const h = b.querySelector("h3");
    return h ? h.textContent.trim() : null;
  });
  if (deltaHead && histRows.length >= 2) {
    const d1 = Date.parse(histRows[histRows.length - 2].date + "T00:00:00Z");
    const d2 = Date.parse(histRows[histRows.length - 1].date + "T00:00:00Z");
    const gap = Math.round((d2 - d1) / 86400000);
    check("the delta heading claims adjacency only when the two days are adjacent",
          gap === 1 ? /day before/i.test(deltaHead)
                    : (/last recorded day/i.test(deltaHead) && deltaHead.includes(String(gap))),
          `gap ${gap} day(s), heading ${JSON.stringify(deltaHead)}`);
  }

  /* "Three tickers do match a row" was a typed word over a generated list. */
  const nicPresent = ((snap.not_in_this_catalogue || {}).present || []).length;
  if (nicPresent) {
    const WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven",
                   "eight", "nine", "ten"];
    const word = WORDS[nicPresent] || String(nicPresent);
    const refusedTxt = await page.evaluate(() =>
      (document.getElementById("refused") || {}).innerText || document.body.innerText);
    const bodyTxt = await page.evaluate(() => document.body.innerText);
    check("the near-miss ticker count is written from the snapshot, not typed",
          new RegExp("\\b" + word + " tickers? (?:do|does) match", "i").test(bodyTxt),
          `expected "${word} ticker(s)"; ` +
          (bodyTxt.match(/\b\w+ tickers? (?:do|does) match/i) || ["not found"])[0]);
  }

  // Under three days there must be no line: two points drawn as a trend would
  // claim more than the archive knows.
  if (histRows.length < 3) {
    check("with fewer than three runs no line is plotted",
          hist.paths === 0 && /line starts/i.test(hist.cap), JSON.stringify(hist));
  }

  // Inject a longer archive to exercise the branch the real data cannot reach
  // yet, then put the page back as it was.
  const synth = await page.evaluate(() => {
    /* Guarded: if the history card is ever removed these must FAIL, not throw
       and abort every check after them. */
    const node = document.getElementById("histdata");
    if (!node || typeof renderHistory !== "function") return null;
    const real = node.textContent;
    const base = JSON.parse(real);
    const seed = base[base.length - 1] || { hhi: 2000, effective_n: 4, top5: 0.9,
                                            largest_asset_share: 0.6, date: "2026-09-19" };
    const rows = [];
    for (let i = 0; i < 5; i++) {
      rows.push(Object.assign({}, seed, {
        date: `2026-09-${String(10 + i).padStart(2, "0")}`,
        hhi: seed.hhi + i * 25,
        effective_n: seed.effective_n - i * 0.05,
      }));
    }
    node.textContent = JSON.stringify(rows);
    renderHistory();
    const out = {
      paths: document.querySelectorAll("#chart-history path").length,
      dots: document.querySelectorAll("#chart-history circle").length,
      cap: document.getElementById("histcap").textContent,
      desc: document.getElementById("histdesc").textContent,
      rows: document.querySelectorAll("#histtable tbody tr").length,
    };
    node.textContent = real;
    renderHistory();
    return out;
  });
  check("with three or more runs the series is plotted, one point per run",
        !!synth && synth.paths === 1 && synth.dots === 5 && synth.rows === 5,
        JSON.stringify(synth));
  check("a plotted series says its axis is not zero-based",
        !!synth && /not zero-based/i.test(synth.cap), synth ? synth.cap : "no history card");
  check("the series description names its start and end value",
        !!synth && synth.desc.length > 0 && /to /.test(synth.desc),
        synth ? synth.desc : "no history card");

  // The series selector must actually change what is drawn.
  const switched = await page.evaluate(() => {
    const b = document.querySelector('[data-series="effective_n"]');
    if (!b) return null;
    b.click();
    return {
      cap: document.getElementById("histcap").textContent,
      pressed: b.getAttribute("aria-pressed"),
    };
  });
  check("choosing a different series redraws the history",
        switched && switched.pressed === "true" && switched.cap.includes("Effective issuers"),
        JSON.stringify(switched));

  // 11h. REGRESSION SCENARIOS from the code review of the presentation work.
  //      Every defect below passed all 57 checks that existed at the time. They
  //      are written as the sequence that reproduced them, not as a property of
  //      the markup, because that is how they were missed.

  // (a) The history card borrows .shock for its pill styling. Boot used to bind
  //     the what-if handler with querySelectorAll(".shock button"), so choosing
  //     a history series reset SHOCK_DROP to 0 and blanked the other group.
  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(300);
  const beforeErrs = consoleErrors.length;
  await page.click('[data-drop="3"]');
  await page.waitForTimeout(250);
  const shockA = await page.evaluate(() =>
    (document.getElementById("shockout") || {}).textContent || "");
  await page.click('[data-series="top5"]');
  await page.waitForTimeout(300);
  const cross = await page.evaluate(() => ({
    shock: (document.getElementById("shockout") || {}).textContent || "",
    drop3: (document.querySelector('[data-drop="3"]') || {}).getAttribute
             ? document.querySelector('[data-drop="3"]').getAttribute("aria-pressed") : null,
    series: (document.querySelector('[data-series="top5"]') || {}).getAttribute
             ? document.querySelector('[data-series="top5"]').getAttribute("aria-pressed") : null,
  }));
  check("choosing a history series does not reset the what-if",
        cross.shock === shockA && shockA.length > 0
          && cross.drop3 === "true" && cross.series === "true",
        JSON.stringify(cross));

  // (b) ?scope= was validated on "does a block exist", which is a wider set than
  //     the <option> list: majorChains() hides chains under the floor. Their
  //     blocks rendered with a blank selector, and the next click threw.
  const hidden = await page.evaluate(() => {
    const offered = new Set(scopeOptions(SNAP).map((o) => o.id));
    const k = Object.keys(SNAP.by_chain || {}).find((c) => !offered.has("chain:" + c));
    return k ? "chain:" + k : null;
  });
  if (hidden) {
    await page.goto(`http://127.0.0.1:${port}/index.html?scope=${encodeURIComponent(hidden)}`);
    await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
    await page.waitForTimeout(300);
    const sel = await page.evaluate(() => (document.getElementById("scope") || {}).value);
    await page.click('[data-drop="1"]');
    await page.waitForTimeout(250);
    check("a scope with no option falls back and leaves the selector usable",
          sel === "all" && consoleErrors.length === beforeErrs,
          `sel=${sel} newErrors=${consoleErrors.slice(beforeErrs).join(" | ")}`);
  }

  // (c) A bare key lookup let ?scope=class:__proto__ resolve to Object.prototype
  //     and render a fully laid-out page about nothing.
  await page.goto(`http://127.0.0.1:${port}/index.html?scope=class:__proto__`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(250);
  const proto = await page.evaluate(() => ({
    sel: (document.getElementById("scope") || {}).value,
    eff: (document.getElementById("hero-eff") || {}).textContent,
  }));
  check("a prototype key in ?scope= does not render a page about nothing",
        proto.sel === "all" && proto.eff !== "—", JSON.stringify(proto));

  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(400);

  // (d) The Lorenz table emits rows for i<10 then every tenth, so the last row
  //     is only the whole scope when the count lands on a multiple of ten. The
  //     note claimed it always was.
  const lnote = await page.evaluate(() => {
    const n = document.getElementById("lorenztable-note");
    return n ? n.textContent : "";
  });
  check("the Lorenz note does not claim the last row is the whole scope",
        lnote.length > 0 && !/last row is the whole scope/i.test(lnote), lnote.slice(0, 100));

  // (e) The issuer panel divided by the whole-catalogue total but called it
  //     "Share of scope". Selecting a class the issuer is absent from still
  //     showed its catalogue share under a label naming that class.
  await page.fill("#issuerfind", (snap.issuers[0].name || "").split(" ")[0]);
  await page.waitForTimeout(300);
  const classKey = Object.keys(snap.by_asset_class || {})[0];
  if (classKey) {
    await page.selectOption("#scope", `class:${classKey}`);
    await page.waitForTimeout(350);
  }
  const panel = await page.evaluate(() => {
    const c = document.getElementById("issuercard");
    return c ? c.innerText.replace(/\s+/g, " ") : "";
  });
  check("the issuer panel names the denominator it divides by",
        panel.includes("Share of catalogue") && !panel.includes("Share of scope"),
        panel.slice(0, 90));

  // (f) With the curve following the scope, a one-issuer scope drew a line
  //     identical to the equality diagonal - the most concentrated possible
  //     scope rendered as a picture of perfect equality.
  const single = await page.evaluate(() => {
    const bc = SNAP.by_chain || {};
    const offered = new Set(scopeOptions(SNAP).map((o) => o.id));
    const k = Object.keys(bc).find((c) => bc[c].n === 1 && offered.has("chain:" + c));
    return k ? "chain:" + k : null;
  });
  if (single) {
    await page.selectOption("#scope", single);
    await page.waitForTimeout(400);
    const degen = await page.evaluate(() => {
      const f = document.getElementById("chart-lorenz").closest("figure");
      return { display: f ? f.style.display : null,
               cap: (document.getElementById("lorenzcap") || {}).textContent || "" };
    });
    check("a scope with too few issuers hides the curve instead of drawing equality",
          degen.display === "none" && /too few/i.test(degen.cap), JSON.stringify(degen));
  }

  // (g) drawHistory gated and scaled on a filtered copy but drew from the
  //     unfiltered rows, so a run with a null field - which append_history emits
  //     whenever the priced total is 0 - put the path thousands of units off
  //     canvas, or produced d="M 54 NaN".
  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(400);
  const nulls = await page.evaluate(() => {
    const node = document.getElementById("histdata");
    if (!node || typeof renderHistory !== "function") return null;
    const real = node.textContent;
    const base = JSON.parse(real);
    const seed = base[base.length - 1];
    const rows = [0, 1, 2, 3].map((i) => Object.assign({}, seed, {
      date: `2026-09-1${i}`, hhi: i === 1 ? null : seed.hhi + i * 10 }));
    node.textContent = JSON.stringify(rows);
    renderHistory();
    const pathEl = document.querySelector("#chart-history path");
    const out = {
      d: pathEl ? pathEl.getAttribute("d") : "",
      cap: (document.getElementById("histcap") || {}).textContent || "",
    };
    node.textContent = real;
    renderHistory();
    return out;
  });
  check("a run with a null field is dropped, not drawn off canvas",
        !!nulls && !/NaN/.test(nulls.d) && nulls.d.split(" L ").length === 3
          && /Plotted only where .* is defined/i.test(nulls.cap),
        JSON.stringify(nulls).slice(0, 220));

  // (h) The issuer rows were clickable and unreachable by keyboard. The first
  //     fix gave every row tabindex=0 and role="button", which was worse:
  //     role="button" on a <tr> takes it out of the table, so a screen reader
  //     stops hearing the cells at all and hears a run of anonymous buttons.
  //     The row stays a row; the search box is the keyboard route.
  const kb = await page.evaluate(() => {
    const tr = document.querySelector("#issuertable tbody tr");
    const nt = document.getElementById("findnote");
    return tr ? { tab: tr.tabIndex, role: tr.getAttribute("role"),
                  cells: tr.cells.length,
                  live: nt ? nt.getAttribute("aria-live") : null } : null;
  });
  check("issuer rows stay table rows rather than becoming buttons",
        !!kb && kb.role === null && kb.tab === -1 && kb.cells > 1,
        JSON.stringify(kb));
  check("and the match count is still announced",
        !!kb && kb.live === "polite", JSON.stringify(kb));

  // The keyboard route that replaces it: type a name, get that issuer's panel.
  const kbName = (snap.issuers[0] || {}).name || "";
  await page.fill("#issuerfind", kbName);
  await page.waitForTimeout(350);
  const kbOpen = await page.evaluate(() => ({
    hidden: (document.getElementById("issuercard") || {}).hidden,
    text: (document.getElementById("issuercard") || {}).innerText || "",
  }));
  check("typing an issuer's name opens its panel without a mouse",
        kbOpen.hidden === false && kbOpen.text.includes(kbName),
        `${kbName}: ${kbOpen.text.replace(/\s+/g, " ").slice(0, 80)}`);
  await page.fill("#issuerfind", "");
  await page.waitForTimeout(200);

  // (i) The band refusal - why no 1,500/2,500 threshold is applied - must stay
  //     in the open paragraph. It is the one claim this page refuses to make,
  //     and it was briefly folded behind a disclosure.
  const band = await page.evaluate(() => {
    const b = document.querySelector(".bandnote");
    const q = document.querySelector(".qual");
    return {
      present: !!b,
      visible: b ? (typeof b.checkVisibility === "function" ? b.checkVisibility() : b.offsetParent !== null) : null,
      folded: !!(b && b.closest(".qualwrap")),
      says: b ? /merger analysis are deliberately not applied/i.test(b.textContent) : false,
      qualFolded: !!(q && q.closest(".qualwrap")),
    };
  });
  check("the merger-band refusal stays in the open, not behind the disclosure",
        band.present && band.visible === true && band.folded === false && band.says === true,
        JSON.stringify(band));
  check("the two-vector decomposition is still the thing that folds",
        band.qualFolded === true, JSON.stringify(band));

  await page.fill("#issuerfind", "");
  await page.waitForTimeout(150);

  // 11i. Scope staleness. The hero, the bars and the curve follow the selector.
  //      The generated punchline and the delta strip are whole-catalogue and
  //      always will be. With a chain selected the same card showed
  //      "Effective number of issuers 1.31" beside "94.1% ... 5 issuer labels"
  //      and a delta of "HHI 2,329.2" - three objects, one card. They are
  //      tagged now, and this asserts the tag appears only when it is needed.
  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(400);
  const tagAll = await page.evaluate(() =>
    document.querySelectorAll("#punchline .scopetag, #deltastrip .scopetag").length);
  check("no whole-catalogue tag is shown when the scope IS the whole catalogue",
        tagAll === 0, `${tagAll} tags`);

  const someScope = await page.evaluate(() => {
    const o = scopeOptions(SNAP).map((x) => x.id).filter((x) => x !== "all");
    return o[0] || null;
  });
  if (someScope) {
    await page.selectOption("#scope", someScope);
    await page.waitForTimeout(400);
    const tagged = await page.evaluate(() => {
      const p = document.getElementById("punchline");
      const d = document.getElementById("deltastrip");
      const t = (el) => {
        const s = el ? el.querySelector(".scopetag") : null;
        return s ? s.textContent.trim() : null;
      };
      return { punch: t(p), delta: d && !d.hidden ? t(d) : "hidden" };
    });
    check("blocks that do not follow the selector say so when a scope is picked",
          tagged.punch === "Whole catalogue"
            && (tagged.delta === "Whole catalogue" || tagged.delta === "hidden"),
          JSON.stringify(tagged));
    await page.selectOption("#scope", "all");
    await page.waitForTimeout(350);
    const cleared = await page.evaluate(() =>
      document.querySelectorAll("#punchline .scopetag, #deltastrip .scopetag").length);
    check("the tag is removed again on returning to the whole catalogue",
          cleared === 0, `${cleared} tags left`);
  }

  // 11j. THE BAR CHART. Every bar used to be labelled only if it was one of the
  //      top three; the rest carried a name, a two-pixel stub and no number,
  //      and the tooltip meant to carry it cannot be opened on a touch screen.
  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(500);

  const barGeom = () => {
    const svg = document.getElementById("chart-bars");
    if (!svg) return null;
    const R = svg.getBoundingClientRect();
    const labs = [...svg.querySelectorAll("text.tiplab")];
    const bars = [...svg.querySelectorAll("path[fill]")];
    const names = [...svg.querySelectorAll("text.catlab")];
    return {
      bars: bars.length,
      labels: labs.length,
      texts: labs.map((l) => l.textContent),
      names: names.map((l) => l.textContent),
      /* positive means the widest label still sits inside the chart */
      rightMargin: labs.length ? Math.round(R.right - Math.max(...labs.map((l) => l.getBoundingClientRect().right))) : null,
      /* the fraction of the plot the longest bar actually uses */
      longestPct: bars.length
        ? Math.round(100 * Math.max(...bars.map((b) => b.getBoundingClientRect().width)) / (R.width - 148))
        : null,
      overlap: labs.some((l, i) => {
        const nx = labs[i + 1];
        return nx && l.getBoundingClientRect().bottom > nx.getBoundingClientRect().top + 1;
      }),
    };
  };

  const bg = await page.evaluate(barGeom);
  check("every bar carries its share, not just the top three",
        !!bg && bg.bars > 3 && bg.labels === bg.bars,
        JSON.stringify({ bars: bg && bg.bars, labels: bg && bg.labels }));
  check("no bar label is pushed outside the chart",
        !!bg && bg.rightMargin >= 0, `right margin ${bg && bg.rightMargin}px`);
  check("bar labels do not collide with each other",
        !!bg && bg.overlap === false, JSON.stringify(bg && bg.texts));

  /* The precision bug: choosing decimals from the smallest bar and applying
     them to every bar printed the leader as "36.6107%". Each label takes the
     decimals IT needs. So the largest must be short, and the smallest must not
     read as zero. */
  check("the leading bar is labelled to one decimal, not to the smallest bar's",
        !!bg && /^\d+\.\d%$/.test(bg.texts[0]), bg && bg.texts[0]);
  const tinyLabels = (bg ? bg.texts : []).filter((s) => /^0(\.0+)?%$/.test(s));
  check("no non-zero bar is labelled as a flat zero",
        tinyLabels.length === 0, `flat zeroes: ${JSON.stringify(tinyLabels)}`);

  /* The padding bug: sizing the right margin from the WIDEST label reserves
     space for a label that belongs to the SHORTEST bar, which has the whole
     plot to its right. It cost 113px of a 490px plot. The longest bar has to
     use most of the width it is given. */
  check("the longest bar uses most of the plot rather than a rounded-up axis",
        !!bg && bg.longestPct >= 70, `longest bar is ${bg && bg.longestPct}% of the plot`);

  /* The scope that holds the genuinely unreadable values. */
  const stockKey = Object.keys(snap.by_asset_class || {}).find((k) => k === "stock")
                   || Object.keys(snap.by_asset_class || {})[0];
  if (stockKey) {
    await page.selectOption("#scope", `class:${stockKey}`);
    await page.waitForTimeout(400);
    const sg = await page.evaluate(barGeom);
    check("a scope whose smallest share rounds to zero still labels every bar",
          !!sg && sg.labels === sg.bars && sg.rightMargin >= 0,
          JSON.stringify({ labels: sg && sg.labels, bars: sg && sg.bars, texts: sg && sg.texts }));
    const flat = (sg ? sg.texts : []).filter((s) => /^0(\.0+)?%$/.test(s));
    check("and none of them reads as a flat zero either",
          flat.length === 0, JSON.stringify(sg && sg.texts));
    await page.selectOption("#scope", "all");
    await page.waitForTimeout(350);
  }

  /* shareLabel is the thing under all of that. Exercised directly, including
     the two branches no snapshot currently reaches: an exact zero, and a value
     too small for four decimals. */
  const sl = await page.evaluate(() => {
    if (typeof shareLabel !== "function") return null;
    return {
      big: shareLabel(0.366107),
      mid: shareLabel(0.0026),
      small: shareLabel(0.0000886),
      tiny: shareLabel(0.0000001486),
      zero: shareLabel(0),
      nul: shareLabel(null),
    };
  });
  check("shareLabel gives every value two significant digits, and no more",
        !!sl && sl.big === "36.6%" && sl.mid === "0.26%" && sl.small === "0.0089%",
        JSON.stringify(sl));
  /* The reason two significant digits and not "enough to not be zero": under
     that rule these two bars, different by a factor of 1.5, both read 0.01%. */
  const near = await page.evaluate(() =>
    (typeof shareLabel === "function" ? [shareLabel(0.00014), shareLabel(0.000094)] : null));
  check("two bars of different size are never given the same label",
        !!near && near[0] !== near[1], JSON.stringify(near));

  /* THE BOUND MUST NEVER OVERSTATE SMALLNESS. The first rule returned
     "<0.0001%" whenever two significant digits could not be reached at four
     decimals, which labelled everything from 0.00011% to 0.00095% with a bound
     up to 9.4x too small - 49 of the 791 assets - by the function written to
     stop exactly that kind of claim. Fuzzed, because the three literals the
     suite happened to test all sat outside the broken range. */
  const bound = await page.evaluate(() => {
    if (typeof shareLabel !== "function") return null;
    const liars = [];
    for (let i = 0; i < 20000; i++) {
      const v = Math.random() * 0.004;
      if (shareLabel(v) === "<0.0001%" && v * 100 > 0.0001 + 1e-12) liars.push(v);
    }
    return {
      liars: liars.length,
      worst: liars.length ? Math.max(...liars) * 100 : 0,
      spot: [shareLabel(0.0000094), shareLabel(0.000005), shareLabel(0.0000001486)],
    };
  });
  check("no share is ever labelled with a bound smaller than itself",
        !!bound && bound.liars === 0,
        bound && `${bound.liars} false bounds, worst ${bound.worst}%`);
  check("a value below four decimals still reads as a bound, not a rounded zero",
        !!bound && bound.spot[0] === "0.0009%" && bound.spot[1] === "0.0005%"
          && bound.spot[2] === "<0.0001%",
        JSON.stringify(bound && bound.spot));
  check("below four decimals it states a bound rather than rounding to zero",
        !!sl && sl.tiny === "<0.0001%", JSON.stringify(sl));
  check("an exact zero is labelled 0%, and a missing value is not labelled",
        !!sl && sl.zero === "0%" && sl.nul === "", JSON.stringify(sl));

  /* "Other (1 issuers)" was on the page. So, later, was "1 issuers" under the
     bar chart in three chain scopes - and this check did not see it, for two
     reasons worth fixing rather than patching:

       1. it listed five nouns somebody thought of. Any sixth noun was free.
       2. it ran once, in the default scope. Nine of the eleven scopes were
          never looked at, and the defect lived in three of them.

     So: any lowercase word ending in s after a bare 1, minus a short list of
     words that end in s without being plurals, swept across every scope the
     selector offers. */
  const NOT_PLURALS = new Set(["is", "was", "has", "its", "this", "thus", "less",
                               "plus", "across", "minus", "series", "analysis",
                               "basis", "status", "versus", "always", "perhaps"]);
  const pluralHits = async () => page.evaluate((allow) => {
    const out = [];
    const re = /\b1 ([a-z]{2,}s)\b/g;
    let m;
    const text = document.body.innerText;
    while ((m = re.exec(text)) !== null) {
      if (!allow.includes(m[1])) {
        out.push(m[0] + "  …" + text.slice(Math.max(0, m.index - 40), m.index + 40).replace(/\s+/g, " ") + "…");
      }
    }
    return [...new Set(out)];
  }, [...NOT_PLURALS]);

  const scopeIds = await page.evaluate(() => scopeOptions(SNAP).map((x) => x.id));
  const pluralBad = [];
  for (const sid of scopeIds) {
    await page.selectOption("#scope", sid);
    await page.waitForTimeout(260);
    for (const h of await pluralHits()) pluralBad.push(sid + ": " + h);
  }
  await page.selectOption("#scope", "all");
  await page.waitForTimeout(300);
  /* CROSS-SURFACE INVARIANT, not a presence check. A positive value may never
     render as an all-zero percentage: "0.00%" beside $1,111 said the same thing
     as "0.00%" beside $0, one row apart in the same table. Swept over every
     scope with every table open, because the two tables that did it are behind
     a <details> and only one of them is in the default scope. */
  const zeroPcts = [];
  for (const sid of scopeIds) {
    await page.selectOption("#scope", sid);
    await page.waitForTimeout(240);
    await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
    await page.waitForTimeout(200);
    const bad = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll("table tbody tr").forEach((tr) => {
        const cells = [...tr.querySelectorAll("td")].map((td) => td.textContent.trim());
        /* A row is a liar when it shows a non-zero money figure and an all-zero
           percentage in the same row. $0 with 0.00% is honest; $1K with 0.00%
           is not. */
        const money = cells.find((c) => /^\$/.test(c) && !/^\$0(\.0+)?$/.test(c));
        const zero = cells.find((c) => /^0(\.0+)?%$/.test(c));
        if (money && zero) out.push(cells.join(" | ").slice(0, 90));
      });
      return out;
    });
    for (const b of bad) zeroPcts.push(sid + ": " + b);
  }
  await page.selectOption("#scope", "all");
  await page.waitForTimeout(300);
  check("no row shows a real holding as an all-zero percentage",
        zeroPcts.length === 0, zeroPcts.slice(0, 3).join("  |  "));

  /* A control whose answer contradicts its own label has to explain itself on
     the page, not in the reader's head. "What if the largest issuers were not
     there?" answered with "HHI rises" reads as a bug: the arithmetic is right -
     drop the leader and the rest renormalise upward - but nobody derives that
     at skim speed.

     The first version of this check parsed the direction out of the rendered
     sentence. The old wording put the number after the word HHI rather than
     before it, so the match came back null, the comparison was skipped, and the
     check passed against the very page it was written to condemn. Worth writing
     down: a check that reads its expectation out of the string it is judging
     will agree with whatever that string says.

     So the direction is computed from the snapshot's own share vector with the
     same arithmetic the page uses, and the rendered text is only ever the thing
     being judged. */
  const shockCases = await page.evaluate(() => {
    const hhiOf = (shares, drop) => {
      if (!Array.isArray(shares) || shares.length <= drop) return null;
      const rest = shares.slice(drop);
      const t = rest.reduce((a, v) => a + v, 0);
      if (!(t > 0)) return null;
      const ss = rest.reduce((a, v) => a + (v / t) * (v / t), 0);
      return ss > 0 ? ss * 10000 : null;
    };
    return scopeOptions(SNAP).map((o) => {
      const b = (blockFor(o.id) || {}).block;
      const sh = b && b.shares;
      return { id: o.id, base: hhiOf(sh, 0), dropped: hhiOf(sh, 1) };
    });
  });
  const shockBad = [];
  let shockUps = 0;
  for (const c of shockCases) {
    if (c.base == null || c.dropped == null || !(c.dropped > c.base)) continue;
    shockUps++;
    await page.selectOption("#scope", c.id);
    await page.waitForTimeout(240);
    await page.click('.shock button[data-drop="1"]');
    await page.waitForTimeout(350);
    const stxt = await page.evaluate(() =>
      ((document.getElementById("shockout") || {}).innerText || "").replace(/\s+/g, " ").trim());
    /* The invariant is that the OPERATION is named, not that any particular
       moral is drawn. The first wording asserted "promotes the next name" -
       which was the page editorialising, and a check that demands it entrenches
       the editorial. What has to be true is that a reader can see why the index
       moved: the control says it rebases the remainder, and names who inherits. */
    if (!(/rebase the remaining/i.test(stxt) && /of what is left/i.test(stxt))) {
      shockBad.push(c.id + ": " + stxt.slice(0, 110));
    }
    await page.click('.shock button[data-drop="0"]');
    await page.waitForTimeout(200);
  }
  await page.selectOption("#scope", "all");
  await page.waitForTimeout(300);
  check(`where dropping the leader raises the index, the page says why (${shockUps} scopes)`,
        shockUps > 0 && shockBad.length === 0,
        shockUps === 0 ? "no scope raises the index - this check proves nothing"
                       : shockBad.slice(0, 3).join("  |  "));


  check(`counted nouns agree with their number, in all ${scopeIds.length} scopes`,
        pluralBad.length === 0, pluralBad.slice(0, 4).join("  |  "));

  // 11k. TABLE CLIPPING. Several tables overflowed their card by 17-19px - far
  //      too little to read as a scrollable region and exactly enough to read
  //      as a cut-off column. The cause was the uppercase letter-spaced HEADER
  //      being wider than any cell under it.
  await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; }));
  await page.waitForTimeout(400);
  const wraps = await page.evaluate(() => [...document.querySelectorAll(".tablewrap")].map((wr) => {
    const t = wr.querySelector("table");
    const ths = [...wr.querySelectorAll("thead th")];
    const last = ths[ths.length - 1];
    return {
      id: t ? t.id : "?",
      over: wr.scrollWidth - wr.clientWidth,
      /* a table that does not scroll must not hide its last column either */
      lastThCut: last ? Math.round(last.getBoundingClientRect().right - wr.getBoundingClientRect().right) : 0,
      scrollable: wr.tabIndex === 0,
    };
  }));
  const hairline = wraps.filter((w) => w.over > 0 && w.over < 40);
  check("no table overflows its card by a hairline, which reads as a cut column",
        hairline.length === 0, JSON.stringify(hairline));
  const cutOff = wraps.filter((w) => w.over <= 0 && w.lastThCut > 1);
  check("a table that does not scroll shows its last column in full",
        cutOff.length === 0, JSON.stringify(cutOff));
  const unreachable = wraps.filter((w) => w.over > 0 && !w.scrollable);
  check("every table that does overflow is focusable so it can be scrolled",
        unreachable.length === 0, JSON.stringify(unreachable));

  // 11l. The issuer lookup was the eleventh card of twelve. It is the one
  //      interactive thing on the page a reader would go looking for.
  const lookupPlace = await page.evaluate(() => {
    const cards = [...document.querySelectorAll("section.card")];
    const find = document.getElementById("issuerfind");
    const own = find ? find.closest("section.card") : null;
    return {
      total: cards.length,
      idx: own ? cards.indexOf(own) : -1,
      ids: cards.map((s) => s.id || (s.querySelector("h2") || {}).id || ""),
    };
  });
  check("the issuer lookup sits in the first half of the page, not the eleventh card",
        lookupPlace.idx > 0 && lookupPlace.idx < lookupPlace.total / 2,
        `card ${lookupPlace.idx + 1} of ${lookupPlace.total}: ${lookupPlace.ids.join(" > ")}`);
  check("and it still comes after the finding and the charts",
        lookupPlace.ids.indexOf("h-headline") < lookupPlace.idx
          && lookupPlace.ids.indexOf("h-rank") < lookupPlace.idx,
        lookupPlace.ids.join(" > "));

  await page.goto(`http://127.0.0.1:${port}/index.html`);
  await page.waitForSelector("#assettable tbody tr", { timeout: 15000 });
  await page.waitForTimeout(300);

  // 11m. THE ASSET LOOKUP. The snapshot used to publish 25 of 791 assets, so the
  //      asset side was the one place where the page showed a sample and read
  //      like a whole. Everything here is asserted against the snapshot's own
  //      asset_index rather than against a number typed into this file.
  const ai = snap.asset_index;
  check("the snapshot carries an index over the whole catalogue",
        !!ai && Array.isArray(ai.rows) && ai.rows.length === snap.counts.assets_tokenised,
        `${ai && ai.rows ? ai.rows.length : "none"} rows vs ${snap.counts.assets_tokenised} tokenised`);

  if (ai && ai.rows && ai.rows.length) {
    const col = {};
    ai.fields.forEach((f, i) => { col[f] = i; });

    const look = await page.evaluate(() => ({
      hidden: (document.getElementById("assetlookup") || {}).hidden,
      rows: document.querySelectorAll("#assetindextable tbody tr").length,
      lead: (document.getElementById("assetfindsub") || {}).textContent || "",
      summary: (document.getElementById("assetindexsummary") || {}).textContent || "",
      panelHidden: (document.getElementById("assetcard") || {}).hidden,
      tab: (document.querySelector("#assetindextable tbody tr") || {}).tabIndex,
      role: (document.querySelector("#assetindextable tbody tr") || {}).getAttribute
        ? document.querySelector("#assetindextable tbody tr").getAttribute("role") : null,
      live: (document.getElementById("assetfindnote") || {}).getAttribute
        ? document.getElementById("assetfindnote").getAttribute("aria-live") : null,
    }));
    check("the lookup renders one row per asset in the index",
          look.hidden === false && look.rows === ai.rows.length,
          `hidden=${look.hidden} rows=${look.rows} vs ${ai.rows.length}`);
    /* 791 rows with role="button" put 791 anonymous stops in the tab order and
       removed every figure from the accessibility tree. The row stays a row. */
    check("its rows stay table rows rather than becoming 791 buttons",
          look.role === null && look.tab === -1, JSON.stringify(look));
    check("and the match count is still announced",
          look.live === "polite", JSON.stringify(look));
    check("no asset panel is shown until one is asked for",
          look.panelHidden === true, String(look.panelHidden));
    check("the disclosure says how many assets are behind it",
          look.summary.includes(ai.rows.length.toLocaleString("en-GB")), look.summary);

    // The sentence the full index makes possible, checked against the index.
    const priced = ai.rows.filter((r) => r[col.tokenised_market_cap] > 0).length;
    const quiet = ai.rows.length - priced;
    const noLead = ai.rows.filter((r) => r[col.top_issuer] < 0).length;
    const n = (x) => x.toLocaleString("en-GB");
    check("the lead sentence counts the priced and the unpriced from the data",
          look.lead.includes(n(ai.rows.length)) && look.lead.includes(n(priced))
            && look.lead.includes(n(quiet)),
          look.lead.slice(0, 180));
    check("and says how many assets no issuer leads",
          look.lead.includes(n(noLead)) && /no issuer/i.test(look.lead),
          look.lead.slice(0, 180));

    // An asset priced at zero is the case the index exists to show. It must be
    // listed, and the panel must say what the zero means rather than print it.
    const zeroRow = ai.rows.find((r) => r[col.tokenised_market_cap] === 0);
    if (zeroRow) {
      await page.fill("#assetfind", zeroRow[col.symbol]);
      await page.waitForTimeout(350);
      const z = await page.evaluate(() => ({
        panel: (document.getElementById("assetcard") || {}).innerText || "",
        hidden: (document.getElementById("assetcard") || {}).hidden,
        note: (document.getElementById("assetfindnote") || {}).textContent || "",
      }));
      check("an asset with no asset-level cap is listed and explains the zero",
            z.hidden === false && /asset endpoint reports no market cap/i.test(z.panel),
            z.panel.replace(/\s+/g, " ").slice(0, 160));
      /* The defect this replaces: the panel said such an asset "counts towards
         nothing else on this page". Six of them carry $105.9m on their tokens,
         which IS in overall.total, the index and every chart. Never again. */
      check("and it never claims a token-priced asset counts towards nothing",
            !/towards nothing else on this page/i.test(z.panel),
            z.panel.replace(/\s+/g, " ").slice(0, 200));
      check("and it does not claim a leading issuer it does not have",
            zeroRow[col.top_issuer] >= 0 || /cannot be called the largest|can be called the largest/i.test(z.panel),
            z.panel.replace(/\s+/g, " ").slice(0, 200));
      // "none of its 1 issuer" was the first wording.
      check("its issuer count reads as English, singular or plural",
            !/\bits 1 issuers\b|\bnone of its 1 issuer\b/i.test(z.panel),
            (z.panel.match(/none of its \d+ issuers?/i) || ["n/a"])[0]);
      /* Every counted noun in the panel, not the five nouns the earlier check
         happened to list - it grepped issuers|assets|tokens|chains|runs and so
         sailed past "1 traditional markets" on 778 of the 791 panels. */
      /* A third-person verb after "1" is correct English - "1 token represents
         it" - so the naive "1 <word>s" sweep has to let the verbs through. The
         point of sweeping rather than listing nouns is that the earlier check
         listed five and missed "markets" on 778 panels. */
      const VERBS = /^(represents|mints|carries|reports|holds|is|has)$/;
      const plural = (z.panel.match(/\b1 [a-z]+s\b/gi) || [])
        .filter((m) => !VERBS.test(m.slice(2).toLowerCase()));
      check("no noun in the panel is pluralised against a count of one",
            plural.length === 0, JSON.stringify(plural));
    }

    /* THE TOKEN-ONLY ASSETS. The asset endpoint prices these at zero while
       their tokens report value; that value is inside overall.total and every
       chart. The panel must give the token figure rather than deny it. */
    const tokenOnly = ((snap.coverage || {}).reconciliation || {}).token_only_examples || [];
    if (tokenOnly.length) {
      const worst = tokenOnly.slice().sort((a, b) => b.token_sum - a.token_sum)[0];
      await page.fill("#assetfind", worst.symbol);
      await page.waitForTimeout(400);
      const to = await page.evaluate(() => ({
        panel: (document.getElementById("assetcard") || {}).innerText || "",
        hidden: (document.getElementById("assetcard") || {}).hidden,
      }));
      check("the largest token-only asset is reachable in the lookup",
            to.hidden === false && to.panel.includes(worst.symbol),
            `${worst.symbol}: ${to.panel.replace(/\s+/g, " ").slice(0, 90)}`);
      check("and its panel states the value its tokens do report",
            /tokens? (?:do|does) report/i.test(to.panel) && /\$/.test(to.panel)
              && !/towards nothing/i.test(to.panel),
            to.panel.replace(/\s+/g, " ").slice(0, 220));

      /* EVERY token-only panel, not just the largest. The sentence conjugates a
         verb against the token count, and the only single-token asset among
         them read "Its 1 token do report one". One asset out of six, in the one
         card built to correct an untruth - and the noun sweep above cannot see
         a verb. */
      const verbBad = [];
      for (const t of tokenOnly) {
        await page.fill("#assetfind", t.symbol);
        await page.waitForTimeout(280);
        const txt = await page.evaluate(() =>
          ((document.getElementById("assetcard") || {}).innerText || "").replace(/\s+/g, " "));
        const wrong = txt.match(/\b1 [a-z]+ (?:do|are|were|have|report|mint|carry|hold)\b/gi) || [];
        const plural = (txt.match(/\b1 [a-z]+s\b/gi) || [])
          .filter((m) => !/^(represents|mints|carries|reports|holds|is|has|does)$/.test(m.slice(2).toLowerCase()));
        if (wrong.length || plural.length) verbBad.push(t.symbol + ": " + [...wrong, ...plural].join(", "));
      }
      check(`every token-only panel agrees with its own count (${tokenOnly.length} of them)`,
            verbBad.length === 0, verbBad.join("  |  "));
      await page.fill("#assetfind", worst.symbol);
      await page.waitForTimeout(300);
      /* The figures at the top of the panel must agree with the note under it.
         Reading the asset-level zero there printed "Tokenised cap $0 / Share of
         catalogue 0%" four lines above "$56.9m is counted in the catalogue
         total" - the panel contradicting itself inside one card. */
      const wantShare = (snap.overall || {}).total > 0
        ? worst.token_sum / snap.overall.total : 0;
      check("its headline figures come from the tokens too, not the silent endpoint",
            !/Tokenised cap \$0\b/.test(to.panel) && !/Share of catalogue 0%/.test(to.panel)
              && /reported by its tokens/i.test(to.panel),
            `want ~${(wantShare * 100).toFixed(2)}% — got: ${to.panel.replace(/\s+/g, " ").slice(0, 110)}`);

      // The lead sentence must not call the whole unpriced group worthless.
      const lead2 = await page.evaluate(() =>
        (document.getElementById("assetfindsub") || {}).textContent || "");
      check("the lead sentence separates 'no asset-level cap' from 'worth nothing'",
            !/priced at zero/i.test(lead2)
              && lead2.includes(tokenOnly.length.toLocaleString("en-GB")),
            lead2.slice(0, 200));
    }

    // The largest asset is the one a reader is most likely to type, and it is
    // also the one whose name is a substring of several others.
    const biggest = ai.rows[0];
    await page.fill("#assetfind", biggest[col.name]);
    await page.waitForTimeout(400);
    const big = await page.evaluate(() => ({
      panel: (document.getElementById("assetcard") || {}).innerText || "",
      hidden: (document.getElementById("assetcard") || {}).hidden,
      note: (document.getElementById("assetfindnote") || {}).textContent || "",
      shown: [...document.querySelectorAll("#assetindextable tbody tr")].filter((r) => !r.hidden).length,
    }));
    check("typing an asset's exact name opens that asset, not just a filtered list",
          big.hidden === false && big.panel.includes(biggest[col.symbol]),
          `note="${big.note}" panel="${big.panel.replace(/\s+/g, " ").slice(0, 90)}"`);
    check("and the note says so when other assets also matched",
          big.shown === 1 || /exact match/i.test(big.note),
          `${big.shown} shown, note "${big.note}"`);

    // The panel's numbers come from the index, not from anywhere else.
    const want = {
      cap: biggest[col.tokenised_market_cap],
      tokens: biggest[col.tokens].toLocaleString("en-GB"),
      issuers: biggest[col.issuers].toLocaleString("en-GB"),
    };
    check("the panel's token and issuer counts match the index row",
          big.panel.includes(want.tokens) && big.panel.includes(want.issuers),
          `want tokens ${want.tokens}, issuers ${want.issuers} in: ${big.panel.replace(/\s+/g, " ").slice(0, 140)}`);

    // Where top_assets carries the split, the panel shows who else mints it.
    const topEntry = (snap.top_assets || []).find((a) => a.symbol === biggest[col.symbol]);
    if (topEntry && (topEntry.split || []).length > 1) {
      check("an asset minted by several issuers shows how its value splits",
            /Minted by/i.test(big.panel) && big.panel.includes(topEntry.split[0].issuer),
            big.panel.replace(/\s+/g, " ").slice(0, 200));
      // The tail of a split runs small; a fixed one decimal printed it as 0.0%.
      const flat = (big.panel.match(/\b0\.0%/g) || []);
      check("the small end of a split is not flattened to 0.0%",
            flat.length === 0, JSON.stringify(flat));
    }

    /* The split's `share` field arrives ROUNDED TO 4dp from the generator, so
       an issuer holding 0.00096 of an asset carries share 0.0 and renders "0%"
       - which shareLabel reserves for an exact zero. The page recomputes from
       market_cap. Checked on whichever top-25 asset actually has such a holder,
       not on the largest, whose tail really is zero. */
    const roundedAway = (snap.top_assets || []).find((a) =>
      (a.split || []).some((s) => s.share === 0 && s.market_cap > 0));
    if (roundedAway) {
      const holder = roundedAway.split.find((s) => s.share === 0 && s.market_cap > 0);
      await page.fill("#assetfind", roundedAway.symbol);
      await page.waitForTimeout(400);
      const sp = await page.evaluate(() => {
        const ps = [...document.querySelectorAll("#assetcard .icnote")].map((p) => p.innerText);
        return ps.find((x) => /Minted by/.test(x)) || "";
      });
      const claim = new RegExp(holder.issuer.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*0%");
      check("an issuer with a real holding is never shown as 0% of an asset",
            sp.length > 0 && !claim.test(sp),
            `${roundedAway.symbol} / ${holder.issuer} ($${holder.market_cap}): ${sp.slice(0, 200)}`);
    }

    await page.fill("#assetfind", "zzzzzznotanasset");
    await page.waitForTimeout(300);
    const none = await page.evaluate(() => ({
      note: (document.getElementById("assetfindnote") || {}).textContent || "",
      hidden: (document.getElementById("assetcard") || {}).hidden,
      shown: [...document.querySelectorAll("#assetindextable tbody tr")].filter((r) => !r.hidden).length,
    }));
    check("a search that matches nothing says so and shows no panel",
          none.shown === 0 && none.hidden === true && /no asset matches/i.test(none.note),
          JSON.stringify(none));

    await page.fill("#assetfind", "");
    await page.waitForTimeout(300);
    const back = await page.evaluate(() => ({
      all: [...document.querySelectorAll("#assetindextable tbody tr")].every((r) => !r.hidden),
      open: (document.getElementById("assetindexdetails") || {}).open,
      panelHidden: (document.getElementById("assetcard") || {}).hidden,
    }));
    check("clearing the search restores every asset", back.all);
    /* Searching force-opens the list. Leaving it open on an empty query drops
       791 rows back in - 29,000px on a phone, with the caveats and the method
       pushed below them - for a reader who has just cleared the box. */
    check("and folds the list back rather than leaving 791 rows on the page",
          back.open === false && back.panelHidden === true, JSON.stringify(back));
  }

  /* Neither lookup follows the scope selector, and the issuer one sits directly
     under the charts that do. The tag is what teaches a reader that an untagged
     block follows the selector, so these two must carry it too. */
  const scopeChoice = await page.evaluate(() => {
    const o = scopeOptions(SNAP).map((x) => x.id).filter((x) => x !== "all");
    return o[0] || null;
  });
  if (scopeChoice) {
    await page.selectOption("#scope", scopeChoice);
    await page.waitForTimeout(400);
    const tagged = await page.evaluate(() => ({
      issuer: !!document.querySelector("#h-issuers .scopetag"),
      asset: !!document.querySelector("#h-assetfind .scopetag"),
    }));
    check("both whole-catalogue lookups are tagged when a scope is chosen",
          tagged.issuer && tagged.asset, JSON.stringify(tagged));
    await page.selectOption("#scope", "all");
    await page.waitForTimeout(350);
    const cleared2 = await page.evaluate(() =>
      document.querySelectorAll("#h-issuers .scopetag, #h-assetfind .scopetag").length);
    check("and untagged again on returning to the whole catalogue",
          cleared2 === 0, `${cleared2} tags left`);
  }

  // 12. the page must not scroll sideways, at desktop or phone width
  for (const w of [1600, 390]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(250);
    const over = await page.evaluate(() =>
      Math.round(document.documentElement.scrollWidth - document.documentElement.clientWidth));
    check(`the page does not scroll sideways at ${w}px`, over <= 1, `overflows by ${over}px`);
  }

  // 13. the gate card: the claims about what stops a wrong number shipping
  //
  //     A card that says "the run refuses outside 0.85-1.15x" is a promise about
  //     a file the card cannot see. Quoting it on the page and defining it in the
  //     workflow makes two sources of truth, and the page is the one nobody edits
  //     when the gate moves. So the bands are read back out of the workflow here.
  //     If this check ever fails, the workflow is right and the page is lying.
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.waitForTimeout(200);

  /* The gates are folded behind a <details> now, and a closed <details> has no
     innerText - which is how this check went red on a page that was fine. Open
     it first: what matters is what a reader sees when they expand it, not
     whether it happens to be expanded by default. The lead sentence above it is
     checked separately, because that is the part nobody has to click. */
  await page.evaluate(() => {
    const f = document.getElementById("gatefold");
    if (f) f.open = true;
  });
  await page.waitForTimeout(250);
  const gate = await page.evaluate(() => {
    const card = document.getElementById("gatelist");
    if (!card) return null;
    return {
      heading: (document.getElementById("h-gates") || {}).textContent || "",
      items: [...card.querySelectorAll("li")].map((li) => li.innerText.replace(/\s+/g, " ").trim()),
      bands: [...card.querySelectorAll(".gate-band")].map((b) => b.textContent.trim()),
      recon: ((document.getElementById("gate-recon") || {}).innerText || "").replace(/\s+/g, " ").trim(),
      links: [...(document.getElementById("h-gates").closest("section")
        .querySelectorAll("a[href]"))].map((a) => a.getAttribute("href")),
    };
  });
  /* The one paragraph that is open by default has to carry the claim on its own,
     because most readers will never open the fold. */
  const gateLead = await page.evaluate(() => {
    const h = document.getElementById("h-gates");
    const sec = h && h.closest("section");
    const p = sec && sec.querySelector("p.sub");
    return p ? p.innerText.replace(/\s+/g, " ").trim() : null;
  });
  check("the open sentence says what happens when a run fails, without being expanded",
        !!gateLead && gateLead.length > 120 && /publishes nothing|does not publish/i.test(gateLead)
          && /not sufficient/i.test(gateLead),
        JSON.stringify(gateLead));

  check("the page says what stops a wrong number being published",
        !!gate && /stops a wrong number/i.test(gate.heading), JSON.stringify(gate && gate.heading));
  check("and every gate it lists carries a sentence, not a stub",
        !!gate && gate.items.length >= 6 && gate.items.every((t) => t.length > 80),
        gate ? JSON.stringify(gate.items.map((t) => t.length)) : "no card");

  // The run's own figure against the band, taken from the snapshot rather than
  // typed. Three numbers, all of which move.
  if (gate) {
    const rec = (snap.coverage || {}).reconciliation || {};
    if (rec.ratio == null) {
      check("with no ratio this run, the gate line says so rather than showing a blank",
            /no ratio this run/i.test(gate.recon), gate.recon);
    } else {
      // Matched as phrases, not as a bag of numbers: "0 of them more than 1% out"
      // contains a bare 1, so a set-membership test would accept the wrong count
      // whenever the right one happened to be 1.
      const gb = (v) => Number(v).toLocaleString("en-GB");
      const want = [rec.ratio.toFixed(3) + "×",
                    gb(rec.assets_compared) + " assets",
                    gb(rec.assets_off_by_over_1pct) + " of them"];
      const missing = want.filter((w) => !gate.recon.includes(w));
      check("the gate line reports this run's own reconciliation, not a typed one",
            missing.length === 0,
            `missing ${JSON.stringify(missing)} from ${JSON.stringify(gate.recon)}`);
      // Whether assets_compared is the right count is the generator's problem,
      // not the page's - it said 791 while the ratio covered 785 until that was
      // fixed, and the page rendered the field faithfully either way. The
      // invariant is pinned in tests/test_aggregation.py [13], against a fixture,
      // so it cannot depend on which day's snapshot happens to be committed.
    }

    // The bands, against the workflow that enforces them.
    const wfPath = path.resolve(__dirname, "..", ".github", "workflows", "refresh.yml");
    if (!fs.existsSync(wfPath)) {
      check("the workflow is present so the quoted bands can be checked against it",
            false, wfPath);
    } else {
      const wf = fs.readFileSync(wfPath, "utf8");
      // Both gates are written as a Python chained comparison on the ratio.
      const bands = [...wf.matchAll(/([\d.]+)\s*<=\s*r\["ratio"\]\s*<=\s*([\d.]+)/g)]
        .map((m) => [m[1], m[2]]);
      check("the workflow states two bands on the reconciliation ratio",
            bands.length === 2, JSON.stringify(bands));
      const pageBands = gate.bands.map((t) => (t.match(/[\d.]+/g) || []));
      check("and the page quotes both of them exactly as the workflow enforces them",
            bands.length === 2 && pageBands.length === 2 &&
            JSON.stringify(pageBands) === JSON.stringify(bands),
            `page ${JSON.stringify(pageBands)} vs workflow ${JSON.stringify(bands)}`);
      // Order matters to the sentence: the aborting band has to be the wider one,
      // or the page describes the warning as the refusal.
      check("the aborting band is the wider of the two, as the sentence claims",
            bands.length === 2 &&
            Number(bands[0][0]) < Number(bands[1][0]) &&
            Number(bands[0][1]) > Number(bands[1][1]),
            JSON.stringify(bands));
    }

    // Every link in the card has to point somewhere in this repository. A gate
    // card whose evidence link 404s is worse than no gate card.
    check("every link in the gate card points into this repository",
          gate.links.length >= 3 &&
          gate.links.every((h) => /^https:\/\/github\.com\/mistryrajan87-lang\/backstop(\/|$)/.test(h)),
          JSON.stringify(gate.links));
  }

  await browser.close();
  srv.close();

  console.log("-".repeat(60));
  if (failures.length) {
    console.log(`\n${failures.length} FAILURE(S):`);
    failures.forEach((f) => console.log("  - " + f));
    process.exit(1);
  }
  console.log(`all ${pass} page checks passed`);
})().catch((e) => { console.error(e); process.exit(2); });
