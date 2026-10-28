//! OxCal 4.4's own exports of the fixture runs, read as OxCal wrote them.
//!
//! Each run in `desktop/corpus/fixtures/oxcal*/` is a `.oxcal` model with up
//! to two exports beside it:
//!
//! - `* Raw.csv`: one row per (parameter, distribution, calendar year), the
//!   full likelihood and posterior distributions at OxCal's resolution, in
//!   fractional years AD.
//! - `* Table.csv`: the summary OxCal reports -- unmodelled and modelled
//!   ranges at 68.3% and 95.4%, each date's agreement index A and, under an
//!   outlier model, its inlier probability P -- with Amodel and Aoverall in
//!   the header.
//!
//! Two readings of the table matter. Its from/to columns are the ENVELOPE of
//! the HPD region, not one row per lobe: OxCal's raw distribution for LD10012
//! is AD 1293-1328 plus 1333-1393, which the table reports as 1291-1394. And P
//! is the INLIER probability: Dune Section's wild date LB10002 reads 0.1 while
//! the fifteen sound dates read 98.5-99.4, so the posterior outlier
//! probability is 1 - P/100.

use std::collections::HashMap;
use std::path::PathBuf;

/// OxCal's time axis is fractional years AD with each calendar year at its
/// midpoint, so 0 cal BP -- the year AD 1950 -- sits at 1950.5, and its raw
/// exports fall on x.5 AD, i.e. on whole cal BP years. Converting them with
/// 1950 instead shifts every OxCal distribution half a year young (measured on
/// DS: best-fit constant 1950.54 across all twelve likelihoods).
pub const OXCAL_PRESENT: f64 = 1950.5;

pub fn fixture_path(relative: &str) -> PathBuf {
    radical_corpus::corpus_dir().join("fixtures").join(relative)
}

/// OxCal drops punctuation from names: 'Ld-10012' -> 'LD10012'. Every lookup
/// here goes through this, so a name matches whichever way it was written.
pub fn key(name: &str) -> String {
    name.chars()
        .filter(char::is_ascii_alphanumeric)
        .map(|c| c.to_ascii_uppercase())
        .collect()
}

/// RFC 4180 records: quoted fields may hold commas and newlines, as the
/// table's "Indices" header cell does.
pub fn csv_records(text: &str) -> Vec<Vec<String>> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut records = Vec::new();
    let (mut record, mut field) = (Vec::new(), String::new());
    let (mut quoted, mut chars) = (false, text.chars().peekable());
    while let Some(c) = chars.next() {
        match (quoted, c) {
            (true, '"') if chars.peek() == Some(&'"') => {
                field.push('"');
                chars.next();
            }
            (true, '"') => quoted = false,
            (true, c) => field.push(c),
            (false, '"') => quoted = true,
            (false, ',') => record.push(std::mem::take(&mut field)),
            (false, '\r') => {}
            (false, '\n') => {
                record.push(std::mem::take(&mut field));
                records.push(std::mem::take(&mut record));
            }
            (false, c) => field.push(c),
        }
    }
    if !field.is_empty() || !record.is_empty() {
        record.push(field);
        records.push(record);
    }
    records
}

