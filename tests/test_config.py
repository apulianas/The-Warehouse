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
    monkeypatch.delenv("SUBSTITUTION_CHANNEL_ID", raising=False)
    monkeypatch.delenv("SUBSTITUTION_WEBHOOK_URL", raising=False)


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


URL_A = "https://discord.com/api/webhooks/12345678901234567/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
URL_B = "https://discord.com/api/webhooks/98765432109876543/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_load_config_reads_webhook_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", f"{URL_A},{URL_B}")

    config = load_config()

    assert config.discord_webhook_urls == (URL_A, URL_B)
    assert config.has_announcement_targets


def test_load_config_rejects_non_webhook_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://example.com/hook")

    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL must contain"):
        load_config()


def test_has_announcement_targets_false_without_any(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    assert not load_config().has_announcement_targets


def test_load_config_rejects_webhook_url_missing_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated copy/paste must fail at startup, not silently at post time."""
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.setenv(
        "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/12345678901234567"
    )

    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL must contain"):
        load_config()


def test_webhook_urls_accepted_by_discord_py(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything our config accepts must also parse in discord.Webhook.from_url."""
    import discord

    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", URL_A)

    for url in load_config().discord_webhook_urls:
        assert discord.Webhook.from_url(url, session=None).id == 12345678901234567


def test_substitution_targets_default_to_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")

    config = load_config()

    assert config.substitution_channel_ids == ()
    assert config.substitution_webhook_urls == ()
    assert not config.has_substitution_targets


def test_load_config_reads_substitution_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
    monkeypatch.setenv("SUBSTITUTION_CHANNEL_ID", "456, 789")
    monkeypatch.setenv("SUBSTITUTION_WEBHOOK_URL", URL_A)

    config = load_config()

    assert config.discord_channel_ids == (123,)
    assert config.substitution_channel_ids == (456, 789)
    assert config.substitution_webhook_urls == (URL_A,)
    assert config.has_substitution_targets


def test_load_config_rejects_invalid_substitution_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("SUBSTITUTION_CHANNEL_ID", "not-a-channel")

    with pytest.raises(
        ValueError, match="SUBSTITUTION_CHANNEL_ID must be a channel id"
    ):
        load_config()


def test_load_config_rejects_invalid_substitution_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("SUBSTITUTION_WEBHOOK_URL", "https://example.com/hook")

    with pytest.raises(ValueError, match="SUBSTITUTION_WEBHOOK_URL must contain"):
        load_config()


def test_substitution_only_setup_still_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Polling has to run even if substitutions are the only destination."""
    _base_env(monkeypatch)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("SUBSTITUTION_CHANNEL_ID", "456")

    assert load_config().has_announcement_targets
