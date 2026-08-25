from __future__ import annotations

from dataclasses import dataclass

from db_asyncpg.ports.workflows import ClientWalletTransactionRepositoryPort
from services.cash_requests.card_parser import CashCardParser
from services.cash_requests.constants import CB_ISSUE_DONE
from services.messaging import MessengerPort, ReplierPort


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
    """Compatibility handler for old cards; financial writes require a request link."""

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
        del messenger
        op_kind = self._parser.issue_kind(
            params.callback_data,
            params.card_text,
            prefix=CB_ISSUE_DONE,
        )
        if op_kind not in {"dep", "wd"}:
            error = "Кнопка доступна только для заявок на внесение или выдачу."
        else:
            error = (
                "Эта кнопка больше не проводит баланс. Нажмите «Готово к расчету», "
                "затем выполните кассовую команду с номером заявки."
            )
        await replier.alert(error)
        return RequestIssueResult(ok=False, op_kind=op_kind, error=error)
