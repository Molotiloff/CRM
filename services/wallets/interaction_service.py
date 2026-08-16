from __future__ import annotations

import logging

from models.wallet import WalletError
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import CurrencyChangeCommand, WalletCommandResult
from services.wallets.wallet_service import WalletService

log = logging.getLogger("wallets")


class WalletInteractionService:
    def __init__(self, *, wallet_service: WalletService) -> None:
        self.wallet_service = wallet_service

    async def build_wallet_response(
        self,
        *,
        chat_id: int,
        chat_name: str,
    ) -> WalletCommandResult:
        text = await self.wallet_service.build_wallet_text(
            chat_id=chat_id,
            chat_name=chat_name,
        )
        return WalletCommandResult(
            ok=True,
            message_text=text,
            reply_markup=self.wallet_service.keyboards.statements(),
        )

    async def build_remove_currency_response(
        self,
        *,
        raw_text: str,
        chat_id: int,
        chat_name: str,
    ) -> WalletCommandResult:
        parts = raw_text.split()
        if len(parts) < 2:
            return WalletCommandResult(
                ok=False,
                message_text="Использование: /удали КОД\nПримеры: /удали USD, /удали дол, /удали юсдт",
            )

        return await self.wallet_service.build_remove_currency_confirmation(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=parts[1],
        )

    async def build_add_currency_response(
        self,
        *,
        raw_text: str,
        chat_id: int,
        chat_name: str,
    ) -> WalletCommandResult:
        parts = raw_text.split()
        if len(parts) < 2:
            return WalletCommandResult(
                ok=False,
                message_text=(
                    "Использование: /добавь КОД [точность]\n"
                    "Примеры: /добавь USD 2, /добавь дол 2, /добавь юсдт 0, /добавь доллбел 2"
                ),
            )

        precision = 2
        if len(parts) >= 3:
            try:
                precision = int(parts[2])
            except ValueError:
                return WalletCommandResult(
                    ok=False, message_text="Ошибка: точность должна быть целым числом 0..8"
                )

        return await self.wallet_service.add_currency(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=parts[1],
            precision=precision,
        )

    async def build_currency_change_response(
        self,
        command: CurrencyChangeCommand,
    ) -> WalletCommandResult:
        try:
            return await self.wallet_service.apply_currency_change(command)
        except ValueError as e:
            return WalletCommandResult(ok=False, message_text=str(e))
        except WalletError as we:
            log.exception(
                "WalletError in currency change chat_id=%s msg_id=%s",
                command.chat_id,
                command.message_id,
            )
            return WalletCommandResult(ok=False, message_text=f"Ошибка: {we}")
        except Exception as e:
            log.exception(
                "Exception in currency change chat_id=%s msg_id=%s",
                command.chat_id,
                command.message_id,
            )
            return WalletCommandResult(
                ok=False, message_text=f"Не удалось обработать операцию: {e}"
            )

    def parse_remove_currency_callback(self, data: str | None) -> tuple[str, str]:
        try:
            _, code_raw, answer = (data or "").split(":")
        except Exception as e:
            raise ValueError("Некорректные данные") from e
        return code_raw, answer

    async def build_remove_currency_callback_response(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code_raw: str,
        answer: str,
    ) -> tuple[str, str, bool]:
        if answer == "no":
            code = WalletCommandParser.normalize_code_alias(code_raw)
            return f"Удаление {code} отменено.", "Отмена", False

        result = await self.wallet_service.remove_currency_confirmed(
            chat_id=chat_id,
            chat_name=chat_name,
            code_raw=code_raw,
        )
        return result.message_text, "Удалено" if result.ok else "Отклонено", not result.ok

    def parse_undo_callback(self, data: str | None) -> tuple[str, str, str] | None:
        try:
            kind, code_raw, sign, amt_str = (data or "").split(":")
        except Exception as e:
            raise ValueError("Некорректные данные") from e

        if kind != "undo":
            return None
        return code_raw, sign, amt_str

    async def build_undo_response(
        self,
        *,
        chat_id: int,
        chat_name: str,
        message_id: int,
        code_raw: str,
        sign: str,
        amt_str: str,
    ) -> WalletCommandResult:
        return await self.wallet_service.undo_operation(
            chat_id=chat_id,
            chat_name=chat_name,
            message_id=message_id,
            code_raw=code_raw,
            sign=sign,
            amt_str=amt_str,
        )
