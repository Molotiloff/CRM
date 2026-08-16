from __future__ import annotations

import html
from decimal import Decimal


def _fmt(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.001"))
    return f"{q:,.3f}".replace(",", "’")


class PaymentWatchMessageBuilder:
    def build_started(self, *, address: str, test_mode: bool, manager_note: str | None = None) -> str:
        safe = html.escape(address)
        note_line = f"\nКомментарий менеджера: <code>{html.escape(manager_note)}</code>" if manager_note else ""
        if test_mode:
            return (
                "❗❗❗ Если указанный адрес принадлежит бирже\n"
                "(например, Bybit), пожалуйста, сообщите об этом менеджеру\n"
                "перед отправкой.❗❗❗\n"
                "⏳ Начал отслеживание отправки.\n"
                f"Адрес: <code>{safe}</code>\n"
                "Сценарий: тестовая 1 USDT, затем основная.\n"
                "Ищу переводы только между кошельками.\n"
                "Подтверждение: 1 confirmation."
                f"{note_line}"
            )
        return (
            "❗❗❗ Если указанный адрес принадлежит бирже\n"
            "(например, Bybit), пожалуйста, сообщите об этом менеджеру\n"
            "перед отправкой.❗❗❗\n"
            "⏳ Начал отслеживание отправки.\n"
            f"Адрес: <code>{safe}</code>\n"
            "Жду перевод USDT TRC-20  между кошельками.\n"
            "Подтверждение: 1 confirmation."
            f"{note_line}"
        )

    def build_test_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
        from_address: str,
        to_address: str,
        block_number: int | None,
    ) -> str:
        block_line = (
            f"Номер блока: <code>{block_number}</code>\n"
            if block_number is not None
            else ""
        )
        return (
            "🚀 Тестовая транзакция прошла успешно.\n"
            f"Кошелёк отправителя: <code>{html.escape(from_address)}</code>\n"
            f"Кошелёк получателя: <code>{html.escape(to_address)}</code>\n"
            f"💸: <code>{_fmt(amount)} USDT</code>\n"
            f"{block_line}"
            f"🫆 Хэш: <code>{html.escape(tx_hash)}</code>\n"
            "Ожидаю основную транзакцию."
        )

    def build_main_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
    ) -> str:
        tx_url = f"https://tronscan.org/#/transaction/{html.escape(tx_hash, quote=True)}"
        return (
            "🚀 Средства переведены:\n"
            f"💸 <code>{_fmt(amount)} USDT</code>\n"
            f"🔗 <a href=\"{tx_url}\">Ссылка на Tronscan</a>"
        )

    def build_timeout(self) -> str:
        return "⌛ Отправка не подтверждена за 15 минут. Продолжить ожидание?"

    def build_continued(self) -> str:
        return "⏳ Ожидание отправки продлено ещё на 15 минут."

    def build_stopped(self) -> str:
        return "⛔ Ожидание отправки остановлено."
