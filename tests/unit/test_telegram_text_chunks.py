import pytest

from telegram_adapters.text_chunks import split_telegram_text


@pytest.mark.parametrize("text", ["", "short", "a" * 4096, "a" * 4097, "🟡" * 4097, ("Источник риска 0.01%\n" * 900)])
def test_split_message_preserves_all_content_and_respects_utf16_limit(text):
    chunks = split_telegram_text(text)
    assert "".join(chunks) == text
    assert all(0 < len(chunk.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)


def test_split_prefers_source_line_boundaries():
    assert split_telegram_text("abcd\nefgh\nijkl", limit=10) == ("abcd\nefgh\n", "ijkl")
