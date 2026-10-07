from decimal import Decimal

from services.crm.telegram_deal_registrar import ExchangeDealData, TelegramDealRegistrar


def test_exchange_type_uses_firm_perspective() -> None:
    assert TelegramDealRegistrar._exchange_type("USDT", "RUB") == "purchase"
    assert TelegramDealRegistrar._exchange_type("RUB", "USDT") == "sale"
    assert TelegramDealRegistrar._exchange_type("EUR", "USDT") == "conversion"


def test_crm_exchange_keeps_referrer_without_auto_payout() -> None:
    registrar = TelegramDealRegistrar(None, default_city="екб")
    command = registrar.build_exchange_command(ExchangeDealData(
        source_ref="1:2", client_id=1, client_req_id="123", table_req_id=456,
        client_name="Покупатель", creator_name="Менеджер", recv_code="RUB",
        recv_amount=Decimal("1000"), pay_code="USD", pay_amount=Decimal("10"),
        rate=Decimal("100"), source="crm", referrer_client_id=2,
        referrer_percent=Decimal("15"),
    ))
    assert command.source == "crm"
    assert command.body.to_dict()["referrer_client_id"] == 2
    assert command.body.to_dict()["referrer_percent"] == "15"


def test_exchange_preserves_referrer_spread_as_rubles_per_currency_unit() -> None:
    registrar = TelegramDealRegistrar(None, default_city="екб")
    command = registrar.build_exchange_command(ExchangeDealData(
        source_ref="spread:1", client_id=1, client_req_id="123", table_req_id=456,
        client_name="Покупатель", creator_name="Менеджер", recv_code="RUB",
        recv_amount=Decimal("150000"), pay_code="USDT", pay_amount=Decimal("1704.545"),
        rate=Decimal("88"), source="crm", referrer_client_id=2,
        referrer_spread_rub=Decimal("0.1"),
    ))
    body = command.body.to_dict()
    assert body["referrer_spread_rub"] == "0.1"
    assert body["referrer_percent"] == "0"
    assert body["referrer_client_id"] == 2
    assert Decimal(body["pay_amount"]) * Decimal(body["referrer_spread_rub"]) == Decimal("170.4545")
