# OPEN_QUESTIONS

Things that cannot be confirmed from the current application or prior discussions.
No invented answers — these should be asked of the owner before or during design.

## Usage facts

1. **Actual tab usage frequency** — which tabs are opened daily vs rarely? (The
   inventory classifies by design intent, not measured use; there is no analytics.)
2. **Primary screen setup** — one monitor or several? Is the app full-screen, or
   side-by-side with a broker window? (Affects density and layout assumptions.)
3. **Mobile share of use** — how much of the day is phone-only, and which
   workflows actually happen there beyond reading alerts?
4. **Execution flow** — orders are placed at the broker (outside the app). Where
   exactly: Schwab web, thinkorswim, phone? How many seconds does the current
   research→order handoff take, and what gets retyped?
5. **The journal habit** — are picks/trades journaled consistently, or is the
   journal aspirational? (Determines how central it should be.)

## Feature intent

6. **Discover vs Scanners vs Watchlist-alerts overlap** — does the owner see these
   as distinct engines on purpose, or would consolidation be welcome?
7. **Streaks / Breadth / Market Calendar** — decision inputs or ambient interest?
8. **Swing patterns card (legacy)** — still used since Pattern Discovery v2
   shipped, or kept only under the never-delete rule?
9. **Left/right mini-rails on very wide screens** (52-week / daily high-low
   rails) — actively read, or peripheral?
10. **Weather pill** — purely personal flourish, or does it signal something in
    the daily routine worth preserving prominently?
11. **Presets vs starred watchlist** — what distinguishes a preset symbol from a
    starred symbol in the owner's mental model? Both are quick-switch lists.

## Metrics & thresholds

12. **Radar thresholds (70/80/85) and weights** — hand-tuned by feel or validated?
    Is the user open to the UI exposing/adjusting them?
13. **Juice Score composition** — is the current weighting trusted, or treated as
    a rough rank? (Affects how prominently a design should present the number.)
14. **First-touch stop at ~⅔ of target** — is that ratio how the owner actually
    sets stops, or just the engine's reporting convention?
15. **Position sizing** — is $10k/trade (the backtest default) the real trading
    size? Is per-trade dollar risk a number the owner wants surfaced everywhere?

## Data & accounts

16. **Number of concurrent Schwab accounts** and whether multi-account views
    matter (positions/imports suggest one).
17. **Unusual Whales plan limits** — the "1000000/MIN" badge suggests generous
    limits; are there endpoints the owner avoids for cost reasons?
18. **CSV import cadence** — how often is the watchlist CSV re-imported, and from
    where does it originate?
19. **Comet browser** — is Comet a daily driver alongside Chrome (two-browser
    workflow), or an experiment? (Affects how much design weight the
    extension-status affordances deserve.)

## Design preferences (never explicitly stated)

20. **Information the owner wants at literal top-left** (first glance each
    morning) — never specified.
21. **Sound** — no audio alerts exist; unknown whether they're wanted or disliked.
22. **Keyboard-first trading** — the palette exists; unknown whether deeper
    keyboard workflows (j/k row navigation, hotkey ticker entry) are desired.
23. **Multi-ticker comparison** — the app is single-ticker-centric by design;
    unknown whether side-by-side comparison was ever wanted.
24. **Tolerance for onboarding/education surfaces** — tooltips are mandatory, but
    unknown whether a first-run tour or a glossary page would be welcome or
    considered clutter.
25. **Print/export needs** — no export of boards/results exists (beyond the
    helper zip); unknown whether CSV/report export matters.
