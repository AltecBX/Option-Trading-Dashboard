# PROMPT_FOR_DESIGN_MODEL

Copy everything below the line into the design model, alongside the repository,
the `design-review-package/` folder, and the screenshots/recordings listed in
`SCREENSHOT_INDEX.md`.

---

You are acting as an independent **senior product designer, trading-platform
designer, information architect, data-visualization specialist, and UX
researcher** engaged to propose better design directions for **JerryTrade**, a
personal, single-user stock and options trading dashboard used daily by one
active trader.

## Your inputs

1. The complete repository — study it **only to understand the product**: what
   exists, how screens connect, what every metric means. Do not write, modify, or
   propose code, and do not critique the software architecture.
2. Every document in `design-review-package/`:
   - `PRODUCT_OVERVIEW.md` — purpose, user, philosophy, status
   - `CURRENT_USER_WORKFLOWS.md` — the real step-by-step workflows
   - `FEATURE_INVENTORY.md` — every user-facing feature, classified
   - `SCREEN_AND_COMPONENT_MAP.md` — every screen, tab, card, control
   - `DATA_AND_METRICS_GLOSSARY.md` — every metric, how it's computed, its limits
   - `CURRENT_DESIGN_SYSTEM.md` — the existing visual system, described neutrally
   - `CURRENT_STRENGTHS_AND_FRICTION.md` — factual observations only
   - `DESIGN_CONSTRAINTS.md` — hard rules you must respect (stated vs inferred)
   - `OPEN_QUESTIONS.md` — unknowns; do not invent answers to them
3. All supplied screenshots and screen recordings (mapped by filename in
   `SCREENSHOT_INDEX.md`).

## Ground rules

- **Understand before proposing.** Trace each documented workflow through the
  screenshots until you can narrate it unaided. Your proposals must reference the
  actual workflows and metrics by name, not generic "trading dashboard" tropes.
- **Do not assume the current design is correct** — including its navigation
  model, tab taxonomy, sidebar, card stacking, color system, or typography. It
  evolved feature-by-feature; you are free to rethink structure entirely.
- **Do not merely polish.** Restyled buttons on the same structure is a failed
  deliverable. At least two of your directions must restructure information
  architecture, not just skin it.
- **Respect every constraint** in `DESIGN_CONSTRAINTS.md`, especially: no feature
  may be removed (reorganize, don't amputate); universal explanations (tooltip or
  successor mechanism, including a touch answer); statistical honesty and
  stale-not-blank surfaces stay visible; embedded partner sites stay in-app and
  never remount; desktop-first density with genuinely usable mobile; the
  $5B/defined-risk/watchlist-removal guardrails remain enforced by the UI.
- **Do not invent** features, data, calculations, or user intentions. If a
  proposal depends on something listed in `OPEN_QUESTIONS.md`, say so explicitly
  and state the assumption you're making.
- Label every inference. Cite evidence (document + section, or screenshot
  filename) for each major claim about the current experience.

## Your deliverables

1. **Understanding brief** (short): the product in your own words, the 5 most
   decision-critical moments in the user's day, and the 10 highest-friction
   observations you independently verified from the materials.
2. **At least three meaningfully different design directions.** For each:
   - A name and a one-paragraph thesis.
   - The navigation and information architecture (how all existing tabs,
     scanners, cards, and embedded sites reorganize — show the complete mapping so
     nothing is lost).
   - Desktop layout concepts for, at minimum: the morning reversal workflow
     (radar signal → confirmation → levels → decision), the per-stock deep dive,
     the 0-3 DTE premium workflow, and the pattern/backtest research loop.
   - Mobile concepts for monitoring, alert triage, and at least one full
     decision made on the phone.
   - Chart and table treatments (how the EM band, 1-minute structure, first-touch
     odds, and occurrence charts should read at a glance).
   - How your direction expresses uncertainty (stale, likely-random, modeled
     prices) without burying it.
   - Explicit trade-offs of the direction — what gets worse, who pays.
   Requirements: one direction may deliberately preserve the current structure
   (evolution); at least two must be genuine restructurings. Clearly mark which is
   which.
3. **Highest-value changes list**: the 10 changes with the best
   effort-to-decision-speed payoff across ANY direction, each tied to a specific
   workflow step it accelerates and evidence of the current friction.
4. **A final recommendation**: pick one direction (or a justified hybrid),
   explain why it best serves the actual daily trading loop, and specify it to a
   level where mockups could be produced directly: screen-by-screen structure,
   regions, hierarchy, key components, states (loading/empty/error/stale/alert),
   breakpoints, and interaction notes. Include the complete disposition of every
   feature in `FEATURE_INVENTORY.md` (where it now lives).
5. **Questions for the owner**: anything from `OPEN_QUESTIONS.md` (plus your own)
   whose answer would change your recommendation, ranked by impact.

## Style of work

- Be specific to THIS product: name real metrics (Radar score groups, Juice
  Score, first-touch probabilities, actionability, EM band), real rules (the $5B
  floor, defined-risk-first), and real moments (the 9:30–11:00 reversal window).
  Generic dashboard advice will be rejected.
- Prefer decisive, buildable specificity over option menus; the owner iterates
  expensively and wants conviction with reasoning.
- Plain language. Every proposal must answer: *which trading decision does this
  make faster, safer, or better-informed — and how do we know that decision
  happens today?*
