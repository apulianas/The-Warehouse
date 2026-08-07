# Orioles Discord Bot

A Dockerized Python 3.12 Discord bot that posts Baltimore Orioles lineups and roster transactions from the public MLB Stats API.

## Features

- Slash commands:
  - `/lineup [date]` — scheduled Orioles games, probable/starting pitcher, and batting order when MLB has posted it.
  - `/transactions [date]` — Orioles roster transactions for a date.
  - `/help` — command help.
- Discord embeds with game status, venue, score when available, batting order, positions, pitcher, transaction details, and clickable MLB headshot links.
- Opposing batting orders are included in the same embed as a clickable Discord spoiler.
- Background polling for today's lineup and transaction updates.
- Duplicate announcement prevention across restarts using `/data/state.json`.
- Graceful empty states when there is no game, lineup, or transaction data.

## Setup

1. Create a Discord application and bot at <https://discord.com/developers/applications>.
2. Copy `.env.example` to `.env`.
3. Set `DISCORD_TOKEN`.
4. Optionally set `DISCORD_CHANNEL_ID` to enable background update posts.
5. Start the bot:

```bash
docker compose up --build
```

The compose file mounts a named volume at `/data` so announced lineup and transaction IDs survive container restarts.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | | Discord bot token. Never commit it. |
| `DISCORD_CHANNEL_ID` | No | | Channel ID for background polling announcements. |
| `POLL_INTERVAL_SECONDS` | No | `300` | Poll interval for today's updates. Minimum 30 seconds. |
| `TIME_ZONE` | No | `America/New_York` | Time zone used for "today" and display times. |

## Discord permissions and intents

Use the OAuth2 URL generator in the Discord developer portal:

- Scopes: `bot`, `applications.commands`
- Bot permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`, and optionally `Read Message History`.
- Privileged gateway intents are not required. The bot uses default intents only.

After inviting the bot, slash commands are synced globally when the bot starts. Discord can take several minutes to make new global commands visible.

## Command examples

```text
/lineup
/lineup date:2026-08-06
/transactions
/transactions date:2026-08-06
/help
```

Representative `/lineup` embed:

```text
Baltimore Orioles at New York Yankees
Thu, Aug 6 at 7:05 PM EDT • Yankee Stadium
Status: Pre-Game

Pitcher
Probable pitcher: Zach Eflin

Batting order
1. SS Gunnar Henderson
2. C Adley Rutschman
3. RF Anthony Santander
...
```

The opposing team's lineup appears below this as a spoiler that can be clicked to reveal.

If MLB has not posted a lineup yet:

```text
Batting order
Lineup has not been posted yet.
```

Representative `/transactions` embed:

```text
Orioles transactions — August 6, 2026

2026-08-06
Recalled — Example Player: Baltimore Orioles recalled Example Player from Norfolk Tides.
```

If no transactions are returned:

```text
No Orioles roster transactions found for Thursday, August 6, 2026.
```

## Development

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run checks:

```bash
python -m pytest
python -m compileall orioles_bot tests
```

Tests do not call the network.

## Data source

This project uses the public MLB Stats API at `https://statsapi.mlb.com/api/v1` and does not require MLB API keys.
