-- Version 2: the accounts, for RadiCal on a server.
-- RadiCal Desktop has the same tables and columns, and leaves them empty: one
-- person, no owners.
--
-- The tables are the backend's. The columns that record which account owns a
-- row are added to the end of their tables, where SQLite's ADD COLUMN puts
-- them, so an existing database is migrated in place rather than rebuilt;
-- the backend has them part-way along, which nothing reads by position.
-- They are all nullable here, model_templates.owner_id included (NOT NULL in
-- the backend): a desktop template has no owner, and on a server the server
-- always sets one. tests/schema.rs holds the rest to the backend's own.

CREATE TABLE users (
    id VARCHAR NOT NULL,
    email VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    display_name VARCHAR,
    is_first_account BOOLEAN DEFAULT 0 NOT NULL,
    created_at DATETIME,
    last_login_at DATETIME,
    PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE personal_access_tokens (
    id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    token_hash VARCHAR NOT NULL,
    prefix VARCHAR NOT NULL,
    scope VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    expires_at DATETIME NOT NULL,
    last_used_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ix_personal_access_tokens_token_hash ON personal_access_tokens (token_hash);
CREATE INDEX ix_personal_access_tokens_user_id ON personal_access_tokens (user_id);

ALTER TABLE projects ADD COLUMN owner_id VARCHAR REFERENCES users (id);
CREATE INDEX ix_projects_owner_id ON projects (owner_id);

ALTER TABLE analyses ADD COLUMN owner_id VARCHAR REFERENCES users (id);
CREATE INDEX ix_analyses_owner_id ON analyses (owner_id);

ALTER TABLE model_templates ADD COLUMN owner_id VARCHAR REFERENCES users (id) ON DELETE CASCADE;
CREATE INDEX ix_model_templates_owner_id ON model_templates (owner_id);

ALTER TABLE jobs ADD COLUMN owner_id VARCHAR REFERENCES users (id);
CREATE INDEX ix_jobs_owner_id ON jobs (owner_id);

ALTER TABLE calibration_curves ADD COLUMN created_by VARCHAR REFERENCES users (id);
CREATE INDEX ix_calibration_curves_created_by ON calibration_curves (created_by);

ALTER TABLE marine_reservoir_snapshots ADD COLUMN created_by VARCHAR REFERENCES users (id);
CREATE INDEX ix_marine_reservoir_snapshots_created_by ON marine_reservoir_snapshots (created_by);
