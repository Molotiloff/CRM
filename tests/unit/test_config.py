from __future__ import annotations

import pytest

from config import Config, _parse_city_chat_map


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch) -> None:
    monkeypatch.setenv(
        "CRM_JWT_SECRET",
        "test-jwt-secret-with-at-least-32-characters",
    )


def test_jwt_secret_is_required(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("CRM_JWT_SECRET", "")

    with pytest.raises(RuntimeError, match="CRM_JWT_SECRET"):
        Config.from_env()


def test_city_cash_chat_map_parses_named_cities(monkeypatch) -> None:
    monkeypatch.setenv("MAIN_DASHBOARD_SOURCE_MODE", "sheets")
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv(
        "CITY_CASH_CHAT_IDS",
        "екб:-1003611459690, члб:-5218770251, тюм:-5075605056",
    )
    monkeypatch.setenv("BEST_CHANGE_CHAT_ID", "-100987654321")
    monkeypatch.setenv("MOSCOW_POETS_CHAT_ID", "-100111")
    monkeypatch.setenv("MOSCOW_BS_CHAT_ID", "-100222")

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
    assert config.best_change_chat_id == -100987654321
    assert config.moscow_poets_chat_id == -100111
    assert config.moscow_bs_chat_id == -100222


def test_dashboard_source_mode_is_validated(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("MAIN_DASHBOARD_SOURCE_MODE", "mixed")

    with pytest.raises(RuntimeError, match="MAIN_DASHBOARD_SOURCE_MODE"):
        Config.from_env()


def test_telegram_oidc_credentials_must_be_configured_together(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("TELEGRAM_OIDC_CLIENT_ID", "123456")
    monkeypatch.setenv("TELEGRAM_OIDC_CLIENT_SECRET", "")

    with pytest.raises(RuntimeError, match="должны быть заданы вместе"):
        Config.from_env()


def test_websocket_security_settings_are_loaded(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv(
        "CRM_API_WS_ALLOWED_ORIGINS",
        "https://skyex.work.gd/, http://localhost:3000",
    )
    monkeypatch.setenv("CRM_API_WS_MAX_CONNECTIONS", "25")

    config = Config.from_env()

    assert config.api_ws_allowed_origins == [
        "https://skyex.work.gd",
        "http://localhost:3000",
    ]
    assert config.api_ws_max_connections == 25


def test_websocket_connection_limit_must_be_positive(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("CRM_API_WS_MAX_CONNECTIONS", "0")

    with pytest.raises(RuntimeError, match="должен быть больше нуля"):
        Config.from_env()


def test_message_archive_settings_are_optional_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:test-token")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("MESSAGE_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("MESSAGE_ARCHIVE_MEDIA_DIR", "/archive/media")
    monkeypatch.setenv("MESSAGE_ARCHIVE_TEMP_DIR", "/archive/tmp")
    monkeypatch.setenv("MESSAGE_ARCHIVE_DOWNLOAD_WORKERS", "0")
    monkeypatch.setenv("MESSAGE_ARCHIVE_DOWNLOAD_QUEUE_SIZE", "0")
    monkeypatch.setenv("MESSAGE_ARCHIVE_EXPORT_WORKERS", "0")
    monkeypatch.setenv("MESSAGE_ARCHIVE_EXPORT_PART_BYTES", "1")

    config = Config.from_env()

    assert config.message_archive_enabled is True
    assert config.message_archive_media_dir == "/archive/media"
    assert config.message_archive_temp_dir == "/archive/tmp"
    assert config.message_archive_download_workers == 1
    assert config.message_archive_download_queue_size == 1
    assert config.message_archive_export_workers == 1
    assert config.message_archive_export_part_bytes == 1024 * 1024


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
