# Puffing Billy ticket scanner

Watches **Belgrave → Lakeside (Return)** availability for **4 adults** on **Aug 5–8 2026**
and pushes a phone alert the moment a date opens up (cancellations).

One file, stdlib only (`puffing_billy.py`). Runs anywhere Python runs.

> **Reality as of 24 Jun 2026:** for 4 adults, *every* date in the bookable window
> (now → 15 Aug 2026) is sold out. Dates that look "available" on the site often only
> have 1–3 seats — this scanner sets the party size first, so it only alerts when **4
> seats actually exist together**.

## Get alerts on your phone
1. Install the **ntfy** app (iOS / Android).
2. Pick a private topic name (it's a secret — anyone who knows it sees your alerts).
   Put it in `.env` as `NTFY_TOPIC=` and subscribe to the same name in the app.
3. Done — a push lands the instant 4 adults can book Belgrave→Lakeside on a watched date.

## Configure
Copy `.env.example` to `.env` and fill it in. `.env` is gitignored — never commit it.
On Railway, set the same keys as **Variables** instead of shipping a `.env`.

| var          | default            | notes |
|--------------|--------------------|-------|
| `NTFY_TOPIC` | *(set me)*         | your private ntfy topic |
| `ADULTS`     | `4`                | party size — makes the feed accurate |
| `DATES`      | `05/08/2026,…,08/08/2026` | comma DD/MM/YYYY |
| `PRODUCT`    | `BEL-LAK`          | route code |
| `INTERVAL`   | `300`              | seconds between scans (use `90` for a faster catch) |
| `ALERT_EMAIL`| *(blank)*          | also email — needs `NTFY_TOKEN` |
| `NTFY_TOKEN` | *(blank)*          | free ntfy.sh account token; required for email |

## Run
```bash
RUN_ONCE=1 python3 puffing_billy.py   # one check
python3 puffing_billy.py              # loop forever (the point)
```

## Deploy on Railway (always-on)
1. `git init`, commit, push to a GitHub repo (`.gitignore` keeps `.env` out).
2. Railway → New Project → Deploy from GitHub → pick the repo.
3. Settings → Variables → add the keys above. `railway.json` sets the start command; no web port needed.

Zero-infra alternative: a GitHub Actions cron running `RUN_ONCE=1 python3 puffing_billy.py`
every 5–10 min (it re-alerts each run while open, since cron runs don't share state).

## How it works
GET the booking page → read the `oidToken` it embeds → set the party size
(`updateBookingFareQty`) → `updateAvailability` → check each watched date for a non-sold-out
`BEL-LAK` entry. Alerts only on the sold-out→open transition.

## Not included: auto-hold
Auto-reserving the seats into a cart was scoped but **not built**: the booking wizard is
stateful and doesn't replay reliably over plain HTTP (the departure-time list only renders in
a real browser), and right now there's zero 4-adult inventory to build/test it against. A
reliable version would need a headless browser — revisit if the fast alert proves not enough.
