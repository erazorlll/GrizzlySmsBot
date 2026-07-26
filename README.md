# Grizzly SMS Bot

Acquire a temporary phone number from [Grizzly SMS](https://grizzlysms.com/) and
automatically **watch it for the incoming SMS code** — in a single run — with push
notifications over **ntfy and/or Discord**.

Built for grabbing scarce numbers the moment they come in stock (e.g. **Apple in
Turkey** — `SERVICE=wx`, `COUNTRY=62`) and relaying the verification code straight
to your phone.

> ⚠️ **Real purchases.** `getNumber` is not a stock check — every acquired number
> reserves a real number and holds your Grizzly balance. A number that expires
> without an SMS is auto-refunded. `MAX_ACQUISITIONS` (default `1`) is a hard cap:
> extras won by a concurrency burst are cancelled and refunded automatically.

## Features

- **One-shot flow** — acquire → watch for the SMS code → notify, no manual steps.
- **Hard single-number guarantee** — burst-bought extras are cancelled (`setStatus=8`) and refunded.
- **ntfy and/or Discord** — pick either or both; you choose simply by setting the URL(s).
- **Rate-limited workers** — one shared limiter across threads, with a global backoff on HTTP / `429` errors.
- **Fatal-response aware** — stops on `BAD_KEY` / `NO_BALANCE` / `WRONG_MAX_PRICE` and exits non-zero.
- **Provider filtering** — target or exclude specific providers (`PROVIDER_IDS` / `EXCEPT_PROVIDER_IDS`).
- **Auto-loads `.env`** — no `source .env` needed; zero third-party deps beyond `requests`.
- **Watch-only mode** — re-attach to numbers you already own: `python -m grizzly watch <id> ...`.

## How It Works

1. **Acquire** — worker threads poll `getNumber` at a shared rate limit. The first
   number returned (`ACCESS_NUMBER:<id>:<phone>`) is kept; any extra won in the same
   burst is cancelled and refunded, so you keep exactly `MAX_ACQUISITIONS`.
2. **Watch** — the tool polls `getStatus` for the kept number until the code arrives
   (`STATUS_OK:<code>`), the number expires (`STATUS_CANCEL`), or the watch times out.

`NO_NUMBERS` → keep polling. HTTP / `429` → the whole pool backs off (honouring
`Retry-After`). Fatal response (`BAD_KEY`, `NO_BALANCE`, `WRONG_MAX_PRICE:<min>`) →
notify and stop.

> The SMS only arrives once **you** enter the acquired number into the target
> service. The watcher relays the code — it can't conjure one.

## Project Layout

```
grizzly/
  __main__.py   # CLI entry point (full flow + `watch` subcommand)
  config.py     # env parsing + .env auto-loader
  notify.py     # ntfy / Discord backends + fan-out notifier
  api.py        # Grizzly client, rate limiter, response parsing
  bot.py        # Acquirer (workers + hard cap) and Watcher (SMS polling)
tests/          # stdlib unittest, no network
```

## Quick Start (Docker)

```bash
cp .env.example .env      # then edit it (see Configuration)
docker compose up -d --build
docker compose logs -f --tail=100
docker compose down
```

Set `NTFY_URL` and/or `DISCORD_WEBHOOK_URL` in `.env` (at least one is required).

## Run Without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Full flow: acquire a number, then watch it for the SMS code
python -m grizzly

# Watch numbers you already own (no purchase)
python -m grizzly watch 541507557 541507572
```

`.env` is auto-loaded from the current directory at startup — no `source .env`
needed. Real environment variables still take precedence, and
`GRIZZLY_ENV_FILE=/path/to/env` points at a different file.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `GRIZZLY_API_KEY` | yes | — | Your Grizzly SMS API key. |
| `SERVICE` | yes* | — | Service code (`wx` = Apple). |
| `COUNTRY` | yes* | — | Country code (`62` = Turkey). |
| `MAX_PRICE` | yes* | — | Max bid; must be ≥ the platform minimum (else `WRONG_MAX_PRICE`). |
| `PROVIDER_IDS` | no | — | Comma-separated provider IDs to target; omitted when empty. |
| `EXCEPT_PROVIDER_IDS` | no | — | Comma-separated provider IDs to exclude; omitted when empty. |
| `NTFY_URL` | one of† | — | ntfy topic URL. |
| `DISCORD_WEBHOOK_URL` | one of† | — | Discord webhook URL. |
| `THREADS` | yes* | — | Number of worker threads. |
| `MAX_REQUESTS_PER_SECOND` | yes* | — | Global request rate shared by all workers. |
| `REQUEST_TIMEOUT_SECONDS` | yes* | 10 | HTTP timeout (defaults to 10s in `watch` mode). |
| `MAX_ACQUISITIONS` | no | `1` | Numbers to keep; extras are cancelled + refunded. `0` = unlimited. |
| `STATUS_EVERY_REQUESTS` | no | `100` | Progress-log cadence during acquisition. |
| `STATUS_POLL_SECONDS` | no | `5` | Watch-phase poll interval. |
| `WATCH_TIMEOUT_SECONDS` | no | `1200` | Watch deadline (number lifetime ≈ 20 min). |
| `LOG_LEVEL` | no | `INFO` | Python logging level. |
| `GRIZZLY_API_URL` | no | prod | Override the endpoint (debugging). |

† At least one of `NTFY_URL` / `DISCORD_WEBHOOK_URL` must be set. If both are set,
notifications go to both.

\* Required only for the acquire flow (`python -m grizzly`). The `watch`
subcommand needs just `GRIZZLY_API_KEY` and a notifier (plus optional
`STATUS_POLL_SECONDS` / `WATCH_TIMEOUT_SECONDS`).

Service, country, and provider codes: see the Grizzly SMS
[API docs](https://grizzlysms.com/docs-old), the
[Apple service page](https://grizzlysms.com/apple), and the
[price/country table](https://grizzlysms.com/price).

## Notifications

| Event | Urgent |
| --- | --- |
| Bot started | no |
| Number acquired | yes |
| Extra number cancelled (refunded) | no |
| SMS code received | yes |
| Number expired | no |
| Watch timeout / interrupted | no |
| Fatal / stopped | yes |

## Tests

No network, standard library only:

```bash
python -m unittest discover -s tests -t .
```

## Notes

- Repository: <https://github.com/kurosaki-sol/GrizzlySmsBot>
- Use with your own Grizzly account and API key. You are responsible for how you
  use temporary numbers and for complying with Grizzly SMS' terms of service.
