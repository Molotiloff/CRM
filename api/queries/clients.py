from __future__ import annotations

from dataclasses import dataclass

from api.presentation.clients import build_client, build_clients_page, build_transactions
from api.read_repositories import ClientReadRepository
from api.schemas.clients import ClientDto, ClientsPageResponse, ClientTransactionsResponse


class ClientNotFoundError(LookupError):
    def __init__(self, client_id: int) -> None:
        super().__init__(f"Client {client_id} not found")


@dataclass(frozen=True, slots=True)
class ClientListQuery:
    search: str | None = None
    group: str | None = None
    limit: int = 100
    offset: int = 0


class ClientQueryService:
    def __init__(self, repository: ClientReadRepository) -> None:
        self._repository = repository

    async def list_clients(self, query: ClientListQuery) -> ClientsPageResponse:
        clients = await self._repository.list_clients(
            search=query.search,
            group=query.group,
            limit=query.limit,
            offset=query.offset,
        )
        client_ids = [int(client["id"]) for client in clients]
        balances = _group_by_client_id(
            await self._repository.balances_for_clients(client_ids)
        )
        stats = await self._repository.stats_for_clients(client_ids)
        recent = await self._repository.recent_transactions_by_client(client_ids)
        total = await self._repository.count_clients(search=query.search, group=query.group)
        return build_clients_page(
            clients=clients,
            total_clients=total,
            balances=balances,
            stats=stats,
            recent_transactions=recent,
        )

    async def get_client(self, client_id: int) -> ClientDto:
        client = await self._require_client(client_id)
        balances = await self._repository.balances_for_clients([client_id])
        stats = await self._repository.stats_for_clients([client_id])
        recent = await self._repository.recent_transactions_by_client(
            [client_id], limit_per_client=5
        )
        return build_client(
            client=client,
            balances=balances,
            stats=stats.get(client_id, {}),
            recent_transactions=recent.get(client_id, []),
        )

    async def get_transactions(
        self, client_id: int, *, limit: int
    ) -> ClientTransactionsResponse:
        await self._require_client(client_id)
        rows = await self._repository.client_transactions(client_id, limit=limit)
        return ClientTransactionsResponse(
            items=build_transactions(rows, limit=limit),
            limit=limit,
        )

    async def _require_client(self, client_id: int) -> dict:
        client = await self._repository.get_client(client_id)
        if client is None:
            raise ClientNotFoundError(client_id)
        return client


def _group_by_client_id(rows: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["client_id"]), []).append(row)
    return grouped
