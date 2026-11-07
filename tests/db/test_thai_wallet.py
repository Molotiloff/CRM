from db_asyncpg.repositories.settings import SettingsRepo
from services.admin_client.usdt_wallet_service import (
    SECOND_SETTING_KEY,
    SETTING_KEY,
    THAI_SETTING_KEY,
    UsdtWalletService,
)


async def test_thai_wallet_persists_independently_in_existing_settings_table(pool):
    repo = SettingsRepo(pool)
    first = "TEsHvKtA74RJiHHXHd5VmjdEBaxy33CdK9"
    second = "TCLUeRdTiyzEcMQ5UVDV6z4ET5reUVcPAS"
    thai = "TJZY918ginJqvH9RfCEbzdKJv7T5Xq553r"
    await repo.set_setting(SETTING_KEY, first)
    await repo.set_setting(SECOND_SETTING_KEY, second)
    await UsdtWalletService(repo).set_from_text(f"/setwallett {thai}", wallet_slot="thai")
    fresh_repo = SettingsRepo(pool)
    assert await fresh_repo.get_setting(THAI_SETTING_KEY) == thai
    assert await fresh_repo.get_setting(SETTING_KEY) == first
    assert await fresh_repo.get_setting(SECOND_SETTING_KEY) == second
