import json
import logging
import os
import threading
import time
from datetime import date, datetime, time as day_time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("windwise")

API = "https://data.elexon.co.uk/bmrs/api/v1/datasets"
LONDON = ZoneInfo("Europe/London")
CUTOFF = os.getenv("DAY_AHEAD_CUTOFF", "15:30")
MONTH_START = date(2026, 1, 1)
MONTH_END = date(2026, 2, 1)
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "900"))
_cache: tuple[float, list[dict], list[dict], str] | None = None
_cache_lock = threading.Lock()

app = FastAPI(title="Windwise", description="January 2026 GB wind forecast analytics")
origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in origins],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def fetch(dataset: str, start: datetime, end: datetime, fuel: bool = False) -> list[dict]:
    params = {
        "publishDateTimeFrom": start.isoformat().replace("+00:00", "Z"),
        "publishDateTimeTo": end.isoformat().replace("+00:00", "Z"),
    }
    if fuel:
        params["fuelType"] = "WIND"
    request = Request(f"{API}/{dataset}/stream?{urlencode(params)}", headers={"Accept": "application/json"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=45) as response:
                rows = json.loads(response.read())
            if not isinstance(rows, list):
                raise ValueError(f"Unexpected {dataset} response: expected a JSON array")
            return rows
        except HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
            raise RuntimeError(f"Elexon {dataset} returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"Could not read Elexon {dataset}: {exc}") from exc
    raise RuntimeError(f"Could not fetch Elexon {dataset}")


def month_data() -> tuple[list[dict], list[dict], str]:
    global _cache
    with _cache_lock:
        if _cache and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1], _cache[2], _cache[3]
        start = datetime(2025, 12, 31, tzinfo=timezone.utc)
        end = datetime(2026, 2, 2, tzinfo=timezone.utc)
        try:
            actual = fetch("FUELHH", datetime(2026, 1, 1, tzinfo=timezone.utc), end, fuel=True)
            forecasts = fetch("WINDFOR", start, end)
        except RuntimeError as exc:
            log.exception("Elexon request failed")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        log.info("Fetched %d FUELHH and %d WINDFOR rows", len(actual), len(forecasts))
        fetched_at = datetime.now(timezone.utc).isoformat()
        _cache = (time.monotonic(), actual, forecasts, fetched_at)
        return actual, forecasts, fetched_at


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def time_key(value: str) -> str:
    return parse_time(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def decision_date(start_time: datetime) -> date:
    return start_time.astimezone(LONDON).date() - timedelta(days=1)


def cutoff_for(start_time: datetime, cutoff: str | None = None) -> datetime:
    hour, minute = map(int, (cutoff or CUTOFF).split(":"))
    return datetime.combine(decision_date(start_time), day_time(hour, minute), LONDON).astimezone(timezone.utc)


def select_rows(actual_rows: list[dict], forecast_rows: list[dict], first: date, last: date, cutoff: str | None = None) -> dict:
    cutoff = cutoff or CUTOFF
    actuals: dict[str, dict] = {}
    for row in actual_rows:
        if not row.get("startTime") or row.get("generation") is None or row.get("fuelType") != "WIND":
            continue
        stamp = parse_time(row["startTime"])
        if first <= decision_date(stamp) <= last:
            key = time_key(row["startTime"])
            current = actuals.get(key)
            current_publish = current.get("publishTime") if current else None
            row_publish = row.get("publishTime")
            if current is None or (row_publish and (not current_publish or parse_time(row_publish) >= parse_time(current_publish))):
                actuals[key] = row

    grouped: dict[str, list[dict]] = {}
    for row in forecast_rows:
        if not row.get("startTime") or not row.get("publishTime") or row.get("generation") is None:
            continue
        stamp = parse_time(row["startTime"])
        if first <= decision_date(stamp) <= last:
            grouped.setdefault(time_key(row["startTime"]), []).append(row)

    points = []
    starts = set(actuals) | set(grouped)
    for key in sorted(starts):
        stamp = parse_time(key)
        if not first <= decision_date(stamp) <= last:
            continue
        limit = cutoff_for(stamp, cutoff)
        forecasts = grouped.get(key, [])
        eligible = [row for row in forecasts if parse_time(row["publishTime"]) <= limit and row.get("generation") is not None]
        later = [row for row in forecasts if parse_time(row["publishTime"]) > limit and row.get("generation") is not None]
        chosen = max(eligible, key=lambda row: parse_time(row["publishTime"]), default=None)
        newest_later = max(later, key=lambda row: parse_time(row["publishTime"]), default=None)
        actual = actuals.get(key)
        if chosen and stamp.astimezone(LONDON).minute == 0:
            next_half_hour = time_key((stamp + timedelta(minutes=30)).isoformat())
            second_actual = actuals.get(next_half_hour)
            metric_actual = (
                (actual["generation"] + second_actual["generation"]) / 2
                if actual and second_actual
                else None
            )
        else:
            metric_actual = actual.get("generation") if actual else None
        points.append({
            "startTime": key,
            "actual": actual.get("generation") if actual else None,
            "metricActual": metric_actual,
            "forecast": chosen.get("generation") if chosen else None,
            "forecastPublishedAt": chosen.get("publishTime") if chosen else None,
            "laterForecast": newest_later.get("generation") if newest_later else None,
            "laterForecastPublishedAt": newest_later.get("publishTime") if newest_later else None,
        })

    expected = 24 * ((last - first).days + 1)
    actual_count = sum(point["actual"] is not None for point in points)
    pairs = [(point["forecast"], point["metricActual"]) for point in points if point["forecast"] is not None and point["metricActual"] is not None]
    errors = [forecast - actual for forecast, actual in pairs]
    count = len(errors)
    mae = sum(abs(error) for error in errors) / count if count else None
    rmse = (sum(error * error for error in errors) / count) ** 0.5 if count else None
    bias = sum(errors) / count if count else None
    return {
        "points": points,
        "metrics": {"mae": mae, "rmse": rmse, "bias": bias, "observations": count},
        "coverage": {"expectedIntervals": expected, "actualIntervals": actual_count, "matchedIntervals": count,
                     "percent": round(count / expected * 100, 1) if expected else 0},
        "cutoff": cutoff,
        "timezone": "Europe/London",
        "decisionRange": {"start": first.isoformat(), "end": last.isoformat()},
    }


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/forecast")
def forecast(
    start: date = Query(MONTH_START),
    end: date = Query(date(2026, 1, 31)),
    cutoff: str | None = Query(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
):
    if start < MONTH_START or end >= MONTH_END or end < start:
        raise HTTPException(status_code=400, detail="Select a valid range within January 2026")
    try:
        effective_cutoff = cutoff or CUTOFF
        hour, minute = map(int, effective_cutoff.split(":"))
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="DAY_AHEAD_CUTOFF must use HH:MM") from exc
    actual, forecasts, fetched_at = month_data()
    try:
        data = select_rows(actual, forecasts, start, end, effective_cutoff)
    except (KeyError, TypeError, ValueError) as exc:
        log.exception("Elexon returned an invalid dataset response")
        raise HTTPException(status_code=502, detail="Elexon returned data in an unexpected format") from exc
    data["fetchedAt"] = fetched_at
    return data
