ALTER TABLE engine_versions ADD COLUMN public_hidden BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE engine_version_tags (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL CHECK (char_length(btrim(name)) BETWEEN 1 AND 40),
    color TEXT NOT NULL CHECK (color ~ '^#[0-9a-fA-F]{6}$')
);

CREATE UNIQUE INDEX engine_version_tags_name_key ON engine_version_tags (lower(name));

CREATE TABLE engine_version_tag_assignments (
    version_id BIGINT NOT NULL REFERENCES engine_versions(id) ON DELETE CASCADE,
    tag_id BIGINT NOT NULL REFERENCES engine_version_tags(id) ON DELETE CASCADE,
    PRIMARY KEY (version_id, tag_id)
);

CREATE INDEX engine_version_tag_assignments_tag_idx ON engine_version_tag_assignments (tag_id);

INSERT INTO engine_version_tags (name, color) VALUES ('LLM', '#a855f7'), ('NOT SELFGEN', '#f59e0b');

UPDATE engine_versions version
SET public_hidden = TRUE
WHERE (
    version.version LIKE 'hb-%'
    OR version.dockerfile_path LIKE '%-hand-brain-%'
    OR (version.distribution = 'worker_local' AND version.worker_local_key = 'master-20261106-ccb0dbd8d')
)
AND EXISTS (
    SELECT 1
    FROM event_cast_members member
    JOIN events event ON event.id = member.event_id
    WHERE member.engine_version_id = version.id AND event.slug = 'engine-hand-brain'
)
AND EXISTS (SELECT 1 FROM hand_brain_searches search WHERE search.engine_id = version.id);
