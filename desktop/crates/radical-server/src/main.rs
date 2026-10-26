//! RadiCal's API:
//!
//! ```text
//! radical-server [--host ADDR] [--port N] [--db PATH] [--frontend DIR] [--accounts]
//! radical-server reset-password EMAIL [--db PATH]
//! radical-server import-postgres DATABASE_URL [--db PATH]
//! ```
//!
//! On its own, or inside RadiCal Desktop, it listens on 127.0.0.1 for one
//! person on their own computer. With `--accounts` it is RadiCal on a server:
//! everyone signs in, and each account sees only its own work
//! (docs/RUST_SERVER_PLAN.md); in a container it listens on `--host 0.0.0.0`.
//! With `--frontend` it also serves the built frontend. `reset-password` sets
//! an account's password from the server, for an installation that cannot
//! send email.

use std::io::IsTerminal;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;

fn default_db() -> PathBuf {
    radical_store::default_dir()
        .expect("no home folder to keep the database in")
        .join(radical_store::FILE_NAME)
}

fn reset_password(mut args: impl Iterator<Item = String>) -> i32 {
    let mut email = None;
    let mut db = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next().map(PathBuf::from),
            other if email.is_none() && !other.starts_with("--") => email = Some(other.to_owned()),
            other => {
                eprintln!("unknown argument {other}; use reset-password EMAIL [--db PATH]");
                return 2;
            }
        }
    }
    let Some(email) = email else {
        eprintln!("Which account? radical-server reset-password EMAIL");
        return 2;
    };
    if !std::io::stdin().is_terminal() {
        eprintln!("Run this in a terminal: it asks for the new password.");
        return 2;
    }
    let first = rpassword::prompt_password("New password: ").unwrap_or_default();
    if rpassword::prompt_password("Same again: ").unwrap_or_default() != first {
        eprintln!("The two did not match. Nothing changed.");
        return 1;
    }
    let store = match radical_store::Store::open(db.unwrap_or_else(default_db)) {
        Ok(store) => store,
        Err(e) => {
            eprintln!("The database did not open: {e}");
            return 1;
        }
    };
    match radical_server::accounts::reset_password(&store, &email, &first) {
        Ok(()) => {
            println!("Done. {email} can sign in with the new password.");
            0
        }
        Err(e) => {
            eprintln!("{e} Nothing changed.");
            1
        }
    }
}

/// `import-postgres DATABASE_URL [--db PATH]`: an installation's data, from
/// the backend's PostgreSQL into a new SQLite database.
fn import_postgres(mut args: impl Iterator<Item = String>) -> i32 {
    let mut url = None;
    let mut db = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--db" => db = args.next().map(PathBuf::from),
            other if url.is_none() && !other.starts_with("--") => url = Some(other.to_owned()),
            other => {
                eprintln!("unknown argument {other}; use import-postgres DATABASE_URL [--db PATH]");
                return 2;
            }
        }
    }
    let Some(url) = url else {
        eprintln!(
            "From where? radical-server import-postgres postgresql://USER:PASSWORD@HOST/DATABASE"
        );
        return 2;
    };
    let db = db.unwrap_or_else(default_db);
    match radical_server::import_postgres::import(&url, &db) {
        Ok(report) => {
            for (table, n) in &report {
                println!("{n:>8}  {table}");
            }
            println!("Imported into {}.", db.display());
            0
        }
        Err(e) => {
            eprintln!("{e}");
            1
        }
    }
}

fn main() {
    let mut args = std::env::args().skip(1).peekable();
    match args.peek().map(String::as_str) {
        Some("reset-password") => {
            args.next();
            std::process::exit(reset_password(args));
        }
        Some("import-postgres") => {
            args.next();
            std::process::exit(import_postgres(args));
        }
        _ => {}
    }
    let mut host: IpAddr = [127, 0, 0, 1].into();
    let mut port: u16 = 8000;
    let mut db = None;
    let mut frontend = None;
    let mut accounts = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--host" => {
                host = args
                    .next()
                    .and_then(|h| h.parse().ok())
                    .expect("--host takes an address")
            }
            "--port" => {
                port = args
                    .next()
                    .and_then(|p| p.parse().ok())
                    .expect("--port takes a number")
            }
            "--db" => db = args.next().map(PathBuf::from),
            "--frontend" => frontend = args.next().map(PathBuf::from),
            "--accounts" => accounts = true,
            other => panic!(
                "unknown argument {other}; use --host ADDR, --port N, --db PATH, --frontend DIR \
                 and --accounts"
            ),
        }
    }
    let db = db.unwrap_or_else(default_db);
    let mut state = radical_server::State::open(&db, radical_jobs::default_workers())
        .expect("the database opens");
    if accounts {
        match radical_server::accounts::Accounts::from_env() {
            Ok(a) => state = state.with_accounts(a),
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(1);
            }
        }
    }
    let runtime = tokio::runtime::Runtime::new().expect("a runtime starts");
    runtime.block_on(async {
        let address = SocketAddr::from((host, port));
        let listener = tokio::net::TcpListener::bind(address)
            .await
            .expect("the port is free");
        eprintln!(
            "RadiCal API on http://{address} (database {}{})",
            db.display(),
            if accounts { ", accounts" } else { "" }
        );
        let launch = radical_server::launch::Launch {
            token: None,
            frontend,
        };
        radical_server::launch::serve(listener, state, launch)
            .await
            .expect("the server runs");
    });
}
