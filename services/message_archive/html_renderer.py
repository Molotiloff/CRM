from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

_STYLE = """
:root{color-scheme:light;--bg:#eef2f5;--panel:#fff;--in:#fff;--out:#dcf8c6;--muted:#73808c;--accent:#2688eb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);font:14px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#202b33}
.head{position:sticky;top:0;z-index:2;background:#5682a3;color:#fff;padding:16px 22px}.head h1{font-size:18px;margin:0}.head div{opacity:.82;margin-top:4px}
.messages{max-width:960px;margin:0 auto;padding:20px}.day{text-align:center;color:var(--muted);margin:20px 0 10px}
.message{background:var(--in);border-radius:10px;margin:7px 0;padding:9px 12px;max-width:78%;box-shadow:0 1px 2px #0002;overflow-wrap:anywhere}
.message.outbound{margin-left:auto;background:var(--out)}.message.service{margin:12px auto;max-width:86%;text-align:center;background:#d9e8f1;color:#4c6270}
.meta{font-size:12px;color:var(--muted);margin-bottom:5px}.author{font-weight:600;color:var(--accent)}.edited{font-style:italic}.reply,.forward{border-left:3px solid var(--accent);padding-left:7px;color:var(--muted);font-size:12px;margin-bottom:6px}
.message.album{margin-top:2px;margin-bottom:2px}
.text{white-space:pre-wrap}.photo{display:block;max-width:100%;max-height:720px;border-radius:7px;margin-top:7px}.voice{width:100%;margin-top:7px}
.attachment{font-size:12px;color:var(--muted);margin-top:6px;padding:5px;border:1px solid #d9e0e5;border-radius:5px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0001;padding:1px 3px;border-radius:3px}a{color:#167ac6}
""".strip()


