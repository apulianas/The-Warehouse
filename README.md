# Orioles Discord Bot

A Dockerized Python 3.12 Discord bot that posts Baltimore Orioles lineups, roster transactions, player stats, standings, and schedules from the public MLB Stats API.

## Features

- Slash commands:
  - `/lineup [date]` — scheduled Orioles games, probable/starting pitcher, and batting order when MLB has posted it.
  - `/transactions [date]` — Orioles roster transactions for a date.
  - `/playerstats <player> [days]` — a player's hitting and pitching totals over the last N days.
  - `/standings [view]` — the AL wild card race and the AL East, each with next opponents.
  - `/schedule [days]` — upcoming Orioles games over the next N days.
  - `/bullpen` — which relievers are available, judged from their recent usage.
  - `/ondeck` — who is at bat, on deck, and in the hole in the game being played now.
  - `/pitchmix [pitcher]` — a pitcher's pitch usage in the current or last game, with today's speeds against his season averages.
  - `/injuries` — the current injured list, with dates, injuries, and rehab assignments.
  - `/help` — command help.
- Discord embeds with game status, venue, score when available, both batting orders, positions, both starting pitchers labelled RHP or LHP, transaction details, and hot/cold matchup emojis with the underlying wOBA and plate appearances when enough history exists.
- Every player name — batters, both starters, and everyone named in a transaction — links to their Baseball Savant player page. Multi-player trades link all sides, not just the headliner, and post as a single entry instead of once per player.
- Roster moves found in the same check post as a single message, so a matched set — an option out and the recall it pays for — arrives as one announcement rather than one per move. The card is sorted into "Joining the roster", "Leaving the roster", and "Other moves" for trades, since MLB's feed interleaves them.
- A lone move keeps a thumbnail rather than a full-width headshot, so no roster post fills a phone screen. When several players join at once, each gets his own card with a thumbnail, so no arriving player goes unpictured.
- Each batting order links to the Baseball Savant Player Matchup page for that lineup versus the opposing starter, showing every hitter's career history against him.
- Each lineup embed also links to the Statcast game preview for that game.
- Batter-versus-pitcher summaries are fetched from Baseball Savant and rendered inline, so the hot/cold read is visible without leaving Discord.
- Both batting orders render as single 1-9 lists in one embed.
- The embed thumbnail shows the home team's logo for the ballpark hosting the game.
- `/playerstats` resolves a name through an autocomplete dropdown of the active Orioles
  roster, and falls back to a league-wide search so any big leaguer can be looked up by
  full name. The window defaults to the last 7 days and accepts 1 through 162.
- Rolling stats and the roster are cached in memory, so repeated lookups in a busy
  channel do not re-query the API.
- `/standings` shows each AL East team's record, games back, streak, and clinch marker,
  with the Orioles row bolded. Every row is annotated with that team's next opponent and
  start time, resolved for the whole division in a single schedule request. The footer
  summarises the Orioles' division rank, wild-card position, and run differential.
- `/standings` also shows the full AL wild card race — all twelve teams that do not lead
  a division, ranked, with a drawn playoff line after the third and final berth. Teams
  holding a spot show how many games they are up on the line; everyone else shows games
  back. The view defaults to both tables and accepts `Wild card` or `AL East` to show one.
- `/schedule` lists upcoming games with opponent, start time, and both probable starters.
  The window defaults to the next 7 days and accepts 1 through 30. A game that has already
  finished shows its final score instead of probable starters.
- `/ondeck` reads the live linescore for the game in progress and names the batter, the
  hitter on deck, and the one in the hole, alongside the half inning, the count, the outs,
  the runners on base, and the pitcher facing them, plus each of the three hitters'
  career line against that pitcher. Every name links to Baseball Savant.
  On a doubleheader it follows the game actually underway, and when no game is live it
  says so and points at `/lineup`.
- `/bullpen` grades every reliever on the active roster from his game log: pitching
  today or on back-to-back days reads as unavailable, heavy work the day before as a
  caution, and anything else as available, with the days of rest and the innings, pitch
  count, and batters faced of each recent outing shown underneath. Rotation arms are left
  out, judged by how a pitcher has been used over the last month rather than by any single
  appearance. MLB publishes no availability list, so this is an inference from usage, not
  an official status. The card is cached for a few minutes, since it costs one request per
  pitcher.
- `/pitchmix` breaks one outing down by pitch type: how many of each pitch was
  thrown, each as a whole-percent share of the pitch count, and the average
  speed today beside the same pitch's season average and the gap between them.
  It reads the game's play-by-play, which is the only public feed carrying a
  pitch type and a release speed per pitch, and defaults to the Orioles arm most
  recently on the mound — the pitcher working now, or the last one to work when
  the game is over. Name any pitcher to read his outing instead. When today has
  no game underway or finished, last night's is used, so a card pulled up in the
  morning still describes the start that just happened. Pitches MLB leaves
  untyped are bucketed rather than dropped, so the shares always describe the
  whole pitch count, and the whole-percent shares are rounded to total exactly
  100. A pitcher with no tracked season arsenal keeps his pitch mix and loses
  only the speed comparison.
