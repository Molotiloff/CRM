from services.aml.getblock_parser import build_report_message


def test_full_pdf_message_contains_all_sources_and_no_report_url():
    data = {
        "asset_name": "TRX USDT", "token": "USDT", "type": "Address",
        "hash": "TJZY918ginJqvH9RfCEbzdKJv7TtXq553r",
        "risk_percent": "51.2449%", "risk_emoji": "🟡",
        "risk_label_ru": "Средний уровень риска", "report_date": "15.10.2026 18:15",
        "counterparty": "unknown", "preview_link": "https://getblock.net/en/report-preview/test",
        "trusted_sources": ["Exchange Licensed 70.98%", "Other 16.5%", "Payment 0.51%"],
        "suspicious_sources": ["Liquidity Pools 6.62%", "Exchange Unlicensed 0.75%", "ATM 0.01%"],
        "dangerous_sources": ["Enforcement Action 3.44%", "Exchange Sanctioned Eu 0.67%", "Gambling 0.41%", "Sanctions 0.05%", "Exchange Sanctioned Gb 0.04%", "Illegal Service 0.01%", "Stolen Coins 0.01%"],
    }
    message = build_report_message(data, wallet_balance="0.000000 USDT", pdf_attached=True)
    assert message.startswith("SKYEX · Отчёт AML-проверки\nUSDT Адрес:\n" + data["hash"])
    for item in (
        "На балансе кошелька: 0.000000 USDT", "🟡 Средний уровень риска: 51.2449%",
        "📅 Дата AML проверки: 15.10.2026 18:15", "Контрагент: unknown",
        "✅ Доверенные источники", "⚠️ Подозрительные источники", "🛑 Опасные источники",
        *data["trusted_sources"], *data["suspicious_sources"], *data["dangerous_sources"],
    ):
        assert item in message
    assert message.endswith("Отчёт прикреплён в PDF.")
    assert "https://" not in message
    assert "Полный отчет:" not in message
    # Other consumers still get the legacy link unless opting into PDF delivery.
    assert data["preview_link"] in build_report_message(data)


def test_transaction_report_keeps_transaction_label_and_no_invented_balance():
    message = build_report_message({"type": "Transaction", "hash": "a" * 64, "asset_name": "USDT"}, pdf_attached=True)
    assert "USDT Транзакция:" in message
    assert "На балансе кошелька" not in message
