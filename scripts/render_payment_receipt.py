from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib.util
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_builder_class():
    module_path = PROJECT_ROOT / "services" / "payment_watch" / "receipt_image.py"
    spec = importlib.util.spec_from_file_location("payment_watch_receipt_image", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PaymentReceiptImageBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a test payment receipt PNG using PaymentReceiptImageBuilder.",
    )
    parser.add_argument(
        "--amount",
        default="150000",
        help="Transfer amount in USDT. Default: 150000",
    )
    parser.add_argument(
        "--recipient",
        default="TN8JupuJCrm1cQ2rZUUvzikPUji9nmy92F",
        help="Recipient wallet address.",
    )
    parser.add_argument(
        "--tx-hash",
        default="191c58c4ca01414563e28181d811d9d126027591c1230b28302af1f154ee108c",
        help="Transaction hash to render inside the plate.",
    )
    parser.add_argument(
        "--block-ts",
        default="2026-05-18T10:15:00+00:00",
        help="Transaction datetime in ISO format UTC, e.g. 2026-05-18T10:15:00+00:00",
    )
    parser.add_argument(
        "--output",
        default="images/payment_receipt_preview.png",
        help="Output PNG path. Default: images/payment_receipt_preview.png",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        amount = Decimal(str(args.amount).replace(",", "."))
    except (InvalidOperation, ValueError):
        print(f"Invalid amount: {args.amount}", file=sys.stderr)
        return 2
    try:
        block_ts = datetime.fromisoformat(args.block_ts)
        if block_ts.tzinfo is None:
            block_ts = block_ts.replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"Invalid --block-ts: {args.block_ts}", file=sys.stderr)
        return 2

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    PaymentReceiptImageBuilder = load_builder_class()
    builder = PaymentReceiptImageBuilder()
    png_bytes = builder.build_main_success(
        amount=amount,
        recipient_address=str(args.recipient),
        tx_hash=str(args.tx_hash),
        block_ts=block_ts,
    )

    output_path.write_bytes(png_bytes)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
