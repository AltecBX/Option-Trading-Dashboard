# Hedge Fund Intelligence — architecture audit

**Date:** September 7, 2026 · **Audited at:** `main` @ `d0f7f70` (v4.90)
**Scope:** `hf_*.py` (11 modules), `options_dashboard.py`, `storage.py`,
`tab-hedge.jsx`, the frontend build, `thresholds.json`, `hf_watchlist.json`,
all hedge tests, the HTTP smoke suite, and the v4.85 → v4.90 commit history.

Every claim below cites the file and line, function, route, test or stored
structure that supports it. Claims I could not verify are marked
**UNVERIFIED** rather than asserted.

---

## Executive summary

The **financial reasoning is the strongest part of this system** and should
be left alone. The evidence-class discipline, the refusal to attribute
anonymous data, the two-date model, UNKNOWN as a rendered state, and the
grader's refusal to score verdicts are not merely correct — they are
unusually careful, enforced in code rather than in convention, and pinned by
tests that fail when the discipline is removed.

The **architecture around that reasoning has not kept pace**. It was built
for one weekly reading and now carries a daily filing sweep, a replayed
three-year record, a grader, a consensus layer, and a push channel. Three
specific structural facts now limit it:

1. There is **no raw observation layer**. The only durable record is a
   derived weekly snapshot that is overwritten in place.
2. Everything downstream reads **derived state**, so a correction anywhere
   requires a rebuild rather than a recompute, and nothing can be re-asked
   of history.
3. The **cadence of the whole system is bolted to one 12-hour pulse timer**,
   including the alert layer that should follow the filing clock.

None of this is urgent in the sense of being wrong today. Two items *were*
urgent, and **both are now closed** (September 7, 2026): the unbounded
caches are bounded — the disk half turned out to be worse than the memory
half and this audit had understated it (§D1) — and the persistence volume is
**confirmed attached at `/data`** (§D2).

**PostgreSQL is not justified today** and I recommend against it — the
evidence is in §F6. A raw observation log in the existing JSON idiom, with
SQLite as the escape hatch, meets every stated goal.

---

## A. What is already excellent and must not be touched

| What | Where | Why it stays |
|---|---|---|
| **Attribution rule enforced in code** | `hf_sources.attribution_ok`; raised in `hf_scan._save_snapshot` (`hf_scan.py:213`), `hf_scan.save_report` (`:698`), and checked in `hf_alert.attribution_ok` before any push | Anonymous evidence *cannot* carry a fund name — a stored file that did would be a permanent error, and the check runs again at every write boundary. `test_hf_sources.py`, `test_hf_alert.py::Attribution` |
| **Two dates on every fact** | `hf_sources.evidence()` — `"as_of": as_of, "public_on": public_on` | Filing date is never treated as position date. Guarded in `test_hf_ui.js` ("every evidence row carries as_of and public_on") |
| **UNKNOWN as a rendered state** | `hf_watch.activity_state`, `.hf-activity-unknown` in `styles.css` | Between filings the card says UNKNOWN in that word rather than implying continuity |
| **OPAQUE books refused as views** | `hf_watch` refusal text; `hf_names.eligible()` (`hf_names.py:87`) excludes non-READABLE | Extended correctly into the newest layer: Citadel held the largest position in the render fixture and was absent from every consensus row |
| **The grader never scores a verdict** | `hf_grade.grade_verdicts` — distributions and a base rate, no hit rate | A test asserts "correct", "hit", "accuracy", "score" appear nowhere in that block |
| **Base rate always on the same market** | `hf_grade.grade_crowded_weeks` | A sector that fell all year cannot make crowding look predictive |
| **Episodes, not weeks, temper the intervals** | `hf_grade.episodes`, per horizon | Caught by a random-walk control that produced a false finding |
| **Point-in-time reconstruction** | `hf_scan.crowded_history` (`:1013`), `hf_replay.truncate_cftc` | Nothing that arrived later can leak into a past week |
| **Input-complete or not at all** | `hf_replay.REQUIRES` + the half-window refusals | A verdict rebuilt from fewer inputs is a different answer wearing the same name |
| **Purity discipline** | `hf_pulse`, `hf_press`, `hf_report`, `hf_grade`, `hf_replay`, `hf_names`, `hf_alert` — no I/O, no clock; asserted by regex tests in each suite | Makes every engine reproducible and testable offline |
| **Injection over import** | `funds_fn`, `positions_fn`, `trend_fn`, `alert_fn`, `push_ready_fn` | `hf_scan` never imports `hf_watch`; a test asserts `pushover`/`ntfy` appear nowhere in `hf_scan` |
| **Atomic writes everywhere** | All six writers use `tmp` + `Path.replace` (`hf_scan.py:219`, `:718`, `save_grades`, `save_replay`, `save_alerts`, `save_shvol`) | A torn write cannot corrupt a stored record |
| **Same-origin by default** | `options_dashboard.py:8265-8273` — CORS off unless `ALLOWED_ORIGIN` is set | Correct default |
| **517 Python + 182 JS guards** | 11 hedge suites | Coverage is genuinely high and the tests assert *behaviour and refusals*, not shapes |

