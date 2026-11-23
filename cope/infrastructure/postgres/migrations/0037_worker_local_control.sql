ALTER TABLE workers ADD COLUMN client_threads INTEGER CHECK (client_threads > 0);
ALTER TABLE workers ADD COLUMN client_memory_mb INTEGER CHECK (client_memory_mb > 0);
ALTER TABLE workers ADD COLUMN client_draining BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE workers ADD COLUMN client_auto_update BOOLEAN NOT NULL DEFAULT TRUE;
