from __future__ import annotations

import pytest

from services.aml.getblock_parser import parse_report_preview


@pytest.mark.parametrize(
    ("source_wrapper", "title_class"),
    [
        ('<div class="report-source-list">{groups}</div>', "source-title"),
        ('<div class="details-sidebar cristal-result">{groups}</div>', "block-title"),
    ],
)
def test_transaction_sources_include_usd_amount_after_percent(
    source_wrapper: str, title_class: str
) -> None:
    groups = "".join(
        f'<div class="{group_class}">'
        f'<h3 class="{title_class}">{title}</h3>'
        f'<ul class="source-list">{items}</ul>'
        "</div>"
        for group_class, title, items in (
            (
                "cristal-item source source-success", "Trusted sources",
                '<li class="item">Exchange Licensed 62.93% <span>(~28314.038263 USD)</span></li>'
                '<li class="item">Marketplace 0%</li>',
            ),
            (
                "cristal-item source source-warning", "Suspicious sources",
                '<li class="item"><span>P2p Exchange Unlicensed</span> '
                '0.01% (~4.499291 USD)</li>',
            ),
            (
                "cristal-item source source-alert", "Dangerous sources",
                '<li class="item">Sanctions 0.05% <span>(~22.496455 USD)</span></li>'
                '<li class="item">Scam 0%</li>',
            ),
        )
    )
    markup = (
        '<div id="report-info"><div class="details-info-item">'
        '<p>Hash: <span>tx-hash</span></p></div>'
        '<p class="risk-level">Risk level: <span>50.8%</span> Medium risk level</p>'
        '</div>' + source_wrapper.format(groups=groups)
    )

    report = parse_report_preview(
        markup, "check-id", base_url="https://getblock.net", lang="en"
    )

    assert report["trusted_sources"] == [
        "Exchange Licensed 62.93% (~28314.038263 USD)"
    ]
    assert report["suspicious_sources"] == [
        "P2P Exchange Unlicensed 0.01% (~4.499291 USD)"
    ]
    assert report["dangerous_sources"] == [
        "Sanctions 0.05% (~22.496455 USD)"
    ]
