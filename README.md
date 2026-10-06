# Smarter Playlists

[![Tests](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml/badge.svg)](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml)

Smarter Playlists lets you get the playlists you want by collecting your Apple Music library data over time, building a real play history, and writing playlists in SQL.

It imports your Music library into PostgreSQL, keeps a history of every play, and exports SQL views back out to playlists in the Music app, including a playlist of your most played tracks for every month and year.

## Setup

### Prerequisites
* macOS with the Music app
* Python 3.14+
* PostgreSQL, e.g. `brew install postgresql@18`. It only needs to be installed, not running: Smarter Playlists runs its own server (see [Database](#database))

Install into a virtual environment, then set up the database:

```bash
python3 -m venv venv
./venv/bin/pip install -e .
./venv/bin/smarter-playlists setup
```

`setup` creates a database of its own, with the tables and the built-in playlists, and only needs running once.

## Import

```bash
./venv/bin/smarter-playlists import
```

The library is read with Apple's [iTunesLibrary framework](https://developer.apple.com/documentation/ituneslibrary), so there's no need to export a library file and the Music app doesn't need to be running.

Each import brings the database up to date with the library (see [`schema.sql`](smarter_playlists/schema.sql)):

* `track`, `artist` and `album` - every song in the library. IDs are the persistent IDs Music itself uses.
* `play` - one row for every play of every track.

Tracks removed from the library are kept, with `removed_at` set, so their play history isn't lost.

The database is backed up before every import (see [Backups](#backups)), and the import logs each track played since the last one, with when. Add `--verbose` to list them on the first import too, when there are too many to list by default.

### Play history

Music only tells us how many times a track has been played and when it was last played. Each import compares that with the plays already recorded:

* The newest play is recorded at the track's last played time.
* Any other plays since the previously recorded one are estimated, spaced evenly between the two, and marked with `estimated = TRUE`.

The first import has nothing to go on, so it spreads each track's past plays evenly from the start of 2010 up to its last play. After that, the more often the import runs, the smaller the gaps and the more accurate the history.
Add `WHERE NOT estimated` to a query to count only the plays that were actually observed.

### History from the original version

The original iTunes version of Smarter Playlists kept its own play history from 2018 to 2021. To start from that instead of estimating everything since 2010, restore its database to another Postgres server (e.g. as `music_2021` in Postgres.app) and use `scripts/import_history.py` in place of the first import. `--history` is how to connect to it, as a [connection string](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-CONNSTRING):

```bash
./venv/bin/smarter-playlists setup
./venv/bin/python scripts/import_history.py --history "dbname=music_2021"
```

Its tracks are matched to your library by title, artist and album, and the plays it recorded twice when the clocks changed are dropped. See the script for the details.

## Playlists

Every view in the `playlist` schema is exported to Music. `setup` creates these to start with (see [`playlists.sql`](smarter_playlists/playlists.sql)), all in a "Smarter Playlists" folder:

| View                     | Playlists                                                                                                                                                                                 |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `monthly`                | The 50 most played tracks of each month since October 2026, named like "October 2026", at most 2 per album and 5 per artist. A new one appears each month, in a folder for its year.      |
| `yearly`                 | The 100 most played tracks of each year since 2026, named like "2026", at most 5 per album and 10 per artist. A new one appears each year, in the folder for that year.                   |
| `"Last Month"`           | The 50 most played tracks of the last 30 days, at most 2 per album and 5 per artist.                                                                                                      |
| `"Rising"`               | What you're getting into: up to 50 tracks played more in the last 30 days than in the 90 days before, and at least twice, ordered by how much more. At most 2 per album and 3 per artist. |
| `top_artists`            | A playlist for each of your 10 most played artists, named after them, with their 25 most played tracks, in a "Top Artists" folder.                                                        |
| `"All-Time Favourites"`  | The 100 most played tracks of all time, at most 5 per artist.                                                                                                                             |
| `"Forgotten Favourites"` | Up to 100 tracks played at least 10 times, but not for a year.                                                                                                                            |

```
Smarter Playlists
├── All-Time Favourites
├── Forgotten Favourites
├── Last Month
├── Rising
├── Top Artists
│   ├── Radiohead
│   └── ...
└── 2026
    ├── 2026
    └── October 2026
```

"Last Month" and "Rising" include plays estimated between imports. Rising stays empty until imports have built up a few weeks of real listening, as the first import spreads past plays evenly. Playlists are never deleted, so when an artist drops out of your top 10, their Top Artists playlist stays as it was.

A view needs a `track_id` column, and the playlist follows its `position` column if it has one. It's exported as a playlist named after the view or, if it has a `playlist` column, as one playlist for each value of that column. That's how `monthly` makes a playlist for every month.

A `folder` column puts playlists in a folder, with `/` between nested folders, e.g. `'Smarter Playlists/2026'`. Without one, playlists go at the top level.

For example, to add a playlist of tracks you keep skipping:

```sql
CREATE VIEW playlist."Skipped" AS
SELECT track_id,
       ROW_NUMBER() OVER (ORDER BY skip_count DESC) AS position
  FROM track
 WHERE skip_count >= 5
   AND removed_at IS NULL
 ORDER BY position
 LIMIT 50;
```

Edit or drop the built-in views to change them. Dropping a view doesn't delete its playlists from Music.

To reset the built-in views, for example after updating Smarter Playlists, re-run `playlists.sql`. This drops and recreates the whole `playlist` schema, so any views you've added to it are lost:

```bash
./venv/bin/smarter-playlists psql -f smarter_playlists/playlists.sql
```

### Export

```bash
./venv/bin/smarter-playlists export
```

This creates each playlist, and any folders it needs, if it doesn't exist, or replaces its tracks if it does. Playlists that are already up to date are left alone, and each playlist's description says which view it comes from.
Name playlists to export only those, e.g. `smarter-playlists export "October 2026"` or `"Smarter Playlists/2026/October 2026"`, and add `--dry-run` to see what would change without touching anything.

Playlists are matched by name within their folder, so your own playlists with the same name somewhere else are left alone. If a view's folder changes, the playlist it made is moved to the new folder.

Tracks are added by reference rather than by file, so Apple Music tracks that are only in the cloud work just as well as local files.
Tracks that have been removed from the library, or were only ever added to a playlist rather than the library (`playlist_only`), can't be added and are skipped with a warning.

Smart playlists and folders are never replaced. Any changes you make to an exported playlist in Music are replaced on the next export.

The first time it runs, macOS will ask for permission for your terminal to control the Music app.

## Stats

```bash
./venv/bin/smarter-playlists stats
./venv/bin/smarter-playlists stats 2025 --top 20
```

A year of listening, this year unless you name another: how many plays and hours, hours by month, and your top artists (with how much you played them each month), tracks, and albums, both of any year and released that year. Plays estimated between imports count too, so it says what share of plays are estimated. The more of them there are, the rougher the months are.

## Running on a schedule

To keep the play history accurate and playlists up to date, import and export regularly. `smarter-playlists run` backs up, imports and exports, and this sets it to run every 2 hours, and whenever you log in:

```bash
./venv/bin/smarter-playlists schedule install
```

It also runs straight away, so you can check it works:

```bash
./venv/bin/smarter-playlists schedule status
tail -f ~/Library/Logs/smarter-playlists.log
```

* Use `--every` to change how many hours apart runs are, and `--backup-dir` to keep backups somewhere else.
* It runs as a launchd agent (`~/Library/LaunchAgents/local.smarter-playlists.plist`), so only while you're logged in.
* The first scheduled run may ask again for permission to control Music, this time for Python. If the log shows "Not authorized to send Apple events to Music", allow it in System Settings > Privacy & Security > Automation.
* Your `PATH`, to find the same Postgres, and `SMARTER_PLAYLISTS_HOME`, if set, are saved with the schedule, so run `install` again if they change.

To stop running on a schedule:

```bash
./venv/bin/smarter-playlists schedule uninstall
```

### Run history

Every `run` is recorded in the `run` table: when it started and finished, whether it was scheduled, the plays it recorded, the playlists it changed, and why it failed if it did. `schedule status` says how the last run went, and when the last successful one was if it failed. If a scheduled run fails, you also get a notification.

## Database

Smarter Playlists keeps its database in `~/Library/Application Support/smarter-playlists`, on a Postgres server of its own. The server only runs while a command needs it, and only listens on a socket in that folder, so it doesn't get in the way of any other Postgres. Set `SMARTER_PLAYLISTS_HOME` to keep it somewhere else, though not too deep, as the socket's path has to fit in 103 characters.

To query it, or edit the playlist views, `psql` starts the server, opens psql on the database, and stops the server again when you quit. Any arguments are passed to psql:

```bash
./venv/bin/smarter-playlists psql
./venv/bin/smarter-playlists psql -c 'SELECT count(*) FROM play'
```

For other tools, e.g. Postico or DataGrip, keep it running on a port, and connect to `localhost` on that port as user `postgres`, with no password, to the `music` database:

```bash
./venv/bin/smarter-playlists db start --port 5499
./venv/bin/smarter-playlists db status
./venv/bin/smarter-playlists db stop
```

`db stop` waits for anything still using the database, e.g. a scheduled run, to finish.

To move a database you already have into it, dump it and restore that, which replaces anything already there:

```bash
pg_dump --format=custom --file music.dump music
./venv/bin/smarter-playlists restore music.dump
```

### Backups

The database is backed up before every import, whether by `import`, `run` or the schedule, to `~/Library/Application Support/smarter-playlists/backups`. It keeps the newest 12, the newest of each day for 30 days, and the newest of each month forever. Each is a `pg_dump` in its custom format, a few MB.

Use `--backup-dir` to keep them somewhere else, e.g. in iCloud Drive so they're not only on this Mac:

```bash
./venv/bin/smarter-playlists schedule install --backup-dir ~/Library/Mobile\ Documents/com~apple~CloudDocs/smarter-playlists
```

To back up now, or go back to a backup (backing up the database it replaces first):

```bash
./venv/bin/smarter-playlists backup
./venv/bin/smarter-playlists restore ~/Library/Application\ Support/smarter-playlists/backups/music-2026-10-06T140200.dump
```

### Upgrading Postgres

The database only works with the major version of Postgres that made it, e.g. 18, so it keeps using that version, as long as it's installed (Homebrew's `postgresql@18`, or in Postgres.app), even once a newer one is. Each run warns when a newer one is installed, and this moves the database to it, keeping the old one in `postgres-18.old` to delete once you're happy:

```bash
./venv/bin/smarter-playlists upgrade
```

## Tests

The tests need Postgres installed (`initdb` or `pg_config` on the `PATH`) but not running: they start their own temporary server in `/tmp`, so they never touch your database or Music library. They fake the Music library and app, so they run on Linux too.

```bash
./venv/bin/pip install -e '.[test]'
./venv/bin/pytest
```

`pytest --integration` also runs a few read-only checks against your real Music library and the Music app. `pytest --write-music` checks exporting to the Music app for real: it makes playlists and folders named `SPIT …` in a "Smarter Playlists Integration Test" folder, and deletes them again. It takes a couple of minutes, as it waits to see that iCloud Music Library leaves them where they were put.
