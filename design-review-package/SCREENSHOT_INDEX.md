# SCREENSHOT_INDEX

Checklist of captures needed for a design model to *see* the product. Naming
convention: `NN_area_state_viewport.png` (`d`=desktop ≥1440w, `t`=tablet ~834w,
`m`=mobile ~390w). Record during market hours where noted (**MH**) so boards are
populated; some states only exist at specific times.

## Global shell

- [ ] `01_shell_overview_d.png` — full app, Trade tab active, dark mode
- [ ] `02_shell_overview_light_d.png` — same in light mode
- [ ] `03_sidebar_full_d.png` — sidebar top-to-bottom (scroll capture)
- [ ] `04_tabbar_two_rows_d.png` — both tab rows incl. earnings chip
- [ ] `05_tabbar_drag_reorder_d.png` — mid-drag tab state
- [ ] `06_command_palette_open_d.png`
- [ ] `07_tweaks_panel_open_d.png` — theme/accent/density controls visible
- [ ] `08_accent_presets_variants_d.png` — one shot per accent (5)
- [ ] `09_radar_toast_d.png` — toast visible (**MH**, score ≥80 day)
- [ ] `10_error_card_boundary_d.png` — any card error + Retry (may need forcing)
- [ ] `11_app_crash_screen_d.png` — only if reproducible; else skip
- [ ] `12_shell_overview_m.png` + `13_sidebar_overlay_m.png` + `14_tabbar_scroll_m.png`

## Trade tab

- [ ] `20_trade_top_d.png` — chart (Daily) + EM band visible
- [ ] `21_trade_chart_1min_d.png` — 1-Min toggle active, VWAP/levels (**MH**)
- [ ] `22_trade_chart_tooltip_d.png` — hover on a candle/point
- [ ] `23_trade_mid_d.png` — EM card + CSP/CC recommendations
- [ ] `24_trade_em_card_expiry_dropdown_d.png` — expiry switcher open
- [ ] `25_trade_bottom_d.png` — trade builder, theta, skew, roll manager
- [ ] `26_trade_level_reprice_d.png` — with a level entered
- [ ] `27_trade_full_m.png` (top/mid/bottom: 3 captures)

## Discover / Analyze / News

- [ ] `30_discover_analyst_d.png` · `31_discover_movers_d.png` ·
      `32_discover_trend_d.png` · `33_discover_ivrank_d.png` (sub-tabs)
- [ ] `34_analyze_top_d.png` (analyst card) · `35_analyze_bottom_d.png`
      (valuation/basing/pullback)
- [ ] `36_news_hub_d.png` — ticker + market lists
- [ ] `37_discover_m.png` · `38_analyze_m.png`

## Patterns tab

- [ ] `40_patterns_top_current_setup_d.png` — Current Setup with ≥1 active (**MH** or a triggered day)
- [ ] `41_patterns_current_setup_empty_d.png` — "no pattern active" state
- [ ] `42_patterns_ask_answer_d.png` — Ask box with a returned answer + rules line
- [ ] `43_patterns_ask_refusal_d.png` — earnings/news question honest refusal
- [ ] `44_patterns_row_collapsed_list_d.png` — ranked list with labels/badges
- [ ] `45_patterns_row_expanded_d.png` — stats grid + first-touch + context
- [ ] `46_patterns_occurrence_chart_d.png` — spaghetti + bands (hover a line)
- [ ] `47_patterns_scan_results_d.png` — ⌕ Scan watchlist inline results
- [ ] `48_patterns_intraday_progress_d.png` — mining job progress bar
- [ ] `49_patterns_intraday_results_d.png` — sequence sentences with labels
- [ ] `50_patterns_watches_triggered_d.png` — watch list incl. a TRIGGERED row
- [ ] `51_patterns_filters_d.png` — bullish/bearish/mean-reverting filter active
- [ ] `52_patterns_m.png` (top + expanded row: 2 captures)

## Flow / Scanners / Juice

- [ ] `60_flow_top_d.png` — flow score card (**MH**)
- [ ] `61_flow_market_context_d.png` — tide/sector/net premium areas
- [ ] `62_scanners_radar_lists_d.png` — Long + Short lists populated (**MH**)
- [ ] `63_scanners_radar_row_expanded_d.png` — ticket + group chips
- [ ] `64_scanners_radar_report_d.png` — hit-rate report
- [ ] `65_scanners_open_reclaim_d.png`
- [ ] `66_scanners_empty_offhours_d.png` — off-hours/quiet state
- [ ] `67_juice_board_d.png` — populated (**MH**) · `68_juice_row_strategies_d.png`
      — expanded suggestions with DEFINED/UNDEFINED labels
