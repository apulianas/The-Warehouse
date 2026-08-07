from __future__ import annotations

import pytest

from orioles_bot.config import load_config


def test_load_config_reads_matchup_min_pa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("MATCHUP_MIN_PA", "7")
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("TIME_ZONE", raising=False)

    config = load_config()

    assert config.matchup_min_pa == 7


def test_load_config_rejects_invalid_matchup_min_pa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("MATCHUP_MIN_PA", "0")

    with pytest.raises(ValueError, match="MATCHUP_MIN_PA must be at least 1"):
        load_config()
