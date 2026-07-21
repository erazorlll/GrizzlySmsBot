# Grizzly SMS Bot

A small Docker tool that acquires an SMS-activation number from
[Grizzly SMS](https://grizzlysms.com/) and then **watches it for the incoming SMS
code**, all in one run. It notifies you over **ntfy and/or Discord**.

By default it targets **Apple** (`SERVICE=wx`) in **Turkey** (`COUNTRY=62`).

> ⚠️ **Real purchases.** `getNumber` is not a stock check — every acquired number
> reserves a real number and holds your Grizzly balance. A number that expires
> without receiving an SMS is auto-refunded. Keep `MAX_ACQUISITIONS` low (default
> `1`); extras won by a concurrency burst are cancelled and refunded automatically.

## How It Works

1. **Acquire** — several worker threads poll the Grizzly `getNumber` endpoint at a
   shared rate limit. The moment a number is returned
   (`ACCESS_NUMBER:<activation_id>:<phone>`), it is kept. `MAX_ACQUISITIONS` is a
   **hard cap**: any extra number won in the same burst is cancelled (`setStatus=8`)
   and refunded, so you keep exactly what you asked for.
2. **Watch** — the tool then polls `getStatus` for the kept number until the SMS
   arrives (`STATUS_OK:<code>`), the number expires (`STATUS_CANCEL`), or the watch
   times out. You get a notification for the code.

If Grizzly returns `NO_NUMBERS`, the bot keeps polling. On an HTTP error (incl.
`429`) it backs the whole pool off briefly. On a fatal response (`BAD_KEY`,
`NO_BALANCE`, or `WRONG_MAX_PRICE:<min>` when the bid is below the platform
minimum), it notifies and stops acquiring.

> The SMS only arrives once **you** enter the acquired number into the target
> service (e.g. Apple). The watcher cannot conjure a code on its own.

## Requirements

- [Docker](https://www.docker.com/) with Docker Compose
- A [Grizzly SMS](https://grizzlysms.com/) API key
- A notification channel: an [ntfy](https://ntfy.sh/) topic URL and/or a Discord
  webhook URL (at least one)

## Quick Start

```bash
cp .env.example .env
```

Edit `.env` with your own values (set `NTFY_URL` and/or `DISCORD_WEBHOOK_URL`):

```env
GRIZZLY_API_KEY=your_grizzly_api_key
SERVICE=wx
COUNTRY=62
MAX_PRICE=1
PROVIDER_IDS=

NTFY_URL=https://ntfy.sh/your-topic
DISCORD_WEBHOOK_URL=

THREADS=10
MAX_REQUESTS_PER_SECOND=5
REQUEST_TIMEOUT_SECONDS=10
MAX_ACQUISITIONS=1
STATUS_POLL_SECONDS=5
WATCH_TIMEOUT_SECONDS=1200
```

Run:

```bash
docker compose up -d --build
docker compose logs -f --tail=100
docker compose down
```

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `GRIZZLY_API_KEY` | yes | — | Your Grizzly SMS API key. |
| `SERVICE` | yes | — | Service code (`wx` = Apple). |
| `COUNTRY` | yes | — | Country code (`62` = Turkey). |
| `MAX_PRICE` | yes | — | Max bid; must be ≥ the platform minimum (else `WRONG_MAX_PRICE`). |
| `PROVIDER_IDS` | no | — | Comma-separated provider IDs; omitted when empty. |
| `NTFY_URL` | one of† | — | ntfy topic URL. |
| `DISCORD_WEBHOOK_URL` | one of† | — | Discord webhook URL. |
| `THREADS` | yes | — | Number of worker threads. |
| `MAX_REQUESTS_PER_SECOND` | yes | — | Global request rate shared by all workers. |
| `REQUEST_TIMEOUT_SECONDS` | yes | — | HTTP timeout. |
| `MAX_ACQUISITIONS` | no | `1` | Numbers to keep; extras are cancelled + refunded. `0` = unlimited. |
| `STATUS_EVERY_REQUESTS` | no | `100` | Progress-log cadence during acquisition. |
| `STATUS_POLL_SECONDS` | no | `5` | Watch-phase poll interval. |
| `WATCH_TIMEOUT_SECONDS` | no | `1200` | Watch deadline (number lifetime ≈ 20 min). |
| `LOG_LEVEL` | no | `INFO` | Python logging level. |
| `GRIZZLY_API_URL` | no | prod | Override the endpoint (debugging). |

† At least one of `NTFY_URL` / `DISCORD_WEBHOOK_URL` must be set. If both are set,
notifications go to both.

## Running Without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a

# Full flow: acquire a number, then watch it for the SMS code
python -m grizzly

# Watch already-owned activations only (no purchase)
python -m grizzly watch 541507557 541507572
```

## Tests

No network, standard library only:

```bash
python -m unittest discover -s tests -t .
```

## Notifications

| Event | Urgent |
| --- | --- |
| Bot started | no |
| Number acquired | yes |
| Extra number cancelled (refunded) | no |
| SMS code received | yes |
| Number expired | no |
| Watch timeout | no |
| Fatal / stopped | yes |

Service and country codes: see the Grizzly SMS
[API documentation](https://grizzlysms.com/docs-old), the
[Apple service page](https://grizzlysms.com/apple), and the
[price/country table](https://grizzlysms.com/price).
