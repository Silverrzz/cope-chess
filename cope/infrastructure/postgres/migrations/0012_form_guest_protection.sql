ALTER TABLE form_claims ADD COLUMN challenge_id UUID UNIQUE;
ALTER TABLE form_claims ADD COLUMN release_id UUID REFERENCES form_releases(id);
ALTER TABLE form_claims ADD COLUMN guest_bucket TEXT;
ALTER TABLE form_claims ADD CONSTRAINT form_claim_challenge_scope
CHECK ((challenge_id IS NULL AND release_id IS NULL AND guest_bucket IS NULL)
       OR (challenge_id IS NOT NULL AND release_id IS NOT NULL AND guest_bucket IS NOT NULL AND length(guest_bucket) = 64));

ALTER TABLE artifact_upload_sessions DROP CONSTRAINT artifact_upload_sessions_media_type_check;
ALTER TABLE artifact_upload_sessions ADD CONSTRAINT artifact_upload_sessions_media_type_check
CHECK (media_type IN ('text/plain', 'application/octet-stream', 'image/png', 'image/jpeg', 'image/webp', 'application/pdf'));

CREATE INDEX form_rate_limits_hour ON form_rate_limits(hour);
CREATE INDEX artifact_storage_reserved_upload ON artifact_storage_intents(upload_id) WHERE physical_reserved;
