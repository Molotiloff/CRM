from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from db_asyncpg.ports.workflows import (
    ClientTransferRepositoryPort,
    ClientWalletRepositoryPort,
    ClientWalletTransactionRepositoryPort,
)
from services.wallets.command_parser import WalletCommandParser
from services.wallets.keyboard_port import NullWalletKeyboardPresenter, WalletKeyboardPort
from services.wallets.models import CurrencyChangeCommand, ParsedCurrencyChange, WalletCommandResult
from services.wallets.mutation_service import CurrencyMutationService
from services.wallets.query_service import WalletQueryService
from services.wallets.text_builder import WalletTextBuilder
from services.wallets.undo_service import WalletUndoService


class WalletService:
    def __init__(
        self,
        *,
        repo: ClientTransferRepositoryPort,
        city_cash_chats: Mapping[str, int] | None = None,
        keyboards: WalletKeyboardPort | None = None,
    ) -> None:
        self.repo = repo
        self.keyboards = keyboards or NullWalletKeyboardPresenter()
        self.text_builder = WalletTextBuilder()
        self.parser = WalletCommandParser(city_cash_chats=city_cash_chats)
        wallet_query_repo = cast(ClientWalletRepositoryPort, repo)
        wallet_undo_repo = cast(ClientWalletTransactionRepositoryPort, repo)
        self.query_service = WalletQueryService(
            repo=wallet_query_repo, text_builder=self.text_builder
        )
        self.mutation_service = CurrencyMutationService(
            repo=repo,
            parser=self.parser,
            text_builder=self.text_builder,
            keyboards=self.keyboards,
        )
        self.undo_service = WalletUndoService(
            repo=wallet_undo_repo,
            parser=self.parser,
            text_builder=self.text_builder,
        )

    async def build_wallet_text(self, *, chat_id: int, chat_name: str) -> str:
        return await self.query_service.build_wallet_text(chat_id=chat_id, chat_name=chat_name)

    async def build_remove_currency_confirmation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
    ) -> WalletCommandResult:
        return await self.mutation_service.build_remove_currency_confirmation(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=raw_code,
        )

    async def add_currency(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
        precision: int,
    ) -> WalletCommandResult:
        return await self.mutation_service.add_currency(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=raw_code,
            precision=precision,
        )

    def parse_currency_change(
        self,
        raw_text: str,
        *,
        chat_id: int,
    ) -> ParsedCurrencyChange | None:
        return self.parser.parse_currency_change(raw_text, chat_id=chat_id)

    async def apply_currency_change(
        self,
        command: CurrencyChangeCommand,
    ) -> WalletCommandResult:
        return await self.mutation_service.apply_currency_change(command)

    async def apply_external_currency_change(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code: str,
        amount,
        expr: str,
        extra_comment: str = "",
        source: str = "external",
        idempotency_key: str | None = None,
    ) -> WalletCommandResult:
        return await self.mutation_service.apply_external_currency_change(
            chat_id=chat_id,
            chat_name=chat_name,
            code=code,
            amount=amount,
            expr=expr,
            extra_comment=extra_comment,
            source=source,
            idempotency_key=idempotency_key,
        )

    async def withdraw_all(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code: str,
        comment: str,
        source: str,
        idempotency_key: str,
    ) -> WalletCommandResult:
        return await self.mutation_service.withdraw_all(
            chat_id=chat_id,
            chat_name=chat_name,
            code=code,
            comment=comment,
            source=source,
            idempotency_key=idempotency_key,
        )

    async def remove_currency_confirmed(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code_raw: str,
    ) -> WalletCommandResult:
        return await self.mutation_service.remove_currency_confirmed(
            chat_id=chat_id,
            chat_name=chat_name,
            code_raw=code_raw,
        )

    async def undo_operation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        message_id: int,
        code_raw: str,
        sign: str,
        amt_str: str,
    ) -> WalletCommandResult:
        return await self.undo_service.undo_operation(
            chat_id=chat_id,
            chat_name=chat_name,
            message_id=message_id,
            code_raw=code_raw,
            sign=sign,
            amt_str=amt_str,
        )
