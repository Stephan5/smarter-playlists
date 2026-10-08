# Release notes

Newest first. A release's description on GitHub is its section here.

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
