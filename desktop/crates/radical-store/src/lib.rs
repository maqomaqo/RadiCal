//! RadiCal's database: the backend's tables in one SQLite file, in the OS
//! app-data folder, for one person.
//!
//! The schema is the backend's as its migrations leave it on SQLite, less the
//! accounts and the columns recording which account owned a row
//! (`migrations/`, held to the backend's by `tests/schema.rs`). Timestamps
//! and booleans are stored as SQLAlchemy stores them, so a row reads back as
//! the backend would have read it.
//!
//! One writer connection and a small pool of readers, in WAL mode: readers
//! never wait for the writer, and writes are serialised here rather than by
//! SQLite's busy handler. Foreign keys are enforced, which the backend never
//! asked SQLite to do -- its cascades were the ORM's, and here they are the
//! store's own, so a missed one fails loudly instead of leaving orphans.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Mutex, MutexGuard};
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::types::{FromSql, FromSqlError, FromSqlResult, ToSql, ToSqlOutput, ValueRef};
use rusqlite::{Connection, OpenFlags};

pub use rusqlite;

pub mod rows;

/// Every migration, in order; the database's `user_version` is how many have
/// been applied.
const MIGRATIONS: &[&str] = &[
    include_str!("../migrations/0001_initial.sql"),
    include_str!("../migrations/0002_accounts.sql"),
];

/// The schema version this build writes.
pub const SCHEMA_VERSION: usize = MIGRATIONS.len();

/// Readers kept open beside the writer.
const READERS: usize = 4;

#[derive(Debug)]
pub enum Error {
    Sqlite(rusqlite::Error),
    /// The file was written by a newer RadiCal than this one.
    TooNew {
        found: usize,
        supported: usize,
    },
    Io(std::io::Error),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(e) => write!(f, "{e}"),
            Self::TooNew { found, supported } => write!(
                f,
                "This database was written by a newer RadiCal (schema {found}; this version reads up to {supported}). Update RadiCal to open it."
            ),
            Self::Io(e) => write!(f, "{e}"),
        }
    }
}

impl std::error::Error for Error {}

impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        Self::Sqlite(e)
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

/// Where the database lives when nobody says otherwise:
/// `~/Library/Application Support/RadiCal` on macOS, `%APPDATA%\RadiCal` on
/// Windows, `$XDG_DATA_HOME/radical` (default `~/.local/share/radical`)
/// elsewhere. None when the environment names no home at all.
pub fn default_dir() -> Option<PathBuf> {
    let env = |k: &str| {
        std::env::var_os(k)
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    };
    if cfg!(target_os = "macos") {
        env("HOME").map(|h| h.join("Library/Application Support/RadiCal"))
    } else if cfg!(windows) {
        env("APPDATA").map(|a| a.join("RadiCal"))
    } else {
        env("XDG_DATA_HOME")
            .or_else(|| env("HOME").map(|h| h.join(".local/share")))
            .map(|d| d.join("radical"))
    }
}

/// The database file inside [`default_dir`].
pub const FILE_NAME: &str = "radical.db";

/// The open database.
pub struct Store {
    writer: Mutex<Connection>,
    readers: Vec<Mutex<Connection>>,
    next_reader: AtomicUsize,
    path: PathBuf,
}

fn configure(conn: &Connection) -> Result<()> {
    conn.busy_timeout(std::time::Duration::from_secs(10))?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    Ok(())
}

