from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

CHANNEL_KEY_SEPARATOR = "@"
# Lineup and substitution keys used to open with the date the poll was working.
# That broke once a game running past local midnight could be polled under two
# dates, so the date is migrated away rather than recomputed.
DATED_KEY_PREFIXES = ("lineup:", "substitution:")


def channel_key(key: str, target: int | str) -> str:
    return f"{key}{CHANNEL_KEY_SEPARATOR}{target}"


def undated_key(key: str) -> str | None:
    """An old lineup or substitution key with its leading date removed.

    Returns None when the key is not one of those, or does not carry a date,
    so a key already in the current form is left alone. The dashed form is
    required rather than anything `fromisoformat` accepts, since the segment
    in a current key is a game id and a bare run of digits would parse as a
    compact ISO date.
    """
    if not key.startswith(DATED_KEY_PREFIXES):
        return None
    prefix, _, rest = key.partition(":")
    segment, separator, remainder = rest.partition(":")
    if not separator or len(segment) != 10 or segment[4] != "-" or segment[7] != "-":
        return None
    try:
        date.fromisoformat(segment)
    except ValueError:
        return None
    return f"{prefix}:{remainder}"


class AnnouncementState:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._announced: set[str] = set()

    def load(self) -> None:
        try:
            if not self._path.exists():
                self._announced = set()
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw_announced = data.get("announced", []) if isinstance(data, dict) else []
            self._announced = {str(item) for item in raw_announced}
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Could not read announcement state; starting fresh: %s", exc)
            self._announced = set()

    def unseen(self, key: str) -> bool:
        return key not in self._announced

    def adopt_legacy_keys(self, channel_id: int) -> None:
        """Treat pre-multi-channel keys as already sent to the primary channel.

        Keys used to be channel agnostic. Without this, adding a second channel
        would make the original channel repost everything it had already sent.
        """
        migrated = {
            channel_key(key, channel_id)
            for key in self._announced
            if CHANNEL_KEY_SEPARATOR not in key
        }
        if not migrated - self._announced:
            return
        self._announced |= migrated
        self.save()

    def adopt_undated_keys(self) -> None:
        """Treat dated lineup and substitution keys as their undated form.

        Both used to open with the date the poll was working, which made a
        game polled either side of midnight look unannounced on the second
        pass. Migrating the existing keys rather than letting them lapse stops
        an upgrade mid-game from reposting the day's cards.
        """
        migrated = {
            undated
            for key in self._announced
            if (undated := undated_key(key)) is not None
        }
        if not migrated - self._announced:
            return
        self._announced |= migrated
        self.save()

    def mark(self, key: str) -> None:
        self._announced.add(key)
        self.save()

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {"announced": sorted(self._announced)}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("Could not write announcement state: %s", exc)
