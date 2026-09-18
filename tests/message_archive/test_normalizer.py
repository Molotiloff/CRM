from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import Chat, Message, MessageOriginChannel, MessageOriginUser, PhotoSize, User

from services.message_archive.normalizer import MessageArchiveNormalizer


def test_bot_message_normalization_uses_largest_photo_and_direction() -> None:
    message = Message(
        message_id=15,
        date=datetime(2026, 9, 10, tzinfo=UTC),
        chat=Chat(id=100, type="private", first_name="Клиент"),
        from_user=User(id=200, is_bot=False, first_name="Иван"),
        caption="Подтверждение",
        photo=[
            PhotoSize(file_id="small", file_unique_id="s", width=100, height=100),
            PhotoSize(file_id="large", file_unique_id="l", width=1000, height=1000),
        ],
    )

    normalized = MessageArchiveNormalizer.from_bot_message(message, outbound=True)

    assert normalized.direction == "outbound"
    assert normalized.message_type == "photo"
    assert normalized.text_plain == "Подтверждение"
    assert normalized.attachments[0].telegram_file_id == "large"
    assert normalized.attachments[0].download_status == "pending"


def test_bot_message_normalization_preserves_modern_forward_origin() -> None:
    message = Message(
        message_id=16,
        date=datetime(2026, 9, 10, 10, tzinfo=UTC),
        chat=Chat(id=-100500, type="supergroup", title="Клиенты"),
        from_user=User(id=200, is_bot=False, first_name="Иван"),
        text="Пересланный текст",
        forward_origin=MessageOriginChannel(
            type="channel",
            date=datetime(2026, 9, 9, 12, tzinfo=UTC),
            chat=Chat(id=-100900, type="channel", title="Источник"),
            message_id=42,
        ),
    )

    normalized = MessageArchiveNormalizer.from_bot_message(message, outbound=False)

    assert normalized.text_plain == "Пересланный текст"
    assert normalized.forward_info is not None
    assert normalized.forward_info["type"] == "channel"
    assert normalized.forward_info["sender_chat"]["title"] == "Источник"
    assert normalized.forward_info["source_message_id"] == 42


def test_bot_message_normalization_supports_legacy_forward_fields() -> None:
    message = Message(
        message_id=17,
        date=datetime(2026, 9, 10, 10, tzinfo=UTC),
        chat=Chat(id=-100500, type="supergroup", title="Клиенты"),
        from_user=User(id=200, is_bot=False, first_name="Иван"),
        text="Старый формат пересылки",
        forward_from=User(id=300, is_bot=False, first_name="Пётр"),
        forward_date=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )

    normalized = MessageArchiveNormalizer.from_bot_message(message, outbound=False)

    assert normalized.forward_info is not None
    assert normalized.forward_info["type"] == "legacy"
    assert normalized.forward_info["sender_user"]["first_name"] == "Пётр"


def test_bot_message_normalization_preserves_bot_forward_origin() -> None:
    message = Message(
        message_id=19,
        date=datetime(2026, 9, 10, 22, 55, tzinfo=UTC),
        chat=Chat(id=-100500, type="supergroup", title="Клиенты"),
        from_user=User(id=200, is_bot=False, first_name="Данил"),
        text="/бк usdt",
        forward_origin=MessageOriginUser(
            type="user",
            date=datetime(2026, 9, 10, 22, 54, tzinfo=UTC),
            sender_user=User(
                id=300,
                is_bot=True,
                first_name="Power | SkyEx",
                username="power_skyex_bot",
            ),
        ),
    )

    normalized = MessageArchiveNormalizer.from_bot_message(message, outbound=False)

    assert normalized.author is not None
    assert normalized.author.display_name == "Данил"
    assert normalized.forward_info is not None
    assert normalized.forward_info["sender_user"]["is_bot"] is True
    assert normalized.forward_info["sender_user"]["first_name"] == "Power | SkyEx"


def test_forward_metadata_participates_in_content_hash() -> None:
    common = {
        "message_id": 18,
        "date": datetime(2026, 9, 10, 10, tzinfo=UTC),
        "chat": Chat(id=-100500, type="supergroup", title="Клиенты"),
        "from_user": User(id=200, is_bot=False, first_name="Иван"),
        "text": "Одинаковый текст",
    }
    plain = Message(**common)
    forwarded = Message(
        **common,
        forward_origin=MessageOriginChannel(
            type="channel",
            date=datetime(2026, 9, 9, 12, tzinfo=UTC),
            chat=Chat(id=-100900, type="channel", title="Источник"),
            message_id=42,
        ),
    )

    plain_archive = MessageArchiveNormalizer.from_bot_message(plain, outbound=False)
    forwarded_archive = MessageArchiveNormalizer.from_bot_message(forwarded, outbound=False)

    assert plain_archive.content_hash != forwarded_archive.content_hash


def test_desktop_message_normalization_preserves_entities_and_media() -> None:
    message = MessageArchiveNormalizer.from_desktop_message(
        chat_data={"id": 101, "name": "Клиенты", "type": "private_group"},
        message_data={
            "id": 7,
            "type": "message",
            "date": "2026-09-10T12:30:00",
            "from": "Иван <admin>",
            "from_id": "user42",
            "text": ["Привет ", {"type": "bold", "text": "мир"}],
            "text_entities": [
                {"type": "plain", "text": "Привет "},
                {"type": "bold", "text": "мир"},
            ],
            "photo": "photos/photo_1.jpg",
            "photo_file_size": 123,
        },
        telegram_chat_id=-100500,
    )

    assert message.chat.telegram_chat_id == -100500
    assert message.chat.desktop_chat_id == 101
    assert message.text_plain == "Привет мир"
    assert message.text_entities == [{"type": "bold", "offset": 7, "length": 3}]
    assert message.direction == "imported"
    assert len(message.attachments) == 1
    assert message.attachments[0].attachment_type == "photo"
    assert message.attachments[0].download_status == "pending"


def test_desktop_unknown_attachment_is_metadata_only() -> None:
    message = MessageArchiveNormalizer.from_desktop_message(
        chat_data={"id": 1, "name": "Чат", "type": "personal_chat"},
        message_data={
            "id": 2,
            "date_unixtime": "1789056000",
            "text": "",
            "file": "files/archive.pdf",
            "media_type": "document",
        },
    )

    assert message.attachments[0].attachment_type == "document"
    assert message.attachments[0].download_status == "skipped"

