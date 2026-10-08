# Release notes

Newest first. A release's description on GitHub is its section here.

## 1.1.0

macOS, Apple Silicon (arm64) only. Intel Macs can install from source, see the README.

### Added

* **A history start date.** Plays from before you started importing are only estimates, so the playlist views and `stats` now ignore any before the `history_start` date in the new `setting` table. It's the day `setup` ran, and you can change it, e.g. `smarter-playlists psql -c "UPDATE setting SET history_start = DATE '2026-10-06'"`. It applies to `monthly`, `yearly`, `"Last Month"`, `"Rising"`, `time_of_day` and `seasons`. `monthly` and `yearly` start from it, instead of the October 2026 and 2026 they were fixed to.
* **Repeatable migrations.** A file named like `R__Something.sql` in the migrations folder is applied after the numbered ones, and again whenever it changes. The built-in playlist views are now one, `R__Playlists.sql`, replacing `playlists.sql`, so they're brought up to date by `migrate` (and `import` and `run`) after an update, rather than by re-running a file.

### Changed

* Updating no longer needs `playlists.sql` to be re-run, and the executable no longer needs a clone to reset the built-in views. Only the built-in views are recreated, so views you've added to the `playlist` schema are kept. Changes you've made to the built-in ones are lost whenever the file changes. A view of yours that selects from a built-in view stops them being recreated, so build yours from the tables.
* Migrations are recorded with a checksum, in the one `schema_migration` table. If a numbered migration has been edited since it was applied, commands refuse to run.

### Upgrading from 1.0.x

The `schema_migration` table has a new shape, which `migrate` can't change for you. Back up first, then run this once, then migrate and set your history start:

```bash
smarter-playlists backup
smarter-playlists psql <<'SQL'
BEGIN;
ALTER TABLE schema_migration DROP CONSTRAINT schema_migration_pkey;
ALTER TABLE schema_migration ADD PRIMARY KEY (name);
ALTER TABLE schema_migration ALTER COLUMN version DROP NOT NULL;
ALTER TABLE schema_migration ADD UNIQUE (version);
ALTER TABLE schema_migration ADD COLUMN checksum TEXT;
UPDATE schema_migration SET checksum = '20a75e7f' WHERE version = 1;
ALTER TABLE schema_migration ALTER COLUMN checksum SET NOT NULL;
COMMIT;
SQL
smarter-playlists migrate
smarter-playlists psql -c "UPDATE setting SET history_start = DATE '2026-10-06'"
```

Use your own date for the last step. A new database needs none of this.

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
