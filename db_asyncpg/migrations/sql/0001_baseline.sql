-- Клиенты (Telegram-чаты) ------------------------------------------------------
CREATE TABLE IF NOT EXISTS clients (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    client_group TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_clients_active ON clients(is_active);


-- Счета клиента (валюты) -------------------------------------------------------
CREATE TABLE IF NOT EXISTS client_accounts (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    currency_code TEXT NOT NULL,                       -- хранить в UPPER на уровне приложения
    precision SMALLINT NOT NULL CHECK (precision BETWEEN 0 AND 8),
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    UNIQUE (client_id, currency_code)
);
CREATE INDEX IF NOT EXISTS ix_client_accounts_client ON client_accounts(client_id);


-- Группы/категории операций (необязательно использовать) -----------------------
CREATE TABLE IF NOT EXISTS txn_groups (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);


-- Исполнители/акторы (кто провёл операцию) — опционально -----------------------
CREATE TABLE IF NOT EXISTS actors (
    id BIGSERIAL PRIMARY KEY,
    display_name TEXT NOT NULL,
    external_ref TEXT
);


-- Менеджеры (доступ к управляющим командам) ------------------------------------
CREATE TABLE IF NOT EXISTS managers (
    user_id BIGINT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Транзакции по счетам клиента (immutable аудит: amount + balance_after) --------
CREATE TABLE IF NOT EXISTS transactions (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    account_id BIGINT NOT NULL REFERENCES client_accounts(id) ON DELETE CASCADE,
    txn_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    amount NUMERIC(38,8) NOT NULL,            -- знаковая величина
    balance_after NUMERIC(38,8) NOT NULL,     -- остаток после операции
    group_id INTEGER REFERENCES txn_groups(id),
    actor_id BIGINT REFERENCES actors(id),
    comment TEXT,
    source TEXT,
    idempotency_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_tx_account_time ON transactions(account_id, txn_at, id);
CREATE INDEX IF NOT EXISTS ix_tx_client_time ON transactions(client_id, txn_at, id);
-- Идемпотентность денежных операций: один ключ на клиента
CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_client_idem
    ON transactions(client_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;


-- Расписание заявок по городам -------------------------------------------------
CREATE TABLE IF NOT EXISTS request_schedule_entries (
    id BIGSERIAL PRIMARY KEY,
    req_id TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    hhmm TEXT,
    request_kind TEXT NOT NULL,
    line_text TEXT NOT NULL,
    client_name TEXT NOT NULL,
    request_chat_id BIGINT NOT NULL,
    request_message_id BIGINT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_request_schedule_entries_city_active_hhmm
    ON request_schedule_entries(city, is_active, hhmm);
CREATE INDEX IF NOT EXISTS idx_request_schedule_entries_request_msg
    ON request_schedule_entries(request_chat_id, request_message_id);


-- Сводные «доски» расписания (одно сообщение на город) -------------------------
CREATE TABLE IF NOT EXISTS request_schedule_boards (
    city TEXT PRIMARY KEY,
    board_chat_id BIGINT NOT NULL,
    board_message_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- Связки заявок обмена (клиентское ↔ чат заявок ↔ Google Sheets) ---------------
CREATE TABLE IF NOT EXISTS exchange_request_links (
    client_req_id TEXT PRIMARY KEY,
    table_req_id TEXT NOT NULL,
    client_chat_id BIGINT,
    client_message_id BIGINT,
    request_chat_id BIGINT,
    request_message_id BIGINT,
    request_text TEXT,
    is_table_done BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    table_in_cur TEXT,
    table_out_cur TEXT,
    table_in_amount NUMERIC(38,8),
    table_out_amount NUMERIC(38,8),
    table_rate NUMERIC(38,8)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exchange_request_links_table_req_id
    ON exchange_request_links(table_req_id);


-- Движения по «акту» (привязка транзакций к заявке в чате заявок) ---------------
CREATE TABLE IF NOT EXISTS act_request_transactions (
    id BIGSERIAL PRIMARY KEY,
    req_id TEXT NOT NULL,
    table_req_id TEXT,
    request_chat_id BIGINT NOT NULL,
    request_message_id BIGINT NOT NULL,
    transaction_id BIGINT NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CANCELED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    canceled_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_act_request_transactions_transaction_id
    ON act_request_transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_act_request_transactions_req_id
    ON act_request_transactions(req_id);
CREATE INDEX IF NOT EXISTS idx_act_request_transactions_chat_status_created
    ON act_request_transactions(request_chat_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_act_request_transactions_table_req_id
    ON act_request_transactions(table_req_id);


-- Отслеживание входящих TRON-платежей ------------------------------------------
CREATE TABLE IF NOT EXISTS payment_watches (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    reply_message_id BIGINT NOT NULL,
    address TEXT NOT NULL,
    our_address TEXT NOT NULL,
    created_by_user_id BIGINT,
    mode TEXT NOT NULL CHECK (mode IN ('SINGLE', 'TEST_THEN_MAIN')),
    phase TEXT NOT NULL CHECK (phase IN ('TEST', 'MAIN')),
    status TEXT NOT NULL CHECK (status IN ('WATCHING', 'TIMED_OUT', 'COMPLETED', 'STOPPED')),
    timeout_at TIMESTAMPTZ NOT NULL,
    continue_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    timed_out_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notice_message_id BIGINT,
    chat_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_payment_watches_status_timeout
    ON payment_watches(status, timeout_at, id);
CREATE INDEX IF NOT EXISTS idx_payment_watches_chat_reply
    ON payment_watches(chat_id, reply_message_id, created_at DESC);


-- События по отслеживанию платежей (тестовый/основной перевод) ------------------
CREATE TABLE IF NOT EXISTS payment_watch_events (
    id BIGSERIAL PRIMARY KEY,
    watch_id BIGINT NOT NULL REFERENCES payment_watches(id) ON DELETE CASCADE,
    tx_hash TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('TEST', 'MAIN')),
    direction TEXT NOT NULL CHECK (direction IN ('IN', 'OUT')),
    amount NUMERIC(38,8) NOT NULL,
    token_symbol TEXT NOT NULL,
    confirmations INTEGER NOT NULL,
    block_ts TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (watch_id, tx_hash)
);
CREATE INDEX IF NOT EXISTS idx_payment_watch_events_watch_created
    ON payment_watch_events(watch_id, created_at, id);


-- Ордера по курсу (срабатывают при достижении target_ask) -----------------------
CREATE TABLE IF NOT EXISTS rate_orders (
    id BIGSERIAL PRIMARY KEY,
    client_chat_id BIGINT NOT NULL,
    client_name TEXT NOT NULL,
    requested_rate NUMERIC(18,8) NOT NULL,
    commission NUMERIC(18,8),
    target_ask NUMERIC(18,8),
    status TEXT NOT NULL DEFAULT 'draft',
    order_chat_id BIGINT,
    order_message_id BIGINT,
    created_by_user_id BIGINT,
    activated_by_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    triggered_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_rate_orders_status ON rate_orders(status);
CREATE INDEX IF NOT EXISTS idx_rate_orders_target_ask ON rate_orders(target_ask);


-- Настройки приложения (key/value) ---------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- «Живые» сообщения (одно редактируемое сообщение на ключ в чате) ---------------
CREATE TABLE IF NOT EXISTS live_messages (
    chat_id BIGINT NOT NULL,
    message_key TEXT NOT NULL,
    message_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chat_id, message_key)
);
CREATE INDEX IF NOT EXISTS idx_live_messages_message_key ON live_messages(message_key);


-- Генератор номеров заявок (table_req_id), монотонный, начинается со 100000 -----
CREATE SEQUENCE IF NOT EXISTS request_id_seq START WITH 100000 INCREMENT BY 1;
COMMENT ON SEQUENCE request_id_seq IS 'Последовательные номера заявок (монотонные)';