**Do not "clean up" any of the above.** Several were written in response to
a specific real defect and the comments record why.

---

## B. Confirmed architectural weaknesses

### B1 · No raw observation layer — everything durable is derived (P1)

The only durable pulse record is `hf/pulse/{week}.json`, written by
`_save_snapshot` (`hf_scan.py:212-223`), which is a **fully derived board**:
verdicts, confidence, crowding states, sector rows. The raw inputs that
produced it (`gather_all()`, `hf_scan.py:464-484`) are **never persisted**.

Consequences, all verifiable:

- A change to `hf_pulse.confidence` cannot be applied to history — there is
  nothing to recompute *from*. `hf_replay` exists precisely because of this,
  and it only works for CFTC-derived questions because CFTC is the one
  source with a queryable archive of its own.
- `hf_replay.gather_shvol_history` (`hf_scan.py:1394`) has to re-fetch FINRA
  daily files the board *already downloaded and threw away* — it caches one
  float per session in a side file because the observation was never kept.
- Sector short-interest roll-ups, ETF flows, and tide readings taken every
  refresh are lost the moment the board is rebuilt.

This is the single most consequential structural gap and it underlies
Findings 1, 2, 3, 12 and 13.

### B2 · The weekly snapshot is overwritten in place (P1) — Finding 1 CONFIRMED

`_snap_path(week)` → `hf/pulse/{week}.json` (`hf_scan.py:207-209`), and
`_save_snapshot` writes to that exact path. `_stale()` (`:536-547`) allows a
rebuild every `pulse.refresh_hours` (12), so **a week can be rebuilt roughly
14 times and retain only the last**.

The prompt's Monday→Thursday example is accurate. I confirmed the mechanism;
I did not observe a real intra-week reversal being lost, because the local
store has one week in it.

Two second-order effects the prompt did not mention:

- `_prior_snapshot(this_week)` (`:265`) deliberately compares against the
  most recent *different* week, so `changed_since` is comparing against a
  neighbour that may itself have been overwritten several times.
- `history()` (`:235`) reads every stored week's **full 62.8 KB JSON** and
  keeps five fields. `build_grades` calls `history(400)` (`:1094`), so a
  mature store means parsing ~25 MB of JSON to extract ~2,000 values, on
  every grade build.

### B3 · Alert cadence is bolted to the pulse clock, not the filing clock (P1) — **missed by the prompt**

`check_alerts()` is called from exactly one place: inside `_kick()`'s worker
thread (`hf_scan.py:559-570`). `_kick()` has exactly one call site
(`hf_scan.py:592`), gated by `_stale()` — 12 hours.

So:

- A SCHEDULE 13D can sit unnoticed for up to 12 hours even though
  `hf_watch` sweeps EDGAR every `watch.refresh_hours` (6) and already has
  the filing in `new_filings`.
- `/api/hf/refresh` refreshes the **fund watch**, not the pulse
  (`options_dashboard.py:10502-10504`) — so there is no way to make the
  alert layer look, short of waiting.
- I observed this live during Phase 6 verification: after calling
  `/api/hf/refresh`, `/api/hf/alerts` still reported `primed: false` across
  twelve polls over six minutes.

The alert layer reads daily/6-hourly data on a 12-hourly clock. That is an
architectural mismatch, not a tuning problem.

### B4 · "Independent evidence classes" overstates independence (P1) — Finding 4 CONFIRMED, with a correction

`hf_pulse.confidence` (`:168-201`) counts **distinct evidence classes**
agreeing and renders the sentence *"N independent evidence classes agree"*
(`:198`).

The prompt is right that this overstates independence. ETF creations
(FLOW PROXY), the short-volume share (FLOW PROXY), CFTC futures
(REGULATORY) and a Goldman note (PRIME BROKER) can all be four views of one
liquidation.

