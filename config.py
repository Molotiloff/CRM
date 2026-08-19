import os
from dataclasses import dataclass
from ipaddress import ip_address

from services.aml.getblock_settings import GetBlockSettings


def _parse_int(s: str | None) -> int | None:
    s = (s or "").strip()
    return int(s) if s else None


def _parse_int_list(s: str | None) -> list[int]:
    if not s:
        return []
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def _parse_ids_set(s: str | None) -> set[int]:
    """
    Формат env:
      SCHEDULE_CHAT_IDS="-1001,-1002"
    """
    if not s:
        return set()
    return {int(x.strip()) for x in s.split(",") if x.strip()}


def _parse_bool(s: str | None, *, default: bool) -> bool:
    raw = (s or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_city_chat_map(
    s: str | None,
    *,
    env_name: str,
    unique_chat_ids: bool = False,
) -> dict[str, int]:
    """
    Формат env:
      CASH_CHAT_ID="екб:-49509,члб:-49502"
      CITY_SCHEDULE_CHATS="екб:-60001,члб:-60002"
      CITY_CASH_CHAT_IDS="екб:-4301,члб:-52251,тюм:-50"
    """
    out: dict[str, int] = {}
    raw = (s or "").strip()
    if not raw:
        return out

    for part in raw.split(","):
        part = part.strip().strip('"').strip("'")
        if not part:
            continue
        if ":" not in part:
            raise RuntimeError(
                f"Некорректный формат {env_name}. Ожидаю 'екб:-100...,члб:-100...'"
            )
        city, chat_id = part.split(":", 1)
        city = (city or "").strip().lower()
        chat_id = (chat_id or "").strip()
        if not city or not chat_id:
            raise RuntimeError(f"В {env_name} город и chat_id обязательны")
        if city in out:
            raise RuntimeError(f"В {env_name} город {city!r} указан повторно")
        try:
            parsed_chat_id = int(chat_id)
        except ValueError:
            raise RuntimeError(f"В {env_name} некорректный chat_id: {chat_id!r}") from None
        if parsed_chat_id == 0:
            raise RuntimeError(f"В {env_name} chat_id не может быть равен 0")
        if unique_chat_ids and parsed_chat_id in out.values():
            raise RuntimeError(
                f"В {env_name} chat_id {parsed_chat_id} привязан к нескольким городам"
            )
        out[city] = parsed_chat_id
    return out


@dataclass(slots=True)
class Config:
    bot_token: str
    database_url: str
    converter_api_base_url: str | None
    converter_api_token: str | None
    tronscan_api_base_url: str | None
    tronscan_api_key: str | None
    tronscan_usdt_contract: str
    payment_watch_poll_interval_seconds: int
    payment_watch_timeout_seconds: int

    admin_chat_id: int
    admin_ids: list[int]

    # общий чат заявок (legacy)
    request_chat_id: int | None

    # город -> чат заявок
    cash_chat_map: dict[str, int]

    # город -> чат расписания
    city_schedule_chats: dict[str, int]

    # чаты для автоотчётов scheduler
    schedule_chat_ids: set[int]

    # чат для ордеров по курсу
    rate_orders_chat_id: int | None

    default_city: str  # например "екб"

    # город -> операционный чат физической кассы
    city_cash_chat_map: dict[str, int]
    getblock: GetBlockSettings | None
    api_enabled: bool
    api_host: str
    api_port: int
    api_cors_origins: list[str]
    api_jwt_ttl_seconds: int
    api_dev_auth_bypass: bool
    api_dev_tg_user_id: int | None

    @classmethod
    def from_env(cls) -> "Config":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("Не найден BOT_TOKEN в окружении")

        db_url = os.getenv("DATABASE_URL", "").strip()
        if not db_url:
            raise RuntimeError("Не найден DATABASE_URL в окружении")

        converter_api_base_url = (os.getenv("CONVERTER_API_BASE_URL", "") or "").strip() or None
        converter_api_token = (os.getenv("CONVERTER_API_TOKEN", "") or "").strip() or None
        tronscan_api_base_url = (os.getenv("TRONSCAN_API_BASE_URL", "") or "").strip() or None
        tronscan_api_key = (os.getenv("TRONSCAN_API_KEY", "") or "").strip() or None
        tronscan_usdt_contract = (
            (os.getenv("TRONSCAN_USDT_CONTRACT", "") or "").strip()
            or "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        )
        payment_watch_poll_interval_seconds = int(
            (os.getenv("PAYMENT_WATCH_POLL_INTERVAL_SECONDS", "") or "30").strip()
        )
        payment_watch_timeout_seconds = int(
            (os.getenv("PAYMENT_WATCH_TIMEOUT_SECONDS", "") or str(15 * 60)).strip()
        )

        admin_chat_id = int(os.getenv("ADMIN_CHAT_ID", "0"))
        admin_ids = _parse_int_list(os.getenv("ADMIN_IDS"))

        request_chat_id = _parse_int(os.getenv("REQUEST_CHAT_ID"))

        cash_chat_map = _parse_city_chat_map(
            os.getenv("CASH_CHAT_ID"),
            env_name="CASH_CHAT_ID",
        )

        city_schedule_chats = _parse_city_chat_map(
            os.getenv("CITY_SCHEDULE_CHATS"),
            env_name="CITY_SCHEDULE_CHATS",
        )

        schedule_chat_ids = _parse_ids_set(os.getenv("SCHEDULE_CHAT_IDS"))
        rate_orders_chat_id = _parse_int(os.getenv("RATE_ORDERS_CHAT_ID"))

        default_city = (os.getenv("DEFAULT_CITY", "екб") or "екб").strip().lower()

        city_cash_chat_map = _parse_city_chat_map(
            os.getenv("CITY_CASH_CHAT_IDS"),
            env_name="CITY_CASH_CHAT_IDS",
            unique_chat_ids=True,
        )

        getblock_identity = os.getenv("GETBLOCK_IDENTITY", "").strip()
        getblock_password = os.getenv("GETBLOCK_PASSWORD", "").strip()

        getblock = None
        if getblock_identity and getblock_password:
            getblock = GetBlockSettings(
                identity=getblock_identity,
                password=getblock_password,
                lang=os.getenv("GETBLOCK_LANG", "en").strip(),
                user_id=os.getenv("GETBLOCK_USER_ID", "").strip(),
                currency_code=os.getenv("GETBLOCK_CURRENCY_CODE", "TRX").strip(),
                token_id=os.getenv("GETBLOCK_TOKEN_ID", "9").strip(),
                aml_provider=os.getenv("GETBLOCK_AML_PROVIDER", "2").strip(),
                direction=os.getenv("GETBLOCK_DIRECTION", "2").strip(),
                source=os.getenv("GETBLOCK_SOURCE", "1").strip(),
                type_=os.getenv("GETBLOCK_TYPE", "0").strip(),
                reports_dir=os.getenv("GETBLOCK_REPORTS_DIR", "reports").strip(),
            )

        api_enabled = _parse_bool(os.getenv("CRM_API_ENABLED"), default=True)
        api_host = (os.getenv("CRM_API_HOST", "") or "127.0.0.1").strip()
        api_port = int((os.getenv("CRM_API_PORT", "") or "8000").strip())
        api_cors_origins = [
            origin.strip()
            for origin in (os.getenv("CRM_API_CORS_ORIGINS", "") or "").split(",")
            if origin.strip()
        ]
        api_jwt_ttl_seconds = int(
            (os.getenv("CRM_API_JWT_TTL_SECONDS", "") or str(7 * 24 * 60 * 60)).strip()
        )
        api_dev_auth_bypass = _parse_bool(os.getenv("CRM_DEV_AUTH_BYPASS"), default=False)
        api_dev_tg_user_id = _parse_int(os.getenv("CRM_DEV_TG_USER_ID"))
        if api_dev_auth_bypass and not _is_loopback_host(api_host):
            raise RuntimeError(
                "CRM_DEV_AUTH_BYPASS разрешён только для loopback CRM_API_HOST"
            )

        return cls(
            bot_token=token,
            database_url=db_url,
            converter_api_base_url=converter_api_base_url,
            converter_api_token=converter_api_token,
            tronscan_api_base_url=tronscan_api_base_url,
            tronscan_api_key=tronscan_api_key,
            tronscan_usdt_contract=tronscan_usdt_contract,
            payment_watch_poll_interval_seconds=payment_watch_poll_interval_seconds,
            payment_watch_timeout_seconds=payment_watch_timeout_seconds,
            admin_chat_id=admin_chat_id,
            admin_ids=admin_ids,
            request_chat_id=request_chat_id,
            cash_chat_map=cash_chat_map,
            city_schedule_chats=city_schedule_chats,
            schedule_chat_ids=schedule_chat_ids,
            rate_orders_chat_id=rate_orders_chat_id,
            default_city=default_city,
            city_cash_chat_map=city_cash_chat_map,
            getblock=getblock,
            api_enabled=api_enabled,
            api_host=api_host,
            api_port=api_port,
            api_cors_origins=api_cors_origins,
            api_jwt_ttl_seconds=api_jwt_ttl_seconds,
            api_dev_auth_bypass=api_dev_auth_bypass,
            api_dev_tg_user_id=api_dev_tg_user_id,
        )

    @property
    def city_cash_chat_ids(self) -> frozenset[int]:
        """Compatibility membership view; the city map remains the source of truth."""
        return frozenset(self.city_cash_chat_map.values())
