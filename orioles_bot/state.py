from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)

CHANNEL_KEY_SEPARATOR = "@"


def channel_key(key: str, target: int | str) -> str:
    return f"{key}{CHANNEL_KEY_SEPARATOR}{target}"


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
