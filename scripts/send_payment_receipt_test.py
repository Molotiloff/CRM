from __future__ import annotations

import asyncio
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Впиши вручную нужный chat_id перед запуском.
CHAT_ID = -1003611459690

# Тестовые данные для чека и подписи.
AMOUNT = Decimal("150000")
RECIPIENT_ADDRESS = "TN8JupuJCrm1cQ2rZUUvzikPUji9nmy92F"
TX_HASH = "191c58c4ca01414563e28181d811d9d126027591c1230b28302af1f154ee108c"


def _load_module(module_name: str, relative_path: str):
    module_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_config():
    module = _load_module("project_config", "config.py")
    return module.Config.from_env()


def build_receipt_png() -> bytes:
    module = _load_module("payment_watch_receipt_image", "services/payment_watch/receipt_image.py")
    builder = module.PaymentReceiptImageBuilder()
    return builder.build_main_success(
        amount=AMOUNT,
        recipient_address=RECIPIENT_ADDRESS,
        tx_hash=TX_HASH,
    )


def build_caption() -> str:
    module = _load_module("payment_watch_message_builder", "services/payment_watch/message_builder.py")
    builder = module.PaymentWatchMessageBuilder()
    return builder.build_main_success(
        amount=AMOUNT,
        tx_hash=TX_HASH,
    )


async def main() -> int:
    if not CHAT_ID:
        print("Set CHAT_ID in scripts/send_payment_receipt_test.py before running.", file=sys.stderr)
        return 2

    config = load_config()
    png_bytes = build_receipt_png()
    caption = build_caption()

    url = f"https://api.telegram.org/bot{config.bot_token}/sendPhoto"
    data = {
        "chat_id": str(CHAT_ID),
        "caption": caption,
        "parse_mode": "HTML",
    }
    files = {
        "photo": ("payment_receipt_test.png", png_bytes, "image/png"),
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.post(url, data=data, files=files)

    if response.status_code >= 400:
        print(response.text, file=sys.stderr)
        return 1

    payload = response.json()
    if not payload.get("ok"):
        print(payload, file=sys.stderr)
        return 1

    print("Sent to chat:", CHAT_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
