from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from db_asyncpg.ports.workflows import ClientWalletTransactionRepositoryPort
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.constants import CB_ISSUE_DONE
from services.messaging import MessengerError, MessengerPort, ReplierPort
from services.number_formatting import format_amount_core

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class RequestIssueParams:
    chat_id: int
    chat_name: str
    message_id: int
    card_text: str
    callback_data: str


@dataclass(slots=True, frozen=True)
class RequestIssueResult:
    ok: bool
    op_kind: str | None = None
    error: str | None = None


class RequestIssueService:
    def __init__(
        self,
        *,
        repo: ClientWalletTransactionRepositoryPort,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
        card_parser: CashCardParser | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self._parser = card_parser or CashCardParser()

    async def execute_core(
        self,
        params: RequestIssueParams,
        *,
        messenger: MessengerPort,
        replier: ReplierPort,
    ) -> RequestIssueResult:
        op_kind = self._parser.issue_kind(
            params.callback_data,
            params.card_text,
            prefix=CB_ISSUE_DONE,
        )

        if op_kind not in {"dep", "wd"}:
            error = "Кнопка доступна только для заявок на внесение или выдачу."
            await replier.alert(error)
            return RequestIssueResult(ok=False, error=error)

        parsed_amt = self._parser.amount_and_code(params.card_text, kind=op_kind)
        if not parsed_amt:
            error = "Не удалось распознать сумму/валюту."
            await replier.alert(error)
            return RequestIssueResult(ok=False, op_kind=op_kind, error=error)

        amount_raw, code = parsed_amt
        code = code.upper()

        chat_id = params.chat_id
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=params.chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            error = f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]"
            await replier.reply(error)
            await replier.alert("")
            return RequestIssueResult(ok=False, op_kind=op_kind, error=error)

        prec = int(acc.get("precision") or 2)
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))

        idem = f"cash:{chat_id}:{params.message_id}"
        try:
            if op_kind == "dep":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
        except Exception as e:
            log.exception("Cash issue wallet op failed (chat_id=%s, code=%s)", chat_id, code)
            error = f"Не удалось провести операцию по кошельку: {e}"
            await replier.reply(error)
            await replier.alert("")
            return RequestIssueResult(ok=False, op_kind=op_kind, error=error)

        log.info("Cash issue %s applied: chat_id=%s amount=%s %s", op_kind, chat_id, amount, code)

        try:
            await messenger.edit_reply_markup(
                chat_id=params.chat_id,
                message_id=params.message_id,
                reply_markup=None,
            )
        except MessengerError as e:
            if not e.benign:
                raise
            log.debug("Cash issue keyboard strip skipped: %s", e)

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        prec2 = int(acc2.get("precision") or prec) if acc2 else prec
        pretty_bal = format_amount_core(cur_bal, prec2)

        await replier.reply(
            f"Запомнил.\nБаланс: <code>{pretty_bal} {code.lower()}</code>",
            parse_mode="HTML",
        )
        await replier.alert("Отмечено как выдано", modal=False)
        return RequestIssueResult(ok=True, op_kind=op_kind)