impl Store {
    /// Open (creating if need be) the database at `path`, bringing its
    /// schema up to date.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir)?;
        }
        let writer = Connection::open(&path)?;
        writer.pragma_update(None, "journal_mode", "WAL")?;
        writer.pragma_update(None, "synchronous", "NORMAL")?;
        configure(&writer)?;
        migrate(&writer)?;
        let mut readers = Vec::with_capacity(READERS);
        for _ in 0..READERS {
            let reader = Connection::open_with_flags(
                &path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )?;
            configure(&reader)?;
            readers.push(Mutex::new(reader));
        }
        Ok(Self {
            writer: Mutex::new(writer),
            readers,
            next_reader: AtomicUsize::new(0),
            path,
        })
    }

    /// Open the database in [`default_dir`].
    pub fn open_default() -> Result<Self> {
        let dir = default_dir().ok_or_else(|| {
            Error::Io(std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "no home folder to keep the database in",
            ))
        })?;
        Self::open(dir.join(FILE_NAME))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The one writer. Hold it for as short a time as the write takes.
    pub fn writer(&self) -> MutexGuard<'_, Connection> {
        self.writer
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// A reader, round-robin. It sees the last committed state.
    pub fn reader(&self) -> MutexGuard<'_, Connection> {
        let i = self.next_reader.fetch_add(1, Ordering::Relaxed) % self.readers.len();
        self.readers[i]
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Run `work` in one transaction on the writer: committed if it returns
    /// Ok, rolled back otherwise.
    pub fn write<T>(&self, work: impl FnOnce(&rusqlite::Transaction) -> Result<T>) -> Result<T> {
        let mut conn = self.writer();
        let tx = conn.transaction()?;
        let out = work(&tx)?;
        tx.commit()?;
        Ok(out)
    }

    /// Run `work` on a reader.
    pub fn read<T>(&self, work: impl FnOnce(&Connection) -> Result<T>) -> Result<T> {
        work(&self.reader())
    }
}

/// Apply every migration the database has not had, each in its own
/// transaction with the version bump.
fn migrate(conn: &Connection) -> Result<()> {
    let found: usize =
        conn.pragma_query_value(None, "user_version", |r| r.get::<_, i64>(0))? as usize;
    if found > SCHEMA_VERSION {
        return Err(Error::TooNew {
            found,
            supported: SCHEMA_VERSION,
        });
    }
    for (index, sql) in MIGRATIONS.iter().enumerate().skip(found) {
        conn.execute_batch(&format!(
            "BEGIN;\n{sql}\nPRAGMA user_version = {};\nCOMMIT;",
            index + 1
        ))?;
    }
    Ok(())
}

/// A new row id: a random UUID, as the backend's `str(uuid.uuid4())`.
pub fn new_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

// --------------------------------------------------------------------------
// Timestamps
// --------------------------------------------------------------------------

/// A naive UTC instant to the microsecond, as the backend's
/// `datetime.utcnow()`: stored as `YYYY-MM-DD HH:MM:SS.ffffff` (SQLAlchemy's
/// SQLite format) and sent as `isoformat()`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Timestamp {
    /// Microseconds since 1970-01-01T00:00:00.
    pub micros: i64,
}

/// Days since 1970-01-01 to (year, month, day), proleptic Gregorian
/// (Howard Hinnant's `civil_from_days`).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// (year, month, day) to days since 1970-01-01.
fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let m = i64::from(m);
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

const MICROS_PER_DAY: i64 = 86_400_000_000;

impl Timestamp {
    pub fn now() -> Self {
        let since = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        Self {
            micros: since.as_micros() as i64,
        }
    }

    pub fn from_parts(
        year: i64,
        month: u32,
        day: u32,
        hour: u32,
        minute: u32,
        second: u32,
        micro: u32,
    ) -> Self {
        let days = days_from_civil(year, month, day);
        let secs = i64::from(hour) * 3600 + i64::from(minute) * 60 + i64::from(second);
        Self {
            micros: days * MICROS_PER_DAY + secs * 1_000_000 + i64::from(micro),
        }
    }

    fn parts(&self) -> (i64, u32, u32, u32, u32, u32, u32) {
        let days = self.micros.div_euclid(MICROS_PER_DAY);
        let rem = self.micros.rem_euclid(MICROS_PER_DAY);
        let (y, m, d) = civil_from_days(days);
        let secs = (rem / 1_000_000) as u32;
        (
            y,
            m,
            d,
            secs / 3600,
            secs / 60 % 60,
            secs % 60,
            (rem % 1_000_000) as u32,
        )
    }

    /// SQLAlchemy's SQLite storage form, microseconds always written.
    pub fn stored(&self) -> String {
        let (y, mo, d, h, mi, s, us) = self.parts();
        format!("{y:04}-{mo:02}-{d:02} {h:02}:{mi:02}:{s:02}.{us:06}")
    }

