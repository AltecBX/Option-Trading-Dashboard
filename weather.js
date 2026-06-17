// weather.js (v1.21) — pure helpers for the brand-header weather pill.
// No DOM, no network here. The component does the fetching; this file
// just maps WMO weather codes to a label and glyph, formats the temp,
// and builds the Open-Meteo request URL. Dual exported so node can test
// it (test_weather.js) and the browser can use it via window.WeatherUtil.
//
// Open-Meteo is keyless and CORS friendly, so the browser fetches it
// directly. Docs: https://open-meteo.com/en/docs

(function () {
  // Default location: Yonkers, NY (Jerry's base). The component can
  // override these with device geolocation when the pill is tapped.
  var DEFAULT_COORDS = { lat: 40.9312, lon: -73.8988, label: "Yonkers" };

  // WMO weather interpretation codes grouped to a glyph + short label.
  // Reference: WMO code table 4677 as exposed by Open-Meteo.
  function wxFromCode(code) {
    var c = Number(code);
    if (c === 0) return { icon: "☀️", label: "Clear" };
    if (c === 1) return { icon: "🌤️", label: "Mostly clear" };
    if (c === 2) return { icon: "⛅", label: "Partly cloudy" };
    if (c === 3) return { icon: "☁️", label: "Overcast" };
    if (c === 45 || c === 48) return { icon: "🌫️", label: "Fog" };
    if (c >= 51 && c <= 57) return { icon: "🌦️", label: "Drizzle" };
    if (c >= 61 && c <= 67) return { icon: "🌧️", label: "Rain" };
    if (c >= 71 && c <= 77) return { icon: "🌨️", label: "Snow" };
    if (c >= 80 && c <= 82) return { icon: "🌦️", label: "Showers" };
    if (c === 85 || c === 86) return { icon: "🌨️", label: "Snow showers" };
    if (c === 95) return { icon: "⛈️", label: "Thunderstorm" };
    if (c === 96 || c === 99) return { icon: "⛈️", label: "Thunderstorm, hail" };
    return { icon: "🌡️", label: "Weather" };
  }

  // Round to whole degrees with a degree glyph. Returns "—" for null.
  function formatTemp(t) {
    if (t === null || t === undefined || isNaN(Number(t))) return "—";
    return Math.round(Number(t)) + "°";
  }

  // Build the current-conditions request. unit is "fahrenheit" or
  // "celsius". timezone=auto so the returned time matches the location.
  function buildForecastUrl(lat, lon, unit) {
    var u = unit === "celsius" ? "celsius" : "fahrenheit";
    return "https://api.open-meteo.com/v1/forecast"
      + "?latitude=" + encodeURIComponent(lat)
      + "&longitude=" + encodeURIComponent(lon)
      + "&current=temperature_2m,weather_code"
      + "&temperature_unit=" + u
      + "&timezone=auto";
  }

  // Pull the fields the pill needs out of an Open-Meteo response object.
  // Defensive: returns null if the shape is not what we expect so the
  // component can show a dash instead of throwing.
  function parseCurrent(resp) {
    if (!resp || typeof resp !== "object" || !resp.current) return null;
    var cur = resp.current;
    if (cur.temperature_2m === undefined || cur.weather_code === undefined) return null;
    return {
      temp: cur.temperature_2m,
      code: cur.weather_code,
      time: cur.time || null,
    };
  }

  var api = {
    DEFAULT_COORDS: DEFAULT_COORDS,
    wxFromCode: wxFromCode,
    formatTemp: formatTemp,
    buildForecastUrl: buildForecastUrl,
    parseCurrent: parseCurrent,
  };

  if (typeof window !== "undefined") { window.WeatherUtil = api; }
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})();
