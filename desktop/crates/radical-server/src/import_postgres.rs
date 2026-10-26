//! Moving an installation from the backend's PostgreSQL to SQLite
//! (`radical-server import-postgres`, docs/RUST_SERVER_PLAN.md S4).
//!
//! The backend's tables are this store's tables, column for column (the
//! schema test holds them so), so the move is a copy: every row of every
//! table, ids unchanged -- so bookmarked links still work -- and owners kept,
//! so each account finds exactly its own work. Sign-in tokens and access
//! tokens keep working too, as long as `RADICAL_SECRET_KEY` does not change.
//!
//! Each value is read as PostgreSQL writes it out as text and written as the
//! column's declared type here, which is how SQLAlchemy stored it on SQLite.
//! Rows go in with foreign keys unchecked, since tables reference each other
//! in both directions, and are checked all at once before anything is
//! committed: a copy that would break one is refused whole. So is a target
//! that already holds anything: this fills a new database, never merges.

use std::path::Path;

use radical_store::rusqlite::types::Value as Sql;
use radical_store::rusqlite::{self, Connection};
use radical_store::{Store, Timestamp};

/// How many rows each table received.
pub type Report = Vec<(String, usize)>;

/// A text value from PostgreSQL as a column declared `declared` here stores
/// it.
pub fn convert(text: Option<&str>, declared: &str) -> Result<Sql, String> {
    let Some(text) = text else {
        return Ok(Sql::Null);
    };
    match declared.to_ascii_uppercase().as_str() {
        "FLOAT" | "REAL" | "DOUBLE" => text
            .parse::<f64>()
            .map(Sql::Real)
            .map_err(|_| format!("'{text}' is not a number")),
        "INTEGER" | "BIGINT" => text
            .parse::<i64>()
            .map(Sql::Integer)
            .map_err(|_| format!("'{text}' is not a whole number")),
        "BOOLEAN" => match text {
            "true" | "t" => Ok(Sql::Integer(1)),
            "false" | "f" => Ok(Sql::Integer(0)),
            other => Err(format!("'{other}' is not true or false")),
        },
        "DATETIME" | "TIMESTAMP" => Timestamp::parse(text)
            .map(|t| Sql::Text(t.stored()))
            .ok_or_else(|| format!("'{text}' is not a date and time")),
        // VARCHAR, TEXT and JSON: the text itself, which for JSON is the
        // document as SQLAlchemy wrote it.
        _ => Ok(Sql::Text(text.to_owned())),
    }
}

fn columns(conn: &Connection, table: &str) -> rusqlite::Result<Vec<(String, String)>> {
    conn.prepare(&format!("pragma table_info(\"{table}\")"))?
        .query_map([], |r| Ok((r.get(1)?, r.get(2)?)))?
        .collect()
}

