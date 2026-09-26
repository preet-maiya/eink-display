"""Fridge e-ink dashboard — Azure Functions v2 (Python 3.11)."""
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import azure.functions as func
import requests
from azure.identity import DefaultAzureCredential

import shared.table_ops as table_ops
from shared.render_base import to_bmp, format_updated  # also triggers font path setup on cold start
from shared.widgets import (
    weather as w_weather,
    calendar_widget as w_calendar,
    meal_plan as w_meal,
    commute as w_commute,
    portfolio as w_portfolio,
)

app = func.FunctionApp()

DEFAULT_WIDGETS = ["weather", "calendar", "meal_plan", "commute", "portfolio"]


@app.route(route="testpost", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET", "POST"])
def testpost(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"ok": True, "method": req.method}),
        mimetype="application/json",
    )


# ─── Timer: refresh external data every 10 minutes ──────────────────────────

_WIDGET_SCHEDULE_DEFAULTS = {
    "weather":   {"enabled": True, "start_hour": 7,  "end_hour": 10},
    "calendar":  {"enabled": True, "start_hour": 7,  "end_hour": 10},
    "commute":   {"enabled": True, "start_hour": 7,  "end_hour": 9},
    "portfolio": {"enabled": True, "start_hour": 9,  "end_hour": 16},
    "meal_plan": {"enabled": True, "start_hour": 7,  "end_hour": 10},
}


@app.timer_trigger(
    schedule="0 */10 * * * *",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=False,
)
def refresh_data(timer: func.TimerRequest) -> None:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "America/Los_Angeles"))
    settings = table_ops.get_all_settings()
    schedule = settings.get("schedule", {})
    hour = datetime.now(tz).hour

    active = set()
    for widget, defaults in _WIDGET_SCHEDULE_DEFAULTS.items():
        cfg = schedule.get(widget, defaults)
        if not cfg.get("enabled", True):
            continue
        if int(cfg.get("start_hour", defaults["start_hour"])) <= hour < int(cfg.get("end_hour", defaults["end_hour"])):
            active.add(widget)

    if not active:
        return
    _run_refresh(settings, only=active)


# ─── HTTP: manual refresh (device-token protected) ───────────────────────────

