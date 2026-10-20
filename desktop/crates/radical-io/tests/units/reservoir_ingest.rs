use crate::*;
use radical_core::reservoir::{is_recognised_feeding, normalise_feeding};
use radical_io::python::html_unescape;
use radical_io::reservoir_ingest::{
    Field, IngestError, Record, ReservoirRow, Timestamp, content_hash, decode_field,
    extract_freshness, extract_records, records_to_rows, rows_from_csv, rows_to_csv, snapshot_id,
    wrap_longitude,
};

fn rows_json(rows: &[ReservoirRow]) -> Value {
    Value::Array(rows.iter().map(ReservoirRow::to_json).collect())
}

fn bytes_of(i: &Value) -> Vec<u8> {
    match i["file"].as_str() {
        Some(path) => std::fs::read(radical_corpus::repo_root().join(path)).unwrap(),
        None => crate::date_import::hex(i["hex"].as_str().unwrap()),
    }
}

fn pipeline(raw: &[u8]) -> Result<Value, IngestError> {
    let (records, declared) = extract_records(raw)?;
    let (rows, report) = records_to_rows(&records, declared);
    let text = rows_to_csv(&rows);
    let back = if rows.is_empty() {
        Vec::new()
    } else {
        rows_from_csv(&text)?
    };
    let mut sorted = rows.clone();
    sorted.sort_by_key(|r| r.map_no);
    Ok(serde_json::json!({
        "declared": declared, "record_count": records.len(), "rows": rows_json(&rows),
        "report": report.to_json(), "content_sha256": content_hash(&rows),
        "csv": text, "csv_round_trip": back == sorted,
    }))
}

fn record(v: &Value) -> Record {
    v.as_object()
        .unwrap()
        .iter()
        .map(|(k, v)| {
            let field = v
                .as_str()
                .map_or(Field::Null, |s| Field::Text(s.to_owned()));
            (k.clone(), field)
        })
        .collect()
}

fn strings(v: &Value) -> Vec<Option<String>> {
    v.as_array()
        .unwrap()
        .iter()
        .map(|s| s.as_str().map(str::to_owned))
        .collect()
}

/// The ingest's parsing to the byte, refusals to the word.
#[test]
fn reservoir_ingest() {
    for case in family("reservoir_ingest").cases {
        let i = case.expanded_inputs();
        let name = case.name.as_str();
        let got: Result<Value, IngestError> = match name.split('/').next().unwrap() {
            "pipeline" if i["file"].is_string() => pipeline(&bytes_of(&i)).map(|mut out| {
                // Held by digest: the rows quote the source's citations.
                let o = out.as_object_mut().unwrap();
                o.remove("rows");
                let csv = o.remove("csv").unwrap();
                o.insert(
                    "csv_sha256".into(),
                    Value::from(radical_corpus::sha256_hex(csv.as_str().unwrap().as_bytes())),
                );
                out
            }),
            "pipeline" => pipeline(&bytes_of(&i)),
            "freshness" => extract_freshness(&bytes_of(&i)).map(|f| f.to_json()),
            "decode_field" => Ok(
                serde_json::json!({"decoded": i["hex"].as_array().unwrap().iter().map(|h| {
                let (text, how) = decode_field(&Field::Bytes(crate::date_import::hex(h.as_str().unwrap())));
                serde_json::json!([text, how])
            }).collect::<Vec<_>>()}),
            ),
            "records_to_rows" => {
                let records: Vec<Record> = i["records"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(record)
                    .collect();
                let (rows, report) = records_to_rows(&records, i["declared"].as_u64());
                Ok(serde_json::json!({
                    "rows": rows_json(&rows), "report": report.to_json(), "content_sha256": content_hash(&rows),
                }))
            }
            "rows_from_csv" => rows_from_csv(i["text"].as_str().unwrap())
                .map(|rows| serde_json::json!({"rows": rows_json(&rows)})),
            "html_unescape" => Ok(serde_json::json!({"unescaped": strings(&i["texts"]).iter()
                .map(|t| html_unescape(t.as_deref().unwrap())).collect::<Vec<_>>()})),
            "parse_timestamp" => {
                // Through the one public path that reads a stamp.
                let parsed: Vec<Option<String>> = strings(&i["texts"])
                    .iter()
                    .map(|t| {
                        let rec: Record = vec![
                            ("mapno".into(), Field::Text("1".into())),
                            ("lat".into(), Field::Text("1".into())),
                            ("lon".into(), Field::Text("1".into())),
                            ("last_changed".into(), Field::Text(t.clone().unwrap())),
                        ];
                        records_to_rows(&[rec], None)
                            .1
                            .source_last_changed
                            .map(|s| s.isoformat())
                    })
                    .collect();
                Ok(serde_json::json!({"parsed": parsed}))
            }
            "wrap_longitude" => Ok(
                serde_json::json!({"wrapped": i["values"].as_array().unwrap().iter()
                .map(|v| wrap_longitude(v.as_f64().unwrap())).collect::<Vec<_>>()}),
            ),
            "snapshot_id" => {
                let stamp = Timestamp {
                    year: 2026,
                    month: 5,
                    day: 11,
                    hour: 10,
                    minute: 0,
                    second: 0,
                };
                Ok(serde_json::json!({"id": snapshot_id(i["slug"].as_str().unwrap(), &stamp)}))
            }
            "feeding" => {
                let spellings = strings(&i["spellings"]);
                Ok(serde_json::json!({
                    "normalised": spellings.iter().map(|s| normalise_feeding(s.as_deref())).collect::<Vec<_>>(),
                    "recognised": spellings.iter().map(|s| is_recognised_feeding(s.as_deref())).collect::<Vec<_>>(),
                }))
            }
            other => panic!("unhandled case kind {other}"),
        };
        match (got, &case.error) {
            (Ok(v), None) => same_json(name, &v, &outputs(&case)),
            (Err(e), Some(want)) => assert_eq!(e.0, want["message"].as_str().unwrap(), "{name}"),
            (got, want) => panic!("{name}: {got:?} vs {want:?}"),
        }
    }
}
