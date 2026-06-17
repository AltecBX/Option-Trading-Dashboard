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

console.log("\n" + passed + "/" + (passed + failed) + " passed, " + failed + " failed");
if (failed) { console.log("FAILED: " + fails.join(", ")); process.exit(1); }
