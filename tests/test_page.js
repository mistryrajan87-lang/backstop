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

  // 8. the page must not scroll sideways, at desktop or phone width
  for (const w of [1600, 390]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(250);
    const over = await page.evaluate(() =>
      Math.round(document.documentElement.scrollWidth - document.documentElement.clientWidth));
    check(`the page does not scroll sideways at ${w}px`, over <= 1, `overflows by ${over}px`);
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