fn read(relative: &str) -> String {
    let path = fixture_path(relative);
    std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

/// A distribution as OxCal exported it, ascending in cal BP. The weights are
/// as exported; everything that reads them normalises for itself.
#[derive(Debug, Clone)]
pub struct Dist {
    pub cal: Vec<f64>,
    pub prob: Vec<f64>,
}

impl Dist {
    fn from_ad(mut pairs: Vec<(f64, f64)>) -> Self {
        pairs.sort_by(|a, b| a.0.total_cmp(&b.0));
        pairs.reverse();
        Self {
            cal: pairs.iter().map(|p| OXCAL_PRESENT - p.0).collect(),
            prob: pairs.iter().map(|p| p.1).collect(),
        }
    }

    pub fn mean(&self) -> f64 {
        let total: f64 = self.prob.iter().sum();
        self.cal
            .iter()
            .zip(&self.prob)
            .map(|(c, p)| c * p)
            .sum::<f64>()
            / total
    }

    /// `[[cal BP, weight], ...]`, the shape the agreement index reads.
    pub fn pairs(&self) -> Vec<[f64; 2]> {
        self.cal
            .iter()
            .zip(&self.prob)
            .map(|(&c, &p)| [c, p])
            .collect()
    }
}

/// One exported parameter: its likelihood, if it has one, and its posterior.
#[derive(Debug, Default)]
pub struct Parameter {
    pub op: String,
    pub likelihood: Option<Dist>,
    pub posterior: Option<Dist>,
}

/// A run's `Raw.csv`. Calendar parameters by [`key`]ed name; an outlier
/// model's scale posterior (op `U`, log10 years) separately, since it is not
/// a calendar age and would be meaningless converted as though it were.
pub struct Raw {
    pub parameters: HashMap<String, Parameter>,
    /// (log10 years, weight), ascending.
    pub scale: Vec<(f64, f64)>,
}

impl Raw {
    pub fn read(stem: &str) -> Self {
        let records = csv_records(&read(&format!("{stem} Raw.csv")));
        let header = &records[0];
        let column = |name: &str| header.iter().position(|h| h == name).unwrap();
        let (op, name, kind) = (column("op"), column("name"), column("type"));
        let (value, probability) = (column("value"), column("probability"));
        let mut grouped: HashMap<(String, String, String), Vec<(f64, f64)>> = HashMap::new();
        let mut scale = Vec::new();
        for row in &records[1..] {
            if row.len() <= probability {
                continue;
            }
            let pair = (
                row[value].parse::<f64>().unwrap(),
                row[probability].parse::<f64>().unwrap(),
            );
            match row[op].as_str() {
                "U" if row[kind] == "posterior" => scale.push(pair),
                "R_Date" | "Boundary" => grouped
                    .entry((row[op].clone(), key(&row[name]), row[kind].clone()))
                    .or_default()
                    .push(pair),
                _ => {}
            }
        }
        let mut parameters: HashMap<String, Parameter> = HashMap::new();
        for ((op, name, kind), pairs) in grouped {
            let entry = parameters.entry(name).or_default();
            entry.op = op;
            let dist = Some(Dist::from_ad(pairs));
            match kind.as_str() {
                "likelihood" => entry.likelihood = dist,
                "posterior" => entry.posterior = dist,
                other => panic!("{stem}: unexpected distribution type {other}"),
            }
        }
        scale.sort_by(|a, b| a.0.total_cmp(&b.0));
        Self { parameters, scale }
    }

    pub fn get(&self, name: &str) -> &Parameter {
        self.parameters
            .get(&key(name))
            .unwrap_or_else(|| panic!("{name}: not in the export"))
    }

    pub fn likelihood(&self, name: &str) -> &Dist {
        self.get(name).likelihood.as_ref().unwrap()
    }

    pub fn posterior(&self, name: &str) -> &Dist {
        self.get(name).posterior.as_ref().unwrap()
    }
}

/// HPD envelopes in AD, (from, to): index 0 is 68.3%, 1 is 95.4%. A lobe
/// OxCal reports on a continuation row lands after the first.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct Ranges(pub [Vec<(f64, f64)>; 2]);

#[derive(Debug, Default)]
pub struct Row {
    pub op: String,
    pub unmodelled: Ranges,
    pub modelled: Ranges,
    pub agreement: Option<f64>,
    /// P, the INLIER probability in percent, under an outlier model.
    pub inlier_percent: Option<f64>,
}

impl Row {
    pub fn outlier_probability(&self) -> f64 {
        1.0 - self.inlier_percent.unwrap() / 100.0
    }
}

/// A run's `Table.csv`.
pub struct Table {
    pub rows: HashMap<String, Row>,
    /// Keys in the table's own order.
    pub order: Vec<String>,
    pub amodel: f64,
    pub aoverall: f64,
}

impl Table {
    pub fn read(stem: &str) -> Self {
        let text = read(&format!("{stem} Table.csv"));
        let header = |label: &str| -> f64 {
            let at = text.find(label).unwrap() + label.len();
            text[at..]
                .trim_start()
                .split(|c: char| !(c.is_ascii_digit() || c == '.'))
                .next()
                .unwrap()
                .parse()
                .unwrap()
        };
        let (amodel, aoverall) = (header("Amodel "), header("Aoverall "));
        let (mut rows, mut order) = (HashMap::new(), Vec::new());
        let mut current: Option<String> = None;
        for record in csv_records(&text).into_iter().skip(2) {
            if record.len() < 9 {
                continue;
            }
            let name = record[0].trim();
            let cells: Vec<&str> = record[1..9].iter().map(|c| c.trim()).collect();
            if !name.is_empty() {
                let (op, label) = name.split_once(' ').unwrap_or((name, name));
                let k = key(label);
                current = Some(k.clone());
                if cells.iter().all(|c| c.is_empty()) {
                    continue;
                }
                let number = |i: usize| {
                    record
                        .get(i)
                        .map(|c| c.trim())
                        .filter(|c| !c.is_empty())
                        .map(|c| c.parse().unwrap())
                };
                order.push(k.clone());
                rows.insert(
                    k,
                    Row {
                        op: op.to_owned(),
                        agreement: number(10),
                        inlier_percent: number(12),
                        ..Row::default()
                    },
                );
            }
            let Some(row) = current.as_ref().and_then(|k| rows.get_mut(k)) else {
                continue;
            };
            for (which, level, (a, b)) in [
                (0, 0, (0, 1)),
                (0, 1, (2, 3)),
                (1, 0, (4, 5)),
                (1, 1, (6, 7)),
            ] {
                if !cells[a].is_empty() && !cells[b].is_empty() {
                    let ranges = if which == 0 {
                        &mut row.unmodelled
                    } else {
                        &mut row.modelled
                    };
                    ranges.0[level].push((cells[a].parse().unwrap(), cells[b].parse().unwrap()));
                }
            }
        }
        Self {
            rows,
            order,
            amodel,
            aoverall,
        }
    }

    pub fn get(&self, name: &str) -> &Row {
        self.rows
            .get(&key(name))
            .unwrap_or_else(|| panic!("{name}: not in the table"))
    }

    /// Each R_Date's A, in the table's order.
    pub fn date_indices(&self) -> Vec<(String, f64)> {
        self.order
            .iter()
            .filter(|k| self.rows[*k].op == "R_Date")
            .map(|k| (k.clone(), self.rows[k].agreement.unwrap()))
            .collect()
    }
}

fn median(mut values: Vec<f64>) -> f64 {
    values.sort_by(f64::total_cmp);
    let n = values.len();
    if n % 2 == 1 {
        values[n / 2]
    } else {
        (values[n / 2 - 1] + values[n / 2]) / 2.0
    }
}

fn step_of(cal: &[f64]) -> f64 {
    if cal.len() > 1 {
        median(cal.windows(2).map(|w| w[1] - w[0]).collect())
    } else {
        5.0
    }
}

/// HPD ranges as (min cal BP, max cal BP) from a distribution on a regular
/// grid, the way OxCal builds them: whole bins, highest first, until the
/// level is reached. Uses bin EDGES, so a single-bin range gets the width the
/// bin represents rather than zero.
pub fn hpd_from_distribution(cal: &[f64], prob: &[f64], confidence: f64) -> Vec<(f64, f64)> {
    let step = step_of(cal);
    let total: f64 = prob.iter().map(|p| p * step).sum();
    let mass: Vec<f64> = prob.iter().map(|p| p * step / total).collect();
    let mut order: Vec<usize> = (0..mass.len()).collect();
    order.sort_by(|&a, &b| mass[b].total_cmp(&mass[a]));
    let mut running = 0.0;
    let mut cut = order.len();
    for (i, &k) in order.iter().enumerate() {
        running += mass[k];
        if running >= confidence {
            cut = i + 1;
            break;
        }
    }
    let mut chosen = order[..cut.min(order.len())].to_vec();
    chosen.sort_unstable();
    let mut ranges = Vec::new();
    let mut start = chosen[0];
    for pair in chosen.windows(2) {
        if pair[1] != pair[0] + 1 {
            ranges.push((cal[start] - step / 2.0, cal[pair[0]] + step / 2.0));
            start = pair[1];
        }
    }
    ranges.push((
        cal[start] - step / 2.0,
        cal[*chosen.last().unwrap()] + step / 2.0,
    ));
    ranges
}

pub fn envelope(ranges: &[(f64, f64)]) -> (f64, f64) {
    (
        ranges.iter().map(|r| r.0).fold(f64::INFINITY, f64::min),
        ranges.iter().map(|r| r.1).fold(f64::NEG_INFINITY, f64::max),
    )
}

/// A cal BP (min, max) range as OxCal's AD (from, to).
pub fn as_ad(range: (f64, f64)) -> (f64, f64) {
    (OXCAL_PRESENT - range.1, OXCAL_PRESENT - range.0)
}

/// `numpy.interp`: linear between the points, the end values beyond them
/// unless `outside` is given.
pub fn interp(x: f64, xp: &[f64], fp: &[f64], outside: Option<f64>) -> f64 {
    if x < xp[0] {
        return outside.unwrap_or(fp[0]);
    }
    if x > xp[xp.len() - 1] {
        return outside.unwrap_or(fp[fp.len() - 1]);
    }
    let i = xp.partition_point(|&v| v <= x).clamp(1, xp.len() - 1);
    let (x0, x1) = (xp[i - 1], xp[i]);
    if x1 == x0 {
        return fp[i];
    }
    fp[i - 1] + (fp[i] - fp[i - 1]) * (x - x0) / (x1 - x0)
}

/// Whole years from `lo` while below `hi`: `numpy.arange(lo, hi, 1.0)`.
pub fn years(lo: f64, hi: f64) -> Vec<f64> {
    (0..)
        .map(|i| lo + f64::from(i))
        .take_while(|&x| x < hi)
        .collect()
}

/// The overlapping coefficient of two densities, 1.0 for identical, compared
/// on a shared one-year grid since the two come from different sources.
pub fn overlap(c1: &[f64], p1: &[f64], c2: &[f64], p2: &[f64]) -> f64 {
    let grid = years(c1[0].max(c2[0]), c1[c1.len() - 1].min(c2[c2.len() - 1]));
    let normalised = |c: &[f64], p: &[f64]| -> Vec<f64> {
        let values: Vec<f64> = grid.iter().map(|&x| interp(x, c, p, None)).collect();
        let total: f64 = values.iter().sum();
        values.iter().map(|v| v / total).collect()
    };
    let (a, b) = (normalised(c1, p1), normalised(c2, p2));
    a.iter().zip(&b).map(|(x, y)| x.min(*y)).sum()
}