**Two corrections to the prompt's framing:**

1. The *mechanism* is more careful than the prompt implies. A `weight < 1.0`
   input can never decide a verdict (`_deciding`, `:164`); disagreement with
   only one agreeing class forces LOW (`:189`); and HIGH additionally
   requires zero disagreement (`:191`). It is a corroboration counter with
   guard rails, not a naive vote.
2. The defect is therefore **the word, not the maths**. "Independent" is a
   statistical claim the code does not establish. "Corroborating" would be
   true today and would cost nothing.

Moving to historical calibration is the right long-term answer and
`hf_grade`/`hf_replay` are the right tools — but note the honest ceiling:
the four verdicts now have 159 graded weeks (2 recorded + 157 replayed),
and the observed forward returns sit within ~1 point of the 3.4% base at
every horizon. **The current empirical answer is that these verdicts do not
predict returns**, which is exactly what the board says. Calibrated
confidence should be about *persistence* ("how often did this state hold?"),
not about returns.

### B5 · Version stamps are inconsistent across persisted documents (P1) — Finding 10 CONFIRMED

| Document | Carries engine version | Carries schema version |
|---|---|---|
| `hf/pulse/{week}.json` | `version` from `hf_pulse.build` | no |
| `hf/reports/{week}.json` | `HF_REPORT_VERSION` + `scan_version` (`:814`) | no |
| `hf/grades.json` | `HF_GRADE_VERSION` + `scan_version` | no |
| `hf/replay.json` | `HF_REPLAY_VERSION` + `scan_version` | no |
| `hf/alerts.json` | **none** | no |
| `hf/shvol_daily.json` | **none** | no |
| `hf/funds/{key}.json` | `watch_version` | no |

`hf_report.normalize()` exists solely because an older stored shape had to
be reinterpreted, and it detects the version by the *presence of a field*
(`n_filed_since`) rather than by a stamp — the comment says so. That is a
symptom worth fixing generally.

The v4.88 finding where the status route reported the **code** version while
serving a **stored** card is the same class, already fixed in one place
(`grade_status`, `report_status`) but not established as a rule.

### B6 · No source-health model (P1) — Finding 11 CONFIRMED

What exists: `gather_all` collects free-text failure strings into
`out["_failed"]` (`hf_scan.py:472-483`), surfaced as `board["unavailable"]`;
`hf_watch` keeps `_STATE["errors"]` per manager key. That is it.

There is no per-provider record of: last success, last failure, next retry,
expected cadence, staleness, rate-limit state, or "response shape changed".
A provider that silently starts returning an empty list is indistinguishable
from a quiet market — precisely the failure the prompt is worried about, and
precisely how the Phase 5 "0 symbols" short-interest result looked before I
traced it to an unpublished settlement.

### B7 · Loose dictionaries are implicated in a real bug pattern (P1) — Finding 9 CONFIRMED

This is verifiable from the commit history, and most of these are mine:

| Bug | Commit | Class |
|---|---|---|
| Build week vs data week priced two layers a week apart | `d2557ec` §12d | key identity |
| Status reported code version while serving a stored card | `0656164` | version identity |
| `n_crowded_weeks_graded` summed four horizons (127 vs 33) | `84d2fa6` | count reused with a different meaning |
| Stored replay not reloaded after restart | `093a769` | lifecycle |
| Report `sentence` absent from older stored shapes | #354 | schema drift |
| Revision collision after retention trim (build 14 → revision 13) | #351 | identity |
| Window kept the market's original `as_of` (204 rows in one week) | v4.88 | field reuse |

Seven instances of one pattern: **a value that was correct where it was
computed became wrong where it was reused.** Every one crossed a module or a
persistence boundary as an untyped `dict`.

### B8 · `options_dashboard.py` is a monolith (P2) — Finding 7 CONFIRMED

13,907 lines; 158 path branches; 65 distinct `/api/*` prefixes; the hedge
section alone spans `:10470-10570`. The HTTP layer, provider wiring, domain
calls and background scheduling all live in one file.

**But:** the stdlib HTTP server is *not* currently a bottleneck. The smoke
suite exercises 159 routes with two pre-existing timeouts unrelated to load.
The real cost is navigational and blast-radius — as demonstrated when
`push_ready_fn=_push_configured` referenced a function defined 1,600 lines
later, raised at import, and took **every hedge route to 503** behind a
`try/except`.

### B9 · Report retention silently drops revisions (P2)

