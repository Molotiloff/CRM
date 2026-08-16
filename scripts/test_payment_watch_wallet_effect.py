from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from config import Config
from db_asyncpg.adapters import ClientWalletTransactionRepositoryAdapter
from db_asyncpg.pool import close_pool, create_pool
from db_asyncpg.repositories import ClientsRepo, TransactionsRepo
from services.wallets import WalletService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# Впиши вручную тестовый чат клиента.
CHAT_ID = -1003611459690
CHAT_NAME = "заявки члб"

# Тестовые параметры.
AMOUNT = Decimal("123")
TX_HASH = "ZXJrZm5sJ2U7cmtlO2lyZmhwZW9oO3Jmb2U7bGpyZg=="
RECIPIENT_ADDRESS = "TN8JupuJCrm1cQ2rZUUvzikPUji9nmy92F"
BLOCK_TS = datetime(2026, 5, 18, 10, 15, 0, tzinfo=UTC)

# "in"  -> перевод на наш кошелек => клиенту +USDT
# "out" -> перевод с нашего кошелька => клиенту -USDT
DIRECTION = "in"

# Отправлять ли чек перед проведением операции.
SEND_RECEIPT_TO_CHAT = True

# Отправлять ли текстовое сообщение в чат после изменения кошелька.
SEND_MESSAGE_TO_CHAT = True


def _load_module(module_name: str, relative_path: str):
    module_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_receipt_png() -> bytes:
    module = _load_module(
        "payment_watch_receipt_image_test", "services/payment_watch/receipt_image.py"
    )
    builder = module.PaymentReceiptImageBuilder()
    return builder.build_main_success(
        amount=AMOUNT,
        recipient_address=RECIPIENT_ADDRESS,
        tx_hash=TX_HASH,
        block_ts=BLOCK_TS,
    )


def build_receipt_caption() -> str:
    module = _load_module(
        "payment_watch_message_builder_test", "services/payment_watch/message_builder.py"
    )
    builder = module.PaymentWatchMessageBuilder()
    return builder.build_main_success(
        amount=AMOUNT,
        tx_hash=TX_HASH,
    )


async def main() -> int:
    if not CHAT_ID:
        print("Set CHAT_ID in scripts/test_payment_watch_wallet_effect.py before running.")
        return 2

    if DIRECTION not in {"in", "out"}:
        print("DIRECTION must be 'in' or 'out'.")
        return 2

    config = Config.from_env()
    pool = await create_pool(config.database_url)

    try:
        repo = ClientWalletTransactionRepositoryAdapter(ClientsRepo(pool), TransactionsRepo(pool))
        wallet_service = WalletService(repo=repo)
        signed_amount = AMOUNT if DIRECTION == "in" else -AMOUNT

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            if SEND_RECEIPT_TO_CHAT:
                receipt_url = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
                receipt_data = {
                    "chat_id": str(CHAT_ID),
                    "caption": build_receipt_caption(),
                    "parse_mode": "HTML",
                }
                receipt_files = {
                    "photo": ("payment_receipt_test.png", build_receipt_png(), "image/png"),
                }
                receipt_response = await client.post(
                    receipt_url, data=receipt_data, files=receipt_files
                )
                if receipt_response.status_code >= 400:
                    print("sendPhoto failed:", receipt_response.text)
                else:
                    payload = receipt_response.json()
                    print("sendPhoto ok:", payload.get("ok"))

        result = await wallet_service.apply_external_currency_change(
            chat_id=CHAT_ID,
            chat_name=CHAT_NAME,
            code="USDT",
            amount=signed_amount,
            expr=f"{AMOUNT.normalize():f}",
            source="payment_watch",
            idempotency_key=f"payment_watch:test:{TX_HASH}:wallet",
        )

        print("ok:", result.ok)
        print(result.message_text)
        if result.reply_markup:
            print("reply_markup:", result.reply_markup)

        if SEND_MESSAGE_TO_CHAT:
            url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                response = await client.post(
                    url,
                    data={
                        "chat_id": str(CHAT_ID),
                        "text": result.message_text,
                    },
                )
            if response.status_code >= 400:
                print("sendMessage failed:", response.text)
            else:
                payload = response.json()
                print("sendMessage ok:", payload.get("ok"))

        client_id = await repo.ensure_client(CHAT_ID, CHAT_NAME)
        rows = await repo.snapshot_wallet(client_id)
        usdt_row = next((r for r in rows if str(r["currency_code"]).upper() == "USDT"), None)
        print("usdt_account:", usdt_row)
        return 0
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
