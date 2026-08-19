-- =============================================================================
-- КАНОН СХЕМЫ (читаемый слепок). Схема версионируется Alembic
-- (db_asyncpg/migrations/, workflow_crm.md C0.1): любое изменение схемы —
-- новая ревизия в versions/ + синхронное обновление этого файла.
-- Применять schema.sql напрямую не нужно: бот на старте сам делает
-- `alembic upgrade head` (db_asyncpg/migrator.AlembicMigrator).
-- =============================================================================

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
    deal_id BIGINT,                                  -- FK добавляется после CREATE TABLE deals
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
CREATE INDEX IF NOT EXISTS idx_payment_watches_deal
    ON payment_watches(deal_id, created_at DESC) WHERE deal_id IS NOT NULL;


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


-- =============================================================================
-- CRM (workflow_crm.md раздел 3.2): сделки, пользователи, бухгалтерия, статистики
-- Синхронизировано с db_asyncpg/migrations/2026-07-08_crm_core.sql
-- =============================================================================

-- Пользователи CRM и роли -----------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT UNIQUE NOT NULL,           -- seed из managers.user_id
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('cashier','manager','accountant','owner','admin')),
    cities TEXT[] NOT NULL DEFAULT '{}',         -- для кассиров: свои города
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Контрагенты (лист «Контрагенты», карточка сделки: «% который нужно отдать КТ»)
CREATE TABLE IF NOT EXISTS counterparties (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    default_fee_percent NUMERIC(9,4),            -- фикс. процент из карточки КТ
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    comment TEXT
);

-- Сделки: единая шапка для всех 10 типов из CRM.pdf ----------------------------
-- Типоспецифичные поля живут в body (JSONB), контракт по типам:
--   sale:     {currency, qty, entry_rate, exit_rate, buy_amount, sale_amount,
--              spread, kt_spread, kt_amount, our_profit}
--             (лист «Продажа»: Валюта, Кол-во, Вход, Выход, Сумма покупки,
--              Сумма продажи, Спред, КТ Спред, КТ Сумма, наша прибыль)
--   purchase: {currency, qty, rate, rub_amount, seller}
--             (лист «Покупка»: Валюта, Сумма, Курс, В рубле, Продавец)
--   transfer_city: {transfer_kind, city_to, our_fee, kt_fee, amount, payout_amount}
--   conversion/yuan/invoice: {currency, amount, usdt_rate, usdt_amount, kt_usdt_amount}
--   deposit/withdrawal/delivery: {amount, denomination, courier_user_id,
--              courier_reward, address, scheduled_at}
--   profit:   {amount_expr}
CREATE TABLE IF NOT EXISTS deals (
    id BIGSERIAL PRIMARY KEY,
    deal_no BIGINT NOT NULL DEFAULT nextval('request_id_seq'),   -- сквозной номер
    deal_type TEXT NOT NULL CHECK (deal_type IN
        ('sale','purchase','deposit','withdrawal','delivery','transfer_city',
         'conversion','yuan','invoice','profit')),
    city TEXT NOT NULL DEFAULT 'Екб',
    client_id BIGINT REFERENCES clients(id),
    counterparty_id BIGINT REFERENCES counterparties(id),
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN
        ('new','fixed','awaiting_payment','balance_check','in_delivery',
         'done','canceled')),
    created_by BIGINT REFERENCES users(id),      -- «кто создал сделку» (из PDF)
    source TEXT NOT NULL DEFAULT 'crm'           -- 'tg_bot' | 'crm' | 'import'
        CHECK (source IN ('tg_bot','crm','import')),
    comment TEXT,
    penalty NUMERIC(38,8),                       -- «потом добавим строчку штраф»
    tronscan_url TEXT,                           -- чек по usdt-расчётам
    body JSONB NOT NULL DEFAULT '{}',
    profit NUMERIC(38,8),                        -- «Прибыль» строки журнала (до вычета КТ),
                                                 -- денормализовано для отчётов
    deal_at DATE NOT NULL DEFAULT CURRENT_DATE,  -- дата журнала (стата группируется по ней,
                                                 -- у импортированной истории != created_at)
    -- связь со старым контуром (пока живут оба):
    exchange_client_req_id TEXT REFERENCES exchange_request_links(client_req_id),
    source_kind TEXT,                            -- exchange | cash
    source_ref TEXT,                             -- стабильный id команды в исходном контуре
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deals_active
    ON deals(status) WHERE status NOT IN ('done','canceled');
