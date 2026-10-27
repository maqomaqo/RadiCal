# Releasing RadiCal Desktop

How to put a new version of RadiCal Desktop's installers on GitHub. The build itself is done by
[`.github/workflows/desktop-release.yml`](../.github/workflows/desktop-release.yml); a release is a
version bump, a tag, a check, and a click.

## Checklist

1. `main` is green on the **Desktop** and **Tests** workflows.
2. Bump the version in `tauri.conf.json` and `desktop/Cargo.toml`, by a PR, and merge it.
3. Tag the merged commit `desktop-vX.Y.Z` and push the tag.
4. Wait for **Desktop release** to finish (up to about an hour and a half).
5. Check the draft release: seven files, `latest.json` names the new version, the installers open.
6. Write the notes, tick **Set as latest release**, and publish.
7. Confirm `latest.json` is served and an installed older copy offers the update.

The details of each step follow.

## Before you start

- **The update-signing key.** The repository secret `TAURI_SIGNING_PRIVATE_KEY` must be set
  (`gh secret list` shows it). A tag is refused without it. Its public half is the `pubkey` in
  `desktop/crates/radical-desktop/tauri.conf.json`, and every installed copy trusts only updates
  signed by it. Keep a copy of the private key somewhere safe outside GitHub: if it is lost, the
  copies already installed can never update again, and their users have to reinstall by hand.
- **Code signing (optional).** With the `APPLE_*` secrets set the macOS build is signed and
  notarised; without them it is ad-hoc signed and macOS warns before the first open (as
  [INSTALLATION.md](INSTALLATION.md) describes). Windows is not signed yet.
- **Dry run (optional).** To build the installers without releasing anything, run the workflow by
  hand. The installers are kept as the run's artifacts, and no release is made.

  ```bash
  gh workflow run desktop-release.yml --ref main
  ```

## 1. Choose the version

Use `MAJOR.MINOR.PATCH`, and always higher than the last published release: installed copies
update only to a version greater than their own. Never reuse a version that has been published,
even if its release was later deleted.

- **PATCH** (0.1.0 → 0.1.1): fixes only.
- **MINOR** (0.1.0 → 0.2.0): new features; projects from the old version still open.
- **MAJOR**: something users must act on.

## 2. Bump the version

On a branch, change the version in two places:

- `desktop/crates/radical-desktop/tauri.conf.json` — `"version"`. This is the one that counts: it
  names the installers, is what the app reports, and the workflow refuses a tag that disagrees with it.
- `desktop/Cargo.toml` — `version` under `[workspace.package]`, kept the same so the crates agree.

Then refresh the lock file, and open the PR:

```bash
cargo check --manifest-path desktop/Cargo.toml
```

```bash
git switch -c release/desktop-0.2.0
```

```bash
git commit -am "Desktop 0.2.0"
```

```bash
git push -u origin release/desktop-0.2.0
```

```bash
gh pr create --fill
```

Merge it once CI passes.

## 3. Tag and push

Tag the merged commit on `main`, not your branch:

```bash
git switch main
```

```bash
git pull
```

```bash
git tag -a desktop-v0.2.0 -m "RadiCal Desktop 0.2.0"
```

```bash
git push origin desktop-v0.2.0
```

The `desktop-v` prefix matters. A plain `v2.1.0` tag is the server's: it publishes the container
image instead, and builds no installers.

## 4. Wait for the build

The push starts **Desktop release**, which builds macOS, Windows and Linux side by side, then
gathers them into a draft release. Follow it with:

```bash
gh run watch $(gh run list --workflow=desktop-release.yml --limit 1 --json databaseId -q '.[0].databaseId')
```

If it fails, see [If something goes wrong](#if-something-goes-wrong).

## 5. Check the draft

On the [releases page](https://github.com/maqomaqo/RadiCal/releases) the new release is marked
**Draft**: only people with write access can see it. It should hold seven files:

| File | For |
|---|---|
| `RadiCal_X.Y.Z_universal.dmg` | Mac installer |
| `RadiCal.app.tar.gz` | Mac update (read by installed copies, not downloaded by people) |
| `RadiCal_X.Y.Z_x64-setup.exe` | Windows installer, and Windows update |
| `RadiCal_X.Y.Z_x64_en-US.msi` | Windows installer for organisations |
| `RadiCal_X.Y.Z_amd64.AppImage` | Linux, and Linux update |
| `RadiCal_X.Y.Z_amd64.deb` | Debian and Ubuntu |
| `latest.json` | Tells installed copies this version exists |

Check:

- `latest.json` says `"version": "X.Y.Z"` and lists four platforms.
- The installers for the computers you have to hand install and open, and a project from the
  previous version opens in them.

```bash
gh release view desktop-v0.2.0
```

## 6. Publish

On the draft, choose **Edit**, then:

1. Write the notes: what changed, in plain words for users. The generated notes button lists the
   merged PRs, which is a starting point but not the notes.
2. Tick **Set as latest release**. This is required, not cosmetic: both the download link in
   [INSTALLATION.md](INSTALLATION.md) and the updater in installed copies read
   `releases/latest`, which is whichever release is marked latest.
3. Leave **Set as a pre-release** unticked (a pre-release is never "latest", so no one would get it).
4. **Publish release**.

## 7. Confirm

This should print the new version:

```bash
curl -sL https://github.com/maqomaqo/RadiCal/releases/latest/download/latest.json | grep version
```

Then open an installed copy of the previous version: it should offer the update and, once
accepted, restart as the new one.

## If something goes wrong

**The build fails before publishing.** Nothing has reached users. Fix the cause on `main`, then
remove the tag and the draft, and tag again (the same version is fine, since it was never published):

```bash
gh release delete desktop-v0.2.0 --yes
```

```bash
git push --delete origin desktop-v0.2.0
```

```bash
git tag -d desktop-v0.2.0
```

Common causes:

- *Tag says X but tauri.conf.json says Y* — the version bump is missing from the tagged commit, or
  the tag was put on the wrong commit.
- *TAURI_SIGNING_PRIVATE_KEY is not set* — the secret is missing or was renamed.
- *One platform times out* — re-run the failed job from the run's page; the others are kept.

**A published release is broken.** Do not delete it or swap its files: installed copies may
already be updating from it. Fix the problem and release the next PATCH version; until it is out,
you can tick **Set as latest release** on the previous good release so new downloads and updates
point back to it.

**Server releases.** If a server tag (`v2.1.0`) ever gets a GitHub release of its own, it must not
be marked latest, or it takes `releases/latest` away from the desktop app and updates stop.
