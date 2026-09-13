#!/usr/bin/env python3
"""Watch the Nintendo Museum ticket calendar and alert when a watched date becomes buyable.

Runs either way. By default it loops every INTERVAL seconds, which is what Railway
wants. Set RUN_ONCE=1 and it sweeps once then exits, which is what launchd wants. State
is diffed against state.json so a date that stays open does not re-alert every sweep.

The calendar API 302s unless you carry a session cookie, so prime /en/calendar first and
then GET /en/api/calendar?target_year=&target_month= for each month of interest.

Stdlib only, same as puffing_billy.py, so there is nothing to install.

Env (read from .env if present, real env vars win):
  NTFY_TOPIC  ntfy.sh topic for phone push (blank = Mac notification only)
  WATCH       comma YYYY-MM-DD list to alert on
  HORIZON     YYYY-MM month probed for new releases
  INTERVAL    seconds between sweeps when looping
  RUN_ONCE    "1" = one sweep then exit (launchd / cron)
"""
from __future__ import annotations

import datetime as dt
import http.cookiejar
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from enum import IntEnum

HERE = pathlib.Path(__file__).parent
STATE_FILE = HERE / "state.json"
LOG_FILE = HERE / "log.txt"

BASE = "https://museum-tickets.nintendo.com/en"
CALENDAR_URL = f"{BASE}/calendar"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
TIMEOUT = 30
HORIZON_KEY = "_horizon_released"


class Apply(IntEnum):
    """calendar.apply_type — how a date is sold."""
    LOTTERY = 2
    ON_SALE = 3


class Sale(IntEnum):
    """calendar.sale_status — whether inventory remains."""
    NOT_SOLD_OUT = 1
    SOLD_OUT = 2


class Openness(IntEnum):
    """calendar.open_status — whether the museum operates that day."""
    OPEN = 1
    CLOSED = 2


def _load_env() -> None:
    """Read .env into the environment without clobbering real env vars."""
    try:
        lines = (HERE / ".env").read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
WATCH = tuple(d.strip() for d in os.environ.get(
    "WATCH", "2026-11-28,2026-11-29,2026-11-30").split(",") if d.strip())
HORIZON = os.environ.get("HORIZON", "2027-01").strip()
INTERVAL = int(os.environ.get("INTERVAL", "300"))
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"


def open_session() -> urllib.request.OpenerDirector:
    """Return an opener holding the session cookie the calendar API requires."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": UA})
    opener.open(request, timeout=TIMEOUT).read()
    if not any(c.name == "history_session" for c in jar):
        raise RuntimeError("no session cookie from /en/calendar")
    return opener


def fetch_month(opener: urllib.request.OpenerDirector, year: int, month: int) -> dict:
    """Return {YYYY-MM-DD: raw day dict} for one month."""
    request = urllib.request.Request(
        f"{BASE}/api/calendar?target_year={year}&target_month={month}",
        headers={"User-Agent": UA,
                 "X-Requested-With": "XMLHttpRequest",
                 "Accept": "application/json, text/plain, */*",
                 "Referer": CALENDAR_URL})
    body = opener.open(request, timeout=TIMEOUT).read().decode()
    if not body.lstrip().startswith("{"):
        raise RuntimeError(f"{year}-{month:02d}: expected JSON, got {body[:80]!r}")
    return json.loads(body)["data"]["calendar"]


def classify(day: dict) -> str:
    """Collapse the three API codes into one state name.

    Closed is checked before sale_status on purpose: scheduled closures such as
    2026-11-28 report sale_status=1 (not sold out) because no inventory was ever
    created for them. Reading sale_status alone would alert on every poll forever.
    """
    apply_type = day.get("apply_type")
    sale = day.get("sale_status")
    openness = day.get("open_status")
    if apply_type is None:
        return "unreleased"
    if openness == Openness.CLOSED:
        return "closed"
    if apply_type == Apply.LOTTERY:
        return "lottery"
    if apply_type == Apply.ON_SALE:
        return "buyable" if sale == Sale.NOT_SOLD_OUT else "soldout"
    return f"unknown(apply={apply_type},sale={sale},open={openness})"


def months_for(dates: tuple[str, ...]) -> list[tuple[int, int]]:
    """Distinct (year, month) pairs needed to cover the watched dates."""
    pairs = {(int(d[:4]), int(d[5:7])) for d in dates}
    return sorted(pairs)


def scan(opener: urllib.request.OpenerDirector) -> tuple[dict[str, str], bool]:
    """Return ({date: state} for watched dates, horizon_released)."""
    states: dict[str, str] = {}
    for year, month in months_for(WATCH):
        calendar = fetch_month(opener, year, month)
        for date, day in calendar.items():
            if date in WATCH:
                states[date] = classify(day)
    year, month = int(HORIZON[:4]), int(HORIZON[5:7])
    horizon = fetch_month(opener, year, month)
    released = any(classify(day) != "unreleased" for day in horizon.values())
    return states, released


def _push_ntfy(title: str, message: str) -> None:
    """Publish anonymously. The topic accepts it; an Authorization header 401s."""
    headers = {"User-Agent": UA, "Title": title, "Priority": "urgent",
               "Tags": "video_game,rotating_light", "Click": CALENDAR_URL}
    request = urllib.request.Request(f"https://ntfy.sh/{TOPIC}",
                                     data=message.encode(), headers=headers)
    urllib.request.urlopen(request, timeout=TIMEOUT).read()


def _push_mac(title: str, message: str) -> None:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{esc(message)}" with title "{esc(title)}" '
         f'sound name "Glass"'],
        capture_output=True, check=False)


def notify(title: str, message: str) -> list[str]:
    """Fire both channels independently; neither failure may silence the other."""
    results = []
    if TOPIC:
        try:
            _push_ntfy(title, message)
            results.append("ntfy ok")
        except (urllib.error.URLError, OSError) as exc:
            results.append(f"ntfy FAILED: {exc}")
    else:
        results.append("ntfy skipped (no NTFY_TOPIC)")
    _push_mac(title, message)
    results.append("mac ok")
    return results


def diff(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Alert lines for transitions worth waking someone up for."""
    alerts = []
    for date in WATCH:
        before, after = old.get(date), new.get(date)
        if after is None or after == before:
            continue
        if after == "buyable":
            alerts.append(f"{date} is BUYABLE (was {before or 'unseen'})")
        elif after.startswith("unknown"):
            alerts.append(f"{date} unrecognised state {after} (was {before or 'unseen'})")
        elif before == "closed":
            alerts.append(f"{date} closure lifted: closed -> {after}")
    return alerts


