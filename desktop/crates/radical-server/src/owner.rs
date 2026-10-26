//! Whose rows a request may reach (`api/auth.owned_or_404`, and the
//! `owner_id == user.id` filters throughout the backend).
//!
//! The backend's two rules, which every route follows through here:
//!
//! - **Someone else's row is a 404, not a 403**: [`check`] answers the same
//!   for a row that is not there and one that is someone else's, so an id
//!   cannot be used to find out whether something exists.
//! - **A list filters, it does not check**: [`Scope::sql`] goes in the query,
//!   so a new row type cannot leak through a loop that forgot to check.
//!
//! On RadiCal Desktop the caller is [`Caller::Local`], who owns everything:
//! nothing is filtered and nothing is a 404 for being someone else's.

use crate::accounts::Caller;
use crate::{ApiError, ApiResult};

/// Whether `caller` may reach a row owned by `owner`.
pub fn may(caller: &Caller, owner: Option<&str>) -> bool {
    match caller.owner() {
        None => true,
        Some(me) => owner == Some(me),
    }
}

/// `row`, when `caller` may reach it: otherwise the same 404 as for no row,
/// "{what} was not found."
pub fn check<T>(
    caller: &Caller,
    row: Option<T>,
    owner_of: impl FnOnce(&T) -> Option<&str>,
    what: &str,
) -> ApiResult<T> {
    match row {
        Some(row) if may(caller, owner_of(&row)) => Ok(row),
        _ => Err(ApiError::not_found(format!("{what} was not found."))),
    }
}

/// The rows a caller may list, as SQL.
pub struct Scope(Option<String>);

impl Scope {
    pub fn of(caller: &Caller) -> Self {
        Self(caller.owner().map(str::to_owned))
    }

    /// A condition true for the rows whose `column` says they are the
    /// caller's: everything on the desktop. The account id is written into
    /// the SQL rather than bound, so it can join any query's own parameters;
    /// it is the id of the caller's own row in `users`, found from a verified
    /// token, never anything a request sent.
    pub fn sql(&self, column: &str) -> String {
        match &self.0 {
            None => "1 = 1".to_owned(),
            Some(id) => {
                debug_assert!(id.chars().all(|c| c.is_ascii_hexdigit() || c == '-'));
                format!("{column} = '{}'", id.replace('\'', "''"))
            }
        }
    }

    /// The owner new rows are given: the caller's account, or none on the
    /// desktop.
    pub fn owner(&self) -> Option<String> {
        self.0.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn user(id: &str) -> Caller {
        Caller::User {
            id: id.to_owned(),
            access_token: None,
        }
    }

    #[test]
    fn the_desktop_reaches_everything_and_an_account_only_its_own() {
        assert!(may(&Caller::Local, None));
        assert!(may(&Caller::Local, Some("a")));
        assert!(may(&user("a"), Some("a")));
        assert!(!may(&user("a"), Some("b")));
        assert!(!may(&user("a"), None));
        assert_eq!(Scope::of(&Caller::Local).sql("owner_id"), "1 = 1");
        assert_eq!(
            Scope::of(&user("4fd7e410-fe6a")).sql("p.owner_id"),
            "p.owner_id = '4fd7e410-fe6a'"
        );
        let missing = check(&user("a"), Some(("x", Some("b"))), |r| r.1, "That project");
        assert_eq!(
            missing.unwrap_err().detail,
            serde_json::json!("That project was not found.")
        );
    }
}
