-- =============================================================================
-- CRM: seed справочников из листа «Данные» боевой Google-таблицы (спека 4.6)
-- + владельцы оборотки со ставками (лист «Оборотка», колонка «Залог под %»)
-- + контрагенты из «Данные», + users из managers.
-- Применение: psql "$DATABASE_URL" -f db_asyncpg/migrations/2026-07-08_crm_seed.sql
-- Идемпотентно (ON CONFLICT DO NOTHING).
-- =============================================================================

BEGIN;

-- Города («Данные» E) ---------------------------------------------------------
INSERT INTO ref_values (kind, value, position) VALUES
    ('city', 'Екб', 1),
    ('city', 'Члб', 2),
    ('city', 'Мск', 3),
    ('city', 'Спб', 4),
    ('city', 'Тюмень', 5),
    ('city', 'Краснодар', 6),
    ('city', 'Новосибирск', 7),
    ('city', 'Уфа', 8)
ON CONFLICT (kind, value) DO NOTHING;

-- Валюты позиций фирмы («Данные» B) --------------------------------------------
INSERT INTO ref_values (kind, value, position) VALUES
    ('position_currency', 'USDT', 1),
    ('position_currency', 'EUR', 2),
    ('position_currency', 'USD BL', 3),
    ('position_currency', 'USD WH', 4),
    ('position_currency', 'CNY', 5)
ON CONFLICT (kind, value) DO NOTHING;

-- Категории расходов («Данные» D, ~30 шт.) --------------------------------------
INSERT INTO ref_values (kind, value, position) VALUES
    ('expense_category', 'TRX', 1),
    ('expense_category', 'Абонемент', 2),
    ('expense_category', 'Аренда', 3),
    ('expense_category', 'Билеты', 4),
    ('expense_category', 'Расходы Мск', 5),
    ('expense_category', 'Интернет', 6),
    ('expense_category', 'Еда', 7),
    ('expense_category', 'Расходники офис', 8),
    ('expense_category', 'Уборка', 9),
    ('expense_category', 'ТО магнера', 10),
    ('expense_category', 'Перестановка рубля', 11),
    ('expense_category', 'Доставка', 12),
    ('expense_category', 'Чаты', 13),
    ('expense_category', 'Бензин', 14),
    ('expense_category', 'Броник', 15),
    ('expense_category', 'ЗП', 16),
    ('expense_category', 'Такси', 17),
    ('expense_category', 'Расход ПРМ', 18),
    ('expense_category', 'Транспортный расход', 19),
    ('expense_category', 'AML', 20),
    ('expense_category', '%', 21),
    ('expense_category', 'Реклама', 22),
    ('expense_category', 'Процент от 1Ex члб', 23),
    ('expense_category', 'Дивиденды', 24),
    ('expense_category', 'Оплата Veesp vpd', 25),
    ('expense_category', 'Переработка', 26),
    ('expense_category', 'Паблик', 27),
    ('expense_category', 'Завтраки', 28),
    ('expense_category', 'Паркинг', 29),
    ('expense_category', 'Инкас члб', 30)
ON CONFLICT (kind, value) DO NOTHING;

-- Курьеры для типа «Доставка» («Данные» F) ---------------------------------------
INSERT INTO ref_values (kind, value, position) VALUES
    ('courier', 'Дмитрий', 1),
    ('courier', 'Александр', 2),
    ('courier', '1exch|crypto', 3),
    ('courier', '1exch|operator', 4),
    ('courier', '1exch|support', 5)
ON CONFLICT (kind, value) DO NOTHING;

-- Виды перестановок («Данные» H) → deals.body.transfer_kind ----------------------
INSERT INTO ref_values (kind, value, position) VALUES
    ('transfer_kind', 'Инвойс', 1),
    ('transfer_kind', 'Другой город', 2),
    ('transfer_kind', 'Доставка', 3),
    ('transfer_kind', 'Код', 4),
    ('transfer_kind', 'TRX', 5),
    ('transfer_kind', 'Конвертация', 6),
    ('transfer_kind', 'Юань', 7),
    ('transfer_kind', 'Обналичить', 8)
ON CONFLICT (kind, value) DO NOTHING;

-- Владельцы оборотки («Данные» G; ставки — лист «Оборотка», «Залог под %») -------
INSERT INTO capital_owners (name, monthly_rate) VALUES
    ('Алексей', NULL),
    ('Влад', NULL),
    ('Лев', NULL),
    ('Никита', 0.025),
    ('Костя', 0.025),
    ('Иван', 0.025),
    ('Сочи', 0.02),
    ('Бабушка', 0.02)
ON CONFLICT (name) DO NOTHING;

-- Контрагенты («Данные» C) --------------------------------------------------------
INSERT INTO counterparties (name) VALUES
    ('Алексей Зыря'),
    ('Рома берёза'),
    ('Рамиль'),
    ('Алексей М'),
    ('Рома Гусейнов')
ON CONFLICT (name) DO NOTHING;

-- Users: seed из managers (роль по умолчанию — manager, уточняется в админке) -----
INSERT INTO users (tg_user_id, display_name, role)
SELECT m.user_id, COALESCE(NULLIF(m.display_name, ''), m.user_id::text), 'manager'
FROM managers m
ON CONFLICT (tg_user_id) DO NOTHING;

COMMIT;