def sweep() -> int:
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        opener = open_session()
        states, released = scan(opener)
    except (urllib.error.URLError, OSError, RuntimeError, ValueError, KeyError) as exc:
        line = f"[{stamp}] scan error: {type(exc).__name__}: {exc}"
        print(line, flush=True)
        with LOG_FILE.open("a") as handle:
            handle.write(line + "\n")
        return 1

    first_run = not STATE_FILE.exists()
    old = json.loads(STATE_FILE.read_text()) if not first_run else {}
    alerts = diff({k: v for k, v in old.items() if k != HORIZON_KEY}, states)
    # Stay quiet about an already-released horizon on a cold start: that is a baseline,
    # not news. Matters on Railway, where a redeploy wipes state.json.
    if released and not old.get(HORIZON_KEY) and not first_run:
        alerts.append(f"{HORIZON} has been released, new dates are on sale")

    lines = [f"[{stamp}] " + "  ".join(f"{d}:{states.get(d, '?')}" for d in WATCH)
             + f"  horizon({HORIZON}):{'released' if released else 'unreleased'}"]
    if alerts:
        lines.append("  !!! " + " | ".join(alerts))
        title = "Nintendo Museum tickets OPEN"
        lines += [f"  {r}" for r in notify(title, "; ".join(alerts)[:300])]

    report = "\n".join(lines)
    print(report, flush=True)
    with LOG_FILE.open("a") as handle:
        handle.write(report + "\n")
    STATE_FILE.write_text(json.dumps({**states, HORIZON_KEY: released},
                                     indent=1, sort_keys=True))
    return 10 if alerts else 0


def main() -> int:
    """Single sweep for launchd, or an endless loop for an always-on worker."""
    if RUN_ONCE:
        return sweep()
    print(f"watching {', '.join(WATCH)} | horizon {HORIZON} | every {INTERVAL}s",
          flush=True)
    while True:
        sweep()
        time.sleep(INTERVAL)


def _selftest() -> None:
    closed = {"apply_type": 3, "sale_status": 1, "open_status": 2}
    assert classify(closed) == "closed", "closed day must not read as buyable"

    assert classify({"apply_type": 3, "sale_status": 1, "open_status": 1}) == "buyable"
    assert classify({"apply_type": 3, "sale_status": 2, "open_status": 1}) == "soldout"
    assert classify({"apply_type": 2, "sale_status": 1, "open_status": 1}) == "lottery"
    assert classify({"apply_type": None, "sale_status": None,
                     "open_status": None}) == "unreleased"
    assert classify({"apply_type": 9, "sale_status": 1,
                     "open_status": 1}).startswith("unknown")

    assert diff({"2026-11-30": "soldout"}, {"2026-11-30": "buyable"})
    assert not diff({"2026-11-30": "buyable"}, {"2026-11-30": "buyable"}), "no re-alert"
    assert not diff({"2026-11-28": "closed"}, {"2026-11-28": "closed"}), "closed is quiet"
    assert diff({"2026-11-28": "closed"}, {"2026-11-28": "soldout"}), "closure lifted"

    assert months_for(("2026-11-28", "2026-11-30", "2026-12-01")) == [(2026, 11), (2026, 12)]
    print("selftest ok")


if __name__ == "__main__":
    if os.environ.get("SELFTEST") == "1":
        _selftest()
    else:
        sys.exit(main())
