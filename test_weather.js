// test_weather.js (v1.21) — verifies the weather pill helpers. Pure
// node, no DOM, no network. Run from the active dir:  node test_weather.js
const path = require("path");
const W = require(path.join(__dirname, "weather.js"));

let passed = 0, failed = 0; const fails = [];
function ok(name, cond) {
  if (cond) { passed++; console.log("  PASS  " + name); }
  else { failed++; fails.push(name); console.log("  FAIL  " + name); }
}

// wxFromCode buckets
ok("code 0 is clear", W.wxFromCode(0).label === "Clear");
ok("code 2 is partly cloudy", W.wxFromCode(2).label === "Partly cloudy");
ok("code 3 is overcast", W.wxFromCode(3).label === "Overcast");
ok("code 48 is fog", W.wxFromCode(48).label === "Fog");
ok("code 63 is rain", W.wxFromCode(63).label === "Rain");
ok("code 75 is snow", W.wxFromCode(75).label === "Snow");
ok("code 95 is thunderstorm", W.wxFromCode(95).label === "Thunderstorm");
ok("string code coerces", W.wxFromCode("0").label === "Clear");
ok("unknown code falls back", W.wxFromCode(1234).icon === "🌡️");

// formatTemp
ok("temp rounds", W.formatTemp(71.6) === "72°");
ok("temp rounds down", W.formatTemp(71.2) === "71°");
ok("null temp is dash", W.formatTemp(null) === "—");
ok("NaN temp is dash", W.formatTemp("abc") === "—");
ok("zero temp shows", W.formatTemp(0) === "0°");

// buildForecastUrl
const url = W.buildForecastUrl(40.9312, -73.8988, "fahrenheit");
ok("url has latitude", url.indexOf("latitude=40.9312") !== -1);
ok("url has longitude", url.indexOf("longitude=-73.8988") !== -1);
ok("url requests temp + code", url.indexOf("current=temperature_2m,weather_code") !== -1);
ok("url honors fahrenheit", url.indexOf("temperature_unit=fahrenheit") !== -1);
ok("url celsius option", W.buildForecastUrl(0, 0, "celsius").indexOf("temperature_unit=celsius") !== -1);
ok("url defaults to fahrenheit on junk unit", W.buildForecastUrl(0, 0, "kelvin").indexOf("temperature_unit=fahrenheit") !== -1);

// parseCurrent
ok("parseCurrent reads fields", (function () {
  const r = W.parseCurrent({ current: { temperature_2m: 70, weather_code: 2, time: "2026-05-29T12:00" } });
  return r && r.temp === 70 && r.code === 2 && r.time === "2026-05-29T12:00";
})());
ok("parseCurrent null on bad shape", W.parseCurrent({}) === null);
ok("parseCurrent null on missing fields", W.parseCurrent({ current: { temperature_2m: 70 } }) === null);
ok("DEFAULT_COORDS is Yonkers", W.DEFAULT_COORDS.label === "Yonkers");

// ── v5.12: a sun at 11pm ──────────────────────────────────────────────────
// The pill drew a sun at 11:15 PM because the icon only ever read the
// weather code. Open-Meteo sends is_day; the request asks for it now, and
// the three glyphs with a sun in them swap after dark.
ok("the forecast request asks for the day/night flag",
   W.buildForecastUrl(40.9, -73.9, "fahrenheit").indexOf("is_day") > -1);
ok("clear is a sun by day", W.wxFromCode(0, 1).icon === "\u2600\ufe0f");
ok("clear is a moon by night", W.wxFromCode(0, 0).icon === "\ud83c\udf19");
ok("mostly clear is a moon by night", W.wxFromCode(1, 0).icon === "\ud83c\udf19");
ok("partly cloudy drops the sun after dark", W.wxFromCode(2, 0).icon === "\u2601\ufe0f");
ok("the label is the same either way", W.wxFromCode(0, 0).label === "Clear");
// Rain looks like rain at midnight. Only the sun glyphs needed a night face.
ok("rain is unchanged after dark", W.wxFromCode(63, 0).icon === W.wxFromCode(63, 1).icon);
ok("snow is unchanged after dark", W.wxFromCode(73, 0).icon === W.wxFromCode(73, 1).icon);
ok("a storm is unchanged after dark", W.wxFromCode(95, 0).icon === W.wxFromCode(95, 1).icon);
// A response from before this change still renders, as day, not as a crash.
ok("no flag means day, as it always did", W.wxFromCode(0, undefined).icon === "\u2600\ufe0f");
ok("parseCurrent carries the flag through",
   W.parseCurrent({current: {temperature_2m: 72, weather_code: 0,
                             time: "2026-09-13T23:15", is_day: 0}}).isDay === 0);
// The fallback reads the hour the API reported, which timezone=auto makes
// local to the place on screen — not to whatever zone the browser is in.
ok("11pm with no flag is night", W.dayFromTime("2026-09-13T23:15") === 0);
ok("noon with no flag is day", W.dayFromTime("2026-09-13T12:00") === 1);
ok("5am with no flag is night", W.dayFromTime("2026-09-13T05:59") === 0);
ok("an unreadable stamp is null, not a guess", W.dayFromTime("nonsense") === null);
ok("parseCurrent falls back when the field is absent",
   W.parseCurrent({current: {temperature_2m: 72, weather_code: 0,
                             time: "2026-09-13T23:15"}}).isDay === 0);

console.log("\n" + passed + "/" + (passed + failed) + " passed, " + failed + " failed");
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
