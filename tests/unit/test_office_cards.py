from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.office_cards import OFFICE_CARDS
from handlers.office_cards import OfficeCard, OfficeCardsHandler


async def test_local_office_photo_logs_telegram_file_id(
    tmp_path,
    caplog,
) -> None:
    image_path = tmp_path / "office.jpg"
    image_path.write_bytes(b"test image")
    handler = OfficeCardsHandler(
        {
            "екб": OfficeCard(
                command="екб",
                caption="Офис",
                photo_file_id=None,
                image_path=image_path,
            )
        }
    )
    message = SimpleNamespace(
        text="/екб",
        answer=AsyncMock(),
        answer_photo=AsyncMock(
            return_value=SimpleNamespace(
                photo=[SimpleNamespace(file_id="telegram-office-file-id")]
            )
        ),
    )

    with caplog.at_level(logging.WARNING, logger="handlers.office_cards"):
        await handler._send_card(message)

    assert "command=/екб" in caplog.text
    assert "uploaded_photo_file_id=telegram-office-file-id" in caplog.text


def test_office_card_html_has_balanced_supported_tags() -> None:
    for command, card in OFFICE_CARDS.items():
        assert "<\\" not in card.caption, command
        assert card.caption.count("<b>") == card.caption.count("</b>"), command
        assert len(re.findall(r"<a\s+[^>]*>", card.caption)) == card.caption.count(
            "</a>"
        ), command
