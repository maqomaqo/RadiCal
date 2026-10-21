//! Python's `csv` module, reading, as `csv.DictReader(io.StringIO(text),
//! delimiter=...)` reads with the default (excel) dialect: `"` quotes, a
//! doubled quote inside quotes is one, no escape character, not strict.
//!
//! Ported from CPython's `_csv.c` state machine rather than taken from a
//! general CSV library, because what a malformed file turns into is part of
//! what the importer reports -- a stray quote, a ragged row, a line ending
//! the reader refuses -- and those are the cases libraries disagree on.

/// Python's `_csv.Error`: its message, and the reader's `line_num` when it
/// stopped -- the lines read so far, the one it stopped in included.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CsvError {
    pub message: String,
    pub line: usize,
}

/// The longest cell the reader accepts, in characters: Python's default
/// `csv.field_size_limit()`, which the importers leave as it is.
pub const FIELD_LIMIT: usize = 131_072;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    StartRecord,
    StartField,
    InField,
    InQuotedField,
    QuoteInQuotedField,
    EatCrnl,
}

/// `io.StringIO(text)` iterated by line: split after each `\n` only, the
/// newline kept. A lone `\r` is not a line break here, which is how a file
/// with old Mac line endings reaches the reader as one long line.
fn lines(text: &str) -> impl Iterator<Item = &str> {
    text.split_inclusive('\n')
}

/// Every record of `text`, as `csv.reader` returns them; a blank line is an
/// empty record.
pub fn records(text: &str, delimiter: char) -> Result<Vec<Vec<String>>, CsvError> {
    let mut out = Vec::new();
    let mut input = lines(text);
    let mut line_num = 0usize;
    loop {
        let mut state = State::StartRecord;
        let mut fields: Vec<String> = Vec::new();
        let mut field = String::new();
        let mut field_len = 0usize;
        loop {
            let Some(line) = input.next() else {
                // End of input: a record cut off inside quotes, or a field
                // half read, is kept (the reader is not strict).
                if field_len != 0 || state == State::InQuotedField {
                    fields.push(std::mem::take(&mut field));
                    out.push(fields);
                }
                return Ok(out);
            };
            line_num += 1;
            for c in line.chars().map(Some).chain(std::iter::once(None)) {
                // None is the end of the line, CPython's EOL.
                let is_end = |c: Option<char>| matches!(c, None | Some('\n' | '\r'));
                let save = |fields: &mut Vec<String>, field: &mut String, field_len: &mut usize| {
                    fields.push(std::mem::take(field));
                    *field_len = 0;
                };
                // Refused before the character that would pass the limit.
                let add = |field: &mut String, field_len: &mut usize, ch: char| {
                    if *field_len >= FIELD_LIMIT {
                        return Err(CsvError {
                            message: format!("field larger than field limit ({FIELD_LIMIT})"),
                            line: line_num,
                        });
                    }
                    field.push(ch);
                    *field_len += 1;
                    Ok(())
                };
                // START_RECORD falls through to START_FIELD.
                if state == State::StartRecord {
                    match c {
                        None => continue,
                        Some('\n' | '\r') => {
                            state = State::EatCrnl;
                            continue;
                        }
                        _ => state = State::StartField,
                    }
                }
                match state {
                    State::StartRecord => unreachable!(),
                    State::StartField => {
                        if is_end(c) {
                            save(&mut fields, &mut field, &mut field_len);
                            state = if c.is_none() {
                                State::StartRecord
                            } else {
                                State::EatCrnl
                            };
                        } else if c == Some('"') {
                            state = State::InQuotedField;
                        } else if c == Some(delimiter) {
                            save(&mut fields, &mut field, &mut field_len);
                        } else {
                            add(&mut field, &mut field_len, c.unwrap())?;
                            state = State::InField;
                        }
                    }
                    State::InField => {
                        if is_end(c) {
                            save(&mut fields, &mut field, &mut field_len);
                            state = if c.is_none() {
                                State::StartRecord
                            } else {
                                State::EatCrnl
                            };
                        } else if c == Some(delimiter) {
                            save(&mut fields, &mut field, &mut field_len);
                            state = State::StartField;
                        } else {
                            add(&mut field, &mut field_len, c.unwrap())?;
                        }
                    }
                    State::InQuotedField => match c {
                        None => {}
                        Some('"') => state = State::QuoteInQuotedField,
                        Some(ch) => add(&mut field, &mut field_len, ch)?,
                    },
                    State::QuoteInQuotedField => {
                        if c == Some('"') {
                            add(&mut field, &mut field_len, '"')?;
                            state = State::InQuotedField;
                        } else if c == Some(delimiter) {
                            save(&mut fields, &mut field, &mut field_len);
                            state = State::StartField;
                        } else if is_end(c) {
                            save(&mut fields, &mut field, &mut field_len);
                            state = if c.is_none() {
                                State::StartRecord
                            } else {
                                State::EatCrnl
                            };
                        } else {
                            add(&mut field, &mut field_len, c.unwrap())?;
                            state = State::InField;
                        }
                    }
                    State::EatCrnl => match c {
                        Some('\n' | '\r') => {}
                        None => state = State::StartRecord,
                        Some(_) => {
                            return Err(CsvError {
                                message: "new-line character seen in unquoted field - do you \
                                          need to open the file with newline=''?"
                                    .to_owned(),
                                line: line_num,
                            });
                        }
                    },
                }
            }
            if state == State::StartRecord {
                break;
            }
        }
        out.push(fields);
    }
}

