-- =============================================================================
-- CRM: новые таблицы (workflow_crm.md, раздел 3.2) + хранилище значений статистик
-- (реверс Google-таблицы: листы Продажа/Покупка/Сделки/Главная/Расходы/Прибыль/
--  Оборотка/Посещаемость/Статистика/Контрагенты/стата/Данные).
-- Применение: psql "$DATABASE_URL" -f db_asyncpg/migrations/2026-07-08_crm_core.sql
-- Идемпотентно.
-- =============================================================================

BEGIN;

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
    currency_code TEXT NOT NULL,                 -- USDT, EUR, USD BL, USD WH, CNY…
    deal_id BIGINT REFERENCES deals(id),
    kind TEXT NOT NULL CHECK (kind IN ('purchase','sale','adjust')),
    qty NUMERIC(38,8) NOT NULL,                  -- + покупка / − продажа (знаковая)
    rub_amount NUMERIC(38,8) NOT NULL,           -- рублёвая стоимость движения (знаковая)
    qty_after NUMERIC(38,8) NOT NULL,            -- денормализованный остаток
    rub_cost_after NUMERIC(38,8) NOT NULL,       -- остаток рублёвой себестоимости
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_firm_position_moves_cur ON firm_position_moves(currency_code, id);

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
        CHECK (status IN ('pending','sent','failed')),
    attempts INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tg_outbox_pending ON tg_outbox(id) WHERE status = 'pending';

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
WHERE d.deal_type = 'sale' AND d.status <> 'canceled';

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
WHERE d.deal_type = 'purchase' AND d.status <> 'canceled';

COMMIT;