- [ ] `69_juice_stale_note_d.png` — amber rate-limited note (if it occurs)
- [ ] `70_scanners_m.png` · `71_juice_m.png`

## Backtest tab

- [ ] `80_backtest_blank_d.png` — textarea + example chips
- [ ] `81_backtest_rules_editor_d.png` — after Interpret, warnings + editors
- [ ] `82_backtest_rules_json_d.png` — JSON power view open
- [ ] `83_backtest_add_condition_dropdown_d.png` — dropdown open
- [ ] `84_backtest_running_progress_d.png` — intraday run mid-progress
- [ ] `85_backtest_results_d.png` — tiles + equity curve + regime table
- [ ] `86_backtest_trades_table_d.png` — trade log open
- [ ] `87_backtest_option_warning_d.png` — modeled-premium warning visible
- [ ] `88_backtest_m.png`

## Breadth / Journal / Watchlist / Streaks / Calendar / Manage

- [ ] `90_breadth_d.png` · `91_journal_d.png` · `92_streaks_d.png` · `93_calendar_d.png`
- [ ] `94_watchlist_board_top_d.png` — filters visible ·
      `95_watchlist_board_scrolled_d.png` · `96_watchlist_filters_open_d.png`
- [ ] `97_manage_top_d.png` — watchlist manager area ·
      `98_manage_remove_warning_d.png` — removal flow/warning ·
      `99_manage_bottom_d.png` — broker import, positions, push, Schwab reconnect
- [ ] `100_manage_push_test_d.png` — push settings incl. a test send
- [ ] `101_watchlist_m.png` · `102_manage_m.png`

## Embedded sites (row 2)

- [ ] `110_finviz_loaded_d.png` — embedded Elite with both toolbar rows
- [ ] `111_finviz_setup_no_helper_d.png` — helper-absent install panel
- [ ] `112_finviz_star_badge_d.png` — "★ on watchlist" static badge state
- [ ] `113_tview_loaded_d.png` — with nav chips · `114_tview_signin_popup_d.png`
- [ ] `115_uw_loaded_d.png` — with nav chips
- [ ] `116_embed_update_chip_d.png` — "update helper" ⚠ chip (any panel)
- [ ] `117_embed_compat_chip_d.png` — "cookies: compat mode" chip (Comet)
- [ ] `118_embed_mobile_notice_m.png` — mobile explanation state

## States sweep (any tab where visible)

- [ ] `120_hover_tooltip_stat_d.png` — long-form tooltip open on a pattern stat
- [ ] `121_stale_tags_d.png` — stale price marker
- [ ] `122_loading_board_d.png` — a board mid-scan ("scanning n/total")
- [ ] `123_empty_states_d.png` — one composite of 2–3 empty-state sentences
- [ ] `124_warn_banner_d.png` — amber warn box (backtest/patterns)
- [ ] `125_disabled_buttons_d.png` — Run/Interpret disabled state
- [ ] `126_earnings_chip_soon_d.png` — amber "soon" earnings chip

## Tablet (only where layout differs)

- [ ] `130_shell_t.png` · `131_trade_t.png` · `132_watchlist_t.png` · `133_patterns_t.png`

## Screen recordings (short, 30–90s each)

- [ ] `R1_ticker_sync.mp4` — click a stock inside embedded Finviz → whole app follows; then change ticker in sidebar → frames follow
- [ ] `R2_radar_to_trade.mp4` — radar row → Chart → (1-Min) → EM check (**MH**)
- [ ] `R3_pattern_to_backtest.mp4` — pattern row → → Backtest → Run → results
- [ ] `R4_ask_flow.mp4` — Ask box question → answer → expand pattern
- [ ] `R5_juice_flow.mp4` — juice row → strategy suggestions → Trade tab (**MH**)
- [ ] `R6_mobile_walkthrough.mp4` — phone: tabs, watchlist, alerts, sidebar overlay
- [ ] `R7_tab_reorder_palette.mp4` — drag a tab; open command palette; switch theme
