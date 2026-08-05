# Grizzly SMS Bot

Small Docker bot that repeatedly calls the Grizzly SMS `getNumber` endpoint until
it gets a phone number. When a number is acquired, the bot sends one notification
to an ntfy topic.

## How It Works

The bot starts several worker threads. Each worker calls the Grizzly SMS API with
the configured service, country, max price, and optional provider IDs.

When Grizzly returns:

```text
ACCESS_NUMBER:<activation_id>:<phone_number>
```

the bot logs the number and sends a notification to ntfy.

If Grizzly returns `NO_NUMBERS`, the bot keeps polling. If Grizzly returns an HTTP
error, the bot pauses briefly before sending more requests.

Separately, the bot checks live stock and price for `SERVICE`+`COUNTRY` via Grizzly's
[`getPricesV3`](https://grizzlysms.com/docs/activation#activation-api-getPrices-v3)
action — once at startup (included in the startup ntfy notification) and again every
`PRICE_CHECK_INTERVAL_SECONDS`. This costs no balance and reserves no number, so it
runs independently of the polling rate limit. See
[Stock & price check](#stock--price-check) below.

## Requirements

- [Docker](https://www.docker.com/) with Docker Compose
- A [Grizzly SMS](https://grizzlysms.com/) API key
- An [ntfy](https://ntfy.sh/) topic URL

ntfy is a simple notification app. This bot uses it to send a push notification
when Grizzly SMS returns a phone number.

## Quick Start

```bash
cp .env.example .env
```

Edit `.env` with your own values:

```env
GRIZZLY_API_KEY=your_grizzly_api_key
SERVICE=wx
COUNTRY=62
MAX_PRICE=2
PROVIDER_IDS=311

NTFY_URL=https://ntfy.sh/your-topic

THREADS=10
MAX_REQUESTS_PER_SECOND=5
REQUEST_TIMEOUT_SECONDS=10
STATUS_EVERY_REQUESTS=100
LOG_LEVEL=INFO
```

By default, this example targets **Apple** with `SERVICE=wx` and **Turkey** with
`COUNTRY=62`. You can verify service and country codes in the Grizzly SMS
[API documentation](https://grizzlysms.com/docs-old), the
[Apple service page](https://grizzlysms.com/apple), and the
[price/country table](https://grizzlysms.com/price).

Start the bot:

```bash
docker compose up -d --build
```

Watch logs:

```bash
docker compose logs -f --tail=100
```

Stop the bot:

```bash
docker compose down
```

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `GRIZZLY_API_KEY` | yes | Your Grizzly SMS API key. |
| `SERVICE` | yes | Grizzly service code. The example `wx` is Apple. See the [API docs](https://grizzlysms.com/docs-old) and [Apple page](https://grizzlysms.com/apple). |
| `COUNTRY` | yes | Grizzly country code. The example `62` is Turkey. See the [API docs](https://grizzlysms.com/docs-old) and [price/country table](https://grizzlysms.com/price). |
| `MAX_PRICE` | yes | Maximum price accepted by Grizzly SMS. |
| `PROVIDER_IDS` | no | Comma-separated provider IDs. Leave empty to omit `providerIds`. |
| `NTFY_URL` | yes | ntfy topic URL used for notifications. |
| `THREADS` | yes | Number of worker threads. |
| `MAX_REQUESTS_PER_SECOND` | yes | Global request start limit shared by all workers. |
| `REQUEST_TIMEOUT_SECONDS` | yes | HTTP timeout for Grizzly and ntfy requests. |
| `STATUS_EVERY_REQUESTS` | no | Log a progress message every N requests. Defaults to `100`. |
| `LOG_LEVEL` | no | Python logging level, for example `INFO` or `DEBUG`. |
| `PRICE_CHECK_INTERVAL_SECONDS` | no | How often to re-check stock/price for `SERVICE`+`COUNTRY`. Defaults to `300` (5 minutes), minimum `10`. |
| `GRIZZLY_API_URL` | no | Override the Grizzly API endpoint. Mostly useful for debugging. |

## Logs

At `LOG_LEVEL=INFO`, the bot logs startup, ntfy connectivity, progress, errors,
and acquired numbers.

Example:

```text
startup service=wx country=62 maxPrice=2 providerIds=311 workers=10 limit=5.0/s
ntfy test: OK
still polling requests=100 no_numbers=100 acquired=0
still polling requests=200 no_numbers=200 acquired=0
number acquired worker=4 activation=123456 number=33612345678
notification sent activation=123456
```

## Provider IDs

`PROVIDER_IDS` is optional.

Use a provider:

```env
PROVIDER_IDS=311
```

Use multiple providers:

```env
PROVIDER_IDS=311,312
```

Do not send `providerIds` at all:

```env
PROVIDER_IDS=
```

## Stock & price check

The bot shows how many numbers are available and at what price for the configured
`SERVICE`+`COUNTRY`, using Grizzly's
[`getPricesV3`](https://grizzlysms.com/docs/activation#activation-api-getPrices-v3)
action. This is a separate, free lookup — it does not reserve a number or spend
balance, unlike `getNumber`.

It runs:

- **Once at startup**, logged and included in the startup ntfy notification.
- **Every `PRICE_CHECK_INTERVAL_SECONDS`** (default 300s) in the background, logged only.

Example log line:

```text
service=wx country=62 available=1307 price=2 providers=[311:820@2, 312:487@2.5/3]
```

`available` and `price` are the totals Grizzly reports for the service+country pair.
`providers` breaks the same numbers down per provider ID — useful together with
`PROVIDER_IDS`, since a provider can be in stock even when the total looks low, or vice
versa. A provider's `price` can list more than one value; Grizzly returns a short list
of price tiers per provider rather than a single number.

The startup notification also tries to show human-readable service/country names (via
`getServicesList`/`getCountries`) instead of just the codes. This is best-effort: if
Grizzly's response for either lookup doesn't parse, the bot falls back to the raw codes
and logs the reason at `DEBUG` — it never blocks the stock check itself.

If Grizzly ever changes what these codes mean or price data isn't available for your
account, `getPricesV3` returns `BAD_KEY`, `BAD_ACTION`, or `BAD_SERVICE`; the bot logs
that exact reason rather than a generic failure.

## Tuning

Start with conservative values:

```env
THREADS=5
MAX_REQUESTS_PER_SECOND=2
```

Increase them slowly if your machine, network, and Grizzly SMS account can handle
it. If you receive HTTP errors, lower `THREADS` or `MAX_REQUESTS_PER_SECOND`.




## Running Without Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a
source .env
set +a
python bot.py
```
