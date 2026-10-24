# Installing RadiCal

RadiCal comes two ways:

- **RadiCal Desktop**, an app for your own computer. Nothing else to install, no account to make,
  and your work stays in one file on your computer. Start here unless you need the server.
- **RadiCal on a server**, in [Docker](https://docs.docker.com/get-started/), for a group who share
  one installation and sign in to it. See [RadiCal on a server](#radical-on-a-server) below.

# RadiCal Desktop

Download the installer for your computer from the
[latest release](https://github.com/maqomaqo/RadiCal/releases/latest), and open it.

| Computer | Download | Then |
|---|---|---|
| Mac (macOS 11 or later, Apple silicon or Intel) | `RadiCal_…_universal.dmg` | Drag RadiCal into Applications. |
| Windows 10 or 11 | `RadiCal_…_x64-setup.exe` | Run it. (The `.msi` is the same app, for installing across an organisation.) |
| Linux | `RadiCal_…_amd64.AppImage`, or the `.deb` for Debian and Ubuntu | Make the AppImage executable and run it, or install the `.deb`. |

Open RadiCal and it starts on the Dashboard: there is no account to create.

**If your computer warns about the app.** Until the installers are signed, your computer cannot
check who made them, and asks before opening them the first time:

- **macOS** says RadiCal "cannot be opened" or "is not from an identified developer". Open
  **System Settings → Privacy & Security**, scroll down to the message about RadiCal, and choose
  **Open Anyway**.
- **Windows** says "Windows protected your PC". Choose **More info**, then **Run anyway**.

You are asked once; after that RadiCal opens like any other app.

### Using RadiCal Desktop

- **Your work** is kept in one file, `radical.db`, in RadiCal's folder:
  `~/Library/Application Support/RadiCal` on a Mac, `%APPDATA%\RadiCal` on Windows, and
  `~/.local/share/radical` on Linux. To back it up, quit RadiCal and copy that file.
- **Opening files.** Double-click an OxCal model (`.oxcal`) to open it in RadiCal's import, or use
  **File → Import Dates…**. **File → Import Project…** opens a project exported from either kind of
  RadiCal.
- **Moving from RadiCal on a server.** On the server, open each project and choose **Export**; then
  in RadiCal Desktop choose **File → Import Project…** and pick the file.
- **Updating.** RadiCal looks for a new version when it opens, and asks before installing one;
  **Help → Check for Updates…** looks straight away. Your work is kept: the file is brought up to
  date when the new version first opens. If you installed the `.deb`, update by installing the new
  version's `.deb` instead.
- **Removing it.** Delete the app as you would any other, and delete RadiCal's folder (above) to
  remove your work as well.

# RadiCal on a server

RadiCal runs in [Docker](https://docs.docker.com/get-started/): one container for the app and one for its
database. Docker is the only thing to install — no Python, Node.js or PostgreSQL.

The steps are the same on macOS and Windows. Type the commands into **Terminal** on macOS, or
**PowerShell** on Windows; where the two differ, both are given.

## 1. Install Docker Desktop

Download [Docker Desktop](https://www.docker.com/products/docker-desktop/) and install it.

- **macOS:** choose the download for your Mac's chip — *Apple silicon* or *Intel* (Apple menu →
  About This Mac shows which).
- **Windows:** Windows 10 or 11, 64-bit. Accept the installer's offer to use **WSL 2**, and restart
  if it asks you to.

Open Docker Desktop and wait until it reports that Docker is running. It needs to be running
whenever you use RadiCal.

## 2. Download RadiCal's setup file

Make a folder for RadiCal and download its setup file into it. The folder is where RadiCal's
settings live, and every command below is run from inside it.

```bash
mkdir RadiCal
cd RadiCal
curl -fLo docker-compose.yml https://raw.githubusercontent.com/maqomaqo/RadiCal/main/docker-compose.prod.yml
```

On Windows, type `curl.exe` rather than `curl` in the last line — in PowerShell, plain `curl` is a
different command.

## 3. Create a signing key

RadiCal signs everyone's sign-in with a secret key of your own, kept in a file called `.env` in the
same folder. This command makes one; it also downloads RadiCal itself, a few hundred megabytes, so
the first run takes a while.

macOS:

```bash
docker run --rm ghcr.io/maqomaqo/radical python -c "import secrets; print('RADICAL_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
```

Windows (PowerShell):

```powershell
docker run --rm ghcr.io/maqomaqo/radical python -c "import secrets; print('RADICAL_SECRET_KEY=' + secrets.token_urlsafe(48))" | Add-Content .env
```

Do this once. Keep `.env` and don't share it: anyone with the key could sign in as any account, and
replacing it signs everyone out.

## 4. Start RadiCal

```bash
docker compose up -d
```

After a minute, open **http://localhost:8000** in your browser and create an account.

## Everyday use

Run these from the RadiCal folder.

| To... | Run |
|---|---|
| Stop RadiCal | `docker compose stop` |
| Start it again | `docker compose up -d` |
| Update to the latest version | `docker compose pull` then `docker compose up -d` |

Your analyses are kept through all of these, and through updates: RadiCal brings its database up to
date by itself when a new version starts. If Docker Desktop is set to start when you sign in
(Docker Desktop → Settings → General), RadiCal comes back by itself after the computer restarts.

To **remove RadiCal and everything in it**, run `docker compose down -v` and delete the folder. This
deletes every analysis and project permanently.

## Who can reach it

Anyone who can reach port 8000 on this computer can open RadiCal and create an account; each
account sees only its own projects and analyses. To make it reachable from this computer only,
change the `ports:` line in `docker-compose.yml` from `"8000:8000"` to `"127.0.0.1:8000:8000"`
and run `docker compose up -d`.

## Forgotten passwords

To set a new password for an account, run this in the RadiCal folder with its email address, and
type the new password twice when asked:

```bash
docker compose exec radical python -m app.reset_password someone@example.org
```

### Password-reset emails

If RadiCal can send email, **Forgot your password?** on the sign-in page emails people a link to
choose a new password themselves. The link works once, for an hour. To turn this on, add your mail
server's details to `.env` and run `docker compose up -d`:

```
RADICAL_PUBLIC_URL=https://radical.example.org
RADICAL_SMTP_HOST=smtp.example.org
RADICAL_SMTP_PORT=587
RADICAL_SMTP_USER=radical@example.org
RADICAL_SMTP_PASSWORD=...
RADICAL_SMTP_FROM=radical@example.org
```

`RADICAL_PUBLIC_URL` is the address people open RadiCal at; the link in the email points there.
For a server on port 465, add `RADICAL_SMTP_SECURITY=ssl`. If an email does not arrive,
`docker compose logs radical` shows why.

## Using a different port

If something else already uses port 8000, change the *first* number in that same `ports:` line —
for example `"8080:8000"` — run `docker compose up -d`, and open http://localhost:8080 instead.

## Using your own PostgreSQL server

To keep RadiCal's data in a PostgreSQL server you already run (version 15 or later), run the app
container on its own and point it there:

```bash
docker run -d --name radical -p 8000:8000 --restart unless-stopped \
  -e DATABASE_URL=postgresql://USER:PASSWORD@DB-HOST:5432/DATABASE \
  -e RADICAL_SECRET_KEY=YOUR-KEY \
  -v radical_uploads:/app/uploads \
  ghcr.io/maqomaqo/radical
```

(In PowerShell, end each continued line with a backtick `` ` `` instead of `\`.) The database must
be empty and **owned** by the user in `DATABASE_URL` — `createdb -O USER DATABASE` — because
RadiCal creates its own tables on first start.

## Troubleshooting

**`no configuration file provided`** — the command was run outside the RadiCal folder. `cd` into
it first.

**`required variable RADICAL_SECRET_KEY is missing a value`** — there is no `.env` with a key in the
folder. Do step 3.

**`Cannot connect to the Docker daemon`**, or `docker` is not recognised — Docker Desktop is not
running. Start it and wait until it says so.

**`port is already allocated`** — another program uses port 8000. See
[Using a different port](#using-a-different-port).

**The page does not load** — give it a minute after starting. `docker compose ps` should show both
containers, the app as `healthy`; if not, `docker compose logs radical` shows why.

**Windows: Docker Desktop reports a WSL problem** — run `wsl --update` in PowerShell, restart the
computer, and start Docker Desktop again.
