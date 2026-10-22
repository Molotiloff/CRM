from services.aml.getblock_parser import build_report_message


def test_image_caption_has_statistics_without_pdf_or_getblock_link():
    text = build_report_message({
        "asset_name": "USDT", "hash": "Taddress", "risk_percent": "50%",
        "trusted_sources": ["Exchange 90%"], "dangerous_sources": ["Sanctions 10%"],
        "preview_link": "https://getblock.net/report",
    }, wallet_balance="123.456789 USDT", image_attached=True)
    assert text.startswith("SKYEX · Отчёт AML-проверки\n")
    assert "Exchange 90%" in text
    assert "Sanctions 10%" in text
    assert "123.456789 USDT" in text
    assert "PDF" not in text
    assert "getblock.net" not in text
