"""External web tools used by the Knowledge agent for general-purpose questions.

All calls use a 5 s timeout, honor ENABLE_WEB_TOOLS=false (checked at call time) and never
raise. The httpx client is injectable (``client=`` argument or ``set_client_factory``) so
tests run with httpx.MockTransport and zero real network.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from getnet_agents.schemas import ToolResult

TIMEOUT_S = 5.0
USER_AGENT = "getnet-agents-sdd/0.1 (+https://github.com/maathmartins-ux/getnet-agents-sdd)"

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
EXCHANGE_URL = "https://economia.awesomeapi.com.br/json/last/{base}-{quote}"
SEARCH_URL = "https://html.duckduckgo.com/html/"

# WMO weather interpretation codes (Open-Meteo docs) -> (pt, en)
WEATHER_CODES: dict[int, tuple[str, str]] = {
    0: ("céu limpo", "clear sky"),
    1: ("predominantemente limpo", "mainly clear"),
    2: ("parcialmente nublado", "partly cloudy"),
    3: ("nublado", "overcast"),
    45: ("nevoeiro", "fog"),
    48: ("nevoeiro com geada", "depositing rime fog"),
    51: ("garoa fraca", "light drizzle"),
    53: ("garoa moderada", "moderate drizzle"),
    55: ("garoa intensa", "dense drizzle"),
    61: ("chuva fraca", "slight rain"),
    63: ("chuva moderada", "moderate rain"),
    65: ("chuva forte", "heavy rain"),
    66: ("chuva congelante fraca", "light freezing rain"),
    67: ("chuva congelante forte", "heavy freezing rain"),
    71: ("neve fraca", "slight snow"),
    73: ("neve moderada", "moderate snow"),
    75: ("neve forte", "heavy snow"),
    80: ("pancadas de chuva fracas", "slight rain showers"),
    81: ("pancadas de chuva moderadas", "moderate rain showers"),
    82: ("pancadas de chuva fortes", "violent rain showers"),
    95: ("trovoada", "thunderstorm"),
    96: ("trovoada com granizo fraco", "thunderstorm with slight hail"),
    99: ("trovoada com granizo forte", "thunderstorm with heavy hail"),
}

ClientFactory = Callable[[], httpx.Client]
_client_factory: ClientFactory | None = None


def set_client_factory(factory: ClientFactory | None) -> None:
    """Override how httpx clients are built (tests inject a MockTransport-backed client)."""
    global _client_factory
    _client_factory = factory


def _new_client() -> httpx.Client:
    if _client_factory is not None:
        return _client_factory()
    return httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def web_enabled() -> bool:
    return os.getenv("ENABLE_WEB_TOOLS", "true").strip().lower() not in {"false", "0", "no", "off"}


def _ok(data: dict[str, Any] | list[Any], source: str) -> ToolResult:
    return {"ok": True, "data": data, "source": source, "error": None}


def _fail(error: str, source: str) -> ToolResult:
    return {"ok": False, "data": {}, "source": source, "error": error}


def _http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http_{exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"network_error: {type(exc).__name__}"
    if isinstance(exc, (KeyError, IndexError, ValueError, TypeError)):
        return f"parse_error: {type(exc).__name__}: {exc}"
    return f"internal_error: {type(exc).__name__}: {exc}"


def _run(source: str, client: httpx.Client | None, fn: Callable[[httpx.Client], ToolResult]) -> ToolResult:
    """Common envelope: feature flag, client lifecycle and error mapping."""
    if not web_enabled():
        return _fail("disabled", source)
    owned = client is None
    c: httpx.Client | None = client
    try:
        if c is None:
            c = _new_client()
        return fn(c)
    except Exception as exc:  # noqa: BLE001 - contract: tools never raise
        return _fail(_http_error(exc), source)
    finally:
        if owned and c is not None:
            c.close()


# ---------------------------------------------------------------- weather
def get_weather_forecast(city: str, days_ahead: int = 1, client: httpx.Client | None = None) -> ToolResult:
    """Daily forecast for a city via Open-Meteo (geocoding + forecast). days_ahead: 0=today .. 7."""
    city = str(city or "").strip()
    if not city:
        return _fail("invalid_args: city is required", FORECAST_URL)
    try:
        days = int(days_ahead)
    except (TypeError, ValueError):
        return _fail("invalid_args: days_ahead must be an integer", FORECAST_URL)
    if not 0 <= days <= 7:
        return _fail("invalid_args: days_ahead must be between 0 and 7", FORECAST_URL)

    def call(c: httpx.Client) -> ToolResult:
        geo = c.get(GEOCODING_URL, params={"name": city, "count": 5, "language": "pt", "format": "json"}, timeout=TIMEOUT_S)
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            return _fail("city_not_found", str(geo.url))
        # Merchants are in Brazil: prefer a Brazilian match when the name is ambiguous.
        place = next((r for r in results if r.get("country_code") == "BR"), results[0])
        fc = c.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,precipitation_sum",
                "timezone": "auto",
                "forecast_days": days + 1,
            },
            timeout=TIMEOUT_S,
        )
        fc.raise_for_status()
        daily = fc.json()["daily"]
        code = int(daily["weather_code"][days])
        pt, en = WEATHER_CODES.get(code, ("condição desconhecida", "unknown conditions"))
        data = {
            "city": place.get("name", city),
            "admin1": place.get("admin1"),
            "country": place.get("country"),
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "date": daily["time"][days],
            "days_ahead": days,
            "temp_max_c": daily["temperature_2m_max"][days],
            "temp_min_c": daily["temperature_2m_min"][days],
            "precipitation_probability_max": daily.get("precipitation_probability_max", [None] * (days + 1))[days],
            "precipitation_sum_mm": daily.get("precipitation_sum", [None] * (days + 1))[days],
            "weather_code": code,
            "description_pt": pt,
            "description_en": en,
        }
        return _ok(data, str(fc.url))

    return _run(FORECAST_URL, client, call)


# ---------------------------------------------------------------- exchange rate
def get_exchange_rate(base: str = "EUR", quote: str = "BRL", client: httpx.Client | None = None) -> ToolResult:
    """Latest commercial exchange rate base->quote from AwesomeAPI (economia.awesomeapi.com.br)."""
    b = str(base or "").strip().upper()
    q = str(quote or "").strip().upper()
    url = EXCHANGE_URL.format(base=b, quote=q)
    if not (len(b) == 3 and b.isalpha() and len(q) == 3 and q.isalpha()):
        return _fail("invalid_args: base and quote must be 3-letter currency codes", url)

    def call(c: httpx.Client) -> ToolResult:
        r = c.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        row = r.json()[f"{b}{q}"]
        data = {
            "base": b,
            "quote": q,
            "name": row.get("name"),
            "bid": float(row["bid"]),
            "ask": float(row["ask"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "pct_change": float(row.get("pctChange", 0) or 0),
            "timestamp": row.get("timestamp"),
            "create_date": row.get("create_date"),
        }
        return _ok(data, url)

    return _run(url, client, call)


# ---------------------------------------------------------------- web search
class _DDGParser(HTMLParser):
    """Extracts (title, url, snippet) from DuckDuckGo's HTML results page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None
        self._buf: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._field is not None:
            self._depth += 1
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": _clean_ddg_url(a.get("href") or ""), "snippet": ""})
            self._field, self._buf, self._depth = "title", [], 0
        elif "result__snippet" in classes and self.results:
            self._field, self._buf, self._depth = "snippet", [], 0

    def handle_endtag(self, tag: str) -> None:
        if self._field is None:
            return
        if self._depth > 0:
            self._depth -= 1
            return
        self.results[-1][self._field] = " ".join("".join(self._buf).split())
        self._field = None

    def handle_data(self, data: str) -> None:
        if self._field is not None:
            self._buf.append(data)


