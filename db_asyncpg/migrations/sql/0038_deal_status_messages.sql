CREATE TABLE IF NOT EXISTS deal_status_messages (
    deal_id BIGINT NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (deal_id, chat_id)
);
