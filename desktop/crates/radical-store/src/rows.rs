//! One struct per table, field for field, and the few generic operations
//! every table needs. The queries particular to one screen live with the
//! server routes that ask them.
//!
//! JSON columns are `serde_json::Value`: the backend's SQLAlchemy JSON type
//! stores Python's None as the text `null` when a row is written with it,
//! and SQL NULL when the column was never set, and both read back as JSON
//! null here.

use rusqlite::types::ToSql;
use rusqlite::{Connection, OptionalExtension, Params, Row};
use serde_json::Value;

use crate::{Result, Timestamp};

/// A table's row type.
pub trait Table: Sized {
    const NAME: &'static str;
    /// In the schema's order, which `from_row` and `values` follow.
    const COLUMNS: &'static [&'static str];
    fn from_row(row: &Row) -> rusqlite::Result<Self>;
    fn values(&self) -> Vec<&dyn ToSql>;
}

macro_rules! column {
    ($field:ident) => {
        stringify!($field)
    };
    ($field:ident, $col:literal) => {
        $col
    };
}

macro_rules! table {
    ($(#[$meta:meta])* $name:ident = $table:literal {
        $($(#[$fmeta:meta])* $field:ident $(= $col:literal)? : $ty:ty),+ $(,)?
    }) => {
        $(#[$meta])*
        #[derive(Debug, Clone, PartialEq, Default)]
        pub struct $name {
            $($(#[$fmeta])* pub $field: $ty),+
        }

        impl Table for $name {
            const NAME: &'static str = $table;
            const COLUMNS: &'static [&'static str] = &[$(column!($field $(, $col)?)),+];

            fn from_row(row: &Row) -> rusqlite::Result<Self> {
                let mut index = 0usize..;
                Ok(Self { $($field: row.get(index.next().unwrap())?),+ })
            }

            fn values(&self) -> Vec<&dyn ToSql> {
                vec![$(&self.$field as &dyn ToSql),+]
            }
        }
    };
}

type Json = Option<Value>;
type When = Option<Timestamp>;

table! {
    /// An analysis: one calibration, group calibration or Bayesian model.
    Analysis = "analyses" {
        id: String,
        name: String,
        /// 'calibration', 'group_calibration' or 'bayesian'.
        analysis_type: String,
        /// 'pending', 'running', 'completed' or 'failed'.
        status: Option<String>,
        error_message: Option<String>,
        created_at: When,
        updated_at: When,
        project_id: Option<String>,
        bayesian_config: Json,
        solver: Json,
        intervals: Json,
        summaries: Json,
        influence: Json,
        /// The account that owns it, on a server; empty on the desktop.
        owner_id: Option<String>,
    }
}

table! {
    /// One date's (or Boundary's) result in an analysis.
    AnalysisResult = "analysis_results" {
        id: String,
        analysis_id: String,
        sample_id: Option<String>,
        calibrated_age_bp: Option<f64>,
        posterior_mean: Option<f64>,
        posterior_median: Option<f64>,
        posterior_std: Option<f64>,
        hpd_68_min: Option<f64>,
        hpd_68_max: Option<f64>,
        hpd_95_min: Option<f64>,
        hpd_95_max: Option<f64>,
        agreement_index: Option<f64>,
        outlier_probability: Option<f64>,
        hpd_68_ranges: Json,
        hpd_95_ranges: Json,
        posterior_samples: Json,
        probability_distribution: Json,
        created_at: When,
    }
}

table! {
    /// A stored run of an analysis: everything needed to reproduce it.
    AnalysisRun = "analysis_runs" {
        id: String,
        analysis_id: String,
        created_at: Timestamp,
        app_commit: Option<String>,
        inputs_hash: Option<String>,
        payload: Value,
    }
}

table! {
    /// A user-added calibration curve, imported or mixed.
    CalibrationCurve = "calibration_curves" {
        id: String,
        /// 'imported' or 'mixed'.
        kind: String,
        name: String,
        description: Option<String>,
        reference: Option<String>,
        doi: Option<String>,
        curve_type = "type": String,
        sample_type: String,
        source_text: Option<String>,
        source_filename: Option<String>,
        parse_options: Json,
        recipe: Json,
        content_sha256: String,
        aliases: Json,
        archived: bool,
        created_at: When,
        updated_at: When,
        /// The account that added it, on a server; empty on the desktop.
        created_by: Option<String>,
    }
}

table! {
    /// A background job: a long solve, an influence run, an ingest.
    Job = "jobs" {
        id: String,
        kind: String,
        target_id: Option<String>,
        /// 'queued', 'running', 'done', 'failed' or 'cancelled'.
        state: String,
        progress: Option<f64>,
        detail: Json,
        error_message: Option<String>,
        created_at: When,
        started_at: When,
        finished_at: When,
        /// The account that owns it, on a server; empty on the desktop.
        owner_id: Option<String>,
    }
}

table! {
    /// One point of a marine reservoir snapshot.
    MarineReservoirPoint = "marine_reservoir_points" {
        snapshot_id: String,
        map_no: i64,
        latitude: f64,
        longitude: f64,
        delta_r: Option<f64>,
        delta_r_error: Option<f64>,
        reference: Option<String>,
        locality: Option<String>,
        subregion: Option<String>,
        notes: Option<String>,
        c14_age: Option<f64>,
        c14_error: Option<f64>,
        lab_id: Option<String>,
        collection_year: Option<i64>,
        reservoir_age: Option<f64>,
        genus: Option<String>,
        species: Option<String>,
        feeding: Option<String>,
        feeding_raw: Option<String>,
    }
}

table! {
    /// An ingested marine reservoir database.
    MarineReservoirSnapshot = "marine_reservoir_snapshots" {
        id: String,
        source: String,
        fetched_at: When,
        source_last_changed: When,
        record_count: i64,
        content_sha256: String,
        citation: Option<String>,
        note: Option<String>,
        created_at: When,
        /// The account that added it, on a server; empty on the desktop.
        created_by: Option<String>,
    }
}

table! {
    /// One node of an analysis's model tree.
    ModelElement = "model_elements" {
        id: String,
        analysis_id: String,
        parent_id: Option<String>,
        position: i64,
        element_type: String,
        name: Option<String>,
        tpq_age_bp: Option<f64>,
        taq_age_bp: Option<f64>,
        posterior: Json,
        created_at: When,
        params: Json,
    }
}

table! {
    /// A saved model structure, to start new analyses from.
    ModelTemplate = "model_templates" {
        id: String,
        name: String,
        description: Option<String>,
        model_tree: Value,
        bayesian_config: Json,
        created_at: Timestamp,
        updated_at: Timestamp,
        /// The account that owns it, on a server; empty on the desktop.
        owner_id: Option<String>,
    }
}

table! {
    /// A date collected in a project, before any analysis uses it.
    ProjectSample = "project_samples" {
        id: String,
        project_id: String,
        sample_name: String,
        age_bp: f64,
        error_1sigma: f64,
        material_type: String,
        sample_type: String,
        curve_id: Option<String>,
        depth_cm: Option<f64>,
        location: Option<String>,
        notes: Option<String>,
        default_context_name: Option<String>,
        tpq_age_bp: Option<f64>,
        taq_age_bp: Option<f64>,
        created_at: When,
        updated_at: When,
        delta_r: Option<f64>,
        delta_r_error: Option<f64>,
        ring_offset: Option<f64>,
        ring_offset_error: Option<f64>,
        latitude: Option<f64>,
        longitude: Option<f64>,
        delta_r_source: Json,
        inbuilt_years: Option<f64>,
        inbuilt_shape: Option<String>,
    }
}

table! {
    /// A project: its dates and the analyses run on them.
    Project = "projects" {
        id: String,
        name: String,
        description: Option<String>,
        created_at: When,
        updated_at: When,
        /// 'full' or a coarsening the exports apply.
        coordinate_precision: String,
        map_tiles_enabled: bool,
        /// The account that owns it, on a server; empty on the desktop.
        owner_id: Option<String>,
    }
}

table! {
    /// A date as one analysis uses it.
    RadiocarbonSample = "radiocarbon_samples" {
        id: String,
        analysis_id: String,
        sample_name: String,
        age_bp: f64,
        error_1sigma: f64,
        material_type: String,
        sample_type: String,
        curve_id: Option<String>,
        delta_r: Option<f64>,
        delta_r_error: Option<f64>,
        outlier_prior: Option<f64>,
        outlier_model: Option<String>,
        depth_cm: Option<f64>,
        location: Option<String>,
        notes: Option<String>,
        created_at: When,
        group_id: Option<String>,
        element_id: Option<String>,
        tpq_age_bp: Option<f64>,
        taq_age_bp: Option<f64>,
        sort_order: i64,
        ring_offset: Option<f64>,
        ring_offset_error: Option<f64>,
        latitude: Option<f64>,
        longitude: Option<f64>,
        delta_r_source: Json,
        inbuilt_years: Option<f64>,
        inbuilt_shape: Option<String>,
    }
}

table! {
    /// A context of the older contexts-and-relationships representation.
    SampleGroup = "sample_groups" {
        id: String,
        analysis_id: String,
        name: String,
        tpq_age_bp: Option<f64>,
        taq_age_bp: Option<f64>,
        modeled_start: Json,
        modeled_end: Json,
        created_at: When,
    }
}

table! {
    /// An ordering between two samples or groups, in the older
    /// representation.
    StratigraphicRelationship = "stratigraphic_relationships" {
        id: String,
        analysis_id: String,
        from_sample_id: String,
        to_sample_id: String,
        from_type: String,
        to_type: String,
        /// 'above', 'below' or 'equal'.
        relationship: String,
        created_at: When,
    }
}

table! {
    /// An account, on a server. The desktop has none.
    User = "users" {
        id: String,
        /// Lowercased, and unique.
        email: String,
        /// bcrypt. Never the password.
        password_hash: String,
        display_name: Option<String>,
        /// The first account made adopts the work from before there were
        /// accounts.
        is_first_account: bool,
        created_at: When,
        last_login_at: When,
    }
}

table! {
    /// A long-lived credential for scripts (`Authorization: Bearer rct_...`),
    /// of which only a SHA-256 of the secret is kept.
    PersonalAccessToken = "personal_access_tokens" {
        id: String,
        user_id: String,
        name: String,
        token_hash: String,
        /// The secret's first characters, to tell tokens apart in a list.
        prefix: String,
        /// 'read' or 'write'.
        scope: String,
        created_at: Timestamp,
        expires_at: Timestamp,
        last_used_at: When,
    }
}

/// Every row type, for the tests that check them against the schema.
pub const TABLES: [(&str, &[&str]); 16] = [
    (Analysis::NAME, Analysis::COLUMNS),
    (AnalysisResult::NAME, AnalysisResult::COLUMNS),
    (AnalysisRun::NAME, AnalysisRun::COLUMNS),
    (CalibrationCurve::NAME, CalibrationCurve::COLUMNS),
    (Job::NAME, Job::COLUMNS),
    (MarineReservoirPoint::NAME, MarineReservoirPoint::COLUMNS),
    (
        MarineReservoirSnapshot::NAME,
        MarineReservoirSnapshot::COLUMNS,
    ),
    (ModelElement::NAME, ModelElement::COLUMNS),
    (ModelTemplate::NAME, ModelTemplate::COLUMNS),
    (ProjectSample::NAME, ProjectSample::COLUMNS),
    (PersonalAccessToken::NAME, PersonalAccessToken::COLUMNS),
    (Project::NAME, Project::COLUMNS),
    (RadiocarbonSample::NAME, RadiocarbonSample::COLUMNS),
    (SampleGroup::NAME, SampleGroup::COLUMNS),
    (
        StratigraphicRelationship::NAME,
        StratigraphicRelationship::COLUMNS,
    ),
    (User::NAME, User::COLUMNS),
];

// --------------------------------------------------------------------------
// Generic operations
// --------------------------------------------------------------------------

fn select<T: Table>() -> String {
    format!("SELECT \"{}\" FROM {}", T::COLUMNS.join("\", \""), T::NAME)
}

/// Insert a row.
pub fn insert<T: Table>(conn: &Connection, row: &T) -> Result<()> {
    let marks = vec!["?"; T::COLUMNS.len()].join(", ");
    let sql = format!(
        "INSERT INTO {} (\"{}\") VALUES ({marks})",
        T::NAME,
        T::COLUMNS.join("\", \"")
    );
    conn.prepare_cached(&sql)?
        .execute(rusqlite::params_from_iter(row.values()))?;
    Ok(())
}

/// Rewrite every column of the row whose `id` this is; false when there is
/// none.
pub fn update<T: Table>(conn: &Connection, row: &T) -> Result<bool> {
    let sets: Vec<String> = T::COLUMNS[1..]
        .iter()
        .map(|c| format!("\"{c}\" = ?"))
        .collect();
    let sql = format!(
        "UPDATE {} SET {} WHERE \"{}\" = ?",
        T::NAME,
        sets.join(", "),
        T::COLUMNS[0]
    );
    let mut values = row.values();
    let id = values.remove(0);
    values.push(id);
    Ok(conn
        .prepare_cached(&sql)?
        .execute(rusqlite::params_from_iter(values))?
        == 1)
}

/// The row with this `id`, if there is one.
pub fn get<T: Table>(conn: &Connection, id: &str) -> Result<Option<T>> {
    let sql = format!("{} WHERE \"{}\" = ?", select::<T>(), T::COLUMNS[0]);
    Ok(conn
        .prepare_cached(&sql)?
        .query_row([id], T::from_row)
        .optional()?)
}

/// The rows matching `condition` (SQL after WHERE, `?` for each parameter),
/// in `order` (SQL after ORDER BY, or empty for none).
pub fn select_where<T: Table>(
    conn: &Connection,
    condition: &str,
    params: impl Params,
    order: &str,
) -> Result<Vec<T>> {
    let mut sql = format!("{} WHERE {condition}", select::<T>());
    if !order.is_empty() {
        sql.push_str(&format!(" ORDER BY {order}"));
    }
    let mut stmt = conn.prepare_cached(&sql)?;
    let rows = stmt.query_map(params, T::from_row)?;
    Ok(rows.collect::<rusqlite::Result<Vec<T>>>()?)
}

/// Delete the rows matching `condition`; how many went.
pub fn delete_where<T: Table>(
    conn: &Connection,
    condition: &str,
    params: impl Params,
) -> Result<usize> {
    let sql = format!("DELETE FROM {} WHERE {condition}", T::NAME);
    Ok(conn.prepare_cached(&sql)?.execute(params)?)
}
