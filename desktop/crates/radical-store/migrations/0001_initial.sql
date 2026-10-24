-- Version 1: the backend's schema as its migrations leave it on SQLite,
-- without the accounts (users, personal_access_tokens) and without the
-- columns that recorded which account owned a row: one person uses this
-- database. Declared types are the backend's, so SQLite gives every column
-- the same affinity. tests/schema.rs holds this to the backend's own.

CREATE TABLE analyses (
    id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    analysis_type VARCHAR NOT NULL,
    status VARCHAR,
    error_message VARCHAR,
    created_at DATETIME,
    updated_at DATETIME,
    project_id VARCHAR,
    bayesian_config JSON,
    solver JSON,
    intervals JSON,
    summaries JSON,
    influence JSON,
    PRIMARY KEY (id),
    FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE TABLE analysis_results (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    sample_id VARCHAR,
    calibrated_age_bp FLOAT,
    posterior_mean FLOAT,
    posterior_median FLOAT,
    posterior_std FLOAT,
    hpd_68_min FLOAT,
    hpd_68_max FLOAT,
    hpd_95_min FLOAT,
    hpd_95_max FLOAT,
    agreement_index FLOAT,
    outlier_probability FLOAT,
    hpd_68_ranges JSON,
    hpd_95_ranges JSON,
    posterior_samples JSON,
    probability_distribution JSON,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id),
    FOREIGN KEY(sample_id) REFERENCES radiocarbon_samples (id)
);

CREATE TABLE analysis_runs (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    app_commit VARCHAR,
    inputs_hash VARCHAR,
    payload JSON NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
);

CREATE TABLE calibration_curves (
    id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    reference VARCHAR,
    doi VARCHAR,
    type VARCHAR NOT NULL,
    sample_type VARCHAR NOT NULL,
    source_text VARCHAR,
    source_filename VARCHAR,
    parse_options JSON,
    recipe JSON,
    content_sha256 VARCHAR NOT NULL,
    aliases JSON,
    archived BOOLEAN NOT NULL,
    created_at DATETIME,
    updated_at DATETIME,
    PRIMARY KEY (id)
);

CREATE TABLE jobs (
    id VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    target_id VARCHAR,
    state VARCHAR NOT NULL,
    progress FLOAT,
    detail JSON,
    error_message VARCHAR,
    created_at DATETIME,
    started_at DATETIME,
    finished_at DATETIME,
    PRIMARY KEY (id)
);

CREATE TABLE marine_reservoir_points (
    snapshot_id VARCHAR NOT NULL,
    map_no INTEGER NOT NULL,
    latitude FLOAT NOT NULL,
    longitude FLOAT NOT NULL,
    delta_r FLOAT,
    delta_r_error FLOAT,
    reference VARCHAR,
    locality VARCHAR,
    subregion VARCHAR,
    notes VARCHAR,
    c14_age FLOAT,
    c14_error FLOAT,
    lab_id VARCHAR,
    collection_year INTEGER,
    reservoir_age FLOAT,
    genus VARCHAR,
    species VARCHAR,
    feeding VARCHAR,
    feeding_raw VARCHAR,
    PRIMARY KEY (snapshot_id, map_no),
    FOREIGN KEY(snapshot_id) REFERENCES marine_reservoir_snapshots (id)
);

CREATE TABLE marine_reservoir_snapshots (
    id VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    fetched_at DATETIME,
    source_last_changed DATETIME,
    record_count INTEGER NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    citation VARCHAR,
    note VARCHAR,
    created_at DATETIME,
    PRIMARY KEY (id)
);

CREATE TABLE model_elements (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    parent_id VARCHAR,
    position INTEGER NOT NULL,
    element_type VARCHAR NOT NULL,
    name VARCHAR,
    tpq_age_bp FLOAT,
    taq_age_bp FLOAT,
    posterior JSON,
    created_at DATETIME,
    params JSON,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE TABLE model_templates (
    id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    model_tree JSON NOT NULL,
    bayesian_config JSON,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE project_samples (
    id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    sample_name VARCHAR NOT NULL,
    age_bp FLOAT NOT NULL,
    error_1sigma FLOAT NOT NULL,
    material_type VARCHAR NOT NULL,
    sample_type VARCHAR NOT NULL,
    curve_id VARCHAR,
    reservoir_age FLOAT,
    depth_cm FLOAT,
    location VARCHAR,
    notes VARCHAR,
    default_context_name VARCHAR,
    tpq_age_bp FLOAT,
    taq_age_bp FLOAT,
    created_at DATETIME,
    updated_at DATETIME,
    delta_r FLOAT,
    delta_r_error FLOAT,
    ring_offset FLOAT,
    ring_offset_error FLOAT,
    latitude FLOAT,
    longitude FLOAT,
    delta_r_source JSON,
    inbuilt_years FLOAT,
    inbuilt_shape VARCHAR,
    PRIMARY KEY (id),
    FOREIGN KEY(project_id) REFERENCES projects (id)
);

CREATE TABLE projects (
    id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR,
    created_at DATETIME,
    updated_at DATETIME,
    coordinate_precision VARCHAR DEFAULT 'full' NOT NULL,
    map_tiles_enabled BOOLEAN DEFAULT 0 NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE radiocarbon_samples (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    sample_name VARCHAR NOT NULL,
    age_bp FLOAT NOT NULL,
    error_1sigma FLOAT NOT NULL,
    material_type VARCHAR NOT NULL,
    sample_type VARCHAR NOT NULL,
    curve_id VARCHAR,
    delta_r FLOAT,
    delta_r_error FLOAT,
    outlier_prior FLOAT,
    outlier_model VARCHAR,
    reservoir_age FLOAT,
    depth_cm FLOAT,
    location VARCHAR,
    notes VARCHAR,
    created_at DATETIME,
    group_id VARCHAR,
    element_id VARCHAR,
    tpq_age_bp FLOAT,
    taq_age_bp FLOAT,
    sort_order INTEGER DEFAULT '0' NOT NULL,
    ring_offset FLOAT,
    ring_offset_error FLOAT,
    latitude FLOAT,
    longitude FLOAT,
    delta_r_source JSON,
    inbuilt_years FLOAT,
    inbuilt_shape VARCHAR,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id),
    FOREIGN KEY(group_id) REFERENCES sample_groups (id)
);

CREATE TABLE sample_groups (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    tpq_age_bp FLOAT,
    taq_age_bp FLOAT,
    modeled_start JSON,
    modeled_end JSON,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE TABLE stratigraphic_relationships (
    id VARCHAR NOT NULL,
    analysis_id VARCHAR NOT NULL,
    from_sample_id VARCHAR NOT NULL,
    to_sample_id VARCHAR NOT NULL,
    from_type VARCHAR NOT NULL,
    to_type VARCHAR NOT NULL,
    relationship VARCHAR NOT NULL,
    created_at DATETIME,
    PRIMARY KEY (id),
    FOREIGN KEY(analysis_id) REFERENCES analyses (id)
);

CREATE INDEX ix_analysis_runs_analysis_id ON analysis_runs (analysis_id);

CREATE INDEX ix_jobs_target_id ON jobs (target_id);
