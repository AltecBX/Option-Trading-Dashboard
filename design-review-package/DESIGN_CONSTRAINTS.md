# DESIGN_CONSTRAINTS

Constraints any proposed design must respect. **[Stated]** = explicitly required by
the owner in past discussions. **[Inferred]** = evident from the product and its
history; labeled as inference.

## Product identity

1. **[Stated]** This is a serious stock & options trading application for real
   daily use — not a demo, not a toy, not a general-audience product.
2. **[Stated]** The interface must support **fast decision-making during market
   hours**; the morning reversal window is the highest-stakes period.
3. **[Stated]** It must prioritize decision-useful information and avoid
   distractions and feature bloat ("Do not add random features… filter them hard").
4. **[Stated]** It should feel **modern, premium, fast, highly polished** — but
   never sacrifice usability to appear minimal.
5. **[Stated]** The design should make relationships between signals, patterns,
   prices, options data, and trade decisions easier to understand, and help the
   user recognize opportunities and risks quickly.

## Hard functional constraints

6. **[Stated]** **Never delete features.** Every existing capability must survive a
   redesign (it may be reorganized, but not removed).
7. **[Stated]** **Tooltips are mandatory everywhere** — every metric, control, and
   badge must self-explain on hover (and a redesign should consider a touch
   equivalent, since this rule currently has no mobile expression).
8. **[Stated]** Watchlist **removal must only exist in the Manage workflow**;
   all other star/add controls are add-only (removal destroys imported metadata).
9. **[Stated]** The imported CSV (tags, sector, industry, weekly flag) is the
   **source of truth** and must not be overwritten by live data.
10. **[Stated]** The user **never trades sub-$5B market-cap stocks**; scanners
    enforce this and must continue to.
11. **[Stated]** Options suggestions must **clearly separate defined from
    undefined risk** and never lead with undefined risk merely because premium is
    high.
12. **[Stated]** Partner sites must appear **inside the app** (no popups, external
    tabs, or companion windows), using the user's real accounts, with two-way
    ticker sync — and without proxying, scraping, recreating, or rehosting their
    content, respecting each site's ToS and browser security. Credentials must
    never be stored, intercepted, transmitted, or logged by the app.
13. **[Inferred]** Statistical honesty is a product value: baselines, out-of-sample
    checks, multiple-testing correction, "likely random" labels, modeled-price
    warnings, and can't-test refusals must remain visible in any redesign — hiding
    uncertainty to look cleaner is a regression.
14. **[Inferred]** "Stale, never blank": data surfaces keep last-good values with
    stale markers under rate limits or failures. A redesign must not introduce
    blank/spinner-only states for live boards.

## Technical & data constraints

15. **[Inferred]** Data budget: Schwab API self-capped at ~110 requests/min; heavy
    jobs (intraday backtests, sequence mining) run as background jobs with
    progress. Designs assuming instant arbitrary data are invalid.
16. **[Inferred]** Data horizons: ~10y daily bars; ~6mo minute bars (archive grows
    forward); **no** historical option quotes, IV history beyond local
    accumulation, news-day history, or earnings-date history. Features must not be
    designed around unavailable data.
17. **[Inferred]** Single-page React app, precompiled (no build-time framework
    swap implied); one persistent global ticker as the app's spine; tabs stay
    mounted (embedded iframes must never remount on navigation).
18. **[Inferred]** The embedded sites require a user-installed browser extension;
    setup/repair/status affordances (install panel, update chips, compat chips,
    sign-in popup, repair button) must remain reachable.
19. **[Inferred]** Push notifications and toasts are load-bearing alerting
    channels; any notification redesign must keep score thresholds and per-day
    dedupe behavior.
20. **[Inferred]** Persistence: user prefs (tab order, theme, accent, follows)
    sync/persist; server state lives on a Railway volume. Offline is not a target.

## Platform priorities

21. **[Stated]** **Desktop first** — the app is data-dense and used on a large
    screen all session. **[Stated]** Mobile must still be highly usable (the owner
    reads boards and receives alerts on phone); embedded sites are desktop-only by
    platform limitation and mobile must explain that gracefully.
22. **[Inferred]** Charts and tables must remain readable at density — tabular
    numerals, semantic green/red, and per-row scan-ability are entrenched
    expectations.
23. **[Inferred]** Dark mode is the primary environment (light exists and must
    keep parity); a user-selectable accent color system already exists and the
    owner uses it.

## Working-relationship constraints

24. **[Stated]** The owner is cost-sensitive about iteration ("burning tokens"):
    proposals should be decisive and buildable, not open-ended exploration that
    requires many rounds.
25. **[Inferred]** The owner gives feedback in concrete visual terms (exact hex
    colors, "2pts bigger", "second line for sites") and expects proposals at that
    level of specificity.
26. **[Inferred]** Version visibility (the v3.xx pill) and status badges (SCHWAB
    LIVE, UW rate) are part of the owner's operational trust; keep equivalent
    affordances.
