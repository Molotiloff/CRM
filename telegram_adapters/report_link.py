from urllib.parse import urlparse

from aiogram.types import MessageEntity


def append_getblock_link(text: str, url: str) -> tuple[str, str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in {"getblock.net", "www.getblock.net"}:
        return text, None
    return text.rstrip() + "\n\nОтчет", url


def getblock_link_entities(text: str, url: str | None) -> list[MessageEntity]:
    if not url or not text.endswith("Отчет"):
        return []
    return [MessageEntity(
        type="text_link", offset=len(text[:-5].encode("utf-16-le")) // 2,
        length=5, url=url,
    )]
