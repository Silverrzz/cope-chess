CREATE TABLE form_response_closures (
    response_id UUID NOT NULL REFERENCES form_responses(id),
    state_version BIGINT NOT NULL CHECK (state_version > 1),
    closed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (response_id, state_version)
);