def _clean_ddg_url(href: str) -> str:
    """DDG wraps links as //duckduckgo.com/l/?uddg=<encoded>; unwrap to the real URL."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def parse_ddg_html(html: str, k: int = 5) -> list[dict[str, str]]:
    parser = _DDGParser()
    parser.feed(html)
    return [r for r in parser.results if r["url"] and r["title"]][:k]


def web_search(query: str, k: int = 5, client: httpx.Client | None = None) -> ToolResult:
    """General web search (DuckDuckGo HTML endpoint). Returns up to k {title, url, snippet}."""
    q = str(query or "").strip()
    if not q:
        return _fail("invalid_args: query is required", SEARCH_URL)
    try:
        n = max(1, min(int(k), 10))
    except (TypeError, ValueError):
        return _fail("invalid_args: k must be an integer", SEARCH_URL)

    def call(c: httpx.Client) -> ToolResult:
        r = c.get(SEARCH_URL, params={"q": q}, timeout=TIMEOUT_S)
        r.raise_for_status()
        if r.status_code != 200:  # DDG answers 202 when it rate-limits automated clients
            return _fail(f"http_{r.status_code}", str(r.url))
        results = parse_ddg_html(r.text, n)
        return _ok({"query": q, "results": results}, str(r.url))

    return _run(SEARCH_URL, client, call)
