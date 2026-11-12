from __future__ import annotations


def split_telegram_text(text: str, *, limit: int = 4096) -> tuple[str, ...]:
    """Split plain text on newlines, respecting Telegram's UTF-16 size limit.

    Keep all characters and do not split supplementary Unicode characters.
    This is deliberately not an HTML/entity splitter; send with parse_mode=None.
    """
    if limit < 2:
        raise ValueError("The message limit must be at least 2")
    chunks = []
    while text:
        end = min(limit, len(text))
        units = 0
        for index, char in enumerate(text[:end]):
            units += 2 if ord(char) > 0xFFFF else 1
            if units > limit:
                end = index
                break
        if end < len(text):
            newline = text.rfind("\n", 0, end)
            if newline >= 0:
                end = newline + 1
        chunks.append(text[:end])
        text = text[end:]
    return tuple(chunks)
