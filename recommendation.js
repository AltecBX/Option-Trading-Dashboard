// recommendation.js — pure rec-engine helpers shared between app.jsx
// and the standalone test_recommendation.js node test.
//
// Phase B (v1.11): mirrors the recommendation engine signal direction
// for short cash-secured puts. The CC path preserves v109/v110 behavior
// exactly. The CSP path inverts directional bias so price weakness is
// favorable (rich premium plus bounce bias) and price strength is
// "wait" (premium thin). Analyst overlay rules also flip per Jerry's
// v106 spec: fresh upgrade reduces CSP danger, fresh downgrade
// escalates it, far above highest target stays bad for both (mean
// reversion drops price toward the put strike).
//
// All four exports are pure: no DOM, no React, no globals.

(function () {
  // ── Base case from price vs weekly medians ──────────────────────
  // direction: "cc" or "csp"
  // currReturn: current price as % change from baseline
  // medianClose, medianHigh, medianLow: weekly medians from history
  function buildRecBase(direction, currReturn, medianClose, medianHigh, medianLow) {
    if (direction === "cc") {
      // Existing CC logic, untouched from v109.
      if (currReturn < medianClose) {
        return { kind: "info", title: "Wait on calls",
          body: `Price is ${(medianClose - currReturn).toFixed(2)}% below the typical Friday close. Holding off may give a better strike later in the week.` };
      }
      if (currReturn < medianHigh) {
        return { kind: "warn", title: "Approaching the zone",
          body: "Price is inside the normal range. You can sell now for steady premium, or wait for a push higher." };
      }
      return { kind: "success", title: "Favorable timing",
        body: `Price has exceeded the typical weekly high by ${(currReturn - medianHigh).toFixed(2)}%. Selling a call near the suggested strike now yields better premium with lower assignment risk.` };
    }

    // CSP base case — inverse directional bias.
    //   currReturn > medianHigh  → price overextended, premium thin, wait for pullback
    //   between medianClose and medianHigh → neutral, watch
    //   currReturn < medianClose → price weak, premium rich, bounce bias favorable for puts
    if (currReturn > medianHigh) {
      return { kind: "info", title: "Wait on puts",
        body: `Price has exceeded the typical weekly high by ${(currReturn - medianHigh).toFixed(2)}%. Premium on short puts is thin here, and a pullback would give you a better strike.` };
    }
    if (currReturn > medianClose) {
      return { kind: "warn", title: "Approaching the zone",
        body: "Price is inside the normal range. You can sell puts now for steady premium, or wait for a dip for a richer strike." };
    }
    // currReturn <= medianClose: weakness favors short puts
    if (currReturn < medianLow) {
      return { kind: "success", title: "Favorable timing",
        body: `Price is ${(medianLow - currReturn).toFixed(2)}% below the typical weekly low. Premium is rich and a mean reversion bounce above your put strike is the base case.` };
    }
    return { kind: "success", title: "Favorable timing",
      body: `Price is ${(medianClose - currReturn).toFixed(2)}% below the typical Friday close. Premium is richer here, and the bias is for a bounce that keeps the stock above your put strike.` };
  }

  // ── Analyst overlay ─────────────────────────────────────────────
  // direction: "cc" or "csp"
  // rec: { kind, title, body } from buildRecBase
  // analystVerdict: { fresh_upgrade, fresh_downgrade, ... } or null
  // analystTargets: { upside_pct, upside_to_high_pct, ... } or null
  // consensusTrend: "more_bearish" | "more_bullish" | "stable" | null
  function applyAnalystOverlay(direction, rec, analystVerdict, analystTargets, consensusTrend) {
    if (!analystVerdict) return rec;

    if (direction === "cc") {
      // ── CC overlay (v109 logic preserved) ──
      if (analystVerdict.fresh_upgrade) {
        const note = "Fresh upgrade today, covered calls may cap upside if a re-rating is in progress.";
        if (rec.kind === "success" || rec.kind === "warn") {
          rec = { kind: "danger", title: "Caution: fresh analyst upgrade",
            body: rec.body + " " + note };
        }
      }
      // Above average target — bumps success/warn one step toward danger.
      if (analystTargets && analystTargets.upside_pct != null
          && analystTargets.upside_pct < 0 && rec.kind !== "danger") {
        const overBy = Math.abs(analystTargets.upside_pct).toFixed(1);
        const addendum = ` Stock is ${overBy}% above the average analyst target, upside may already be priced in.`;
        rec = { kind: rec.kind === "success" ? "warn" : rec.kind,
          title: rec.title, body: rec.body + addendum };
      }
      // Far above highest target — strongest overextension signal.
      if (analystTargets && analystTargets.upside_to_high_pct != null
          && analystTargets.upside_to_high_pct < -5 && rec.kind !== "danger") {
        const overBy = Math.abs(analystTargets.upside_to_high_pct).toFixed(1);
        rec = { kind: "danger",
          title: "Caution: above highest analyst target",
          body: `Stock is ${overBy}% above the HIGHEST analyst target. Possible mean reversion risk. ${rec.body}` };
      }
      // Trend deteriorating — informational addendum, no kind change.
      if (consensusTrend === "more_bearish" && !analystVerdict.fresh_downgrade) {
        rec = Object.assign({}, rec, { body: rec.body + " Analyst sentiment has turned more bearish over recent months." });
      }
      return rec;
    }

    // ── CSP overlay (Phase B, v1.11) ──
    // Fresh upgrade is BULLISH for short puts: catalyst supports the
    // stock staying above the put strike. We do not escalate, we
    // soften any pre-existing danger and add a positive note.
    if (analystVerdict.fresh_upgrade) {
      const note = "Fresh upgrade today, bullish catalyst supports the stock staying above your put strike.";
      if (rec.kind === "danger") {
        rec = { kind: "warn", title: rec.title, body: rec.body + " " + note };
      } else {
        rec = Object.assign({}, rec, { body: rec.body + " " + note });
      }
    }
    // Fresh downgrade is BEARISH for short puts: downgrade-driven dip
    // could push price into the strike. Escalate to danger.
    if (analystVerdict.fresh_downgrade && rec.kind !== "danger") {
      rec = { kind: "danger", title: "Caution: fresh analyst downgrade",
        body: rec.body + " Fresh downgrade today, downgrade-driven dip could push price into your put strike." };
    }
    // Above average target — for puts this is only an issue when
    // SUBSTANTIALLY above (10%+), since mild premium-to-target is fine
    // for the put seller (stock stays above strike). Threshold tighter
    // than the CC version (which fires at any amount above target).
    if (analystTargets && analystTargets.upside_pct != null
        && analystTargets.upside_pct < -10 && rec.kind !== "danger") {
      const overBy = Math.abs(analystTargets.upside_pct).toFixed(1);
      const addendum = ` Stock is ${overBy}% above the average analyst target, mean reversion could drag price toward your put strike.`;
      rec = { kind: rec.kind === "success" ? "warn" : rec.kind,
        title: rec.title, body: rec.body + addendum };
    }
    // Far above highest target — bad for puts too. Mean reversion from
    // overextension drops price toward the strike, raising assignment
    // risk on a passive put seller.
    if (analystTargets && analystTargets.upside_to_high_pct != null
        && analystTargets.upside_to_high_pct < -5 && rec.kind !== "danger") {
      const overBy = Math.abs(analystTargets.upside_to_high_pct).toFixed(1);
      rec = { kind: "danger",
        title: "Caution: above highest analyst target",
        body: `Stock is ${overBy}% above the HIGHEST analyst target. Mean reversion would drop price toward your put strike. ${rec.body}` };
    }
    // Trend deteriorating — addendum about assignment risk on the put.
    if (consensusTrend === "more_bearish" && !analystVerdict.fresh_downgrade) {
      rec = Object.assign({}, rec, { body: rec.body + " Analyst sentiment turning more bearish raises assignment risk on a short put." });
    }
    return rec;
  }

  // ── Convenience: build both at once ─────────────────────────────
  // Returns { cc, csp } where each is a fully-overlayed rec object.
  // Pass analystData = null when the analyst card has no data; the
  // overlay is skipped in that case.
  function buildBoth(inputs) {
    var currReturn = inputs.currReturn;
    var medianClose = inputs.medianClose;
    var medianHigh = inputs.medianHigh;
    var medianLow = inputs.medianLow;
    var analystData = inputs.analystData;

    var av = analystData && analystData.data_available ? analystData.verdict : null;
    var at = analystData && analystData.data_available ? analystData.targets : null;
    var ct = analystData && analystData.consensus ? analystData.consensus.trend : null;

    var cc = applyAnalystOverlay("cc", buildRecBase("cc", currReturn, medianClose, medianHigh, medianLow), av, at, ct);
    var csp = applyAnalystOverlay("csp", buildRecBase("csp", currReturn, medianClose, medianHigh, medianLow), av, at, ct);
    return { cc: cc, csp: csp };
  }

  var api = {
    buildRecBase: buildRecBase,
    applyAnalystOverlay: applyAnalystOverlay,
    buildBoth: buildBoth,
  };

  // Browser: expose on window.
  if (typeof window !== "undefined") {
    window.RecEngine = api;
  }
  // Node (test_recommendation.js): expose on module.exports.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})();