- `/injuries` lists everyone on the injured list, grouped by which list they are on:
  the day the stint started, the announcement date when the placement was backdated, how
  many days he has been out, the injury when MLB names it in the transaction wording, the
  most recent roster move naming him, and any rehab assignment with the affiliate, the day
  it began, how many rehab games he has played, and the date of the last one. The Stats
  API publishes no injury report, so everything past the roster status is read back out of
  the team's transaction feed and the rehabbing player's minor league game log. The card is
  cached for fifteen minutes.
- Standings and schedules are served from a short-lived TTL cache that collapses
  concurrent lookups into one request, so a burst of commands does not multiply API calls.
- Background polling for today's lineup and transaction updates.
- Polling adapts to the schedule: faster in the hours before first pitch, faster
  again once the game starts, and back to the idle baseline in between.
  Postponed and cancelled games are treated as idle.
- A game still being played after local midnight is followed to the last out.
  MLB files a game under the date it started on, so a late West Coast start
  that runs into extra innings would otherwise drop out of the poll the moment
  the date rolled over, silently taking every substitution after midnight with
  it. The previous day is only re-checked in the small hours, and only while
  something from it is genuinely live.
- Each game of a doubleheader is tracked on its own. Both games share a date,
  so cards are identified by game instead — the two never share a lineup post
  or swallow each other's substitutions, even when the same player pinch hits
  in the same lineup slot twice.
- Automatic lineup posts wait until both teams' batting orders are available.
- The full lineup is posted once. Later changes arrive as a compact substitution
  card instead of reposting the whole batting order.
- Substitution cards match the role the player entered in. A pinch hitter or
  defensive replacement gets his history against the pitcher on the mound, his
  season splits versus that hand, and the same split over the last 14 days, so
  a slump the season line has absorbed still shows; a pinch runner gets his
  stolen base record and Statcast sprint speed, since he is not coming up to bat.
- Substitution cards can be routed to their own channel with
  `SUBSTITUTION_CHANNEL_ID`, keeping the daily lineup card where it is.
- Duplicate announcement prevention across restarts using `/data/state.json`.
- Announcements are posted as embeds alone. Every card titles itself, so no line
  of message text repeats it above the card.
- Graceful empty states when there is no game, lineup, transaction, standings, or
  schedule data.

## Setup

1. Create a Discord application and bot at <https://discord.com/developers/applications>.
2. Copy `.env.example` to `.env`.
3. Set `DISCORD_TOKEN`.
4. Optionally set `DISCORD_CHANNEL_ID` (and/or `DISCORD_WEBHOOK_URL`) to enable background update posts.
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

### Keeping substitutions out of the lineup channel

Substitutions land constantly once a game starts, which can bury the daily
lineup card. Point them at their own channel and the lineup and transaction
posts stay where they are:

```env
DISCORD_CHANNEL_ID=123456789012345678
SUBSTITUTION_CHANNEL_ID=987654321098765432
```

```bash
docker compose up -d
```

`SUBSTITUTION_WEBHOOK_URL` does the same thing for a server the bot is not
installed in, and both accept comma-separated lists. Leave both unset and
substitutions keep going to the lineup channel, as before.

### Posting to a server you cannot add the bot to

Adding a bot to a server requires the **Manage Server** permission, so a server
you are only a member of will not appear in the install dropdown. A webhook is
the way around this: an admin of that server creates one for you, and the bot
posts through it without ever joining.

Ask an admin for a webhook URL:

> Server Settings → Integrations → Webhooks → New Webhook → choose the channel →
> Copy Webhook URL

