CREATE TABLE IF NOT EXISTS message_archive_chats (
    id BIGSERIAL PRIMARY KEY,
    telegram_chat_id BIGINT,
    desktop_chat_id BIGINT,
    chat_type TEXT NOT NULL,
    current_title TEXT NOT NULL,
    username TEXT,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    first_message_at TIMESTAMPTZ,
    last_message_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_archive_chats_telegram
    ON message_archive_chats(telegram_chat_id) WHERE telegram_chat_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_archive_chats_desktop
    ON message_archive_chats(desktop_chat_id) WHERE desktop_chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_message_archive_chats_title
    ON message_archive_chats(LOWER(current_title));

CREATE TABLE IF NOT EXISTS message_archive_chat_names (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    valid_from TIMESTAMPTZ,
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, normalized_name)
);
CREATE INDEX IF NOT EXISTS ix_message_archive_chat_names_normalized
    ON message_archive_chat_names(normalized_name);

CREATE TABLE IF NOT EXISTS message_archive_authors (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT,
    desktop_source_key TEXT,
    display_name TEXT NOT NULL,
    username TEXT,
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_archive_authors_telegram
    ON message_archive_authors(telegram_user_id) WHERE telegram_user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_archive_authors_desktop
    ON message_archive_authors(desktop_source_key) WHERE desktop_source_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS message_archive_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE CASCADE,
    telegram_message_id BIGINT NOT NULL,
    author_id BIGINT REFERENCES message_archive_authors(id) ON DELETE SET NULL,
    message_type TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'imported')),
    sent_at TIMESTAMPTZ NOT NULL,
    edited_at TIMESTAMPTZ,
    reply_to_message_id BIGINT,
    media_group_id TEXT,
    text_plain TEXT,
    text_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    forward_info JSONB,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    source TEXT NOT NULL CHECK (source IN ('bot_api', 'telegram_desktop')),
    revision_no INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chat_id, telegram_message_id)
);
CREATE INDEX IF NOT EXISTS ix_message_archive_messages_chat_time
    ON message_archive_messages(chat_id, sent_at, telegram_message_id);
CREATE INDEX IF NOT EXISTS ix_message_archive_messages_chat_author_time
    ON message_archive_messages(chat_id, author_id, sent_at);

CREATE TABLE IF NOT EXISTS message_archive_revisions (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES message_archive_messages(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    text_plain TEXT,
    text_entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    edited_at TIMESTAMPTZ NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, revision_no),
    UNIQUE (message_id, content_hash)
);

CREATE TABLE IF NOT EXISTS message_archive_attachments (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES message_archive_messages(id) ON DELETE CASCADE,
    attachment_type TEXT NOT NULL,
    ordinal SMALLINT NOT NULL DEFAULT 0,
    telegram_file_id TEXT,
    telegram_file_unique_id TEXT,
    mime_type TEXT,
    original_name TEXT,
    file_size BIGINT,
    duration_seconds INTEGER,
    width INTEGER,
    height INTEGER,
    storage_key TEXT,
    sha256 TEXT,
    download_status TEXT NOT NULL DEFAULT 'skipped'
        CHECK (download_status IN ('pending', 'ready', 'skipped', 'failed', 'unavailable')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    download_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (message_id, attachment_type, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_message_archive_attachments_download
    ON message_archive_attachments(download_status, id)
    WHERE download_status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS ix_message_archive_attachments_sha256
    ON message_archive_attachments(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS message_archive_imports (
    id BIGSERIAL PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_checksum TEXT,
    dry_run BOOLEAN NOT NULL,
    status TEXT NOT NULL,
    chats_found INTEGER NOT NULL DEFAULT 0,
    messages_created INTEGER NOT NULL DEFAULT 0,
    messages_updated INTEGER NOT NULL DEFAULT 0,
    messages_skipped INTEGER NOT NULL DEFAULT 0,
    files_missing INTEGER NOT NULL DEFAULT 0,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS message_archive_exports (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL REFERENCES message_archive_chats(id) ON DELETE RESTRICT,
    requested_by_user_id BIGINT NOT NULL,
    requested_in_chat_id BIGINT NOT NULL,
    status TEXT NOT NULL,
    message_count BIGINT NOT NULL DEFAULT 0,
    part_count INTEGER NOT NULL DEFAULT 0,
    total_size BIGINT NOT NULL DEFAULT 0,
    telegram_result_message_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);
