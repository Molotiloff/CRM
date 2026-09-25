from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO

from PIL import Image

from services.receipts import PaymentReceiptImageBuilder, ReceiptImageBuilder, ReceiptRow
from services.receipts.image import format_receipt_datetime


def test_receipt_datetime_uses_yekaterinburg_time() -> None:
    assert format_receipt_datetime(datetime(2026, 9, 25, 10, 30, tzinfo=UTC)) == (
        "25 сен 2026 15:30"
    )


def test_payment_receipt_still_renders_png() -> None:
    content = PaymentReceiptImageBuilder().build_main_success(
        amount=Decimal("190"),
        recipient_address="TP7pmYVrHa1234567890123456783Pwi5w7",
        tx_hash="0e317faba01d43a07d9b474fb4efd3ce3968f1622ad6c706e1a8e17235df7924",
        block_ts=datetime(2026, 9, 25, 10, 30, tzinfo=UTC),
    )

    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        assert image.width == 980
        assert image.height >= 700


def test_client_transfer_receipt_wraps_long_chat_names() -> None:
    content = ReceiptImageBuilder().build(
        amount=Decimal("1250.5"),
        currency="RUB",
        precision=2,
        rows=(
            ReceiptRow("Статус", "Проведено", accent=True),
            ReceiptRow("Номер операции", "#123"),
            ReceiptRow("Отправитель", "Очень длинное название клиентского чата отправителя"),
            ReceiptRow("Получатель", "Ещё одно длинное название клиентского чата получателя"),
            ReceiptRow("Дата и время", "25 сен 2026 15:30"),
        ),
    )

    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        assert image.width == 980
        assert image.height > 760
