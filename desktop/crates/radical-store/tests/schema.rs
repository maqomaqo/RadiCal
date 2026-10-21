//! The store's schema held to the backend's, as its migrations leave it on
//! SQLite (corpus `schema/sqlite`): every table, column (name, declared
//! type, NOT NULL, default, primary-key position), foreign key and declared
//! index, less what single-user leaves out.

use radical_corpus::load_family;
use radical_store::Store;
use radical_store::rusqlite::Connection;
use serde_json::{Value, json};

/// The backend's tables that have no place in a one-person database.
const NOT_HERE: [&str; 3] = ["alembic_version", "users", "personal_access_tokens"];
/// The columns that said which account owned a row.
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

/// The backend's description of a table, less ownership.
fn without_ownership(want: &Value) -> Value {
    let owned = |v: &Value, key: &str| OWNERSHIP.contains(&v[key].as_str().unwrap_or(""));
    let keep = |key: &str, f: &dyn Fn(&Value) -> bool| -> Vec<Value> {
        want[key]
            .as_array()
            .unwrap()
            .iter()
            .filter(|v| f(v))
            .cloned()
            .collect()
    };
    json!({
        "columns": keep("columns", &|c| !owned(c, "name")),
        "foreign_keys": keep("foreign_keys", &|f| f["references"] != "users"),
        "indexes": keep("indexes", &|i| !i["columns"].as_array().unwrap().iter()
            .any(|c| OWNERSHIP.contains(&c.as_str().unwrap()))),
    })
}

#[test]
fn the_schema_is_the_backends_less_the_accounts() {
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
        let want = without_ownership(case.outputs.as_ref().unwrap());
        assert_eq!(describe(&conn, &case.name), want, "table {}", case.name);
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
