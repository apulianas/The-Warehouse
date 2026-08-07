# Orioles Discord Bot

A Dockerized Python 3.12 Discord bot that posts Baltimore Orioles lineups and roster transactions from the public MLB Stats API.

## Features

- Slash commands:
  - `/lineup [date]` — scheduled Orioles games, probable/starting pitcher, and batting order when MLB has posted it.
  - `/transactions [date]` — Orioles roster transactions for a date.
  - `/help` — command help.
- Discord embeds with game status, venue, score when available, both batting orders, positions, both starting pitchers, transaction details, and hot/cold matchup emojis with the underlying wOBA and plate appearances when enough history exists.
- Every player name — batters, both starters, and everyone named in a transaction — links to their Baseball Savant player page. Multi-player trades link all sides, not just the headliner, and post as a single entry instead of once per player.
- Each batting order links to the Baseball Savant Player Matchup page for that lineup versus the opposing starter, showing every hitter's career history against him.
- Each lineup embed also links to the Statcast game preview for that game.
- Batter-versus-pitcher summaries are fetched from Baseball Savant and rendered inline, so the hot/cold read is visible without leaving Discord.
- Both batting orders render as single 1-9 lists in one embed.
- The embed thumbnail shows the home team's logo for the ballpark hosting the game.
- Background polling for today's lineup and transaction updates.
- Automatic lineup posts wait until both teams' batting orders are available.
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

### Posting to more than one channel

Set `DISCORD_CHANNEL_ID` to a comma-separated list, then restart:

```env
DISCORD_CHANNEL_ID=123456789012345678,987654321098765432
```

```bash
docker compose up -d
```

Each channel is tracked separately, so a channel you add later starts posting from the
next update rather than replaying everything the first channel already announced, and a
channel the bot cannot reach is retried instead of being silently marked as sent. The bot
needs View Channel, Send Messages, and Embed Links in every channel you list.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | | Discord bot token. Never commit it. |
| `DISCORD_CHANNEL_ID` | No | | Channel ID for background polling announcements. Separate multiple IDs with commas to post the same updates to several channels. |
| `POLL_INTERVAL_SECONDS` | No | `300` | Poll interval for today's updates. Minimum 30 seconds. |
| `MATCHUP_MIN_PA` | No | `5` | Minimum historical plate appearances versus the opposing starter before a hot/cold emoji is shown. |
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
Baltimore Orioles starter: Zach Eflin (Probable pitcher)
New York Yankees starter: Max Fried (Probable pitcher)

Baltimore Orioles batting order — full matchup vs Max Fried
1. SS Gunnar Henderson 🔥 (.450 wOBA, 8 PA)
2. C Adley Rutschman
3. RF Anthony Santander
...

New York Yankees batting order — full matchup vs Zach Eflin
1. RF Aaron Judge 🧊 (.267 wOBA, 24 PA)
2. DH Giancarlo Stanton
...

Statcast game preview
```

Both lineups appear in the same embed, every name links to Baseball Savant, and the thumbnail shows the home team's logo.

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

This project uses the public MLB Stats API at `https://statsapi.mlb.com/api/v1` and Baseball Savant Statcast matchup data. It does not require MLB API keys.