`save_report` keeps the last `report.keep_revisions` (12) revisions
(`hf_scan.py:714-715`). This is deliberate and documented, but it does
conflict with the stated principle "historical reports should remain
reproducible and auditable": revision 3 of a week that has been rebuilt 20
times is gone. `n_revisions` correctly records that it existed.

---

## C. Findings in the prompt that are NOT problems

### C1 · Finding 5 (coverage) — largely already done

Coverage reporting already exists in four places and is good:

- `sector_coverage` — `{"placed": N, "of": M, "sectors": K, "min_symbols": F}` (`hf_scan.py:384`)
- `hf_replay.coverage()` — per question, weeks reached and **why it stopped**
- `hf_names.eligible()` → `basis` — counted / opaque / not-read, with the denominator on screen
- `grade card.readings_from` — `{recorded, replayed}`

What is genuinely missing is **uniformity**, not the concept. Confidence and
coverage are already separate ideas in the code; the prompt's concern that
"HIGH confidence hides poor coverage" is not currently realisable, because
`confidence` is computed only from inputs that exist and `n_classes` is
reported beside the level.

### C2 · Finding 6 (PostgreSQL) — not justified by the evidence

Measured: one weekly board is **62.8 KB**. Projected annual volume for
everything the engines actually consume:

| Store | Rows or files per year | Bytes per year |
|---|---|---|
| Weekly boards | 52 | ~3.3 MB |
| Report revisions | ≤ 624 | ~25 MB |
| Grade + replay cards | 2 (rewritten) | < 2 MB |
| Daily short-volume floats | 252 | ~10 KB |
| Fund records | 32 (rewritten) | < 1 MB |

That is **tens of megabytes a year**. No query pattern in the codebase needs
a join, a transaction across tables, or concurrent writers — there is one
writer process and a handful of daemon threads with an `RLock`.

Postgres would be justified if the observation tape stored **per-symbol**
history (short volume alone would be ~2.3M rows/year). It should not:
every engine consumes market-wide aggregates and sector roll-ups. The one
per-symbol consumer, `hf_pulse.crowded_shorts`, needs only the current
settlement's top 12.

**Recommendation: do not adopt PostgreSQL.** Adopt an append-only JSONL
observation log (§K). If per-symbol history ever becomes a requirement,
SQLite on the same volume is the next step and needs no infrastructure.

### C3 · Finding 8 (frontend) — aimed at the wrong file

`tab-hedge.jsx` is **2,025 lines**, already lazy-loaded
(`build_frontend.js:38,51`; `app.jsx:3521` `<LazyTab chunk="tab-hedge">`),
and renders 11 components. It is the *healthiest* large frontend file in the
repo.

The actual problems are `app-cards.jsx` (**15,236 lines**) and `styles.css`
(**13,447 lines**), neither of which is hedge-related. Splitting
`tab-hedge.jsx` into the 17 files the prompt lists would create churn
against a file that is not causing pain, and would risk the lazy-chunk
registration (4 edits + a TABS entry per chunk).

**Recommendation: leave `tab-hedge.jsx` alone for now.** Revisit at ~3,000
lines or when two panels need to be loaded independently.

### C4 · Finding 14 (named fund consensus) — the denominator rule is already kept

`hf_names.build()` returns `basis` with `n_counted` / `n_managers` /
`n_opaque` / `n_not_read`; the headline reads *"held among the ten largest
positions of 10 of the 21 readable books"*; and the card's own `note` says
"not how many own it". `test_hf_names.py::WhatItCanActuallySee` asserts the
phrase "most owned" is never used as a label.

The prompt's warning ("never say 7 hedge funds are buying NVDA") is already
implemented — the tables are headed *"Added or newly bought last quarter"*,
past tense, with the quarter span on every row.

### C5 · Finding 15 (security) — mostly already correct

- API key enforced when `API_KEY` is set; refuses to bind non-loopback
  without it for position endpoints (`options_dashboard.py:13677-13685`).
- CORS is same-origin unless `ALLOWED_ORIGIN` is explicitly set (`:8265`).
- All six hedge writers are atomic.
- Provider caching has per-kind TTLs (`hf_sources.TTL`, `:88`), EDGAR
  documents cached FOREVER, and cooldowns after failed grade/replay builds.

The real reliability problems are in §D, and the prompt did not find them.

---

## D. Important problems the prompt missed

> **RESOLVED — September 7, 2026.** Both D1 and D2 are closed. D1 was fixed
> and shipped; D2 was confirmed to be a non-issue. The disk half of D1 was
> **understated below** — see the correction at the end of D1.