CREATE INDEX IF NOT EXISTS idx_deals_client_created ON deals(client_id, created_at);
CREATE INDEX IF NOT EXISTS idx_deals_type_deal_at ON deals(deal_type, deal_at);
CREATE INDEX IF NOT EXISTS idx_deals_city_deal_at ON deals(city, deal_at);
CREATE INDEX IF NOT EXISTS idx_deals_deal_at ON deals(deal_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deals_deal_no ON deals(deal_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deals_source_ref
    ON deals(source, source_kind, source_ref) WHERE source_ref IS NOT NULL;

DO $$ BEGIN
    ALTER TABLE payment_watches
        ADD CONSTRAINT payment_watches_deal_id_fkey
        FOREIGN KEY (deal_id) REFERENCES deals(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- updated_at автоматом
CREATE OR REPLACE FUNCTION crm_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_deals_touch_updated ON deals;
CREATE TRIGGER trg_deals_touch_updated
    BEFORE UPDATE ON deals
    FOR EACH ROW EXECUTE FUNCTION crm_touch_updated_at();

-- Денежные «ноги» сделки: связь с журналом transactions -------------------------
CREATE TABLE IF NOT EXISTS deal_legs (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    transaction_id BIGINT REFERENCES transactions(id),
    direction TEXT NOT NULL CHECK (direction IN ('IN','OUT')),
    currency_code TEXT NOT NULL,
    amount NUMERIC(38,8) NOT NULL,
    rate NUMERIC(38,8),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','CANCELED'))
);
CREATE INDEX IF NOT EXISTS idx_deal_legs_deal ON deal_legs(deal_id);

-- История статусов (шаг 5 целевого flow) ----------------------------------------
CREATE TABLE IF NOT EXISTS deal_status_events (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    old_status TEXT,
    new_status TEXT NOT NULL,
    actor_user_id BIGINT REFERENCES users(id),
    payload JSONB NOT NULL DEFAULT '{}',         -- зафиксированный курс, сумма оплаты…
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_deal_status_events_deal ON deal_status_events(deal_id, id);

-- Маппинг клиент → контрагент с процентами (лист «Контрагенты», правая таблица) --
CREATE TABLE IF NOT EXISTS client_kt_fees (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id),
    counterparty_id BIGINT NOT NULL REFERENCES counterparties(id),
    sale_fee NUMERIC(9,4),                       -- «% с продажи» — КТ-спред на единицу валюты
    purchase_fee NUMERIC(9,4),                   -- «% с покупки»
    UNIQUE (client_id, counterparty_id)
);

-- Кассы фирмы по городам и валютам («Главная»: RUB ЕКБ / Мск Поэты / Члб / …) ----
CREATE TABLE IF NOT EXISTS cash_desks (
    id BIGSERIAL PRIMARY KEY,
    city TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',               -- в городе бывает >1 кассы («Мск Поэты», «Мск BS»)
    currency_code TEXT NOT NULL DEFAULT 'RUB',
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (city, name, currency_code)
);
CREATE TABLE IF NOT EXISTS cash_desk_moves (     -- перемещения между кассами + аудит
    id BIGSERIAL PRIMARY KEY,
    from_desk_id BIGINT REFERENCES cash_desks(id),
    to_desk_id BIGINT REFERENCES cash_desks(id),
    amount NUMERIC(38,8) NOT NULL,
    balance_after_from NUMERIC(38,8),
    balance_after_to NUMERIC(38,8),
    deal_id BIGINT REFERENCES deals(id),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cash_desk_moves_from ON cash_desk_moves(from_desk_id, id);
CREATE INDEX IF NOT EXISTS idx_cash_desk_moves_to ON cash_desk_moves(to_desk_id, id);

-- Расходы (лист «Расходы»: постоянные + переменные) ------------------------------
CREATE TABLE IF NOT EXISTS expenses (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('fixed','variable')),
    category TEXT NOT NULL,                      -- fixed: категория из справочника; variable: назначение
    city TEXT,
    amount NUMERIC(38,8) NOT NULL,
    currency_code TEXT NOT NULL DEFAULT 'RUB',
    comment TEXT,
    expense_at DATE NOT NULL DEFAULT CURRENT_DATE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_expenses_at ON expenses(expense_at);
CREATE INDEX IF NOT EXISTS idx_expenses_kind_at ON expenses(kind, expense_at);
CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);

-- Оборотка: владельцы капитала и вложения (лист «Оборотка») ----------------------
CREATE TABLE IF NOT EXISTS capital_owners (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    monthly_rate NUMERIC(9,4),                   -- «Залог под %» в месяц (0.02, 0.025…); NULL = без выплат
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS capital_moves (       -- журнал вкладов/выводов (знаковая сумма, как в листе)
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capital_owners(id),
    amount NUMERIC(38,8) NOT NULL,               -- + вклад / − вывод
    move_at DATE NOT NULL DEFAULT CURRENT_DATE,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_capital_moves_owner ON capital_moves(owner_id, move_at);
CREATE TABLE IF NOT EXISTS capital_payouts (     -- фактические выплаты владельцам (правая табличка листа)
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES capital_owners(id),
    period DATE NOT NULL,                        -- месяц выплаты (1-е число)
    accrued NUMERIC(38,8) NOT NULL,              -- начислено = вклад × ставка
    paid NUMERIC(38,8) NOT NULL DEFAULT 0,       -- фактически выплачено
    UNIQUE (owner_id, period)
);

-- Валютная позиция фирмы (ядро модели, спека 4.2) --------------------------------
-- Средневзвешенная себестоимость: покупка увеличивает qty и rub_cost, продажа
-- списывает по среднему курсу. Позиция/средний курс = последняя строка по валюте
-- (та же конструкция, что transactions.balance_after), НЕ пересчёт истории.
CREATE TABLE IF NOT EXISTS firm_position_moves (
    id BIGSERIAL PRIMARY KEY,
    currency_code TEXT NOT NULL,                 -- USDT, EUR, USD_BL, USD_WH
    deal_id BIGINT REFERENCES deals(id),
    deal_leg_id BIGINT REFERENCES deal_legs(id),
    kind TEXT NOT NULL CHECK (kind IN (
        'opening','purchase','sale','adjust','reversal','profit_capitalization'
    )),
    qty NUMERIC(38,8) NOT NULL,                  -- + покупка / − продажа (знаковая)
    rub_amount NUMERIC(38,8) NOT NULL,           -- рублёвая стоимость движения (знаковая)
    qty_after NUMERIC(38,8) NOT NULL CHECK (qty_after >= 0),
    rub_cost_after NUMERIC(38,8) NOT NULL CHECK (rub_cost_after >= 0),
    entry_rate NUMERIC(38,8),
    effective_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT REFERENCES users(id),
    idempotency_key TEXT NOT NULL,
    reversal_of_id BIGINT REFERENCES firm_position_moves(id),
    reason TEXT,
    CHECK (kind <> 'adjust' OR NULLIF(BTRIM(reason), '') IS NOT NULL),
    CHECK (
        kind <> 'reversal'
        OR (reversal_of_id IS NOT NULL AND NULLIF(BTRIM(reason), '') IS NOT NULL)
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_cur ON firm_position_moves(currency_code, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_idempotency
    ON firm_position_moves(idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_reversal
    ON firm_position_moves(reversal_of_id) WHERE reversal_of_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_position_moves_deal_leg
    ON firm_position_moves(deal_leg_id) WHERE deal_leg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_effective_at
    ON firm_position_moves(effective_at);

CREATE OR REPLACE FUNCTION prevent_firm_position_move_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'firm_position_moves is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_firm_position_moves_append_only ON firm_position_moves;
CREATE TRIGGER trg_firm_position_moves_append_only
BEFORE UPDATE OR DELETE ON firm_position_moves
FOR EACH ROW
EXECUTE FUNCTION prevent_firm_position_move_mutation();

-- Фактические остатки кошельков по валютам («Разрыв USDT» на Главной считается
-- от руками вбитого факта; для USDT позже — из payment_watch/Tronscan) -----------
CREATE TABLE IF NOT EXISTS firm_wallet_facts (
    currency_code TEXT PRIMARY KEY,
    actual_qty NUMERIC(38,8) NOT NULL,
    comment TEXT,
    updated_by BIGINT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Внутренние балансы SkyEx («Балансы SkyEx» на Главной: Баланс ВВ, Никита, Влад,
-- Лев, Монах, Миша, б_бабушка, б_сочи, б_костет, «разрыв 17.10»…) ----------------
-- Это НЕ клиенты и НЕ кассы.
CREATE TABLE IF NOT EXISTS internal_accounts (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'employee'
        CHECK (kind IN ('employee','partner','owner_pledge','tech')),  -- tech = разрывы/фиксации
    balance NUMERIC(38,8) NOT NULL DEFAULT 0,    -- RUB-эквивалент, знаковый
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS internal_account_moves (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES internal_accounts(id),
    amount NUMERIC(38,8) NOT NULL,
    balance_after NUMERIC(38,8) NOT NULL,
    deal_id BIGINT REFERENCES deals(id),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_internal_account_moves_acc ON internal_account_moves(account_id, id);

-- Посещаемость (лист «Посещаемость»: сотрудники × дни + переработки) --------------
CREATE TABLE IF NOT EXISTS attendance (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    work_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'work'          -- Рабочий день / Пропуск / Полёт / Дистант
        CHECK (status IN ('work','absent','flight','remote')),
    overtime_hours NUMERIC(5,2) NOT NULL DEFAULT 0,
    UNIQUE (user_id, work_date)
);

-- Комментарии в карточке клиента (из CRM.pdf) -------------------------------------
CREATE TABLE IF NOT EXISTS client_comments (
    id BIGSERIAL PRIMARY KEY,
    client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    author_user_id BIGINT REFERENCES users(id),
    text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_client_comments_client
    ON client_comments(client_id, id) WHERE deleted_at IS NULL;

-- Outbox для Telegram (спека 2.2) --------------------------------------------------
CREATE TABLE IF NOT EXISTS tg_outbox (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,                          -- 'edit_request_card' | 'notify_client' | ...
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processing','sent','failed')),
    attempts INT NOT NULL DEFAULT 0,
    dedup_key TEXT,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_tg_outbox_dedup_key
    ON tg_outbox(dedup_key) WHERE dedup_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tg_outbox_pending
    ON tg_outbox(available_at, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_tg_outbox_processing
    ON tg_outbox(locked_at, id) WHERE status = 'processing';

-- Справочники (лист «Данные»: города, категории расходов, курьеры, виды
-- перестановок, валюты позиций) — редактируются в админке -------------------------
CREATE TABLE IF NOT EXISTS ref_values (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,       -- 'city' | 'expense_category' | 'courier' | 'transfer_kind' | 'position_currency'
    value TEXT NOT NULL,
    position INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (kind, value)
);

-- Типизированные представления над deals для статистик ----------------------------
-- (все расчёты листов «Статистика»/«стата»/«Прибыль»/«Контрагенты» идут через них)
CREATE OR REPLACE VIEW crm_sales AS
SELECT d.id,
       d.deal_no,
       d.deal_at,
       btrim(d.city) AS city,
       d.client_id,
       d.counterparty_id,
       d.status,
       upper(btrim(d.body->>'currency'))  AS currency_code,
       (d.body->>'qty')::numeric          AS qty,          -- «Кол-во»
       (d.body->>'entry_rate')::numeric   AS entry_rate,   -- «Вход»
       (d.body->>'exit_rate')::numeric    AS exit_rate,    -- «Выход»
       (d.body->>'buy_amount')::numeric   AS buy_amount,   -- «Сумма покупки» = вход×кол-во
       (d.body->>'sale_amount')::numeric  AS sale_amount,  -- «Сумма продажи» = выход×кол-во
       (d.body->>'kt_spread')::numeric    AS kt_spread,    -- «КТ Спред»
       (d.body->>'kt_amount')::numeric    AS kt_amount,    -- «КТ Сумма» = КТ-спред×кол-во
       d.profit,                                           -- «Прибыль» = продажа−покупка
       (d.body->>'our_profit')::numeric   AS our_profit    -- «наша прибыль» = прибыль−КТ-сумма
FROM deals d
WHERE d.deal_type = 'sale' AND d.status = 'done';

CREATE OR REPLACE VIEW crm_purchases AS
SELECT d.id,
       d.deal_no,
       d.deal_at,
       btrim(d.city) AS city,
       d.client_id,
       d.counterparty_id,
       d.status,
       upper(btrim(d.body->>'currency')) AS currency_code,
       (d.body->>'qty')::numeric         AS qty,           -- «Сумма» (кол-во валюты)
       (d.body->>'rate')::numeric        AS rate,          -- «Курс»
       (d.body->>'rub_amount')::numeric  AS rub_amount,    -- «В рубле» = сумма×курс
       d.body->>'seller'                 AS seller
FROM deals d
WHERE d.deal_type = 'purchase' AND d.status = 'done';

-- Accounting registries and operational journals --------------------------------
CREATE TABLE IF NOT EXISTS cash_chat_registry (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL UNIQUE,
    client_id BIGINT NOT NULL UNIQUE REFERENCES clients(id),
    city TEXT NOT NULL CHECK (NULLIF(BTRIM(city), '') IS NOT NULL),
    location_name TEXT NOT NULL CHECK (NULLIF(BTRIM(location_name), '') IS NOT NULL),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at TIMESTAMPTZ,
    CHECK ((is_active AND deactivated_at IS NULL) OR (NOT is_active AND deactivated_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_cash_chat_registry_active_city
    ON cash_chat_registry(LOWER(BTRIM(city))) WHERE is_active;
CREATE INDEX IF NOT EXISTS idx_cash_chat_registry_active_client
    ON cash_chat_registry(client_id) WHERE is_active;

CREATE TABLE IF NOT EXISTS firm_wallet_addresses (
    id BIGSERIAL PRIMARY KEY,
    network TEXT NOT NULL CHECK (NULLIF(BTRIM(network), '') IS NOT NULL),
    address TEXT NOT NULL CHECK (NULLIF(BTRIM(address), '') IS NOT NULL),
    active_from TIMESTAMPTZ NOT NULL,
    active_to TIMESTAMPTZ,
    reason TEXT NOT NULL CHECK (NULLIF(BTRIM(reason), '') IS NOT NULL),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (network, address),
    CHECK (active_to IS NULL OR active_to > active_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_firm_wallet_addresses_active_network
    ON firm_wallet_addresses(UPPER(BTRIM(network))) WHERE active_to IS NULL;

CREATE TABLE IF NOT EXISTS firm_wallet_fact_snapshots (
    id BIGSERIAL PRIMARY KEY,
    currency_code TEXT NOT NULL CHECK (
        currency_code = UPPER(BTRIM(currency_code))
        AND currency_code IN ('EUR', 'USDT', 'USD_BL', 'USD_WH')
    ),
    address_id BIGINT REFERENCES firm_wallet_addresses(id),
    actual_qty NUMERIC(38,8) NOT NULL CHECK (actual_qty >= 0),
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (
        source IN ('manual', 'payment_watch', 'tronscan', 'cash_chat_ledger', 'import')
    ),
    actor_user_id BIGINT REFERENCES users(id),
    comment TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_firm_wallet_fact_snapshots_latest
    ON firm_wallet_fact_snapshots(currency_code, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_firm_wallet_fact_snapshots_address
    ON firm_wallet_fact_snapshots(address_id, observed_at DESC) WHERE address_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS deal_settlements (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    payment_event_id BIGINT REFERENCES payment_watch_events(id),
    direction TEXT NOT NULL CHECK (direction IN ('incoming', 'outgoing')),
    currency_code TEXT NOT NULL CHECK (NULLIF(BTRIM(currency_code), '') IS NOT NULL),
    expected_qty NUMERIC(38,8) NOT NULL CHECK (expected_qty > 0),
    actual_qty NUMERIC(38,8) NOT NULL CHECK (actual_qty > 0),
    delta_qty NUMERIC(38,8) GENERATED ALWAYS AS (actual_qty - expected_qty) STORED,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status TEXT NOT NULL CHECK (review_status IN ('matched', 'needs_review', 'resolved')),
    resolution TEXT CHECK (resolution IN ('accept_actual', 'amend_unposted', 'cancel_and_recreate')),
    resolved_by BIGINT REFERENCES users(id),
    resolved_at TIMESTAMPTZ,
    comment TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (review_status = 'matched' AND delta_qty = 0 AND resolution IS NULL
            AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (review_status = 'needs_review' AND delta_qty <> 0 AND resolution IS NULL
            AND resolved_by IS NULL AND resolved_at IS NULL)
        OR (review_status = 'resolved' AND resolution IS NOT NULL
            AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_deal_settlements_payment_event
    ON deal_settlements(payment_event_id) WHERE payment_event_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deal_settlements_deal
    ON deal_settlements(deal_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deal_settlements_review
    ON deal_settlements(created_at) WHERE review_status = 'needs_review';

CREATE TABLE IF NOT EXISTS usdt_fulfillment_queue (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    request_kind TEXT NOT NULL CHECK (request_kind IN ('sale', 'client_withdrawal')),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    sequence_no BIGINT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'executing', 'completed', 'canceled')
    ),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reordered_at TIMESTAMPTZ,
    reordered_by BIGINT REFERENCES users(id),
    completed_at TIMESTAMPTZ,
    cancel_reason TEXT,
    CHECK ((reordered_at IS NULL) = (reordered_by IS NULL)),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (status <> 'canceled' OR NULLIF(BTRIM(cancel_reason), '') IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_usdt_fulfillment_queue_active_deal
    ON usdt_fulfillment_queue(deal_id) WHERE status IN ('queued', 'executing');
CREATE INDEX IF NOT EXISTS idx_usdt_fulfillment_queue_order
    ON usdt_fulfillment_queue(sequence_no) WHERE status IN ('queued', 'executing');

CREATE TABLE IF NOT EXISTS profit_usdt_accruals (
    id BIGSERIAL PRIMARY KEY,
    deal_id BIGINT NOT NULL REFERENCES deals(id),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    valuation_rate NUMERIC(38,8),
    rub_value NUMERIC(38,8),
    quote_source TEXT,
    quote_observed_at TIMESTAMPTZ,
    valuation_actor_id BIGINT REFERENCES users(id),
    valuation_reason TEXT,
    capitalization_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        capitalization_status IN ('pending', 'capitalized')
    ),
    capitalized_at TIMESTAMPTZ,
    capitalization_move_id BIGINT REFERENCES firm_position_moves(id),
    settlement_status TEXT NOT NULL DEFAULT 'in_transit' CHECK (
        settlement_status IN ('in_transit', 'received')
    ),
    received_at TIMESTAMPTZ,
    payment_event_id BIGINT UNIQUE REFERENCES payment_watch_events(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        (valuation_rate IS NULL AND rub_value IS NULL AND quote_source IS NULL
            AND quote_observed_at IS NULL AND valuation_actor_id IS NULL
            AND valuation_reason IS NULL)
        OR (valuation_rate > 0 AND rub_value IS NOT NULL
            AND NULLIF(BTRIM(quote_source), '') IS NOT NULL
            AND quote_observed_at IS NOT NULL
            AND (
                quote_source <> 'manual'
                OR (valuation_actor_id IS NOT NULL
                    AND NULLIF(BTRIM(valuation_reason), '') IS NOT NULL)
            ))
    ),
    CHECK (
        (capitalization_status = 'pending' AND capitalized_at IS NULL
            AND capitalization_move_id IS NULL)
        OR (capitalization_status = 'capitalized' AND valuation_rate IS NOT NULL
            AND capitalized_at IS NOT NULL AND capitalization_move_id IS NOT NULL)
    ),
    CHECK (
        (settlement_status = 'in_transit' AND received_at IS NULL)
        OR (settlement_status = 'received' AND received_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_pending
    ON profit_usdt_accruals(created_at, id) WHERE capitalization_status = 'pending';
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_in_transit
    ON profit_usdt_accruals(created_at, id) WHERE settlement_status = 'in_transit';
CREATE INDEX IF NOT EXISTS idx_profit_usdt_accruals_capitalization_move
    ON profit_usdt_accruals(capitalization_move_id)
    WHERE capitalization_move_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS partner_transfer_watches (
    id BIGSERIAL PRIMARY KEY,
    purchase_deal_id BIGINT NOT NULL REFERENCES deals(id),
    sale_deal_id BIGINT NOT NULL REFERENCES deals(id),
    network TEXT NOT NULL CHECK (NULLIF(BTRIM(network), '') IS NOT NULL),
    from_address TEXT NOT NULL CHECK (NULLIF(BTRIM(from_address), '') IS NOT NULL),
    to_address TEXT NOT NULL CHECK (NULLIF(BTRIM(to_address), '') IS NOT NULL),
    expected_qty NUMERIC(38,8) NOT NULL CHECK (expected_qty > 0),
    actual_qty NUMERIC(38,8),
    status TEXT NOT NULL DEFAULT 'watching' CHECK (
        status IN ('watching', 'matched', 'needs_review', 'stopped')
    ),
    tx_hash TEXT,
    event_index INTEGER,
    confirmed_at TIMESTAMPTZ,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ,
    stop_reason TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    CHECK ((tx_hash IS NULL) = (event_index IS NULL)),
    CHECK ((tx_hash IS NULL) = (confirmed_at IS NULL)),
    CHECK (actual_qty IS NULL OR actual_qty > 0),
    CHECK (status = 'watching' OR status = 'stopped' OR tx_hash IS NOT NULL),
    CHECK (status NOT IN ('matched', 'needs_review') OR actual_qty IS NOT NULL),
    CHECK (status <> 'stopped' OR (
        stopped_at IS NOT NULL AND NULLIF(BTRIM(stop_reason), '') IS NOT NULL
    ))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_transfer_watches_event
    ON partner_transfer_watches(UPPER(BTRIM(network)), LOWER(BTRIM(tx_hash)), event_index)
    WHERE tx_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_transfer_watches_active_sale
    ON partner_transfer_watches(sale_deal_id) WHERE status = 'watching';

CREATE TABLE IF NOT EXISTS partner_purchase_allocations (
    id BIGSERIAL PRIMARY KEY,
    purchase_deal_id BIGINT NOT NULL REFERENCES deals(id),
    sale_deal_id BIGINT REFERENCES deals(id),
    destination_kind TEXT NOT NULL CHECK (destination_kind IN ('firm_wallet', 'client_direct')),
    qty NUMERIC(38,8) NOT NULL CHECK (qty > 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'settled', 'reversed')),
    transfer_watch_id BIGINT REFERENCES partner_transfer_watches(id),
    position_move_id BIGINT UNIQUE REFERENCES firm_position_moves(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ,
    reversed_at TIMESTAMPTZ,
    reversal_reason TEXT,
    CHECK (
        (destination_kind = 'firm_wallet' AND sale_deal_id IS NULL
            AND transfer_watch_id IS NULL)
        OR (destination_kind = 'client_direct' AND sale_deal_id IS NOT NULL
            AND transfer_watch_id IS NOT NULL AND position_move_id IS NULL)
    ),
    CHECK (status <> 'settled' OR settled_at IS NOT NULL),
    CHECK (status <> 'active' OR (settled_at IS NULL AND reversed_at IS NULL)),
    CHECK (status <> 'reversed' OR (
        reversed_at IS NOT NULL AND NULLIF(BTRIM(reversal_reason), '') IS NOT NULL
    ))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_purchase_allocations_active_sale
    ON partner_purchase_allocations(sale_deal_id)
    WHERE sale_deal_id IS NOT NULL AND status = 'active';
CREATE INDEX IF NOT EXISTS idx_partner_purchase_allocations_purchase
    ON partner_purchase_allocations(purchase_deal_id, created_at, id);

CREATE OR REPLACE FUNCTION prevent_accounting_snapshot_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'firm_wallet_fact_snapshots is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_firm_wallet_fact_snapshots_append_only
    ON firm_wallet_fact_snapshots;
CREATE TRIGGER trg_firm_wallet_fact_snapshots_append_only
BEFORE UPDATE OR DELETE ON firm_wallet_fact_snapshots
FOR EACH ROW EXECUTE FUNCTION prevent_accounting_snapshot_mutation();
