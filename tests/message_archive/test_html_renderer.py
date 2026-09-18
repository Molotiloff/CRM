from __future__ import annotations

import json
from datetime import UTC, datetime

from services.message_archive.html_renderer import MessageHtmlRenderer


def test_renderer_escapes_user_content_and_keeps_supported_entity() -> None:
    rendered = MessageHtmlRenderer().render_message(
        {
            "telegram_message_id": 5,
            "sent_at": datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
            "author_name": '<img src=x onerror="alert(1)">',
            "direction": "inbound",
            "message_type": "message",
            "text_plain": "<script>x</script> link",
            "text_entities": [{"type": "bold", "offset": 19, "length": 4}],
            "source": "telegram_desktop",
        },
        [],
        media_names={},
        show_day=True,
    )

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in rendered
    assert "&lt;script&gt;x&lt;/script&gt;" in rendered
    assert "<strong>link</strong>" in rendered


def test_renderer_marks_desktop_service_message() -> None:
    rendered = MessageHtmlRenderer().render_message(
        {
            "telegram_message_id": 1,
            "sent_at": datetime(2026, 9, 10, tzinfo=UTC),
            "direction": "imported",
            "message_type": "service",
            "source": "telegram_desktop",
        },
        [],
        media_names={},
        show_day=False,
    )

    assert 'class="message imported service"' in rendered


def test_renderer_marks_album_and_escapes_forward_information() -> None:
    rendered = MessageHtmlRenderer().render_message(
        {
            "telegram_message_id": 2,
            "sent_at": datetime(2026, 9, 10, tzinfo=UTC),
            "direction": "inbound",
            "message_type": "photo",
            "source": "bot_api",
            "media_group_id": 'album"><script>alert(1)</script>',
            "forward_info": {"forwarded_from": "<b>Источник</b>"},
        },
        [],
        media_names={},
        show_day=False,
    )

    assert 'class="message inbound album"' in rendered
    assert 'data-media-group="album&quot;&gt;&lt;script&gt;' in rendered
    assert "Переслано от: &lt;b&gt;Источник&lt;/b&gt;" in rendered
    assert "<script>" not in rendered


def test_renderer_shows_modern_forward_sources() -> None:
    renderer = MessageHtmlRenderer()

    assert (
        renderer._forward_label({"type": "channel", "chat": {"title": "Новости"}})
        == "Переслано от: Новости"
    )
    assert (
        renderer._forward_label({"type": "hidden_user", "sender_user_name": "Скрытый автор"})
        == "Переслано от: Скрытый автор"
    )
    assert (
        renderer._forward_label(
            {
                "type": "user",
                "sender_user": {
                    "first_name": "Power | SkyEx",
                    "username": "power_skyex_bot",
                    "is_bot": True,
                },
            }
        )
        == "Переслано от: Power | SkyEx (@power_skyex_bot)"
    )


def test_renderer_decodes_asyncpg_json_strings() -> None:
    rendered = MessageHtmlRenderer().render_message(
        {
            "telegram_message_id": 3,
            "sent_at": datetime(2026, 9, 14, 11, 6, tzinfo=UTC),
            "author_name": "Данил",
            "direction": "inbound",
            "message_type": "text",
            "text_plain": "Заявка на выдачу: Б-384469",
            "text_entities": "[]",
            "forward_info": json.dumps(
                {
                    "type": "user",
                    "sender_user": {
                        "first_name": "Power | SkyEx",
                        "username": "power_skyex_bot",
                        "is_bot": True,
                    },
                },
                ensure_ascii=False,
            ),
            "source": "bot_api",
        },
        [],
        media_names={},
        show_day=False,
    )

    assert "Переслано от: Power | SkyEx (@power_skyex_bot)" in rendered