class MessageHtmlRenderer:
    @staticmethod
    def document_start(*, title: str, part: int) -> str:
        safe_title = html.escape(title)
        return (
            "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta http-equiv=\"Content-Security-Policy\" "
            "content=\"default-src 'none'; img-src 'self' data:; media-src 'self'; "
            "style-src 'unsafe-inline'\">"
            f"<title>{safe_title}</title><style>{_STYLE}</style></head><body>"
            f"<header class=\"head\"><h1>{safe_title}</h1><div>Часть {part}</div></header>"
            "<main class=\"messages\">"
        )

    @staticmethod
    def document_end() -> str:
        return "</main></body></html>"

    def render_message(
        self,
        message: dict[str, Any],
        attachments: list[dict[str, Any]],
        *,
        media_names: dict[int, str],
        show_day: bool,
    ) -> str:
        sent_at = message["sent_at"]
        if not isinstance(sent_at, datetime):
            raise TypeError("sent_at must be datetime")
        chunks: list[str] = []
        if show_day:
            chunks.append(f'<div class="day">{sent_at:%d.%m.%Y}</div>')

        message_type = str(message.get("message_type") or "message")
        classes = ["message", html.escape(str(message.get("direction") or "inbound"))]
        if message_type == "service" or message_type.startswith(("new_", "left_", "group_")):
            classes.append("service")
        media_group_id = str(message.get("media_group_id") or "")
        album_attribute = ""
        if media_group_id:
            classes.append("album")
            album_attribute = f' data-media-group="{html.escape(media_group_id, quote=True)}"'
        author = html.escape(str(message.get("author_name") or "Telegram"))
        edited = " <span class=\"edited\">изменено</span>" if message.get("edited_at") else ""
        chunks.append(
            f'<article id="m{int(message["telegram_message_id"])}" '
            f'class="{" ".join(classes)}"{album_attribute}>'
        )
        chunks.append(
            f'<div class="meta"><span class="author">{author}</span> · {sent_at:%H:%M}{edited}</div>'
        )
        if message.get("reply_to_message_id"):
            reply_id = int(message["reply_to_message_id"])
            chunks.append(
                f'<div class="reply">Ответ на <a href="#m{reply_id}">сообщение {reply_id}</a></div>'
            )
        forward_label = self._forward_label(message.get("forward_info"))
        if forward_label:
            chunks.append(
                f'<div class="forward">{html.escape(forward_label)}</div>'
            )
        text = str(message.get("text_plain") or "")
        if text:
            text_entities = self._json_list(message.get("text_entities"))
            chunks.append(
                f'<div class="text">{self.render_text(text, text_entities, source=str(message.get("source") or ""))}</div>'
            )

        for attachment in attachments:
            attachment_id = int(attachment["id"])
            kind = str(attachment["attachment_type"])
            archive_name = media_names.get(attachment_id)
            if archive_name and kind == "photo":
                chunks.append(
                    f'<a href="{html.escape(archive_name, quote=True)}"><img class="photo" loading="lazy" src="{html.escape(archive_name, quote=True)}" alt="Фотография"></a>'
                )
            elif archive_name and kind == "voice":
                chunks.append(
                    f'<audio class="voice" controls preload="metadata" src="{html.escape(archive_name, quote=True)}"></audio>'
                )
            else:
                name = html.escape(str(attachment.get("original_name") or kind))
                raw_status = str(attachment.get("download_status") or "skipped")
                if raw_status == "ready" and not archive_name:
                    raw_status = "missing in storage"
                status = html.escape(raw_status)
                chunks.append(f'<div class="attachment">Вложение: {name} ({status})</div>')
        chunks.append("</article>")
        return "".join(chunks)

    @staticmethod
    def _forward_label(value: Any) -> str | None:
        value = MessageHtmlRenderer._json_dict(value)
        if not isinstance(value, dict) or not value:
            return None
        for key in ("forwarded_from", "saved_from", "author_signature", "via_bot"):
            if value.get(key):
                return f"Переслано от: {value[key]}"
        sender_user = value.get("sender_user")
        if isinstance(sender_user, dict):
            name = " ".join(
                str(sender_user.get(key) or "") for key in ("first_name", "last_name")
            ).strip()
            username = str(sender_user.get("username") or "").strip().lstrip("@")
            if name or username:
                source = name or f"@{username}"
                if name and username:
                    source = f"{name} (@{username})"
                return f"Переслано от: {source}"
        if value.get("sender_user_name"):
            return f"Переслано от: {value['sender_user_name']}"
        sender_chat = value.get("sender_chat") or value.get("chat")
        if isinstance(sender_chat, dict):
            title = str(sender_chat.get("title") or "").strip()
            username = str(sender_chat.get("username") or "").strip().lstrip("@")
            if title or username:
                source = title or f"@{username}"
                if title and username:
                    source = f"{title} (@{username})"
                return f"Переслано от: {source}"
        return "Пересланное сообщение"

    @staticmethod
    def _json_dict(value: Any) -> dict[str, Any] | None:
        decoded = MessageHtmlRenderer._decode_json(value)
        return decoded if isinstance(decoded, dict) else None

    @staticmethod
    def _json_list(value: Any) -> list[dict[str, Any]]:
        decoded = MessageHtmlRenderer._decode_json(value)
        if not isinstance(decoded, list):
            return []
        return [item for item in decoded if isinstance(item, dict)]

    @staticmethod
    def _decode_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def render_text(cls, text: str, entities: list[dict[str, Any]], *, source: str) -> str:
        ranges: list[tuple[int, int, str, str]] = []
        for entity in entities:
            try:
                start = int(entity["offset"])
                length = int(entity["length"])
            except (KeyError, TypeError, ValueError):
                continue
            if source == "bot_api":
                start = cls._utf16_to_python_index(text, start)
                end = cls._utf16_to_python_index(text, int(entity["offset"]) + length)
            else:
                end = start + length
            tag = cls._entity_tag(entity)
            if tag and 0 <= start < end <= len(text):
                ranges.append((start, end, tag[0], tag[1]))

        openings: dict[int, list[tuple[int, str]]] = {}
        closings: dict[int, list[tuple[int, str]]] = {}
        for start, end, opening, closing in ranges:
            openings.setdefault(start, []).append((end, opening))
            closings.setdefault(end, []).append((start, closing))

        output: list[str] = []
        for index, char in enumerate(text):
            for _, closing in sorted(closings.get(index, []), reverse=True):
                output.append(closing)
            for _, opening in sorted(openings.get(index, []), reverse=True):
                output.append(opening)
            output.append(html.escape(char))
        for _, closing in sorted(closings.get(len(text), []), reverse=True):
            output.append(closing)
        return "".join(output)

    @staticmethod
    def _utf16_to_python_index(text: str, offset: int) -> int:
        units = 0
        for index, char in enumerate(text):
            if units >= offset:
                return index
            units += 2 if ord(char) > 0xFFFF else 1
        return len(text)

    @staticmethod
    def _entity_tag(entity: dict[str, Any]) -> tuple[str, str] | None:
        kind = str(entity.get("type") or "")
        if kind in {"bold"}:
            return "<strong>", "</strong>"
        if kind in {"italic"}:
            return "<em>", "</em>"
        if kind in {"code", "pre"}:
            return "<code>", "</code>"
        if kind in {"underline"}:
            return "<u>", "</u>"
        if kind in {"strikethrough"}:
            return "<s>", "</s>"
        if kind == "text_link":
            url = str(entity.get("url") or "")
            if urlparse(url).scheme in {"http", "https", "mailto", "tg"}:
                return f'<a href="{html.escape(url, quote=True)}">', "</a>"
        return None

