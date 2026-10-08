# Release notes

Newest first. A release's description on GitHub is its section here.

## 1.0.1

macOS, Apple Silicon (arm64) only. Intel Macs can install from source, see the README.

### Fixed

* **Scheduling from the standalone executable.** In 1.0.0, `schedule install` wrote a launchd job that failed on every run, with `invalid choice: 'smarter_playlists'`, so nothing was ever imported or exported. If you scheduled it from the 1.0.0 executable, download this one and run `smarter-playlists schedule install` again to replace the job. Installing from a clone was never affected.

### Added

* `schedule install --dry-run` shows the launchd agent that would be installed, without installing it.

### Changed

* The executable is now tested by running it, before a release is published: setting up a database, migrating, backing up and restoring, keeping the database running for other tools, and checking that what a schedule runs is something it accepts. `make binary-test` does this locally.
* The test commands are `make test`, `make music-test` (reads your real Music library), `make music-write-test` (exports to Music for real) and `make binary-test`. The pytest flags are now `--music` and `--music-write`, not `--integration` and `--write-music`.
* The README compares installing from a clone with the executable.

### Download

The executable is a single file, so every command takes about 6 seconds to start while macOS scans what it unpacks. A clone, installed into a virtual environment, starts in about 0.3 seconds. Either way, PostgreSQL has to be installed.

## 1.0.0

A ground-up rewrite. It imports your Apple Music library into PostgreSQL, keeps a history of every play, and exports SQL views back to Music as playlists.

### Library and play history

* Reads the library through Apple's iTunesLibrary framework, so no export file is needed and Music doesn't have to be running.
* Records a row for every play. Plays that happen between imports are estimated and marked `estimated`, so you can filter to real plays only.
* Keeps tracks removed from your library, with `removed_at` set, so their history isn't lost.
* Can import history from the original 2018–2021 version (`scripts/import_history.py`).

### Playlists as SQL

* Every view in the `playlist` schema is exported to Music, with folders and one-view-many-playlists support.
* Built in: monthly and yearly, Last Month, Rising, Top Artists, All-Time Favourites, Forgotten Favourites, New and Unplayed, Time of Day, and Seasons.
* `stats` reports a year of listening.

### Running it

* It manages a PostgreSQL server of its own, so it doesn't interfere with any other Postgres.
* `schedule install` runs it every 2 hours, and at login, through launchd. Runs are recorded, with a notification on failure, and also a warning if nothing has succeeded for 24 hours or if the share of estimated plays rises.
* The database is backed up before every import, with pruning, and `restore` and `upgrade` (to a newer Postgres) are included.
* Schema changes are applied by numbered migrations (`migrate`, and automatically on `import` and `run`).
* Logging says what's slow and how long it took.

### Download

* Apple silicon (arm64) only. The single-file executable has about a 6 second lag on every command, because macOS scans what it unpacks. On an Intel Mac, or for faster commands, install into a virtual environment with Python 3.14. Postgres must be installed either way.
