from __future__ import annotations

import json

from services.message_archive.telegram_desktop_parser import TelegramDesktopParser


def test_parser_streams_full_account_export(tmp_path) -> None:
    export = {
        "about": "Telegram Desktop export",
        "chats": {
            "list": [
                {
                    "name": "Первый",
                    "type": "personal_chat",
                    "id": 11,
                    "messages": [{"id": 1, "date": "2026-01-01", "text": "one"}],
                },
                {
                    "name": "Второй",
                    "type": "private_group",
                    "id": 22,
                    "messages": [{"id": 2, "date": "2026-01-02", "text": "two"}],
                },
            ]
        },
    }
    result_json = tmp_path / "result.json"
    result_json.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")

    records = list(TelegramDesktopParser().iter_messages(result_json))

    assert [(item.chat["id"], item.message["id"]) for item in records] == [(11, 1), (22, 2)]


def test_parser_summarizes_single_chat_and_missing_media(tmp_path) -> None:
    export = {
        "name": "Один чат",
        "type": "personal_chat",
        "id": 33,
        "messages": [
            {"id": 1, "date": "2026-01-01", "text": "photo", "photo": "photos/a.jpg"},
            {
                "id": 2,
                "date": "2026-01-02",
                "text": "voice",
                "media_type": "voice_message",
                "file": "voice/b.ogg",
            },
        ],
    }
    (tmp_path / "result.json").write_text(
        json.dumps(export, ensure_ascii=False), encoding="utf-8"
    )

    summary = TelegramDesktopParser().summarize(tmp_path)

    assert summary["chat_count"] == 1
    assert summary["message_count"] == 2
    assert summary["photo_count"] == 1
    assert summary["voice_count"] == 1
    assert summary["missing_files"] == 2

