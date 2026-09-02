# Brawl Stars State (brawlpro)

A self-hosted Brawl Stars statistics platform built on the official API:
per-player analytics, a global leaderboard tracker, cross-player meta
analytics, a generated player card, and a Telegram bot — deployed as a
single Docker Compose stack (FastAPI + PostgreSQL + Redis).

## Features

- **Player analytics** — win rate, streaks, recent form, per-brawler and
  per-mode breakdowns, trophy progress, favorite brawler/mode, and a
  performance score. Every value is tagged `api`, `derived`, or `estimate`,
  and low-sample statistics carry an explicit confidence level.
- **Global & local rank** — exact rank for anyone on the API-visible
  top-200 (the deepest rankings the API publishes), a labeled estimate
  just below the cutoff, and an honest "unknown" beyond it. Local
  (per-country) ranks resolve the same way from any country board this
  instance has collected.
- **Leaderboard tracker** — polls the rankings on a schedule, maintains a
  live membership table, logs every entry/exit as an auditable event, and
  keeps full snapshots for trend analysis. Any country can also be fetched
  on demand.
- **Meta analytics** — per-brawler pick rate, win rate, and average
  trophies aggregated in SQL across all stored battles, with mode/map
  filters, time-window trends, and most-popular/underrated/overpicked
  tiers.
- **Player Card** — a Pillow-rendered PNG card in three sizes
  (`telegram` / `square` / `desktop`), with icons fetched from a
  configurable CDN.
- **Telegram bot** — slash commands, database-backed keyword triggers
  manageable at runtime, and a linked-identity shortcut so regular users
  never retype their tag.

## Architecture

```
Brawl Stars API ──> api_client (rate-limited, cached, retried)
                          │
                          ▼
                  database (Postgres)
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
   analytics.py       meta.py          leaderboard.py
        └─────────────────┼──────────────────┘
                          ▼
                 services/player_service
                    │              │
                    ▼              ▼
              HTTP API (FastAPI)  Telegram bot
                          │
                          ▼
                   Card generator (Pillow)
```

Handlers (HTTP routes, bot commands) contain no analytics or data-access
logic of their own — both front ends call the same shared modules, so
their output can never drift apart.

## Requirements

- Docker and Docker Compose. A Linux VPS with a public IPv4 is the usual
  target — the Brawl Stars API locks each key to a single IP.
- A Brawl Stars API token: <https://developer.brawlstars.com>
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Quick start

```bash
# rename env-exmaple tp .env and:
# edit .env: BRAWL_STARS_API_TOKEN, TELEGRAM_BOT_TOKEN,
#            POSTGRES_PASSWORD, SECRET_KEY
docker compose up -d --build
```

Create the API token with the server's public IP (`curl ifconfig.me`)
entered as the allowed IP; the token must be reissued if that IP changes. surely

## Configuration

All settings come from environment variables via `.env` (see
`.env.example`):

| Variable | Purpose |
|---|---|
| `BRAWL_STARS_API_TOKEN` | API token, allowlisted to the server IP |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_BOT_OWNER_ID` | Numeric Telegram user ID; enables owner-only (eg; 698634429 is mine) `/addtrigger` / `/removetrigger` |
| `POSTGRES_PASSWORD` | Password for the PostgreSQL container (used by compose) |
| `SECRET_KEY` | Random string, reserved for the future admin panel |
| `LEADERBOARD_COUNTRIES` | Comma-separated boards the scheduler tracks (default `global`) |
| `LEADERBOARD_TOP_N` | Players tracked per board (200 = the API maximum) |
| `LEADERBOARD_COLLECT_INTERVAL_MINUTES` | Collection interval (default 30) |
| `*_CACHE_TTL`, `API_MAX_REQUESTS_PER_SECOND` | Cache and rate-limit tuning |
| `BRAWLER_ICON_URL_TEMPLATE`, `PLAYER_ICON_URL_TEMPLATE` | Icon CDN templates (Brawlify by default) |

`DATABASE_URL` / `REDIS_URL` are injected by compose; override them to run
outside Docker.

## Usage

### HTTP API

| Endpoint | Description |
|---|---|
| `GET /health` | Database/Redis connectivity check |
| `GET /player/{tag}` | Full analytics payload, including the `rank` block |
| `GET /player/{tag}/rank` | Global + local rank |
| `GET /player/{tag}/card?variant=` | Player Card PNG |
| `GET /leaderboard/{country}/top1000` | Tracked leaderboard |
| `GET /leaderboard/{country}/changes` | Entry/exit audit log |
| `GET /leaderboard/{country}/stats` | Aggregate stats for the tracked pool |
| `POST /leaderboard/{country}/collect` | Trigger a collection run now |
| `GET /meta/brawlers?mode=&map=` | Per-brawler pick/win rates |
| `GET /meta/brawlers/{name}/breakdown` | Per (mode, map) breakdown |
| `GET /meta/trending` | Pick/win-rate deltas between two time windows |
| `GET /meta/tiers` | Most-popular / underrated / overpicked |

Tags use the in-game form with `#`, URL-encoded as `%23`.

