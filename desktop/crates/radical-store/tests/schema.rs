//! The store's schema held to the backend's, as its migrations leave it on
//! SQLite (corpus `schema/sqlite`): every table, column (name, declared
//! type, NOT NULL, default, primary-key position), foreign key and declared
//! index, the accounts included.

use radical_corpus::load_family;
use radical_store::Store;
use radical_store::rusqlite::Connection;
use serde_json::{Value, json};

/// The backend's tables that have no place here: Alembic's bookkeeping.
const NOT_HERE: [&str; 1] = ["alembic_version"];
/// The columns that say which account owns a row. Migration 2 adds them to
/// existing tables, so they come last (SQLite's ADD COLUMN), not part-way
/// along as in the backend; and all are nullable, since the desktop's rows
/// have no owner (`model_templates.owner_id` is NOT NULL in the backend).
const OWNERSHIP: [&str; 2] = ["owner_id", "created_by"];

fn describe(conn: &Connection, table: &str) -> Value {
    let rows = |sql: String, f: &dyn Fn(&radical_store::rusqlite::Row) -> Value| -> Vec<Value> {
        let mut stmt = conn.prepare(&sql).unwrap();
        stmt.query_map([], |r| Ok(f(r)))
            .unwrap()
            .map(Result::unwrap)
            .collect()
    };
    let columns = rows(format!("pragma table_info(\"{table}\")"), &|r| {
        json!({
            "name": r.get::<_, String>(1).unwrap(),
            "type": r.get::<_, String>(2).unwrap(),
            "notnull": r.get::<_, bool>(3).unwrap(),
            "default": r.get::<_, Option<String>>(4).unwrap(),
            "pk": r.get::<_, i64>(5).unwrap(),
        })
    });
    let foreign_keys = rows(format!("pragma foreign_key_list(\"{table}\")"), &|r| {
        json!({
            "column": r.get::<_, String>(3).unwrap(),
            "references": r.get::<_, String>(2).unwrap(),
            "to": r.get::<_, String>(4).unwrap(),
            "on_delete": r.get::<_, String>(6).unwrap(),
        })
    });
    let mut indexes = Vec::new();
    for (name, unique, origin) in rows(format!("pragma index_list(\"{table}\")"), &|r| {
        json!([
            r.get::<_, String>(1).unwrap(),
            r.get::<_, bool>(2).unwrap(),
            r.get::<_, String>(3).unwrap()
        ])
    })
    .iter()
    .map(|v| {
        (
            v[0].as_str().unwrap().to_owned(),
            v[1].as_bool().unwrap(),
            v[2].as_str().unwrap().to_owned(),
        )
    }) {
        if origin != "c" {
            continue;
        }
        let cols = rows(format!("pragma index_info(\"{name}\")"), &|r| {
            json!(r.get::<_, String>(2).unwrap())
        });
        indexes.push(json!({"columns": cols, "unique": unique}));
    }
    json!({"columns": columns, "foreign_keys": foreign_keys, "indexes": indexes})
}

/// The backend's description of a table as this store lays it out: the
/// ownership columns moved to the end and made nullable, and the foreign keys
/// and indexes in a fixed order, since adding a column adds its key and index
/// after the rest.
fn as_laid_out_here(want: &Value) -> Value {
    let owned = |c: &Value| OWNERSHIP.contains(&c["name"].as_str().unwrap_or(""));
    let columns = want["columns"].as_array().unwrap();
    let mut laid: Vec<Value> = columns.iter().filter(|c| !owned(c)).cloned().collect();
    for c in columns.iter().filter(|c| owned(c)) {
        let mut c = c.clone();
        c["notnull"] = json!(false);
        laid.push(c);
    }
    json!({
        "columns": laid,
        "foreign_keys": sorted(&want["foreign_keys"]),
        "indexes": sorted(&want["indexes"]),
    })
}

fn sorted(v: &Value) -> Value {
    let mut items: Vec<Value> = v.as_array().unwrap().clone();
    items.sort_by_key(Value::to_string);
    Value::Array(items)
}

