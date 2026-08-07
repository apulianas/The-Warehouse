# Privacy Policy

**Effective date:** August 6, 2026
**Last updated:** August 6, 2026

This policy explains how the Orioles Discord bot ("the Bot") handles data. The
source code lives at <https://github.com/apulianas/The-Warehouse> and can be
independently reviewed to verify everything described here.

In this policy, "the Operator" means the person or group running the instance of
the Bot that is present in your Discord server. The Bot is self-hosted, so it
runs on infrastructure controlled by the Operator rather than a central service.

## Summary

The Bot does not collect, store, or share personal data. It cannot read the
contents of your messages. The only thing it writes to disk is a list of which
baseball updates it has already posted, so it does not post them twice.

## What the Bot does not do

The Bot **cannot** read the contents of your messages. It runs with Discord's
default gateway intents and does **not** request the privileged Message Content
intent or the Server Members intent, so Discord does not deliver message content
to it. The Bot also registers no message handler, so it does not act on messages
at all — it responds only to its own slash commands.

The Bot also does not:

- Collect or store your Discord username, user ID, avatar, or email address.
- Log, read, or store the content of any message.
- Track which users run which commands.
- Build user profiles, or perform analytics, advertising, or behavioral tracking.
- Sell, rent, or share any data with third parties.
- Use cookies or any similar web tracking technology.

## What the Bot stores

The Bot maintains one state file (`state.json`) on the Operator's own
infrastructure. It contains only a list of announcement keys, each made up of:

- A date, an MLB game or transaction identifier, and MLB player identifiers.
- The Discord **channel** ID that the update was posted to.

This exists solely so a restart does not repost the same lineup or transaction.
It contains no Discord user IDs, no usernames, and no message content. A
representative entry looks like:

```
transaction:2026-08-06:860371@123456789012345678
```

Discord channel IDs and the bot token are supplied by the Operator as
configuration. The bot token is a credential belonging to the Operator, not
personal data about you.

## Information processed but not retained

When you run a slash command, Discord sends the Bot an interaction containing
the command name and any options you provided, so it can reply. This is held in
memory only for as long as it takes to respond, and is not written to disk.

Command responses are sent as **ephemeral** messages, meaning they are visible
only to the person who ran the command.

The Operator's hosting environment may produce operational logs, for example a
warning that a configured channel could not be reached. These are diagnostic
messages about the Bot's own operation and are not used to track individuals.

## Third-party services

To fetch baseball information and images, the Bot makes outbound requests to:

| Service | Purpose |
| --- | --- |
| `statsapi.mlb.com` | Schedules, lineups, probable pitchers, and roster transactions |
| `baseballsavant.mlb.com` | Batter-versus-pitcher matchup statistics |
| `img.mlbstatic.com` | Player headshot images |
| `midfield.mlbstatic.com` | Team logo images |

These requests are made by the Bot's server and contain **no information about
you or your Discord server**. They ask only for public baseball data.

Image URLs are embedded in the messages the Bot posts. As with any image in
Discord, your Discord client loads them, and Discord's own media proxy is
typically involved. That behavior is governed by
[Discord's Privacy Policy](https://discord.com/privacy).

The Bot also necessarily interacts with Discord itself to receive commands and
send messages, which is governed by Discord's Privacy Policy.

## Data retention and deletion

The state file persists until the Operator deletes it. Because it contains no
personal data, there is nothing tied to an individual user to delete.

A server administrator can stop all data flow at any time by removing the Bot
from the server. The Operator can erase stored state entirely by deleting the
Bot's data volume:

```bash
docker compose down -v
```

## Children's privacy

The Bot is not directed at children under 13 and does not knowingly collect
information from them. Discord's own terms require users to meet a minimum age.

## Security

State is stored on infrastructure controlled by the Operator. Because no
personal data is collected, a compromise of the state file would not expose
information about users. No method of storage or transmission is completely
secure, and no absolute guarantee of security can be given.

## International users

The Bot is operated from the United States. Because it does not collect personal
data, no personal data is transferred internationally by the Bot.

## Your rights

Privacy laws such as the GDPR and CCPA give individuals rights to access,
correct, and delete their personal data. The Bot does not collect personal data,
so there is generally nothing to access, correct, or delete. Requests can still
be submitted using the contact method below.

## Changes to this policy

This policy may be updated from time to time. Material changes will be reflected
by an updated "Last updated" date above, and the full revision history is
publicly visible in the repository's Git history.

## Contact

Questions about this policy can be raised as an issue at
<https://github.com/apulianas/The-Warehouse/issues>.
