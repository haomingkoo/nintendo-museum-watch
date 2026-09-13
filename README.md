# Nintendo Museum ticket watcher

Watches the Nintendo Museum ticket calendar and pushes a phone alert the moment a
watched date becomes buyable, which in practice means the moment someone cancels.

One file, stdlib only (`watch.py`). Runs anywhere Python runs.

> **Reality as of 13 Sep 2026:** every bookable day from September through November is
> sold out, all 74 of them. December is lottery-only, with entries closing 30 Sep at
> 23:59 JST. There is no first-come inventory left in the current window, so the only
> way in is a cancellation.

## Get alerts on your phone
1. Install the **ntfy** app (iOS / Android).
2. Pick a private topic name. It is a secret, since anyone who knows it sees your
   alerts. Put it in `.env` as `NTFY_TOPIC=` and subscribe to the same name in the app.
3. Done. A push lands the instant a watched date opens.

Publish anonymously. Do not set an ntfy auth token, because a stale one makes the
publish return 401 and the alert is lost.

## Configure
Copy `.env.example` to `.env` and fill it in. `.env` is gitignored, never commit it.
On Railway, set the same keys as **Variables** instead of shipping a `.env`.

| var          | default                  | notes |
|--------------|--------------------------|-------|
| `NTFY_TOPIC` | *(set me)*               | your private ntfy topic |
| `WATCH`      | `2026-11-28,29,30`       | comma YYYY-MM-DD |
| `HORIZON`    | `2027-01`                | month probed for new releases |
| `INTERVAL`   | `300`                    | seconds between sweeps when looping |
| `RUN_ONCE`   | *(blank)*                | `1` = one sweep then exit |

## Run
```bash
SELFTEST=1 python3 watch.py    # unit checks, no network
RUN_ONCE=1 python3 watch.py    # one sweep
python3 watch.py               # loop forever (the point)
```

## Deploy on Railway (always-on)
`railway.json` sets the start command and no web port is needed. Push to `main` and
Railway redeploys. Set `NTFY_TOPIC` as a Variable.

Always-on matters here. A laptop-based scheduler stops sweeping when the lid shuts,
and cancellations reportedly cluster around 23:00 to 02:00 JST.

## How it works
GET `/en/calendar` to pick up the session cookie, then GET
`/en/api/calendar?target_year=&target_month=` per month. Called cold that endpoint 302s
to `/en`, so priming the session is required. No login is needed.

Each day carries three codes:

| field | values |
|-------|--------|
| `apply_type` | 3 = on sale, 2 = lottery, null = not yet released |
| `sale_status` | 1 = not sold out, 2 = sold out |
| `open_status` | 1 = museum open, 2 = closed |

Buyable means all three together: `apply_type 3`, `sale_status 1`, `open_status 1`.

> **The trap.** Scheduled closures report `sale_status: 1`, meaning "not sold out",
> because no inventory was ever created for them. 2026-11-28 and 11-29 are both
> closures. Reading `sale_status` alone would alert on every sweep forever, so
> `open_status` is checked first. The selftest guards this.

Two alert triggers, plus a safety net:
- **Cancellation.** A watched date flips to buyable.
- **New release.** The horizon month stops returning null, so a new block went on sale.
- **Unknown state.** Any code combination the classifier does not recognise alerts
  rather than being swallowed, so a schema change surfaces loudly.

State lives in `state.json` and is diffed each sweep, so a date that stays open does
not renotify. A cold start stays quiet about an already-released horizon, which matters
on Railway where a redeploy wipes the file.

## History
This repo was the Puffing Billy railway scanner, watching Belgrave to Lakeside for 4
adults on 5-8 Aug 2026. Those dates have passed, so the repo was repurposed rather than
abandoned. The Puffing Billy version is in the git history at `puffing_billy.py`.

Worth knowing if you resurrect it: its email alert path is gated on `NTFY_TOKEN`, and
that token is expired, so its emails were failing silently. Its phone push was fine,
because it only attached the token to the email attempt.