#[test]
fn the_schema_is_the_backends() {
    let dir = std::env::temp_dir().join(format!("radical-schema-{}", radical_store::new_id()));
    let store = Store::open(dir.join("radical.db")).unwrap();
    let conn = store.reader();
    let mut ours: Vec<String> = conn
        .prepare("select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    let family = load_family("schema/sqlite").unwrap();
    let mut theirs: Vec<String> = Vec::new();
    for case in &family.cases {
        if NOT_HERE.contains(&case.name.as_str()) {
            continue;
        }
        theirs.push(case.name.clone());
        let want = as_laid_out_here(case.outputs.as_ref().unwrap());
        let mut ours = describe(&conn, &case.name);
        for key in ["foreign_keys", "indexes"] {
            ours[key] = sorted(&ours[key]);
        }
        assert_eq!(ours, want, "table {}", case.name);
    }
    ours.sort();
    theirs.sort();
    assert_eq!(ours, theirs);
    drop(conn);
    drop(store);
    std::fs::remove_dir_all(dir).unwrap();
}

/// Every row struct reads and writes exactly the table's columns, in order,
/// and survives a round trip.
#[test]
fn the_row_types_are_the_tables() {
    use radical_store::rows::{self, TABLES};
    let dir = std::env::temp_dir().join(format!("radical-rows-{}", radical_store::new_id()));
    let store = Store::open(dir.join("radical.db")).unwrap();
    {
        let conn = store.reader();
        for (table, columns) in TABLES {
            let names: Vec<String> = conn
                .prepare(&format!("pragma table_info(\"{table}\")"))
                .unwrap()
                .query_map([], |r| r.get(1))
                .unwrap()
                .map(Result::unwrap)
                .collect();
            assert_eq!(names, columns, "{table}");
        }
    }
    let project = rows::Project {
        id: radical_store::new_id(),
        name: "P".into(),
        created_at: Some(radical_store::Timestamp::from_parts(2026, 1, 2, 3, 4, 5, 6)),
        coordinate_precision: "full".into(),
        map_tiles_enabled: true,
        ..Default::default()
    };
    let curve = rows::CalibrationCurve {
        id: "c".into(),
        kind: "mixed".into(),
        name: "Mix".into(),
        curve_type: "terrestrial".into(),
        sample_type: "terrestrial".into(),
        recipe: Some(json!({"a": "intcal20", "b": "marine20", "percent_a": 30.0})),
        content_sha256: "x".into(),
        ..Default::default()
    };
    store
        .write(|tx| {
            rows::insert(tx, &project)?;
            rows::insert(tx, &curve)?;
            Ok(())
        })
        .unwrap();
    let back: rows::Project = store.read(|c| rows::get(c, &project.id)).unwrap().unwrap();
    assert_eq!(back, project);
    let back: rows::CalibrationCurve = store.read(|c| rows::get(c, "c")).unwrap().unwrap();
    assert_eq!(back, curve);
    // A sample of a project that is not there is refused: foreign keys hold.
    let orphan = rows::ProjectSample {
        id: "s".into(),
        project_id: "nowhere".into(),
        ..Default::default()
    };
    assert!(store.write(|tx| rows::insert(tx, &orphan)).is_err());
    drop(store);
    std::fs::remove_dir_all(dir).unwrap();
}

/// A desktop database from before the accounts (version 1), with work in it,
/// opens under version 2 with that work intact and owned by nobody.
#[test]
fn a_desktop_database_gains_the_accounts_in_place() {
    use radical_store::rows::{self, ModelTemplate, Project, User};
    let dir = std::env::temp_dir().join(format!("radical-upgrade-{}", radical_store::new_id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("radical.db");
    {
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(include_str!("../migrations/0001_initial.sql"))
            .unwrap();
        conn.execute_batch(
            "INSERT INTO projects (id, name, coordinate_precision, map_tiles_enabled)
                 VALUES ('p1', 'Before', 'full', 1);
             INSERT INTO model_templates (id, name, model_tree, created_at, updated_at)
                 VALUES ('t1', 'Kept', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00');
             PRAGMA user_version = 1;",
        )
        .unwrap();
    }
    let store = Store::open(&path).unwrap();
    let version: i64 = store
        .read(|c| Ok(c.pragma_query_value(None, "user_version", |r| r.get(0))?))
        .unwrap();
    assert_eq!(version as usize, radical_store::SCHEMA_VERSION);
    let project: Project = store.read(|c| rows::get(c, "p1")).unwrap().unwrap();
    assert_eq!((project.name.as_str(), project.owner_id), ("Before", None));
    let template: ModelTemplate = store.read(|c| rows::get(c, "t1")).unwrap().unwrap();
    assert_eq!((template.name.as_str(), template.owner_id), ("Kept", None));
    let users: Vec<User> = store
        .read(|c| rows::select_where(c, "1 = 1", [], ""))
        .unwrap();
    assert!(users.is_empty());
    drop(store);
    std::fs::remove_dir_all(dir).unwrap();
}