/// Copy everything at `database_url` into a new SQLite database at `target`.
pub fn import(database_url: &str, target: &Path) -> Result<Report, String> {
    // Made, or brought up to the current schema, by the store itself.
    drop(Store::open(target).map_err(|e| format!("The SQLite database did not open: {e}"))?);
    let mut lite = Connection::open(target).map_err(|e| e.to_string())?;
    let tables: Vec<String> = lite
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        .and_then(|mut s| s.query_map([], |r| r.get(0))?.collect())
        .map_err(|e| e.to_string())?;
    for table in &tables {
        let n: i64 = lite
            .query_row(&format!("SELECT count(*) FROM \"{table}\""), [], |r| {
                r.get(0)
            })
            .map_err(|e| e.to_string())?;
        if n > 0 {
            return Err(format!(
                "{} already holds data ({n} rows in {table}). Import into a new database: this \
                 fills one, it never merges.",
                target.display()
            ));
        }
    }

    let mut pg = postgres::Client::connect(database_url, postgres::NoTls)
        .map_err(|e| format!("Could not reach PostgreSQL: {e}"))?;
    let pg_columns = |pg: &mut postgres::Client, table: &str| -> Result<Vec<String>, String> {
        pg.query(
            "SELECT column_name FROM information_schema.columns \
             WHERE table_schema = current_schema() AND table_name = $1",
            &[&table],
        )
        .map(|rows| rows.iter().map(|r| r.get::<_, String>(0)).collect())
        .map_err(|e| e.to_string())
    };

    lite.pragma_update(None, "foreign_keys", "OFF")
        .map_err(|e| e.to_string())?;
    let tx = lite.transaction().map_err(|e| e.to_string())?;
    let mut report = Report::new();
    for table in &tables {
        let theirs = pg_columns(&mut pg, table)?;
        if theirs.is_empty() {
            report.push((table.clone(), 0));
            continue;
        }
        let ours = columns(&tx, table).map_err(|e| e.to_string())?;
        // A column the backend has not got (none today) arrives empty.
        let select: Vec<String> = ours
            .iter()
            .map(|(name, _)| {
                if theirs.contains(name) {
                    format!("\"{name}\"::text")
                } else {
                    "NULL::text".to_owned()
                }
            })
            .collect();
        let rows = pg
            .query(
                &format!("SELECT {} FROM \"{table}\"", select.join(", ")),
                &[],
            )
            .map_err(|e| format!("Could not read {table}: {e}"))?;
        let names: Vec<String> = ours.iter().map(|(n, _)| format!("\"{n}\"")).collect();
        let marks = vec!["?"; ours.len()].join(", ");
        let mut insert = tx
            .prepare(&format!(
                "INSERT INTO \"{table}\" ({}) VALUES ({marks})",
                names.join(", ")
            ))
            .map_err(|e| e.to_string())?;
        for row in &rows {
            let values: Vec<Sql> = ours
                .iter()
                .enumerate()
                .map(|(i, (name, declared))| {
                    convert(row.get::<_, Option<&str>>(i), declared)
                        .map_err(|e| format!("{table}.{name}: {e}"))
                })
                .collect::<Result<_, _>>()?;
            insert
                .execute(rusqlite::params_from_iter(values))
                .map_err(|e| format!("Could not write a row of {table}: {e}"))?;
        }
        report.push((table.clone(), rows.len()));
    }
    let broken: i64 = tx
        .query_row("SELECT count(*) FROM pragma_foreign_key_check", [], |r| {
            r.get(0)
        })
        .map_err(|e| e.to_string())?;
    if broken > 0 {
        return Err(format!(
            "{broken} rows refer to rows that are not there. Nothing was imported."
        ));
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn each_value_is_stored_as_its_column_declares() {
        assert_eq!(convert(None, "FLOAT").unwrap(), Sql::Null);
        assert_eq!(convert(Some("2.5"), "FLOAT").unwrap(), Sql::Real(2.5));
        assert_eq!(
            convert(Some("-Infinity"), "FLOAT").unwrap(),
            Sql::Real(f64::NEG_INFINITY)
        );
        assert_eq!(convert(Some("42"), "INTEGER").unwrap(), Sql::Integer(42));
        assert_eq!(convert(Some("true"), "BOOLEAN").unwrap(), Sql::Integer(1));
        assert_eq!(convert(Some("false"), "BOOLEAN").unwrap(), Sql::Integer(0));
        // PostgreSQL leaves off the fraction when there is none; SQLAlchemy's
        // SQLite form always writes it.
        assert_eq!(
            convert(Some("2026-10-24 09:37:49"), "DATETIME").unwrap(),
            Sql::Text("2026-10-24 09:37:49.000000".to_owned())
        );
        assert_eq!(
            convert(Some("2026-10-24 09:37:49.288862"), "DATETIME").unwrap(),
            Sql::Text("2026-10-24 09:37:49.288862".to_owned())
        );
        assert_eq!(
            convert(Some(r#"{"a": [1, 2]}"#), "JSON").unwrap(),
            Sql::Text(r#"{"a": [1, 2]}"#.to_owned())
        );
        assert!(convert(Some("yes"), "BOOLEAN").is_err());
        assert!(convert(Some("soon"), "DATETIME").is_err());
    }
}