    /// Python's `datetime.isoformat()`: microseconds only when there are any.
    pub fn isoformat(&self) -> String {
        let (y, mo, d, h, mi, s, us) = self.parts();
        if us == 0 {
            format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}")
        } else {
            format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{us:06}")
        }
    }

    /// Read either form back: a date, a `T` or a space, and optional
    /// fractional seconds of up to six digits.
    pub fn parse(text: &str) -> Option<Self> {
        let b = text.as_bytes();
        let num = |r: std::ops::Range<usize>| -> Option<u32> {
            let s = text.get(r)?;
            s.bytes()
                .all(|c| c.is_ascii_digit())
                .then(|| s.parse().ok())?
        };
        if b.len() < 10 || b[4] != b'-' || b[7] != b'-' {
            return None;
        }
        let (y, mo, d) = (i64::from(num(0..4)?), num(5..7)?, num(8..10)?);
        let (mut h, mut mi, mut s, mut us) = (0, 0, 0, 0);
        if b.len() > 10 {
            if !(b[10] == b' ' || b[10] == b'T') || b.len() < 19 || b[13] != b':' || b[16] != b':' {
                return None;
            }
            (h, mi, s) = (num(11..13)?, num(14..16)?, num(17..19)?);
            if b.len() > 19 {
                let frac = text.get(20..)?;
                if b[19] != b'.' || frac.is_empty() || frac.len() > 6 {
                    return None;
                }
                us = num(20..b.len())? * 10u32.pow(6 - frac.len() as u32);
            }
        }
        let valid = (1..=12).contains(&mo) && (1..=31).contains(&d) && h < 24 && mi < 60 && s < 60;
        let t = Self::from_parts(y, mo, d, h, mi, s, us);
        // A day past the month's end would roll into the next one.
        (valid && t.parts().2 == d).then_some(t)
    }
}

impl ToSql for Timestamp {
    fn to_sql(&self) -> rusqlite::Result<ToSqlOutput<'_>> {
        Ok(ToSqlOutput::from(self.stored()))
    }
}

impl FromSql for Timestamp {
    fn column_result(value: ValueRef<'_>) -> FromSqlResult<Self> {
        let text = value.as_str()?;
        Self::parse(text)
            .ok_or_else(|| FromSqlError::Other(format!("not a timestamp: {text:?}").into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps_read_and_write_as_the_backend_does() {
        let t = Timestamp::from_parts(2026, 10, 21, 9, 29, 15, 792_830);
        assert_eq!(t.stored(), "2026-10-21 09:29:15.792830");
        assert_eq!(t.isoformat(), "2026-10-21T09:29:15.792830");
        assert_eq!(Timestamp::parse("2026-10-21 09:29:15.792830"), Some(t));
        assert_eq!(Timestamp::parse("2026-10-21T09:29:15.79283"), Some(t));
        let whole = Timestamp::from_parts(2026, 1, 2, 3, 4, 5, 0);
        assert_eq!(whole.isoformat(), "2026-01-02T03:04:05");
        assert_eq!(whole.stored(), "2026-01-02 03:04:05.000000");
        assert_eq!(
            Timestamp::parse("2024-02-29"),
            Some(Timestamp::from_parts(2024, 2, 29, 0, 0, 0, 0))
        );
        for bad in [
            "2023-02-29",
            "2026-13-01",
            "2026-01-01 24:00:00",
            "2026/01/01",
            "2026-01-01 00:00:00.1234567",
        ] {
            assert_eq!(Timestamp::parse(bad), None, "{bad}");
        }
        assert_eq!(
            Timestamp::from_parts(1969, 12, 31, 23, 59, 59, 999_999).micros,
            -1
        );
    }

    #[test]
    fn a_new_database_is_migrated_and_a_newer_one_refused() {
        let dir = std::env::temp_dir().join(format!("radical-store-{}", new_id()));
        let path = dir.join("db/radical.db");
        let store = Store::open(&path).unwrap();
        let version: i64 = store
            .read(|c| Ok(c.pragma_query_value(None, "user_version", |r| r.get(0))?))
            .unwrap();
        assert_eq!(version as usize, SCHEMA_VERSION);
        let mode: String = store
            .read(|c| Ok(c.pragma_query_value(None, "journal_mode", |r| r.get(0))?))
            .unwrap();
        assert_eq!(mode, "wal");
        drop(store);
        // Opening again applies nothing twice.
        drop(Store::open(&path).unwrap());
        Connection::open(&path)
            .unwrap()
            .pragma_update(None, "user_version", 99)
            .unwrap();
        assert!(matches!(
            Store::open(&path),
            Err(Error::TooNew { found: 99, .. })
        ));
        std::fs::remove_dir_all(dir).unwrap();
    }
}
