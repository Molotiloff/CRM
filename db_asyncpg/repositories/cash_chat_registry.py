from __future__ import annotations

from domain import DomainStateError
from services.accounting.models import CashChatBinding, CashChatRegistrySyncResult

from .base import ConnectionBoundRepo


class CashChatRegistryRepo(ConnectionBoundRepo):
    """Atomic adapter for reconciling the env cash map with existing clients."""

    async def sync_configured(
        self,
        bindings: tuple[CashChatBinding, ...],
    ) -> CashChatRegistrySyncResult:
        async with self._connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('cash_chat_registry_sync', 0))"
                )
                if not bindings:
                    active_count = await connection.fetchval(
                        "SELECT COUNT(*) FROM cash_chat_registry WHERE is_active"
                    )
                    if active_count:
                        raise DomainStateError(
                            "CITY_CASH_CHAT_IDS is empty while active cash registry rows exist"
                        )
                    return CashChatRegistrySyncResult(
                        configured=0,
                        inserted=0,
                        reactivated=0,
                        deactivated=0,
                    )

                chat_ids = [binding.chat_id for binding in bindings]
                clients = await connection.fetch(
                    """
                    SELECT id, chat_id
                    FROM clients
                    WHERE chat_id = ANY($1::bigint[])
                      AND is_active
                    FOR UPDATE
                    """,
                    chat_ids,
                )
                client_id_by_chat = {int(row["chat_id"]): int(row["id"]) for row in clients}
                missing_cities = [
                    binding.city for binding in bindings if binding.chat_id not in client_id_by_chat
                ]
                if missing_cities:
                    raise DomainStateError(
                        "Configured city cash chats do not have active clients: "
                        + ", ".join(missing_cities)
                    )

                active_chat_ids = {
                    int(row["chat_id"])
                    for row in await connection.fetch(
                        "SELECT chat_id FROM cash_chat_registry WHERE is_active FOR UPDATE"
                    )
                }
                await connection.execute(
                    """
                    UPDATE cash_chat_registry
                    SET is_active = FALSE,
                        deactivated_at = NOW()
                    WHERE is_active
                    """
                )
                deactivated = len(active_chat_ids - set(chat_ids))
                inserted = 0
                reactivated = 0
                for binding in bindings:
                    client_id = client_id_by_chat[binding.chat_id]
                    existing_rows = await connection.fetch(
                        """
                        SELECT id, chat_id, client_id
                        FROM cash_chat_registry
                        WHERE chat_id = $1 OR client_id = $2
                        FOR UPDATE
                        """,
                        binding.chat_id,
                        client_id,
                    )
                    if len(existing_rows) > 1:
                        raise DomainStateError(
                            f"Cash registry identity conflict for city {binding.city}"
                        )
                    existing = existing_rows[0] if existing_rows else None
                    if existing is None:
                        await connection.execute(
                            """
                            INSERT INTO cash_chat_registry(
                                chat_id, client_id, city, location_name
                            )
                            VALUES ($1, $2, $3, $4)
                            """,
                            binding.chat_id,
                            client_id,
                            binding.city,
                            binding.location_name,
                        )
                        inserted += 1
                        continue
                    if (
                        int(existing["chat_id"]) != binding.chat_id
                        or int(existing["client_id"]) != client_id
                    ):
                        raise DomainStateError(
                            f"Cash registry identity conflict for city {binding.city}"
                        )
                    if binding.chat_id not in active_chat_ids:
                        reactivated += 1
                    await connection.execute(
                        """
                        UPDATE cash_chat_registry
                        SET city = $2,
                            location_name = $3,
                            is_active = TRUE,
                            deactivated_at = NULL
                        WHERE id = $1
                        """,
                        int(existing["id"]),
                        binding.city,
                        binding.location_name,
                    )

                return CashChatRegistrySyncResult(
                    configured=len(bindings),
                    inserted=inserted,
                    reactivated=reactivated,
                    deactivated=deactivated,
                )
