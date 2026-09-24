# Развёртывание SkyEx CRM на Ubuntu/Debian

Инструкция рассчитана на один Linux-сервер с `systemd`, PostgreSQL и доменом
`skyex.work.gd`. Backend запускает Telegram-бота и FastAPI в одном процессе:

- backend и Telegram polling: `/root/CRM`, API `127.0.0.1:8000`;
- frontend и standalone bundle: `/root/bookkeeping`, порт `127.0.0.1:3000`;
- Nginx принимает публичные `80/443` и проксирует запросы внутрь;
- оба systemd-сервиса запускаются от `root`.

Не запускайте одновременно старый SkyEx nanи новый CRM с одним `BOT_TOKEN`: Telegram
polling должен выполняться только одним процессом.

## 1. Предварительные проверки

Проверьте DNS до начала настройки HTTPS:

```bash
dig +short A skyex.work.gd
curl -4 https://icanhazip.com
```

Оба адреса должны совпадать. В firewall провайдера должны быть открыты TCP `22`,
`80` и `443`. Порты `3000`, `8000` и `5432` публиковать нельзя.

Инструкция ниже предполагает работу в root-shell:

```bash
sudo -i
```

## 2. Системные пакеты

```bash
apt update
apt install -y \
  git curl ca-certificates xz-utils rsync openssl \
  python3 python3-venv python3-pip build-essential libpq-dev \
  postgresql-client nginx certbot python3-certbot-nginx
```

Проект требует Python `3.11+` и Node.js `20.9+`. Рекомендуемая проверенная ветка
Node.js — `22 LTS`.

Проверьте Python:

```bash
python3 --version
```

Установка официального Node.js binary для сервера `x86_64`:

```bash
mkdir -p /opt/node
cd /tmp
curl -fLO https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz
curl -fLO https://nodejs.org/dist/v22.23.2/SHASUMS256.txt
grep ' node-v22.23.2-linux-x64.tar.xz$' SHASUMS256.txt | sha256sum --check
tar -xJf node-v22.23.2-linux-x64.tar.xz --strip-components=1 -C /opt/node
export PATH="/opt/node/bin:$PATH"
corepack enable --install-directory /opt/node/bin
corepack prepare pnpm@11.3.0 --activate
node --version
pnpm --version
```

Для ARM64 замените `linux-x64` на `linux-arm64` в имени файла и строке проверки.

## 3. Получение исходников

```bash
mkdir -p /etc/skyex /var/backups/skyex
chmod 700 /etc/skyex /var/backups/skyex
```

Если каталоги уже находятся на сервере, проверьте remotes и подтяните только
fast-forward изменения:

```bash
git -C /root/CRM remote -v
git -C /root/CRM pull --ff-only

git -C /root/bookkeeping remote -v
git -C /root/bookkeeping pull --ff-only
```

На чистом сервере вместо этого клонируйте репозитории:

```bash
git clone https://github.com/Molotiloff/CRM.git /root/CRM
git clone https://github.com/Molotiloff/bookkeeping.git /root/bookkeeping
```

Для приватных репозиториев настройте GitHub deploy key или выполните clone с уже
настроенными credentials. Не сохраняйте GitHub token в URL репозитория или shell
history.

Зафиксируйте ревизии, которые разворачиваются:

```bash
git -C /root/CRM rev-parse HEAD
git -C /root/bookkeeping rev-parse HEAD
```

## 4. Backend: окружение и зависимости

Создайте виртуальное окружение:

```bash
cd /root/CRM
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Создайте `/etc/skyex/crm-backend.env`. За основу возьмите текущий production
`.env` старого SkyEx, чтобы сохранить chat ID, Google credentials, AML и прочие
интеграции. Обязательно добавьте CRM-настройки:

```dotenv
BOT_TOKEN=replace-me
DATABASE_URL=postgresql://skyex:replace-me@127.0.0.1:5432/skyex

# Сгенерировать командой: openssl rand -base64 48
CRM_JWT_SECRET=replace-with-random-secret-at-least-32-characters

CRM_API_ENABLED=true
CRM_API_HOST=127.0.0.1
CRM_API_PORT=8000
CRM_API_CORS_ORIGINS=
CRM_API_WS_ALLOWED_ORIGINS=https://skyex.work.gd
CRM_API_WS_MAX_CONNECTIONS=100
CRM_API_JWT_TTL_SECONDS=604800
CRM_DEV_AUTH_BYPASS=false
CRM_DEV_TG_USER_ID=

