"""Read economic calendar releases exported by the MT5 terminal bridge."""

from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


CALENDAR_API_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_CACHE_SECONDS = 900
_calendar_lock = threading.Lock()
_calendar_cache = None
_calendar_checked_at = 0.0


def _api_cache_file():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MT5_RMS" / "calendar_api_cache.json"


def _load_api_cache():
    try:
        cached = json.loads(_api_cache_file().read_text(encoding="utf-8"))
        if isinstance(cached.get("events"), list) and cached.get("updated_at"):
            return cached
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def _save_api_cache(data):
    try:
        path = _api_cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _server_zone():
    return ZoneInfo(os.getenv("RMS_MT5_SERVER_TIMEZONE", "Europe/Athens"))


def _api_events(rows, server_zone):
    if not isinstance(rows, list):
        raise ValueError("Calendar API did not return an event list.")
    events = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        currency = str(row.get("country") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        impact = str(row.get("impact") or "").strip().lower()
        if not re.fullmatch(r"[A-Z]{3}", currency) or not title or impact not in {"low", "medium", "high", "holiday"}:
            continue
        try:
            release = datetime.fromisoformat(str(row.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if release.tzinfo is None:
            continue
        release = release.astimezone(timezone.utc)
        events.append({
            "time": release.isoformat(),
            "server_time": release.astimezone(server_zone).strftime("%Y.%m.%d %H:%M"),
            "currency": currency, "title": title[:256],
            "impact": "low" if impact == "holiday" else impact,
            "time_mode": "all_day" if impact == "holiday" else "timed",
            "period": "", "actual": "", "forecast": str(row.get("forecast") or ""),
            "previous": str(row.get("previous") or ""),
        })
    if not events:
        raise ValueError("Calendar API returned no usable scheduled releases.")
    return sorted(events, key=lambda event: event["time"])


def read_calendar_api(force=False):
    """Fetch the current week's scheduled releases without a terminal EA."""
    global _calendar_cache, _calendar_checked_at
    with _calendar_lock:
        now = time.time()
        if _calendar_cache is None:
            _calendar_cache = _load_api_cache()
            if _calendar_cache:
                try:
                    _calendar_checked_at = datetime.fromisoformat(_calendar_cache["updated_at"]).timestamp()
                except (ValueError, TypeError):
                    _calendar_checked_at = 0.0
        retry_seconds = (_calendar_cache.get("retry_after_seconds", 60)
                         if _calendar_cache and _calendar_cache.get("status") != "connected"
                         else CALENDAR_CACHE_SECONDS)
        if not force and _calendar_cache and now - _calendar_checked_at < retry_seconds:
            return dict(_calendar_cache, server_today=datetime.now(_server_zone()).date().isoformat())
        try:
            response = requests.get(CALENDAR_API_URL, timeout=10, headers={"User-Agent": "MT5-RMS/1.0"})
            response.raise_for_status()
            zone = _server_zone()
            events = _api_events(response.json(), zone)
            result = {"events": events, "status": "connected", "message": "",
                      "updated_at": datetime.now(timezone.utc).isoformat(),
                      "server_today": datetime.now(zone).date().isoformat(),
                      "source": "Forex Factory weekly calendar API", "server_timezone": str(zone)}
            _calendar_cache = result
            _save_api_cache(result)
        except (requests.RequestException, ValueError, KeyError) as exc:
            message = f"Calendar API unavailable: {exc}"
            retry_after = 300 if getattr(getattr(exc, "response", None), "status_code", None) == 429 else 60
            if _calendar_cache:
                _calendar_cache = dict(_calendar_cache, status="stale", message=message,
                                       retry_after_seconds=retry_after)
            else:
                _calendar_cache = {"events": [], "status": "error", "message": message,
                                   "updated_at": None, "source": "Forex Factory weekly calendar API",
                                   "server_timezone": str(_server_zone()),
                                   "retry_after_seconds": retry_after}
        _calendar_checked_at = now
        return dict(_calendar_cache, server_today=datetime.now(_server_zone()).date().isoformat())


def calendar_file() -> Path:
    configured = os.getenv("RMS_MT5_CALENDAR_FILE")
    if configured:
        return Path(configured)
    return Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / "Common" / "Files" / "RMS_EconomicCalendar.tsv"


def read_terminal_calendar(max_age_seconds: int = 900) -> dict:
    path = calendar_file()
    if not path.is_file():
        return {"events": [], "status": "missing", "message": "MT5 calendar bridge is not running. Attach RMS_EconomicCalendar to a chart in the connected terminal.", "updated_at": None}
    age = max(0.0, time.time() - path.stat().st_mtime)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        events = []
        server_offsets = []
        for row in rows:
            currency = str(row.get("currency") or "").strip().upper()
            title = str(row.get("title") or "").strip()
            impact = str(row.get("impact") or "").strip().lower()
            timestamp = int(row.get("utc_ts") or 0)
            server_time = str(row.get("server_time") or "").strip()
            if not re.fullmatch(r"[A-Z]{3}", currency) or not title or impact not in {"low", "medium", "high"} or timestamp <= 0:
                continue
            if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}", server_time):
                continue
            events.append({
                "time": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                "server_time": server_time,
                "currency": currency, "title": title[:256], "impact": impact,
                "time_mode": row.get("time_mode") or "timed",
                "period": row.get("period") or "", "actual": row.get("actual") or "",
                "forecast": row.get("forecast") or "", "previous": row.get("previous") or "",
            })
            server_offsets.append((abs(timestamp - path.stat().st_mtime),
                                   datetime.strptime(server_time, "%Y.%m.%d %H:%M") -
                                   datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)))
        offset = min(server_offsets, default=(0, timedelta()))[1]
        exported_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        server_today = (exported_at.replace(tzinfo=None) + offset).date().isoformat()
        stale = age > max_age_seconds
        return {"events": events, "status": "stale" if stale else "connected",
                "message": f"MT5 calendar export is {round(age / 60)} minutes old. Reattach the terminal bridge for live updates." if stale else "",
                "updated_at": exported_at.isoformat(), "server_today": server_today}
    except (OSError, ValueError, UnicodeError) as exc:
        return {"events": [], "status": "error", "message": f"Could not read MT5 calendar export: {exc}", "updated_at": None}
