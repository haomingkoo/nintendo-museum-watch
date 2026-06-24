#!/usr/bin/env python3
"""Puffing Billy ticket scanner — watches Belgrave→Lakeside (Return) availability
for a set of dates and pushes a phone + email alert the moment one opens.

Stdlib only (urllib) so the Railway container stays tiny. Runs as a worker:
loops every INTERVAL seconds; alerts only on the sold-out -> open transition.

Env (all optional, sane defaults):
  NTFY_TOPIC   ntfy.sh topic to push to        (default below)
  ALERT_EMAIL  also forward each alert here     (blank = push only)
  DATES        comma DD/MM/YYYY list to watch   (default Aug 5-8 2026)
  PRODUCT      product code to require          (default BEL-LAK)
  INTERVAL     seconds between scans            (default 300)
  RUN_ONCE     "1" = single scan then exit      (for cron / GitHub Actions)
"""
import json, os, re, sys, time, urllib.request, urllib.parse

# Load .env if present (local dev). On Railway, set real env vars instead.
def _load_env():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass
_load_env()

BASE  = "https://book.puffingbillyrailway.org.au/BookingProduct/AvailabilityBook/?"
UA    = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
BOOK  = "https://book.puffingbillyrailway.org.au/BookingProduct/AvailabilityBook/"
ADULT_FARE = "2867_2810"   # the "Adult" fare code in the guest-selection step

TOPIC    = os.environ.get("NTFY_TOPIC", "pb-CHANGEME")  # real value lives in .env / Railway vars
EMAIL    = os.environ.get("ALERT_EMAIL", "").strip()
DATES    = [d.strip() for d in os.environ.get("DATES", "06/08/2026,07/08/2026").split(",") if d.strip()]
PRODUCT  = os.environ.get("PRODUCT", "BEL-LAK")
ADULTS   = int(os.environ.get("ADULTS", "4"))
INTERVAL = int(os.environ.get("INTERVAL", "300"))
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"


def _dkey(d):
    """DD/MM/YYYY -> (yyyy, mm, dd) for correct chronological comparison."""
    day, mon, year = d.split("/")
    return (int(year), int(mon), int(day))


def _get(url, data=None, cookie=None):
    headers = {"User-Agent": UA}
    if cookie:
        headers["Cookie"] = cookie
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    body = data.encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def scan():
    """Return {date: 'available'|'soldout'|'other'|'unknown'} for the watched dates."""
    page  = _get(BASE)
    m = re.search(r"[0-9]+\.[0-9]+---[A-Za-z0-9_-]+", page)
    if not m:
        raise RuntimeError("no session token in page")
    cookie = f"currentbrandWAFApplicationBookingProduct=PUFFING%20BILLY; oidToken={m.group(0)}"
    # Set party size first: the qty-blind feed reports a date "Available" even when it
    # only has 1-3 seats. Setting N adults makes updateAvailability reflect true N-seat availability.
    _get(f"{BASE}&updateBookingFareQty&localtime={int(time.time())}000",
         data=f"&fare={ADULT_FARE}&roomtype=&increment={ADULTS}", cookie=cookie)
    ts = f"{int(time.time())}000"
    raw = _get(f"{BASE}&updateAvailability&localtime={ts}",
               data="newDate=06/08/2026&direction=0", cookie=cookie)
    rows = {r["date"]: r for r in json.loads(raw).get("availability", [])}
    # Highest bookable date = the sales horizon. When it jumps forward, a new
    # block of dates (e.g. September) has just gone on sale — the moment to grab
    # 4 fresh seats before they sell out.
    horizon = max((d for d in rows), key=_dkey, default=None)

    out = {}
    for d in DATES:
        r = rows.get(d)
        if not r:
            out[d] = "unknown"
        elif r.get("status") != "Available":
            out[d] = "soldout"
        else:
            prod = next((p for p in r.get("detailedAvailability", []) if p.get("code") == PRODUCT), None)
            out[d] = ("available" if prod and prod.get("available") != "Sold out"
                      else "other" if prod is None else "soldout")
    return out, horizon


def _push(msg, headers):
    req = urllib.request.Request(f"https://ntfy.sh/{TOPIC}", data=msg.encode(),
                                 headers={"User-Agent": UA, **headers})
    urllib.request.urlopen(req, timeout=20).read()


def alert(open_dates):
    msg = (f"Belgrave->Lakeside is OPEN for: {', '.join(open_dates)}.\n"
           f"4 adults - book now: {BOOK}")
    base = {"Title": "Puffing Billy tickets OPEN - book now",
            "Priority": "urgent", "Tags": "steam_locomotive,rotating_light", "Click": BOOK}

    # Phone + desktop push — the reliable channel, must always go out.
    try:
        _push(msg, base)
        print(f"  pushed -> ntfy/{TOPIC}", flush=True)
    except Exception as e:
        print(f"  PUSH FAILED: {e}", flush=True)

    # Email is best-effort: ntfy.sh blocks anonymous email, so it needs a free
    # account token (set NTFY_TOKEN). Never let this break the push above.
    token = os.environ.get("NTFY_TOKEN", "").strip()
    if EMAIL and token:
        try:
            _push(msg, {**base, "Email": EMAIL, "Authorization": f"Bearer {token}"})
            print(f"  emailed -> {EMAIL}", flush=True)
        except Exception as e:
            print(f"  email failed (push still sent): {e}", flush=True)
    elif EMAIL and not token:
        print("  email skipped: set NTFY_TOKEN (free ntfy.sh account) to enable", flush=True)


def alert_release(old_horizon, new_horizon):
    msg = (f"Puffing Billy just released new dates: now bookable through {new_horizon} "
           f"(was {old_horizon}). September may be open — grab 4 adults before it sells out: {BOOK}")
    _push(msg, {"Title": "New Puffing Billy dates ON SALE", "Priority": "urgent",
                "Tags": "steam_locomotive,calendar", "Click": BOOK})
    print(f"  pushed release alert -> {new_horizon}", flush=True)


def main():
    print(f"watching {PRODUCT} for {DATES} | topic={TOPIC} | every {INTERVAL}s", flush=True)
    prev_open = set()
    horizon = None
    while True:
        try:
            res, new_horizon = scan()
            now_open = {d for d, s in res.items() if s == "available"}
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{stamp}] horizon={new_horizon}  "
                  + "  ".join(f"{d}:{res[d]}" for d in DATES), flush=True)
            newly = now_open - prev_open
            if newly:
                alert(sorted(newly))
            prev_open = now_open
            # Alert when the sales horizon advances — but stay silent on the first
            # scan (that's just establishing the baseline, not a real release).
            if horizon is not None and new_horizon and _dkey(new_horizon) > _dkey(horizon):
                alert_release(horizon, new_horizon)
            if new_horizon:
                horizon = new_horizon
        except Exception as e:
            print(f"[scan error] {e}", flush=True)
        if RUN_ONCE:
            break
        time.sleep(INTERVAL)


def _selftest():
    # horizon comparison must be chronological, not string-sorted
    assert _dkey("06/08/2026") < _dkey("15/08/2026")
    assert _dkey("31/08/2026") < _dkey("01/09/2026")   # Sept release > end of Aug
    assert _dkey("09/08/2026") < _dkey("10/08/2026")   # not lexical ("09" vs "10")
    assert max(["07/08/2026", "31/08/2026", "01/09/2026"], key=_dkey) == "01/09/2026"
    print("selftest ok")


if __name__ == "__main__":
    if os.environ.get("SELFTEST") == "1":
        _selftest()
    else:
        main()