Then add it and restart:

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/123456789012345678/your-webhook-token
```

```bash
docker compose up -d
```

Webhooks can be combined with `DISCORD_CHANNEL_ID`, and several can be listed
with commas. Each is deduplicated separately, exactly like channels.

Two things to know about the webhook path:

- Posts appear under the webhook's own name and avatar, not the bot's.
- Slash commands such as `/lineup` will **not** work in that server, because
  those require the bot to actually be installed. Only the automatic lineup and
  transaction posts are delivered.

A webhook URL ends in a secret token that lets anyone post to that channel, so
keep it in `.env` and out of source control. Only the webhook's numeric ID is
ever written to the state file or the logs. `DISCORD_TOKEN` is still required
even in a webhook-only setup, because the bot needs a Discord session to run.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | | Discord bot token. Never commit it. |
| `DISCORD_CHANNEL_ID` | No | | Channel ID for background polling announcements. Separate multiple IDs with commas to post the same updates to several channels. |
| `DISCORD_WEBHOOK_URL` | No | | Webhook URL for posting into a server where the bot is not installed. Separate multiple URLs with commas. Contains a secret token; keep it out of source control. |
| `SUBSTITUTION_CHANNEL_ID` | No | | Channel ID for in-game substitution cards. Separate multiple IDs with commas. When neither this nor `SUBSTITUTION_WEBHOOK_URL` is set, substitutions go to the same targets as lineups. |
| `SUBSTITUTION_WEBHOOK_URL` | No | | Webhook URL for in-game substitution cards. Separate multiple URLs with commas. Contains a secret token; keep it out of source control. |
| `POLL_INTERVAL_SECONDS` | No | `300` | Idle poll interval, used when no game is near. Minimum 30 seconds. |
| `LIVE_POLL_INTERVAL_SECONDS` | No | `60` | Poll interval while a game is underway, including during a rain delay. Minimum 30 seconds. |
| `PREGAME_POLL_INTERVAL_SECONDS` | No | `120` | Poll interval in the run up to first pitch, when the lineup card is posted. Minimum 30 seconds. |
| `PREGAME_LEAD_MINUTES` | No | `240` | How long before first pitch the pre-game interval starts. |
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
/bullpen
/ondeck
/pitchmix
/pitchmix pitcher:Grayson Rodriguez
/injuries
/help
```

Representative `/lineup` embed:

```text
Baltimore Orioles at New York Yankees
Thu, Aug 6 at 7:05 PM EDT • Yankee Stadium
Status: Pre-Game
Baltimore Orioles starter: Zach Eflin (RHP, probable)
New York Yankees starter: Max Fried (LHP, probable)

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
Orioles transactions — August 10, 2026

Joining the roster
Recalled — Baltimore Orioles recalled RHP Cam Sanders from Norfolk Tides.

Leaving the roster
Optioned — Baltimore Orioles optioned LHP Cade Povich to Norfolk Tides.
Status Change — Baltimore Orioles placed RHP Zach Eflin on the 15-day
injured list retroactive to August 8, 2026.
```

If no transactions are returned:

```text
No Orioles roster transactions found for Thursday, August 6, 2026.
```

Representative `/pitchmix` embed:

```text
Grayson Rodriguez — pitch usage
Baltimore Orioles at New York Yankees
Grayson Rodriguez (P) — 87 pitches, 3 pitch types
Speeds compared with his 2026 season averages.

Pitch mix
Four-Seam Fastball — 45 (52%) · 96.4 mph (+0.9 vs season 95.5 mph)
Slider — 28 (32%) · 84.6 mph (-0.5 vs season 85.1 mph)
Changeup — 14 (16%) · 88.1 mph (no season average)
```

Representative `/injuries` embed:

```text
Orioles injured list

2 players on the injured list.

15-Day Injured List (1)
🏥 Grayson Rodriguez (P) — 15-Day Injured List
On the IL since Jun 18, 2026 (retroactive; announced Jun 20) — 55 days
Injury: Right elbow inflammation
Rehab assignment with Norfolk Tides since Aug 5 — 3 rehab games, last Aug 11
Latest: Aug 5: Baltimore Orioles sent RHP Grayson Rodriguez on a rehab
assignment to Norfolk Tides.

60-Day Injured List (1)
🏥 Kyle Bradish (P) — 60-Day Injured List
On the IL since Apr 1, 2026 — 133 days
Injury: Right elbow surgery
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

Tests do not call the network, and run on Windows, macOS and Linux alike. Dates
are rendered through `format_moment` rather than `strftime`'s `%-d` and `%-I`,
which are a glibc extension that raises `ValueError` on Windows — a test guards
against either creeping back in, so the suite stays runnable wherever the bot
is being maintained rather than only where it is deployed.

## Data source

This project uses the public MLB Stats API at `https://statsapi.mlb.com/api/v1` and Baseball Savant Statcast matchup data. It does not require MLB API keys.

## Legal

- [Terms of Service](TERMS_OF_SERVICE.md)
- [Privacy Policy](PRIVACY_POLICY.md)

These are the URLs to supply in the Discord developer portal under **General
Information**:

```
https://github.com/apulianas/The-Warehouse/blob/main/TERMS_OF_SERVICE.md
https://github.com/apulianas/The-Warehouse/blob/main/PRIVACY_POLICY.md
```

This project is not affiliated with, endorsed by, or sponsored by Major League
Baseball, the Baltimore Orioles, or Discord Inc. MLB and club trademarks and
copyrights are the property of their respective owners.