TELEGRAM_OIDC_CLIENT_ID=replace-me
TELEGRAM_OIDC_CLIENT_SECRET=replace-me

MAIN_DASHBOARD_SOURCE_MODE=db_primary
DASHBOARD_SHADOW_ABSOLUTE_TOLERANCE=0.01
DASHBOARD_SHADOW_RELATIVE_TOLERANCE=0.000001
```

Остальные обязательные переменные (`ADMIN_IDS`, чаты, Google Sheets, GetBlock,
Tronscan и другие) перенесите из рабочего `.env` и сверьте с
`/root/CRM/.env.example`.

Для Google service account используется отдельный файл в игнорируемом Git
каталоге `secrets`, а не inline JSON:

```bash
install -d -m 700 /root/CRM/secrets
install -m 600 /root/SkyEx/exchange-bot-475712-e48b74e4ec40.json \
  /root/CRM/secrets/google-service-account.json
chown root:root /root/CRM/secrets/google-service-account.json
python3 -m json.tool /root/CRM/secrets/google-service-account.json >/dev/null
```

И добавить:

```dotenv
GOOGLE_CREDENTIALS_FILE=/root/CRM/secrets/google-service-account.json
```

Защитите окружение:

```bash
chown root:root /etc/skyex/crm-backend.env
chmod 600 /etc/skyex/crm-backend.env
```

Перенесите архив сообщений из старого SkyEx. Пока старый бот работает, это
предварительная копия; финальная синхронизация выполняется после его остановки:

```bash
install -d -m 700 /root/CRM/message_archive_data/media
install -d -m 700 /root/CRM/message_archive_data/tmp

rsync -aH --info=progress2 \
  /root/SkyEx/message_archive_data/media/ \
  /root/CRM/message_archive_data/media/
rsync -aH --info=progress2 \
  /root/SkyEx/message_archive_data/tmp/ \
  /root/CRM/message_archive_data/tmp/
```

В `/etc/skyex/crm-backend.env` должны использоваться новые пути:

```dotenv
MESSAGE_ARCHIVE_MEDIA_DIR=/root/CRM/message_archive_data/media
MESSAGE_ARCHIVE_TEMP_DIR=/root/CRM/message_archive_data/tmp
```

Проверьте загрузку конфигурации без печати секретов:

```bash
cd /root/CRM
(
  set -a
  . /etc/skyex/crm-backend.env
  set +a
  .venv/bin/python -c 'from config import Config; Config.from_env(); print("config ok")'
)
```

## 5. PostgreSQL, backup и миграции

Если используется существующая production-база SkyEx, не создавайте новую и не
восстанавливайте старые тестовые dump-файлы. Сначала сделайте backup:

```bash
(
  set -a
  . /etc/skyex/crm-backend.env
  set +a
  pg_dump --format=custom --file=/var/backups/skyex/pre-crm.dump "$DATABASE_URL"
)
chmod 600 /var/backups/skyex/pre-crm.dump
```

Перед миграцией остановите старого бота. Сначала найдите точное имя сервиса:

```bash
systemctl list-units --type=service | grep -Ei 'skyex|bot'
```

Затем остановите найденный старый сервис, например:

```bash
systemctl stop skyex-bot.service
systemctl disable skyex-bot.service
```

После остановки выполните финальную синхронизацию архива. Она докопирует файлы,
которые могли появиться после первого прохода:

```bash
rsync -aH --info=progress2 \
  /root/SkyEx/message_archive_data/media/ \
  /root/CRM/message_archive_data/media/
rsync -aH --info=progress2 \
  /root/SkyEx/message_archive_data/tmp/ \
  /root/CRM/message_archive_data/tmp/
