from __future__ import annotations

import pytest

from api.schemas.deals import CashDealCreateRequest
from services.cash_requests.constants import CMD_MAP
from services.cash_requests.parsing import parse_dep_wd


@pytest.mark.parametrize(
    ("command", "kind"),
    [("депбат", "dep"), ("выдбат", "wd")],
)
def test_baht_cash_commands(command: str, kind: str) -> None:
    parsed = parse_dep_wd(
        f"/{command} екб 1000",
        cmd_map=CMD_MAP,
        city_keys={"екб"},
        default_city="екб",
    )

    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.code == "THB"
    assert parsed.amount_expr == "1000"


def test_crm_cash_request_normalizes_time_and_optional_contacts() -> None:
    payload = CashDealCreateRequest.model_validate(
        {
            "dealType": "withdrawal",
            "clientId": 1,
            "city": "екб",
            "currency": "THB",
            "amount": "1000",
            "idempotencyKey": "test-cash-baht",
            "time": "9:05",
            "contact1": None,
            "contact2": None,
        }
    )

    assert payload.time == "09:05"
    assert payload.contact1 is None
