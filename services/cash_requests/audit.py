from __future__ import annotations

import html
import re
from dataclasses import dataclass

_RE_CREATED_BY = re.compile(r"^\s*Создал:\s*(?:<b>)?(.+?)(?:</b>)?\s*$", re.I | re.M)


@dataclass(frozen=True, slots=True)
class RequestAudit:
    created_by: str
    changed_by: str | None
    changed_ts: str | None


def created_by_from_old_text(old_text: str) -> str | None:
    if not old_text:
        return None
    match = _RE_CREATED_BY.search(old_text)
    if not match:
        return None
    return match.group(1).strip() or None


def audit_lines_for_request_chat(audit: RequestAudit) -> list[str]:
    lines = ["----", f"<b>Создал</b>: <b>{html.escape(audit.created_by)}</b>"]
    if audit.changed_by and audit.changed_ts:
        lines.extend(
            (
                f"<b>Изменил</b>: <b>{html.escape(audit.changed_by)}</b>",
                f"<b>Изменение</b>: <code>{html.escape(audit.changed_ts)}</code>",
            )
        )
    return lines