### Telegram bot

| Command | Description |
|---|---|
| `/link <tag>` | Save your tag; `/player`, `/rate`, `/brawlers`, `/card` then work without it |
| `/player [tag]` · `/rate [tag]` | Full stats summary / win rate |
| `/rank [tag]` | Global + local rank |
| `/brawlers [tag]` | Top brawlers |
| `/card [tag] [variant]` | Graphical card |
| `/leaderboard [country] [limit]` | Top players; any 2-letter country code works (fetched on demand) |
| `/top1000stats [country]` | Tracked-pool aggregate stats (I'll remove this) |
| `/meta` · `/help` | Meta tiers / command list |

Plain words work as triggers in DMs (`rate`, `rank`, `meta`, …); the
default set is seeded on first start and editable at runtime with
`/addtrigger` / `/removetrigger` (owner only). In groups the bot answers
only when replied to or @mentioned.

## Project structure

```
app/
  main.py             FastAPI app; starts the bot and scheduler
  config.py           environment-driven settings
  api_client.py       Brawl Stars API client (rate-limited, cached, retried)
  database.py         SQLAlchemy models (async Postgres)
  ingest.py           raw API JSON -> rows; battle deduplication
  battle_outcome.py   shared win/loss classification
  stats_utils.py      Stat/Confidence provenance primitives
  analytics.py        per-player analytics engine
  meta.py             cross-player meta engine
  leaderboard.py      tracker: collection, membership, snapshots, stats
  scheduler.py        background collection interval
  serialize.py        Stat -> JSON helper
  services/           shared player pipeline (API + bot both call it)
  bot/                handlers, formatting, triggers, runner
  card/               schema, theme, layout, icons, fonts, assets, generator
docker-compose.yml    app + PostgreSQL + Redis
Dockerfile            application container
requirements.txt      Python dependencies
.env.example          environment variable template
```

## Design notes

- **Honest data.** Every stat carries its provenance (`api` / `derived` /
  `estimate`). When the underlying data is insufficient the value is
  `None` with an explanatory note — estimates are never dressed up as
  facts. The rank feature is the clearest example: exact for the visible
  top-200, a short-range estimate near the cutoff, and a plain "unknown"
  below it.
- **One pipeline, two front ends.** The HTTP API and the bot share the
  same service modules, so their answers always agree.

## Limitations

- The official rankings API serves **only the top 200 players** per board
  (deeper position cursors return empty pages). The tracker therefore
  stores at most 200 players, and anything beyond #200 is a labeled
  estimate or unknown. "top1000" in endpoint and command names is legacy
  wording — 200 is the real maximum.
- The API does not expose a player's country, so local ranks resolve
  exactly only for players who appear on a collected country board (via
  `/leaderboard <country>` in the bot, or `LEADERBOARD_COUNTRIES`).
- Schema changes rely on `Base.metadata.create_all`, which creates missing
  tables but never alters existing ones; switch to Alembic once the schema
  stabilizes.
- Meta analytics cover only the players looked up through this instance,
  not a uniform sample of the player base.
- Icons come from Brawlify (community CDN) because the official API
  exposes only numeric icon IDs; the CDN is configurable via templates.

## Roadmap

- Web dashboard
- Admin panel for runtime configuration without redeployment

