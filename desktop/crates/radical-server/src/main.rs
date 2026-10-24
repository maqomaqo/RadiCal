//! RadiCal's API on this machine: `radical-server [--port N] [--db PATH]`.
//! Listens on 127.0.0.1 only -- the data is one person's, on their own
//! computer.

use std::net::SocketAddr;

fn main() {
    let mut args = std::env::args().skip(1);
    let mut port: u16 = 8000;
    let mut db = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--port" => {
                port = args
                    .next()
                    .and_then(|p| p.parse().ok())
                    .expect("--port takes a number")
            }
            "--db" => db = args.next(),
            other => panic!("unknown argument {other}; use --port N and --db PATH"),
        }
    }
    let db = db.map(std::path::PathBuf::from).unwrap_or_else(|| {
        radical_store::default_dir()
            .expect("no home folder to keep the database in")
            .join(radical_store::FILE_NAME)
    });
    let state = radical_server::State::open(&db, radical_jobs::default_workers())
        .expect("the database opens");
    let runtime = tokio::runtime::Runtime::new().expect("a runtime starts");
    runtime.block_on(async {
        let address = SocketAddr::from(([127, 0, 0, 1], port));
        let listener = tokio::net::TcpListener::bind(address)
            .await
            .expect("the port is free");
        eprintln!(
            "RadiCal API on http://{address} (database {})",
            db.display()
        );
        axum::serve(listener, radical_server::app(state))
            .await
            .expect("the server runs");
    });
}
