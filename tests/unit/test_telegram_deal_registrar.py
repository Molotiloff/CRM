from services.crm.telegram_deal_registrar import TelegramDealRegistrar


def test_exchange_type_uses_firm_perspective() -> None:
    assert TelegramDealRegistrar._exchange_type("USDT", "RUB") == "purchase"
    assert TelegramDealRegistrar._exchange_type("RUB", "USDT") == "sale"
    assert TelegramDealRegistrar._exchange_type("EUR", "USDT") == "conversion"