### D1 · `_MEM` is an unbounded in-process cache of raw response bytes (P0 reliability) — **FIXED**

`hf_sources._MEM` (`:85`) maps URL → `(timestamp, raw bytes)` and is written
on **every** fetch (`:187`, `:228`). Nothing evicts it.

My own Phase 5A backfill drives ~40 FINRA daily short-volume files through
it per replay build (`shvol_budget_days`, `thresholds.json`). Those files are
several megabytes each. **A single replay build can add on the order of
200 MB of retained bytes to the process**, and the ceiling
(`shvol_max_days` 800) implies far more over time.

This is the most likely cause of a future out-of-memory restart on Railway,
and it is a defect I introduced without noticing. It needs an LRU bound or
a "do not memoise large bodies" rule.

**Correction — the disk half was worse than the memory half, and this audit
missed it.** `_get` and `_post` wrote every body to the persistent volume as
**hex**, which is exactly twice the size of the bytes it encodes. Measured on
a real cached FINRA short-interest page: a 597 KB body occupied 1,195 KB on
disk — 2.0x — and the local cache directory from two Phase 5 verification
runs held **323 MB across 329 files**. That is the same paid volume that
holds the actual record, and unlike memory it is not reclaimed by a restart.

**What shipped:**

- `_MEM` is now an LRU bounded to `MEM_MAX_BYTES` (32 MB), with a read
  touching its key so eviction takes the least recently *used*. The byte
  counter self-heals when the dict is cleared directly, which the suites do.
- A body over `CACHE_MAX_BODY` (2 MB) is returned to the caller and cached
  **nowhere** — the big FINRA files are read once for a single number, and
  `hf_scan.gather_shvol_history` already keeps that number.
- The disk record is gzip + base64 rather than hex: the same 597 KB page now
  occupies 235 KB, **5.1x smaller than hex**. Records written in the old hex
  form are still read, because a cache file outlives the code that wrote it.
- `cache_stats()` and `prune_cache()` are exposed at `/api/hf/cache` and
  `/api/hf/cache/prune`. Pruning removes files **only by size, never by
  age**: an oversized file will never be written again, while a small one
  may be an EDGAR filing cached forever that saves a round trip on every
  fund card.

Nine tests in `test_hf_sources.py::TheCachesAreBounded`; removing the bounds
fails two of them.

**The backlog was cleared too.** Stopping the growth left 612.9 MB across
1,348 files already on the volume — most of it EDGAR filings cached FOREVER,
which expire never and so would have stayed at double size for the life of
the volume. `recompress_cache(limit)` rewrites hex records in the compact
form, batched and restartable because an HTTP request should not hold a
connection open while hundreds of files are rewritten. Nothing is
re-fetched, the timestamp is preserved so a converted entry keeps exactly
the freshness it had, and a record that will not decode is left alone rather
than deleted.

Proved lossless on a real copy of the cache before it touched the volume —
every body SHA-256'd before and after:

```
before 323 MB / 329 files  ->  after 67 MB      256.2 MB freed
bodies that changed: 0
```

Ten more tests in `RecompressingTheOldRecords`, including byte-for-byte
survival, timestamp preservation, idempotency, and that a converted entry is
still served from cache rather than re-fetched.

### D2 · Persistence depends on a Railway volume that I could not confirm is attached (P0 data loss)

`storage._stable_data_dir()` (`:29-46`) uses `JERRY_DATA_DIR`, else `/data`
**if that directory exists**, else `~/.jerry-dashboard` — which is wiped on
every redeploy.

**CONFIRMED — September 7, 2026: a volume IS attached, and this is not a
risk.** `/api/watchlist/diag` (`options_dashboard.py:9736` →
`storage._watchlist_diag`) reports from the live deployment:

```
data_dir           : /data
JERRY_DATA_DIR env : /data
watchlist present  : True | symbols: 1276
```

Better than the `/data`-exists fallback: `JERRY_DATA_DIR` is set explicitly
as an environment variable, so the ephemeral branch cannot be reached by
accident. Storage is persistent and every finding about history stands.

The remaining nit is that the ephemeral fallback is silent — a deployment
that lost its volume would degrade without saying so. That is P3, not P0.

### D3 · The replay is expensive and rebuilt whole (P2 performance)