@app.route(route="refresh", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
def manual_refresh(req: func.HttpRequest) -> func.HttpResponse:
    token = req.params.get("token", "") or (req.get_json() or {}).get("token", "")
    expected = os.environ.get("DEVICE_TOKEN", "")
    if not token or token != expected:
        return func.HttpResponse("Unauthorized", status_code=401)

    settings = table_ops.get_all_settings()
    results = _run_refresh(settings)
    return func.HttpResponse(json.dumps({"ok": True, "results": results}), mimetype="application/json")


def _run_refresh(settings: dict, only: set | None = None) -> dict:
    """Fetch fresh data for widgets. `only` limits which widgets are refreshed (None = all)."""
    results: dict[str, str] = {}
    addresses = settings.get("addresses", {})
    home = addresses.get("home", {})
    work = addresses.get("work", {})

    if only is None or "weather" in only:
        if home.get("lat") and home.get("lon"):
            try:
                weather = _fetch_weather(home["lat"], home["lon"])
                table_ops.set_cache("weather", weather)
                logging.info("weather refreshed")
                results["weather"] = "ok"
            except Exception as e:
                logging.error("weather refresh failed: %s", e)
                results["weather"] = f"error: {e}"
        else:
            results["weather"] = "skipped — no home coordinates"

    if only is None or "calendar" in only:
        ics_url = (settings.get("integrations") or {}).get("calendar_ics_url") or \
                  os.environ.get("GOOGLE_CALENDAR_ICS_URL", "")
        if ics_url:
            try:
                cal = _fetch_calendar(ics_url)
                table_ops.set_cache("calendar", cal)
                n = len(cal.get("today", []))
                logging.info("calendar refreshed: %d events", n)
                results["calendar"] = f"ok — {n} events today"
            except Exception as e:
                logging.error("calendar refresh failed: %s", e)
                results["calendar"] = f"error: {e}"
        else:
            results["calendar"] = "skipped — no calendar URL"

    if only is None or "commute" in only:
        if home.get("lat") and work.get("lat"):
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(os.environ.get("DISPLAY_TIMEZONE", "America/Los_Angeles"))
                hour = datetime.now(tz).hour
                origin, dest = (home, work) if 3 <= hour < 12 else (work, home)
                routes = _fetch_routes(origin, dest)
                table_ops.set_cache("commute", routes)
                n = len(routes.get("routes", []))
                logging.info("commute refreshed: %d routes", n)
                results["commute"] = f"ok — {n} routes"
            except Exception as e:
                logging.error("commute refresh failed: %s", e)
                results["commute"] = f"error: {e}"
        else:
            results["commute"] = "skipped — no addresses"

    if only is None or "portfolio" in only:
        portfolio_cfg = settings.get("portfolio", [])
        if portfolio_cfg:
            try:
                port = _fetch_portfolio(portfolio_cfg)
                table_ops.set_cache("portfolio", port)
                logging.info("portfolio refreshed")
                results["portfolio"] = "ok"
            except Exception as e:
                logging.error("portfolio refresh failed: %s", e)
                results["portfolio"] = f"error: {e}"
        else:
            results["portfolio"] = "skipped — no holdings"

    return results


# ─── HTTP: render endpoint ───────────────────────────────────────────────────

@app.route(route="render", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def render(req: func.HttpRequest) -> func.HttpResponse:
    token = req.params.get("token", "")
    expected = os.environ.get("DEVICE_TOKEN", "")
    if not token or token != expected:
        return func.HttpResponse("Unauthorized", status_code=401)

    device = req.params.get("device", "fridge1")
    battery_str = req.params.get("battery", "")
    battery_pct = int(battery_str) if battery_str.isdigit() else None
    widget_override = req.params.get("widget", "")
    _store_indoor(req.params.get("t_in", ""), req.params.get("rh_in", ""))

    settings = table_ops.get_all_settings()
    widgets_cfg = settings.get("widgets", {})
    enabled = widgets_cfg.get("enabled", DEFAULT_WIDGETS)
    if not enabled:
        enabled = DEFAULT_WIDGETS

    if widget_override and widget_override in enabled:
        rotation_idx = enabled.index(widget_override)
        widget_name = widget_override
    else:
        current = table_ops.get_rotation_state(device)
        rotation_idx = (current + 1) % len(enabled)
        table_ops.set_rotation_state(device, rotation_idx)
        widget_name = enabled[rotation_idx]

    cache = table_ops.get_cache(widget_name)
    cache_data = cache.get("data")

    updated_str = format_updated(cache.get("updated_at", ""))

    bmp = _render_widget(
        widget_name, cache_data, settings, updated_str, battery_pct, rotation_idx, len(enabled)
    )

    return func.HttpResponse(bmp, mimetype="image/bmp", status_code=200)


def _store_indoor(t_str: str, rh_str: str) -> None:
    """Cache the device's SHT40 reading (°C, %RH) for the weather widget."""
    try:
        temp_c = float(t_str) if t_str else None
        rh = float(rh_str) if rh_str else None
    except ValueError:
        return
    if temp_c is None and rh is None:
        return
    try:
        table_ops.set_cache("indoor", {"temp_c": temp_c, "humidity_pct": rh})
    except Exception as e:
        logging.error("indoor cache write failed: %s", e)


def _fresh_indoor(max_age_min: int = 60):
    """Latest indoor reading, or None if the sensor hasn't reported recently."""
    cache = table_ops.get_cache("indoor")
    try:
        ts = datetime.fromisoformat(cache["updated_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if datetime.now(ts.tzinfo) - ts > timedelta(minutes=max_age_min):
        return None
    return cache.get("data")


# ─── HTTP: settings endpoint (SWA-authenticated) ─────────────────────────────

@app.route(route="settings", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET", "POST"])
def settings_api(req: func.HttpRequest) -> func.HttpResponse:
    try:
        if req.method == "GET":
            data = table_ops.get_all_settings()
            return func.HttpResponse(json.dumps(data), mimetype="application/json")

        try:
            body = req.get_json() or {}
        except Exception:
            body = {}

        logging.info("settings POST body keys: %s", list(body.keys()))

        errors = {}
        for key in ("widgets", "addresses", "meal_plan", "menu_items", "portfolio", "integrations", "schedule", "display"):
            if key in body:
                try:
                    logging.info("saving key: %s", key)
                    table_ops.set_settings(key, body[key])
                    logging.info("saved key: %s", key)
                except Exception as e:
                    logging.error("failed key %s: %s", key, e)
                    errors[key] = str(e)

        if errors:
            return func.HttpResponse(
                json.dumps({"ok": False, "error": str(errors)}),
                mimetype="application/json",
            )
        return func.HttpResponse(json.dumps({"ok": True}), mimetype="application/json")

    except Exception as e:
        logging.exception("settings_api unhandled: %s", e)
        return func.HttpResponse(
            json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}),
            mimetype="application/json",
        )


# ─── Widget dispatch ─────────────────────────────────────────────────────────

def _render_widget(
    name: str,
    data,
    settings: dict,
    updated_str: str,
    battery_pct,
    rotation_idx: int,
    total: int,
) -> bytes:
    kw = dict(updated_at=updated_str, battery_pct=battery_pct,
              rotation_idx=rotation_idx, total_widgets=total)
    if name == "weather":
        unit = (settings.get("display") or {}).get("temp_unit", "F")
        indoor = _fresh_indoor()
        return w_weather.render(data, temp_unit=unit, indoor=indoor, **kw)
    if name == "calendar":
        return w_calendar.render(data, **kw)
    if name == "meal_plan":
        return w_meal.render(data, settings, **kw)
    if name == "commute":
        return w_commute.render(data, **kw)
    if name == "portfolio":
        return w_portfolio.render(data, **kw)
    # Unknown widget: return blank
    from PIL import Image as PILImage
    return to_bmp(PILImage.new("L", (800, 480), 255))


# ─── Data fetchers ────────────────────────────────────────────────────────────

def _fetch_weather(lat: float, lon: float) -> dict:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,weathercode,apparent_temperature,"
        f"windspeed_10m,winddirection_10m,relative_humidity_2m"
        f"&daily=weathercode,temperature_2m_max,temperature_2m_min,"
        f"sunrise,sunset,precipitation_probability_max,uv_index_max"
        f"&temperature_unit=fahrenheit&wind_speed_unit=mph"
        f"&timezone=auto&forecast_days=6"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    d = r.json()

    cur = d.get("current", {})
    daily = d.get("daily", {})
    days_abbr = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

    def _fmt_time(s):
        if not s:
            return ""
        try:
            return datetime.fromisoformat(s).strftime("%-I:%M %p")
        except Exception:
            return ""

    def _wind_dir(deg):
        if deg is None:
            return ""
        return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(deg / 45) % 8]

    precip_probs = daily.get("precipitation_probability_max", [])
    forecast = []
    times = daily.get("time", [])
    for i in range(1, min(6, len(times))):
        dt = datetime.strptime(times[i], "%Y-%m-%d")
        entry = {
            "day": days_abbr[dt.weekday()],
            "weathercode": daily["weathercode"][i],
            "high_f": daily["temperature_2m_max"][i],
            "low_f": daily["temperature_2m_min"][i],
        }
        if i < len(precip_probs) and precip_probs[i] is not None:
            entry["precip_pct"] = round(precip_probs[i])
        forecast.append(entry)

    uv_list = daily.get("uv_index_max", [])
    today_uv = uv_list[0] if uv_list else None

    return {
        "current": {
            "temp_f": cur.get("temperature_2m"),
            "weathercode": cur.get("weathercode", 0),
            "high_f": daily["temperature_2m_max"][0] if daily.get("temperature_2m_max") else None,
            "low_f": daily["temperature_2m_min"][0] if daily.get("temperature_2m_min") else None,
            "feels_like_f": cur.get("apparent_temperature"),
            "wind_mph": cur.get("windspeed_10m"),
            "wind_dir": _wind_dir(cur.get("winddirection_10m")),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "uv_index": round(today_uv) if today_uv is not None else None,
            "precip_today_pct": round(precip_probs[0]) if precip_probs else None,
            "sunrise": _fmt_time(daily.get("sunrise", [None])[0]),
            "sunset": _fmt_time(daily.get("sunset", [None])[0]),
        },
        "forecast": forecast,
    }


def _fetch_calendar(ics_url: str) -> dict:
    from icalendar import Calendar as ICal

    r = requests.get(ics_url, timeout=15)
    r.raise_for_status()
    cal = ICal.from_ical(r.content)

    today = datetime.now().date()
    week_end = today + timedelta(days=7)

    events_by_date: dict[str, list] = {}
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        dtstart = component.get("DTSTART")
        if dtstart is None:
            continue
        dt = dtstart.dt
        if isinstance(dt, datetime):
            event_date = dt.date()
            time_str = dt.strftime("%-I:%M %p")
        else:
            event_date = dt
            time_str = "All day"

        if today <= event_date <= week_end:
            key = event_date.isoformat()
            events_by_date.setdefault(key, []).append({
                "time": time_str,
                "summary": str(component.get("SUMMARY", "")),
                "date": key,
            })

    # Sort events within each day by time
    for k in events_by_date:
        events_by_date[k].sort(key=lambda e: e["time"])

    today_events = events_by_date.get(today.isoformat(), [])

    days_abbr = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    week = []
    for i in range(1, 7):
        d = today + timedelta(days=i)
        evts = events_by_date.get(d.isoformat(), [])
        if evts:
            first = evts[0]["summary"]
            summary = first if len(evts) == 1 else f"{len(evts)} events — {first}"
        else:
            summary = "—"
        week.append({"day": days_abbr[d.weekday()], "summary": summary})

    return {"today": today_events, "week": week}


_ROAD_DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def _short_road_name(name: str) -> str:
    # Drop trailing direction so "I-90 W" / "Newport Way NW" fit the card and
    # merge with other pieces of the same road.
    parts = name.split()
    if len(parts) > 1 and parts[-1] in _ROAD_DIRECTIONS:
        parts = parts[:-1]
    return " ".join(parts)


def _fetch_routes(origin: dict, dest: dict) -> dict:
    cred = DefaultAzureCredential()
    token = cred.get_token("https://atlas.microsoft.com/.default").token
    client_id = os.environ.get("AZURE_MAPS_CLIENT_ID", "")

    query = f"{origin['lat']},{origin['lon']}:{dest['lat']},{dest['lon']}"
    url = (
        f"https://atlas.microsoft.com/route/directions/json"
        f"?api-version=1.0&query={query}&maxAlternatives=2&travelMode=car"
        f"&traffic=true&instructionsType=text"
    )
    r = requests.get(url, headers={
        "Authorization": f"Bearer {token}",
        "x-ms-client-id": client_id,
    }, timeout=15)
    r.raise_for_status()
    data = r.json()

    routes = []
    for route in data.get("routes", []):
        summary = route.get("summary", {})
        dur_min = round(summary.get("travelTimeInSeconds", 0) / 60)
        dist_mi = round(summary.get("lengthInMeters", 0) / 1609.34, 1)

        # Traffic severity from delay ratio
        travel = summary.get("travelTimeInSeconds", 1)
        no_traffic = summary.get("noTrafficTravelTimeInSeconds", travel)
        delay_ratio = (travel - no_traffic) / max(no_traffic, 1)
        if delay_ratio > 0.3:
            severity = "heavy"
        elif delay_ratio > 0.1:
            severity = "moderate"
        else:
            severity = "light"

        # Route name: accumulate distance per road; 70/30 rule for dual display
        guidance = route.get("guidance", {})
        instructions = guidance.get("instructions", [])
        _skip = {"LOCATION_DEPARTURE", "LOCATION_ARRIVAL"}
        total_m = max(dist_mi * 1609.34, 1)
        road_dist: dict[str, float] = {}

        for idx, instr in enumerate(instructions):
            if instr.get("instructionType") in _skip:
                continue
            this_off = instr.get("routeOffsetInMeters", 0)
            next_off = instructions[idx + 1].get("routeOffsetInMeters", total_m) \
                if idx + 1 < len(instructions) else total_m
            seg = max(0.0, next_off - this_off)

            road_list = instr.get("roadNumbers") or []
            if isinstance(road_list, str):
                road_list = [road_list]
            # Route number first, else street name. Not signpostText: that's
            # the exit-sign destination ("Bellevue College"), not the road.
            road_str = next((r for r in road_list if r), None) \
                or instr.get("street") or ""
            road_str = _short_road_name(road_str)
            if road_str and seg > 0:
                road_dist[road_str] = road_dist.get(road_str, 0) + seg

        if road_dist:
            ranked = sorted(road_dist.items(), key=lambda x: x[1], reverse=True)
            top, top_d = ranked[0]
            if len(ranked) > 1 and ranked[1][1] / top_d >= 30 / 70:
                name = f"Via {top} & {ranked[1][0]}"
            else:
                name = f"Via {top}"
        else:
            name = f"~{round(no_traffic / 60)} min w/o traffic"

        routes.append({
            "name": name,
            "duration_min": dur_min,
            "distance_mi": dist_mi,
            "traffic": severity,
        })

    return {"routes": routes}


def _fetch_portfolio(holdings: list) -> dict:
    import yfinance as yf

    total_value = 0.0
    total_change = 0.0
    results = []

    for h in holdings:
        ticker = h.get("ticker", "")
        shares = float(h.get("shares", 0))
        try:
            t = yf.Ticker(ticker)
            fi = t.fast_info
            price = fi.last_price
            prev = fi.previous_close
            if price and prev:
                change_pct = (price - prev) / prev * 100
                value = price * shares
                daily_change = (price - prev) * shares
                total_value += value
                total_change += daily_change
                results.append({
                    "ticker": ticker,
                    "shares": shares,
                    "price": round(price, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": round(change_pct, 2),
                })
        except Exception as e:
            logging.warning("Failed to fetch %s: %s", ticker, e)
            results.append({"ticker": ticker, "shares": shares, "price": None,
                            "prev_close": None, "change_pct": 0})

    total_change_pct = (total_change / (total_value - total_change) * 100) if total_value else 0

    return {
        "holdings": results,
        "total_value": round(total_value, 2),
        "total_change": round(total_change, 2),
        "total_change_pct": round(total_change_pct, 2),
    }
