import pytest

from domain import SourceKind
from services.crm.deal_service import DealValidationError
from services.crm.deal_source_adapter import DealSourceAdapterRegistry


class FakeSourceAdapter:
    def __init__(self, source_kind: SourceKind) -> None:
        self.source_kind = source_kind

    async def prepare_edit(self, deal, command, *, actor_name):
        raise NotImplementedError

    async def cancel(self, unit_of_work, deal) -> None:
        raise NotImplementedError


def test_registry_resolves_adapter_by_source_kind() -> None:
    exchange = FakeSourceAdapter(SourceKind.EXCHANGE)
    cash = FakeSourceAdapter(SourceKind.CASH)
    registry = DealSourceAdapterRegistry((exchange, cash))

    assert registry.require(SourceKind.EXCHANGE) is exchange
    assert registry.require(SourceKind.CASH) is cash


def test_registry_rejects_duplicate_and_unknown_source_kinds() -> None:
    exchange = FakeSourceAdapter(SourceKind.EXCHANGE)

    with pytest.raises(DealValidationError, match="Duplicate"):
        DealSourceAdapterRegistry((exchange, exchange))

    with pytest.raises(DealValidationError, match="Unsupported"):
        DealSourceAdapterRegistry((exchange,)).require(SourceKind.CASH)
