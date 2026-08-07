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


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.delenv("MATCHUP_MIN_PA", raising=False)
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("TIME_ZONE", raising=False)


def test_load_config_reads_single_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")

    config = load_config()

    assert config.discord_channel_ids == (123,)
    assert config.discord_channel_id == 123


def test_load_config_reads_multiple_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123, 456 , 789")

    config = load_config()

    assert config.discord_channel_ids == (123, 456, 789)


def test_load_config_deduplicates_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123,123,456")

    config = load_config()

    assert config.discord_channel_ids == (123, 456)


def test_load_config_without_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)

    config = load_config()

    assert config.discord_channel_ids == ()
    assert config.discord_channel_id is None


def test_load_config_rejects_invalid_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123,not-a-channel")

    with pytest.raises(ValueError, match="DISCORD_CHANNEL_ID must be a channel id"):
        load_config()
