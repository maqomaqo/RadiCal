//! Sending email: only ever a password-reset link (`services/mail.py`).
//!
//! Optional, and configured from the environment, read on each call:
//! `RADICAL_PUBLIC_URL` (where the link points), `RADICAL_SMTP_HOST`,
//! `RADICAL_SMTP_PORT` (587, or 465 with `RADICAL_SMTP_SECURITY=ssl`),
//! `RADICAL_SMTP_SECURITY` (starttls, ssl or none), `RADICAL_SMTP_USER` and
//! `RADICAL_SMTP_PASSWORD`, and `RADICAL_SMTP_FROM` (the user when unset).
//!
//! The public address is required rather than taken from the request: a
//! request's Host header is whatever its sender says, and a link built from it
//! could send a real user's reset token to someone else's server.

use lettre::message::Mailbox;
use lettre::transport::smtp::authentication::Credentials;
use lettre::{Message, SmtpTransport, Transport};

#[derive(Debug, Clone)]
pub struct MailSettings {
    pub public_url: String,
    pub host: String,
    pub port: u16,
    pub security: String,
    pub user: Option<String>,
    pub password: Option<String>,
    pub sender: String,
}

/// The configuration, or None when email is not set up.
pub fn settings() -> Option<MailSettings> {
    settings_from(|name| std::env::var(name).ok())
}

/// The configuration from `var`, which reads one setting by name.
fn settings_from(var: impl Fn(&str) -> Option<String>) -> Option<MailSettings> {
    let get = |name: &str| var(name).unwrap_or_default().trim().to_owned();
    let public_url = get("RADICAL_PUBLIC_URL").trim_end_matches('/').to_owned();
    let host = get("RADICAL_SMTP_HOST");
    let user = Some(get("RADICAL_SMTP_USER")).filter(|u| !u.is_empty());
    let sender = Some(get("RADICAL_SMTP_FROM"))
        .filter(|s| !s.is_empty())
        .or_else(|| user.clone())?;
    if public_url.is_empty() || host.is_empty() {
        return None;
    }
    let mut security = get("RADICAL_SMTP_SECURITY").to_lowercase();
    if !["starttls", "ssl", "none"].contains(&security.as_str()) {
        security = "starttls".to_owned();
    }
    let default_port = if security == "ssl" { 465 } else { 587 };
    let port = get("RADICAL_SMTP_PORT").parse().unwrap_or(default_port);
    Some(MailSettings {
        public_url,
        host,
        port,
        security,
        user,
        password: var("RADICAL_SMTP_PASSWORD").filter(|p| !p.is_empty()),
        sender,
    })
}

pub fn is_configured() -> bool {
    settings().is_some()
}

/// The link, with the token in the fragment: a fragment never reaches a
/// server, so the token stays out of access logs and Referer headers.
pub fn reset_link(config: &MailSettings, token: &str) -> String {
    format!("{}/reset-password#token={token}", config.public_url)
}

/// Send one plain-text message. Blocking: run it off the request's thread.
pub fn send(config: &MailSettings, to: &str, subject: &str, body: String) -> Result<(), String> {
    let mailbox = |s: &str| s.parse::<Mailbox>().map_err(|e| e.to_string());
    let message = Message::builder()
        .from(mailbox(&config.sender)?)
        .to(mailbox(to)?)
        .subject(subject)
        .body(body)
        .map_err(|e| e.to_string())?;
    let builder = match config.security.as_str() {
        "ssl" => SmtpTransport::relay(&config.host),
        "none" => Ok(SmtpTransport::builder_dangerous(&config.host)),
        _ => SmtpTransport::starttls_relay(&config.host),
    }
    .map_err(|e| e.to_string())?
    .port(config.port);
    let builder = match (&config.user, &config.password) {
        (Some(user), Some(password)) => {
            builder.credentials(Credentials::new(user.clone(), password.clone()))
        }
        _ => builder,
    };
    builder
        .build()
        .send(&message)
        .map(|_| ())
        .map_err(|e| e.to_string())
}

pub fn send_reset_link(config: &MailSettings, to: &str, token: &str) -> Result<(), String> {
    let link = reset_link(config, token);
    send(
        config,
        to,
        "Reset your RadiCal password",
        format!(
            "Someone, hopefully you, asked to reset the password for this RadiCal account.\n\n\
             To choose a new password, open this link within the next hour:\n\n{link}\n\n\
             The link works once. If you did not ask for this, ignore this email: \
             your password has not changed.\n"
        ),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn from(pairs: &[(&str, &str)]) -> Option<MailSettings> {
        settings_from(|name| {
            pairs
                .iter()
                .find(|(n, _)| *n == name)
                .map(|(_, v)| (*v).to_owned())
        })
    }

    #[test]
    fn email_is_set_up_only_with_an_address_a_server_and_a_sender() {
        assert!(from(&[]).is_none());
        assert!(
            from(&[
                ("RADICAL_SMTP_HOST", "smtp.x.org"),
                ("RADICAL_SMTP_FROM", "a@x.org")
            ])
            .is_none()
        );
        let c = from(&[
            ("RADICAL_PUBLIC_URL", "https://radical.example.com/"),
            ("RADICAL_SMTP_HOST", "smtp.radical.example.com"),
            ("RADICAL_SMTP_USER", "radical@radical.example.com"),
        ])
        .unwrap();
        // The sender falls back to the user; the port to STARTTLS's.
        assert_eq!(c.sender, "radical@radical.example.com");
        assert_eq!((c.security.as_str(), c.port), ("starttls", 587));
        assert_eq!(
            reset_link(&c, "abc"),
            "https://radical.example.com/reset-password#token=abc"
        );
        let ssl = from(&[
            ("RADICAL_PUBLIC_URL", "https://r.org"),
            ("RADICAL_SMTP_HOST", "smtp.r.org"),
            ("RADICAL_SMTP_FROM", "r@r.org"),
            ("RADICAL_SMTP_SECURITY", "SSL"),
        ])
        .unwrap();
        assert_eq!((ssl.security.as_str(), ssl.port), ("ssl", 465));
    }
}
