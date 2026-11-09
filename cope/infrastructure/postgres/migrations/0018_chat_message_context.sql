ALTER TABLE chat_messages
  ADD COLUMN game_id BIGINT REFERENCES games(id) ON DELETE SET NULL,
  ADD COLUMN game_label TEXT,
  ADD COLUMN reply_to_id BIGINT REFERENCES chat_messages(id) ON DELETE SET NULL,
  ADD COLUMN sender_key TEXT,
  ADD COLUMN sender_ip_hash TEXT;

CREATE INDEX chat_messages_game_id_idx ON chat_messages (game_id) WHERE game_id IS NOT NULL;
CREATE INDEX chat_messages_reply_to_id_idx ON chat_messages (reply_to_id) WHERE reply_to_id IS NOT NULL;

CREATE TABLE chat_ip_blocks (
  sender_ip_hash TEXT PRIMARY KEY
);
