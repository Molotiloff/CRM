from services.wallets.command_parser import WalletCommandParser


def test_archive_command_is_not_treated_as_currency_change() -> None:
    parser = WalletCommandParser()
    assert parser.parse_currency_change(
        "/сообщение Предстоящие тест", chat_id=-1001
    ) is None
    assert parser.parse_currency_change(
        "/сообщение@skyex_bot Предстоящие тест", chat_id=-1001
    ) is None
    assert parser.parse_currency_change(
        "/сообщение SkyEx | Данил", chat_id=-1001
    ) is None


def test_real_currency_command_still_parses() -> None:
    parser = WalletCommandParser()
    assert parser.parse_currency_change("/USD 100", chat_id=-1001) is not None
    assert parser.parse_currency_change("/руб 1000 клиент", chat_id=-1001) is not None
    assert parser.parse_currency_change("/EUR -250", chat_id=-1001) is not None
    assert parser.parse_currency_change("/USDT (100+20)*2", chat_id=-1001) is not None
