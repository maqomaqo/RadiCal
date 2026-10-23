//! The one place the app talks to the internet (`reservoir_ingest.fetch_*`):
//! the marine reservoir database's query interface on calib.org, asked one
//! question when a person presses the button, never on a timer or at start.
//!
//! Plain http, deliberately, as the backend's fetch is: the host's TLS is not
//! reachable, and the request carries no credentials and no coordinates --
//! it pulls a published reference table down whole. The form's own session
//! GET comes first, and its cookie goes with the query.

use std::time::Duration;

use radical_io::reservoir_ingest::{
    Freshness, IngestError, Record, extract_freshness, extract_records,
};

const QUERY_URL: &str = "http://calib.org/marine/query/query.php";
const SESSION_URL: &str = "http://calib.org/marine/query/";

pub const SOURCE_QUERY: &str = "select mapno,lon,lat,deltar,deltarerr,reference,locality,subregion,notes,collectionyear,reservoirage,c14age,c14err,labid,last_changed,genus,species,feeding from details left join taxa using (taxano)";
pub const FRESHNESS_QUERY: &str = "select count(*), max(last_changed) from details";

/// Why a fetch did not come back with something to read.
#[derive(Debug)]
pub enum FetchError {
    /// The host could not be reached: a network to try again on.
    Unavailable(String),
    /// It answered, in a shape that no longer parses: a source that changed.
    Unreadable(String),
}

impl From<IngestError> for FetchError {
    fn from(e: IngestError) -> Self {
        Self::Unreadable(e.0)
    }
}

/// One POST to the query interface, after the session GET, as raw bytes
/// (`_query`).
fn query(query: &str, timeout: Duration, purpose: &str) -> Result<Vec<u8>, FetchError> {
    let agent = ureq::AgentBuilder::new()
        .timeout(timeout)
        .user_agent(&format!(
            "RadiCal/1.0 (radiocarbon analysis; marine reservoir mirror; {purpose})"
        ))
        .build();
    let unreachable = |e: &dyn std::fmt::Display| {
        FetchError::Unavailable(format!(
            "calib.org could not be reached: {e}. Whatever is already mirrored is unaffected -- this breaks refresh, never analysis."
        ))
    };
    let session = agent.get(SESSION_URL).call().map_err(|e| unreachable(&e))?;
    let cookies: Vec<String> = session
        .all("set-cookie")
        .iter()
        .filter_map(|c| c.split(';').next())
        .map(str::to_owned)
        .collect();
    let mut post = agent.post(QUERY_URL);
    if !cookies.is_empty() {
        post = post.set("Cookie", &cookies.join("; "));
    }
    let response = match post.send_form(&[("query", query)]) {
        Ok(r) => r,
        Err(ureq::Error::Status(code, _)) => {
            return Err(FetchError::Unavailable(format!(
                "calib.org answered {code} to the query."
            )));
        }
        Err(e) => return Err(unreachable(&e)),
    };
    let mut body = Vec::new();
    std::io::Read::read_to_end(&mut response.into_reader(), &mut body)
        .map_err(|e| unreachable(&e))?;
    Ok(body)
}

/// Every record of the database, fields still undecoded, and the row count
/// the page declared (`fetch_records`).
pub fn fetch_records() -> Result<(Vec<Record>, Option<u64>), FetchError> {
    let raw = query(
        SOURCE_QUERY,
        Duration::from_secs(180),
        "one query per ingest",
    )?;
    Ok(extract_records(&raw)?)
}

/// What the source holds now, without fetching any of it
/// (`fetch_freshness`).
pub fn fetch_freshness() -> Result<Freshness, FetchError> {
    let raw = query(FRESHNESS_QUERY, Duration::from_secs(60), "freshness probe")?;
    Ok(extract_freshness(&raw)?)
}