`build_replay` (`hf_scan.py:1464`) walks 158 weeks × truncating 12 markets ×
running the real verdict functions, plus `crowded_history` separately does
~158 × 12 crowding windows for the grader. Both are full rebuilds on a
168-hour timer. Nothing is incremental, and nothing reuses the previous
card. Acceptable now; it grows linearly with history forever.

### D4 · Alert dedupe can be defeated by the retention cap (P2)

`alerts.keep_sent` is 200 (`thresholds.json`). `check_alerts` trims
`store["sent"]` to the last 200 keys (`hf_scan.py:1730`). If an event's key
is evicted while the event is still present in the 7-day `new_filings`
window, it would re-send. The 7-day window makes this unlikely today, but
the coupling is unstated and untested.

### D5 · The short-volume backfill needs ~20 weeks to reach useful depth (P2)

Self-inflicted in Phase 5A: 40 sessions per weekly build means the `shorts`
question reaches two years around February 2027. Live coverage today is
**5 weeks** against 158 for leverage and longs. It is honest — the panel
says so — but it is a slow path to a stated goal.

### D6 · No test asserts intra-week snapshot behaviour (P2 testing gap)

There is no test covering what happens when the board is rebuilt twice in
one week, because the answer is "the first reading is destroyed". A test
asserting the *intended* behaviour would have made B2 visible earlier.

### D7 · `hf_watch` position rows are not persisted independently (P2)

`hf_watch.positions()` reads `_STATE["records"]`, which is loaded from
`hf/funds/{key}.json` — and those files are **overwritten** on each refresh
(`hf_watch.py:218`). Quarter-over-quarter history exists only as the single
`change` block against the immediately prior period. There is no way to ask
"what did Pershing hold in Q2 2024" without re-fetching EDGAR.

---

## E. Data-correctness risks

| Risk | Evidence | Priority |
|---|---|---|
| Intra-week path destroyed by overwrite | `hf_scan.py:207-223` | P1 |
| Derived-only history: a maths fix cannot be applied backwards | §B1 | P1 |
| "Independent" overstates what the confidence counter establishes | `hf_pulse.py:198` | P1 |
| Silent provider degradation indistinguishable from a quiet market | §B6 | P1 |
| Stored-shape drift detected by field presence, not by a stamp | `hf_report.normalize` | P1 |
| Report revisions beyond 12 are gone | `hf_scan.py:714` | P2 |
| Fund position history not retained | §D7 | P2 |

**Not a risk, verified:** missing data is never coerced to zero. `gather_*`
return `None` on failure, `make_input` accepts `None`, verdicts degrade to
`NO DATA`, and `hf_replay` refuses a week rather than substituting.
`hf_names._unmapped` reports unmapped positions rather than dropping them.

---

## F. Reliability risks

| Risk | Evidence | Priority |
|---|---|---|
| ~~Unbounded `_MEM`~~ **FIXED**, and the disk cache with it | `hf_sources.py` LRU + body ceiling | ~~P0~~ |
| ~~Persistence volume unconfirmed~~ **CONFIRMED attached** | `/api/watchlist/diag` → `/data` | ~~P0~~ |
| Alerts bound to a 12-hour clock they should not be on | §B3 | P1 |
| Five daemon threads with no supervision or backpressure | `hf_scan.py:577,854,1212,1576`; `hf_watch.py:667` | P2 |
| An import-time error takes all 20+ hedge routes to 503 | Observed: `[hf_watch] wiring failed: name '_push_configured' is not defined` | P2 |

**Already handled well:** retry cooldowns after a failed grade or replay
build (`grades_retry_at`, `replay_retry_at`), the `refreshing` flag
preventing concurrent rebuilds, per-kind provider TTLs, and EDGAR documents
cached FOREVER.

---

## G. Performance risks

- `history(400)` parses up to ~25 MB of JSON per grade build to extract five
  fields per week (`hf_scan.py:235,1094`). **P2**
- Full replay and full crowding backfill on every rebuild; both O(weeks ×
  markets) and never incremental. **P2**
- 62.8 KB per weekly board is mostly repeated input rows; the store is
  write-cheap and read-expensive. **P3**

Not a problem: the stdlib HTTP server. Nothing in the smoke suite or live
timings indicates request-handling pressure.

---

## H. Storage risks

- ~~Volume attachment unconfirmed~~ — confirmed attached at `/data` (§D2).
- No raw layer, so the archive cannot answer a question it was not designed
  for (**P1**).
- Growth is trivial in bytes; the risk is *shape*, not size (§C2).
- `hf/pulse/*.json` has no index — every history read is a directory glob
  plus a full parse (**P2**).