```

Проверьте, что второй процесс бота не остался запущен:

```bash
pgrep -af 'python.*(main.py|bot_app)' || true
```

Примените миграции вручную до запуска systemd. Backend также проверяет миграции
на старте, но отдельный запуск позволяет увидеть ошибку до включения бота:

```bash
cd /root/CRM
(
  set -a
  . /etc/skyex/crm-backend.env
  set +a
  .venv/bin/alembic upgrade head
  .venv/bin/alembic current
)
```

Не выполняйте `alembic downgrade` на production без отдельного плана возврата.

## 6. Frontend: окружение и standalone build

Создайте `/etc/skyex/crm-front.env`:

```dotenv
NODE_ENV=production
CRM_USE_API=true
CRM_API_BASE_URL=http://127.0.0.1:8000/api/v1
CRM_WS_URL=wss://skyex.work.gd/ws
CRM_FRONTEND_ORIGIN=https://skyex.work.gd
TELEGRAM_OIDC_CLIENT_ID=replace-me
CRM_DEV_AUTH_BYPASS=false
```

`TELEGRAM_OIDC_CLIENT_ID` должен совпадать с backend. OIDC secret во frontend
никогда не добавляется.

```bash
chown root:root /etc/skyex/crm-front.env
chmod 600 /etc/skyex/crm-front.env
```

Соберите frontend. Окружение нужно подключить и во время build, потому что часть
конфигурации Next.js фиксируется при сборке:

```bash
cd /root/bookkeeping
(
  set -a
  . /etc/skyex/crm-front.env
  set +a
  export PATH="/opt/node/bin:$PATH"
  pnpm install --frozen-lockfile
  pnpm run lint
  pnpm run build
)
```

Команда `build` автоматически кладёт `public/` и `.next/static/` внутрь
`.next/standalone/`. Проверьте собранный bundle:

```bash
cd /root/bookkeeping
test -f .next/standalone/server.js
test -d .next/standalone/.next/static
test -d .next/standalone/public
```

## 7. Systemd

Установите подготовленные unit-файлы:

```bash
cp /root/CRM/deploy/systemd/skyex-crm-backend.service \
  /etc/systemd/system/skyex-crm-backend.service
cp /root/CRM/deploy/systemd/skyex-crm-front.service \
  /etc/systemd/system/skyex-crm-front.service

systemctl daemon-reload
systemctl enable skyex-crm-backend.service skyex-crm-front.service
systemctl start skyex-crm-backend.service
```

Проверьте backend до запуска frontend:

```bash
systemctl status skyex-crm-backend.service --no-pager
journalctl -u skyex-crm-backend.service -n 100 --no-pager
curl --fail http://127.0.0.1:8000/api/v1/health
```

Ожидаемый ответ: `{"status":"ok"}`. Затем запустите frontend:

```bash
systemctl start skyex-crm-front.service
systemctl status skyex-crm-front.service --no-pager
journalctl -u skyex-crm-front.service -n 100 --no-pager
curl --head http://127.0.0.1:3000/login
```

## 8. Первый сертификат Let's Encrypt

Полный Nginx-конфиг ссылается на сертификат, поэтому сначала нужен временный
HTTP-only server. Отключите стандартный сайт и создайте bootstrap-конфиг:

```bash
rm -f /etc/nginx/sites-enabled/default

cat >/etc/nginx/conf.d/skyex-bootstrap.conf <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name skyex.work.gd;

    location / {
        return 200 "SkyEx certificate bootstrap\n";
        add_header Content-Type text/plain;
    }
}
NGINX

nginx -t
systemctl reload nginx
curl http://skyex.work.gd/
```

Получите сертификат:

```bash
certbot certonly --nginx -d skyex.work.gd
certbot certificates
```

Убедитесь, что Certbot создал именно пути:

```text
/etc/letsencrypt/live/skyex.work.gd/fullchain.pem
/etc/letsencrypt/live/skyex.work.gd/privkey.pem
```

Если Certbot добавил суффикс вроде `skyex.work.gd-0001`, исправьте пути в полном
Nginx-конфиге перед следующим шагом.

## 9. Production Nginx

```bash
rm -f /etc/nginx/conf.d/skyex-bootstrap.conf
cp /root/CRM/deploy/nginx/skyex-crm.locations.conf \
  /etc/nginx/conf.d/skyex-crm.conf

