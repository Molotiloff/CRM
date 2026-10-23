from decimal import Decimal

import pytest

from services.wallets.command_parser import WalletCommandParser
from services.wallets.mutation_service import CurrencyMutationService
from tests.db.conftest import balance_of, tx_rows


@pytest.mark.parametrize("text,code,expected", [
    ("/депр 2000/2", "RUB", Decimal("1000")),
    ("/выдт 100", "USDT", Decimal("-100")),
    ("/деп 100 USDT", "USDT", Decimal("100")),
    ("/выд 2000 RUB", "RUB", Decimal("-2000")),
])
async def test_silent_cash_change_is_idempotent_and_balance_only(pool, repo, client_id, text, code, expected):
    parsed = WalletCommandParser.parse_partner_cash_change(text)
    assert parsed is not None
    for _ in range(2):
        result = await CurrencyMutationService(repo=repo).apply_external_currency_change(
            chat_id=-100500, chat_name="Тестовый чат", code=parsed.code,
            amount=parsed.amount, expr=text, source="partner_chat_command",
            idempotency_key="-100500:101",
        )
        assert result.ok
    assert await balance_of(repo, client_id, code) == expected
    assert len(await tx_rows(pool, client_id)) == 1
    async with pool.acquire() as con:
        for table in ("deals", "exchange_request_links", "tg_outbox", "usdt_fulfillment_queue"):
            assert await con.fetchval(f"SELECT count(*) FROM {table}") == 0
