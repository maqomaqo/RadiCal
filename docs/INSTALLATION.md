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

RadiCal runs in [Docker](https://docs.docker.com/get-started/), as one container that keeps
everything in a single file. Docker is the only thing to install.

The steps are the same on macOS, Windows and Linux. Type the commands into **Terminal** on macOS,
or **PowerShell** on Windows; where the two differ, both are given. Already running an earlier
version? See [Upgrading from an earlier version](#upgrading-from-an-earlier-version).

## 1. Install Docker Desktop

Download [Docker Desktop](https://www.docker.com/products/docker-desktop/) and install it.

- **macOS:** choose the download for your Mac's chip — *Apple silicon* or *Intel* (Apple menu →
  About This Mac shows which).
- **Windows:** Windows 10 or 11, 64-bit. Accept the installer's offer to use **WSL 2**, and restart
  if it asks you to.

Open Docker Desktop and wait until it reports that Docker is running. It needs to be running
whenever you use RadiCal. (On a Linux server, [Docker Engine](https://docs.docker.com/engine/install/)
does the same job.)

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
same folder. This command makes one; it also downloads RadiCal itself, so the first run takes a
little while.

macOS and Linux:

```bash
docker run --rm ghcr.io/maqomaqo/radical new-secret >> .env
```

Windows (PowerShell):

```powershell
docker run --rm ghcr.io/maqomaqo/radical new-secret | Add-Content .env
```

Do this once. Keep `.env` and don't share it: anyone with the key could sign in as any account, and
replacing it signs everyone out.

## 4. Start RadiCal

```bash
docker compose up -d
```

Open **http://localhost:8000** in your browser and create an account.

## Everyday use

Run these from the RadiCal folder.

| To... | Run |
|---|---|
| Stop RadiCal | `docker compose stop` |
| Start it again | `docker compose up -d` |
| Update to the latest version | `docker compose pull` then `docker compose up -d` |

Your analyses are kept through all of these, and through updates: RadiCal brings its data up to
date by itself when a new version starts. If Docker Desktop is set to start when you sign in
(Docker Desktop → Settings → General), RadiCal comes back by itself after the computer restarts.

To **remove RadiCal and everything in it**, run `docker compose down -v` and delete the folder. This
deletes every analysis and project permanently.

## Backing up

Everything — accounts, projects and analyses — is in RadiCal's `data` folder inside Docker. To copy
it out, stop RadiCal for a moment:

```bash
docker compose stop
docker compose cp radical:/data ./backup
docker compose up -d
```

This leaves a `backup` folder beside `docker-compose.yml`; give each backup its own name, or move
it somewhere safe. To restore one, putting RadiCal back as it was when the backup was made:

```bash
docker compose stop
docker compose cp ./backup/. radical:/data
docker compose run --rm --user root --entrypoint chown radical -R radical: /data
docker compose up -d
```

The third line hands the copied files back to RadiCal, which cannot save anything without it.

## Who can reach it

Anyone who can reach port 8000 on this computer can open RadiCal and create an account; each
account sees only its own projects and analyses. To make it reachable from this computer only,
change the `ports:` line in `docker-compose.yml` from `"8000:8000"` to `"127.0.0.1:8000:8000"`
and run `docker compose up -d`.

## Forgotten passwords

To set a new password for an account, run this in the RadiCal folder with its email address, and
type the new password twice when asked:

```bash
docker compose exec radical radical-server reset-password someone@example.org
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

## Upgrading from an earlier version

Earlier versions of RadiCal on a server kept their data in a separate PostgreSQL database
container. This version keeps it in one file, so updating means bringing the data across once.
Everything comes with it: accounts and passwords, projects, analyses and results, and the links to
them. People stay signed in, and access tokens keep working, as long as `.env` keeps its key.

If you update without doing this, RadiCal refuses to start and says so; nothing is lost.

Run these from the RadiCal folder, with your existing `.env` left in place:

1. Stop the old version:

   ```bash
   docker compose down
   ```

2. Download the new setup file over the old one (on Windows, `curl.exe`):

   ```bash
   curl -fLo docker-compose.yml https://raw.githubusercontent.com/maqomaqo/RadiCal/main/docker-compose.prod.yml
   docker compose pull
   ```

3. Start the old database on its own, and bring its data across:

   ```bash
   docker compose --profile upgrade up -d --wait postgres
   docker compose run --rm radical import-postgres
   ```

   This lists how many rows of each kind came across, and ends with `Imported into
   /data/radical.db.` If it says anything else, nothing was imported: the old data is untouched,
   and you can run it again.

4. Stop the old database and start RadiCal:

   ```bash
   docker compose --profile upgrade stop postgres
   docker compose up -d
   ```

Sign in and check your projects are there. The old database stays where it was until you remove
it; once you are happy, `docker volume rm radical-app_postgres_data radical-app_uploads` frees its
space.

**If you ran RadiCal against your own PostgreSQL server**, start the new version once with an empty
data volume and pass that server's address to the import instead:

```bash
docker run --rm -v radical_data:/data ghcr.io/maqomaqo/radical import-postgres postgresql://USER:PASSWORD@DB-HOST:5432/DATABASE
```

then run the new version with `-v radical_data:/data` in place of `DATABASE_URL` and the uploads
volume.

### Going back

Bringing the data across copies it and leaves the old database as it was, so until you remove the
old database you can return to the earlier version. Anything done since the upgrade stays behind
in the new version.

```bash
docker compose down
curl -fLo docker-compose.yml https://raw.githubusercontent.com/maqomaqo/RadiCal/v1.0.0/docker-compose.prod.yml
```

In the downloaded `docker-compose.yml`, change `ghcr.io/maqomaqo/radical:latest` to
`ghcr.io/maqomaqo/radical:1`, then run `docker compose up -d`.

## Troubleshooting

**`no configuration file provided`** — the command was run outside the RadiCal folder. `cd` into
it first.

**`required variable RADICAL_SECRET_KEY is missing a value`** — there is no `.env` with a key in the
folder. Do step 3.

**`Cannot connect to the Docker daemon`**, or `docker` is not recognised — Docker Desktop is not
running. Start it and wait until it says so.

**`port is already allocated`** — another program uses port 8000. See
[Using a different port](#using-a-different-port).

**RadiCal stops at once, saying it still points at its old PostgreSQL database** — this is an
earlier installation. See [Upgrading from an earlier version](#upgrading-from-an-earlier-version).

**The page does not load** — `docker compose ps` should show the container as `healthy`; if not,
`docker compose logs radical` shows why.

**Windows: Docker Desktop reports a WSL problem** — run `wsl --update` in PowerShell, restart the
computer, and start Docker Desktop again.
