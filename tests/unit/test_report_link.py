import pytest

from telegram_adapters.report_link import append_getblock_link, getblock_link_entities
from telegram_adapters.text_chunks import split_telegram_text


def test_hyperlink_uses_utf16_offsets_after_emoji():
    text, url = append_getblock_link("🟡 Риск 50%", "https://getblock.net/en/report-preview/test")
    assert text == "🟡 Риск 50%\n\nОтчет"
    entity = getblock_link_entities(text, url)[0]
    encoded = text.encode("utf-16-le")
    assert encoded[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le") == "Отчет"
    assert entity.url == url
    assert "https://" not in text


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "https://getblock.net.evil.org/test", "https://evil.org/test"])
def test_invalid_link_is_not_added(url):
    assert append_getblock_link("report", url) == ("report", None)


def test_link_survives_long_caption_split():
    text, url = append_getblock_link("🟡 Источник риска 0.1%\n" * 300, "https://getblock.net/en/report-preview/test")
    captions = split_telegram_text(text, limit=1024)
    remaining = split_telegram_text("".join(captions[1:]))
    assert remaining[-1].endswith("Отчет")
    assert getblock_link_entities(remaining[-1], url)[0].url == url