---

## I. UI architecture risks

- `tab-hedge.jsx` at 2,025 lines is approaching, but has not reached, the
  point where splitting pays (**P3**, §C3).
- The Pulse panel now mounts five independent fetchers (`PulsePanel`,
  `HfNames`, `HfAlerts`, plus crowding and sector strip), each with its own
  `useEffect` load. No shared cache; switching panels refetches. **P3**
- `app-cards.jsx` (15,236) and `styles.css` (13,447) are the real frontend
  debt and are outside hedge scope. **P2**

Preserved and verified: lazy chunk loading, mobile `data-label` tables, a
tooltip on every header (render check asserts "headers without a tooltip:
none"), and no ISO date on screen.

---

## J. Testing gaps

| Gap | Priority |
|---|---|
| No test for rebuilding the board twice in one week (§D6) | P2 |
| No test that `_MEM` stays bounded | P1 |
| No test asserting a persisted document carries a schema version | P1 |
| No source-health tests (nothing to test yet) | P1 |
| Alert dedupe vs `keep_sent` eviction untested (§D4) | P2 |
| Render checks live in the scratchpad, not the repo — they have caught six real defects and are not in CI | **P1** |

That last one is the most valuable gap: `render_report.py` and
`render_names.py` found the apiFetch/Response bug, the sector-table dashes,
the legacy-caveat ordering, the "push is set up" lie, and the 503 import
failure. None of it runs in CI.

---

## K. Recommended target architecture

Keep every engine. Change what is underneath them.

```
   PROVIDERS (hf_sources, UW client)         unchanged
              │
              ▼
   ┌──────────────────────────────────┐
   │  RAW OBSERVATION LOG  (new)      │   append-only, never rewritten
   │  hf/obs/YYYY-MM.jsonl            │   one line per observation
   └──────────────────────────────────┘
              │
     ┌────────┴─────────┐
     ▼                  ▼
  DAILY PULSE      WEEKLY BOARD          derived, recomputable, disposable
     │                  │
     └────────┬─────────┘
              ▼
        WEEKLY REPORT                    an artefact of what was known then
              │
              ▼
     GRADER · REPLAY · ALERTS            read the log, not the derived state
```

**The observation record.** One JSON object per line, appended, never
edited:

```json
{"schema": 1, "observed_at": "...", "as_of": "...", "public_on": null,
 "source": "cftc.tff", "evidence_class": "REGULATORY POSITIONING DATA",
 "market": "sp500", "sector": null, "symbol": null,
 "metric": "lev_net", "value": -330000, "prev": -318000, "change": -12000,
 "units": "contracts", "quality": "OK", "engine": "hf_sources 1.1.0"}
```

Monthly files keep any single file small; JSONL appends are atomic for a
single writer at typical line sizes and need no rewrite. Estimated volume
with market-wide and sector granularity only: **~15,000 lines/year, ~4 MB**.

**Three explicit tiers** (Finding 12): RAW never changes; DERIVED may be
recomputed from RAW at any time; REPORT is immutable and stamped.

**Schema contracts** (Finding 9): introduce dataclasses only at the four
boundaries where the seven historical bugs actually occurred — `Observation`,
`WeekKey`, `StoredDocument` (schema + engine + created + as_of), and
`AlertEvent`. Not eleven types. The bugs were about **identity and version**,
not about field access, so type the identity.

**Source health** (Finding 11): one record per provider per attempt, written
to the same log with `quality` ∈ OK / STALE / PARTIAL / RATE_LIMITED /
AUTH_MISSING / SHAPE_CHANGED / UNAVAILABLE. Health then becomes a query over
the log rather than a parallel structure to keep in sync.

**No database.** Revisit only if per-symbol history becomes a requirement.

---

## L. Prioritised migration plan

Each step is independently shippable, behind the existing delivery pattern,
and none breaks a live feature.

| # | Step | Priority | Why first |
|---|---|---|---|
| 1 | ~~Bound `_MEM`~~ **DONE** — plus the disk half, which this audit missed | ~~P0~~ | Shipped September 7, 2026 |
| 2 | ~~Confirm the Railway volume~~ **DONE — attached at `/data`** | ~~P0~~ | Confirmed September 7, 2026; making the fallback loud drops to P3 |
| 3 | ~~Move the two render checks into the repo and CI~~ **DONE** | ~~P1~~ | Shipped September 8, 2026 (v4.91). Failed on its first run and found two standing rules broken |
| 4 | ~~Add the raw observation log~~ **DONE** | ~~P1~~ | Shipped v4.91 — `hf_obs.py`, written from `gather()`, read by nothing yet |
| 5 | ~~Stamp every persisted document~~ **DONE** | ~~P1~~ | Shipped v4.91 — all seven kinds; an unknown schema is refused, an unstamped document is still read |
| 6 | ~~Source health derived from the log~~ **DONE** | ~~P1~~ | Shipped v4.92 — `hf_health.py`, five states, panel on the Pulse |
| 7 | ~~Decouple alerts from the pulse clock~~ **DONE** | ~~P1~~ | Shipped v4.92 — `alerts_check()`, `after_sweep_fn`, `/api/hf/alerts/check` |
| 8 | ~~"independent" → "corroborating"~~ **DONE** | ~~P1~~ | Shipped v4.92 — the maths is unchanged and a guard proves it |
| 9 | ~~Daily view derived from the log~~ **DONE** | ~~P2~~ | Shipped v4.92 — states its own depth rather than implying one |
| 10 | ~~Stop overwriting the weekly board~~ **DONE** | ~~P2~~ | Shipped v4.92 — every build appends to `hf/pulse/index.jsonl` |
| 11 | ~~Index `hf/pulse`~~ **DONE** | ~~P2~~ | Shipped v4.92 — the same file; `history()` no longer opens the boards |
| 12 | ~~Extract the hedge routes~~ **DONE** | ~~P2~~ | Shipped v4.92 — `hf_routes.py`; the handler is 9 lines where it was 143 |
| 13 | Calibrated confidence from `hf_grade`/`hf_replay` — persistence, not returns | P2 | **NOT DONE.** Needs steps 4 and 9 to have run for a while. The log is days old; calibrating on it now would be a confident number computed from nothing |
| 14 | Split `tab-hedge.jsx` if it passes ~3,000 lines | P3 | **NOT DONE, deliberately.** 2,163 lines |

**Steps 1–12 are shipped.** 13 and 14 are the two this audit itself says to
wait on, and the reasons have not changed.


---

## AUDIT VERDICT

**Current architecture:** 6 / 10 — sound engines on a persistence layer that
has been outgrown; no raw tier; one clock driving everything.

**Hedge Fund Intelligence logic:** 9 / 10 — the best part of the system. The
refusals are enforced, not documented. One point off for "independent".

**Data integrity:** 7 / 10 — attribution, two dates, missing-stays-missing
and atomic writes are all genuinely enforced; lost to overwriting, retention
trims and inconsistent version stamps.

**Historical architecture:** 5 / 10 — `hf_replay` and `hf_grade` are
excellent and prove the *appetite* for history, but they exist to work
around the absence of a raw record and only succeed where a source keeps its
own archive.

**Frontend architecture:** 6 / 10 — hedge frontend is healthy, lazy and
fully tooltipped; the repo's real debt is `app-cards.jsx` and `styles.css`.

**Backend architecture:** 5 / 10 — a 13,907-line monolith where an
import-time typo took every hedge route to 503; correct concurrency
primitives, unsupervised threads.

**Testing:** 8 / 10 — 517 Python + 182 JS guards that assert refusals and
behaviour; two points off because the render checks that caught the most
real defects are not in the repo.

### Top 5 changes I would make

1. ~~**Bound `_MEM`**~~ — **done**, along with the disk cache this audit
   understated.
2. ~~**Confirm the persistence volume**~~ — **done**, attached at `/data`.
3. **Add the append-only observation log**, written to but not yet read
   from, so history starts accumulating today.
4. **Move the render checks into CI** — they have the best defect-per-line
   record of anything in the repo.
5. **Decouple alerts from the pulse clock.**

### Top 5 things I would NOT change

1. The evidence classes and the attribution rule.
2. `hf_pulse`'s verdict maths, the crowding rule, and the two-measure test.
3. `hf_grade`'s refusal to score verdicts, its base rates and its episodes.
4. `hf_replay`'s input-completeness rule.
5. `tab-hedge.jsx` — do not split it yet, and do not adopt PostgreSQL.

### The single most important architectural improvement

**Introduce a raw, append-only observation log and make every derived
artefact recomputable from it.** It is the one change that unlocks Findings
1, 2, 3, 12 and 13 at once, it removes the reason `hf_replay` had to be
written, it makes source health a query rather than a structure, and it can
be added *without changing a single engine or a single number on screen* —
which is why it should be step 4 and not step 14.