/// One `csv.DictReader` row: the header's values by name, in the header's
/// order with a repeated name keeping its last value, `None` for a field the
/// row is too short to have, and the extra fields of a long row under no
/// name (Python's `restkey` of None).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub values: Vec<(String, Option<String>)>,
    pub extra: Vec<String>,
}

impl Row {
    pub fn get(&self, name: &str) -> Option<&Option<String>> {
        self.values.iter().find(|(k, _)| k == name).map(|(_, v)| v)
    }
}

/// `csv.DictReader`: (fieldnames, rows), the header being the first record
/// (None when there is none) and blank lines skipped.
pub fn dict_rows(text: &str, delimiter: char) -> Result<(Option<Vec<String>>, Vec<Row>), CsvError> {
    let mut records = records(text, delimiter)?.into_iter();
    let Some(header) = records.next() else {
        return Ok((None, Vec::new()));
    };
    let mut rows = Vec::new();
    for record in records {
        if record.is_empty() {
            continue;
        }
        let mut values: Vec<(String, Option<String>)> = Vec::new();
        for (i, name) in header.iter().enumerate() {
            let value = record.get(i).cloned();
            match values.iter_mut().find(|(k, _)| k == name) {
                // dict(zip(...)) then restval: a repeated name keeps its
                // first place and its last value, None where the row is too
                // short to reach it.
                Some(slot) => slot.1 = value,
                None => values.push((name.clone(), value)),
            }
        }
        let extra = if record.len() > header.len() {
            record[header.len()..].to_vec()
        } else {
            Vec::new()
        };
        rows.push(Row { values, extra });
    }
    Ok((Some(header), rows))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r(text: &str) -> Vec<Vec<String>> {
        records(text, ',').unwrap()
    }

    /// Python refuses the character that would take a cell past the limit,
    /// quotes not counted, and says how many lines it had read.
    #[test]
    fn a_cell_past_the_field_limit_is_refused() {
        let at = format!("a\n\"{}\"\n", "x".repeat(FIELD_LIMIT));
        assert_eq!(r(&at)[1][0].len(), FIELD_LIMIT);
        let past = format!("a\n\"open\n{}\n", "x".repeat(FIELD_LIMIT));
        let err = records(&past, ',').unwrap_err();
        assert_eq!(err.message, "field larger than field limit (131072)");
        assert_eq!(err.line, 3);
    }

    #[test]
    fn quoting_follows_pythons_reader() {
        assert_eq!(r("a,\"b,c\",d\n"), [["a", "b,c", "d"]]);
        assert_eq!(r("\"say \"\"hi\"\"\"\n"), [["say \"hi\""]]);
        assert_eq!(r("mid\"quote,x\n"), [["mid\"quote", "x"]]);
        assert_eq!(r("\"two\nlines\",x\n"), [["two\nlines", "x"]]);
        assert_eq!(r("\"ab\"cd,x\n"), [["abcd", "x"]]);
        assert_eq!(r("\"never closed\n"), [["never closed\n"]]);
        assert_eq!(
            r("a,b\r\n\r\nc\r\n"),
            vec![vec!["a", "b"], vec![], vec!["c"]]
        );
        assert_eq!(r("a,b"), [["a", "b"]]);
        assert_eq!(r(",\n"), [["", ""]]);
    }

    #[test]
    fn a_lone_carriage_return_is_refused() {
        assert!(records("a,b\rc,d\r", ',').is_err());
        assert_eq!(r("a,b\r"), [["a", "b"]]);
    }
}
