from __future__ import annotations

import pytest

from config import Config, _parse_city_chat_map


def test_city_cash_chat_map_parses_named_cities(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv(
        "CITY_CASH_CHAT_IDS",
        "екб:-1003611459690, члб:-5218770251, тюм:-5075605056",
    )

    config = Config.from_env()

    assert config.city_cash_chat_map == {
        "екб": -1003611459690,
        "члб": -5218770251,
        "тюм": -5075605056,
    }
    assert config.city_cash_chat_ids == frozenset(
        {-1003611459690, -5218770251, -5075605056}
    )
    assert config.main_dashboard_source_mode == "sheets"


def test_dashboard_source_mode_is_validated(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("MAIN_DASHBOARD_SOURCE_MODE", "mixed")

    with pytest.raises(RuntimeError, match="MAIN_DASHBOARD_SOURCE_MODE"):
        Config.from_env()


def test_city_cash_chat_map_rejects_legacy_unnamed_list() -> None:
    with pytest.raises(RuntimeError, match="Некорректный формат CITY_CASH_CHAT_IDS"):
        _parse_city_chat_map(
            "-1003611459690,-5218770251,-5075605056",
            env_name="CITY_CASH_CHAT_IDS",
            unique_chat_ids=True,
        )


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("екб:-1001,екб:-1002", "город 'екб' указан повторно"),
        ("екб:-1001,члб:-1001", "привязан к нескольким городам"),
        ("екб:not-a-number", "некорректный chat_id"),
        ("екб:0", "не может быть равен 0"),
    ],
)
def test_city_cash_chat_map_rejects_ambiguous_values(raw: str, error: str) -> None:
    with pytest.raises(RuntimeError, match=error):
        _parse_city_chat_map(
            raw,
            env_name="CITY_CASH_CHAT_IDS",
            unique_chat_ids=True,
        )