nginx -t
systemctl reload nginx
systemctl status nginx --no-pager
```

Конфигурация:

- отклоняет неизвестные домены кодом `444`;
- перенаправляет HTTP на HTTPS;
- ограничивает частоту API и OIDC-запросов;
- ограничивает число WebSocket-соединений с одного IP;
- проксирует `/api/` в `127.0.0.1:8000`;
- проксирует `/ws/deals` в backend с Upgrade-заголовками;
- проксирует остальные маршруты в Next.js на `127.0.0.1:3000`;
- добавляет HSTS, CSP и остальные security headers.

Если включён UFW:

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw status
```

Не добавляйте правила для `3000`, `8000` или `5432`.

Проверьте автоматическое продление сертификата:

```bash
systemctl status certbot.timer --no-pager
certbot renew --dry-run
```

## 10. Telegram OIDC

В BotFather/Login Widget должен быть разрешён домен:

```text
skyex.work.gd
```

Redirect URI frontend:

```text
https://skyex.work.gd/api/auth/telegram/oidc/callback
```

В backend должны быть заданы client ID и client secret, во frontend — только
client ID. После смены `CRM_JWT_SECRET` все ранее выданные сессии становятся
недействительными, и пользователи должны войти повторно.

## 11. Проверка после запуска

Локальные сервисы:

```bash
curl --fail http://127.0.0.1:8000/api/v1/health
curl --head http://127.0.0.1:3000/login
ss -lntp | grep -E ':(3000|8000|5432)\b'
```

`3000` и `8000` должны слушать только `127.0.0.1`. PostgreSQL также не должен
слушать публичный интерфейс без отдельной сетевой защиты.

Публичные проверки:

```bash
curl --head https://skyex.work.gd/login
curl --include https://skyex.work.gd/api/v1/health
curl --include https://skyex.work.gd/api/v1/metrics
```

Ожидается:

- `/login` — `200` или redirect на штатную страницу;
- `/api/v1/health` — `200`;
- `/api/v1/metrics` без сессии — `401`;
- защищённые финансовые маршруты без сессии — `401`;
- после входа `/ws/deals` — `101 Switching Protocols`.

В браузере откройте DevTools → Network → WS и проверьте
`wss://skyex.work.gd/ws/deals`. Повторяющихся `403` быть не должно.

Проверьте неизвестный Host, подставив IP сервера:

```bash
curl -kiv --resolve invalid.example:443:SERVER_IP https://invalid.example/
```

Nginx должен закрыть соединение без выдачи CRM (`444` в access log).

## 12. Логи и управление

```bash
journalctl -u skyex-crm-backend.service -f
journalctl -u skyex-crm-front.service -f
journalctl -u nginx.service -f

systemctl restart skyex-crm-backend.service
systemctl restart skyex-crm-front.service
systemctl reload nginx
```

Не запускайте `python main.py` вручную параллельно с backend service.

## 13. Обновление

Сначала обновляйте и проверяйте код, затем делайте короткий restart.

Backend:

```bash
cd /root/CRM
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/ruff check .
.venv/bin/python -m pytest -q

(
  set -a
  . /etc/skyex/crm-backend.env
  set +a
  .venv/bin/alembic upgrade head
)

systemctl restart skyex-crm-backend.service
```

Если на production не устанавливается `requirements-dev.txt`, команды Ruff и
pytest выполняйте в CI или staging, а на сервере оставьте `pip check` и миграции.

Frontend:

```bash
cd /root/bookkeeping
git pull --ff-only
(
  set -a
  . /etc/skyex/crm-front.env
  set +a
  export PATH="/opt/node/bin:$PATH"
  pnpm install --frozen-lockfile
  pnpm run lint
  pnpm run build
)

systemctl restart skyex-crm-front.service
```

После каждого обновления повторите health, login, OIDC и WebSocket-проверки.

## 14. Откат

Frontend можно откатить на предыдущий Git commit, пересобрать и перезапустить:

```bash
cd /root/bookkeeping
git checkout PREVIOUS_COMMIT
set -a
. /etc/skyex/crm-front.env
set +a
export PATH="/opt/node/bin:$PATH"
pnpm install --frozen-lockfile
pnpm run build
systemctl restart skyex-crm-front.service
```

Для backend перед обновлением сохраните commit SHA и database backup. Откат
кода выполняйте на заранее проверенный commit. Не восстанавливайте PostgreSQL и
не откатывайте Alembic автоматически: после записи новых production-данных это
может уничтожить операции. Восстановление backup — отдельная аварийная процедура
с остановленными ботом и API.
