from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
)

log = logging.getLogger(__name__)

# Telegram raises one of these when the target message can't be edited or deleted
# for a benign, expected reason: it was already deleted, never existed, the bot
# was kicked/blocked from the chat, or the new content is byte-identical
# ("message is not modified"). These are the ONLY Telegram errors it is
# legitimate to ignore. Everything else — rate limits (TelegramRetryAfter),
# network errors, server errors — must propagate so it is seen and retried.
# Единственный источник истины: services/messaging помечает MessengerError.benign
# по этому же набору.
SUPPRESSIBLE_TELEGRAM_ERRORS: tuple[type[BaseException], ...] = (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNotFound,
)


@contextmanager
def suppress_telegram_edit_errors(
    *,
    context: str = "",
    log_level: int = logging.DEBUG,
) -> Iterator[None]:
    """Suppress benign Telegram edit/delete failures, log them, re-raise the rest.

    Use ONLY around message edit/delete/answer calls where a vanished or
    unchanged message is an acceptable outcome::

        with suppress_telegram_edit_errors(context="cancel exchange"):
            await msg.edit_reply_markup(reply_markup=None)

    Benign Telegram errors (see ``SUPPRESSIBLE_TELEGRAM_ERRORS``) are logged at
    ``log_level`` (DEBUG by default) and swallowed. Any other exception —
    including rate limits, network/server errors, DB or programming errors —
    propagates unchanged.
    """
    try:
        yield
    except SUPPRESSIBLE_TELEGRAM_ERRORS as exc:
        log.log(
            log_level,
            "Ignored benign Telegram edit/delete error%s: %s",
            f" ({context})" if context else "",
            exc,
        )
