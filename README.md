# Smarter Playlists

[![Tests](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml/badge.svg)](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml)

Smarter Playlists lets you get the playlists you want by collecting your Apple Music library data over time, building a real play history, and writing playlists in SQL.

It imports your Music library into PostgreSQL, keeps a history of every play, and exports SQL views back out to playlists in the Music app, including a playlist of your most played tracks for every month and year.

## Setup

### Prerequisites
* macOS with the Music app
* Python 3.14+
* Postgres
* Basic SQL knowledge to create a playlist of your liking.

Install into a virtual environment, then create and set up a database:

```bash
python3 -m venv venv
./venv/bin/pip install -e .
createdb music
./venv/bin/smarter-playlists setup
```

`setup` creates the tables and the built-in playlists, and only needs running once, on an empty database.

Every command uses the `music` database by default. Use `--db` to choose another, and the standard `PGHOST`, `PGPORT`, `PGUSER` and `PGPASSWORD` environment variables to connect to a database that isn't local.

## Import

```bash
./venv/bin/smarter-playlists import
```

The library is read with Apple's [iTunesLibrary framework](https://developer.apple.com/documentation/ituneslibrary), so there's no need to export a library file and the Music app doesn't need to be running.

Each import brings the database up to date with the library (see [`schema.sql`](smarter_playlists/schema.sql)):

* `track`, `artist` and `album` - every song in the library. IDs are the persistent IDs Music itself uses.
* `play` - one row for every play of every track.

Tracks removed from the library are kept, with `removed_at` set, so their play history isn't lost.

### Play history

Music only tells us how many times a track has been played and when it was last played. Each import compares that with the plays already recorded:

* The newest play is recorded at the track's last played time.
* Any other plays since the previously recorded one are estimated, spaced evenly between the two, and marked with `estimated = TRUE`.

The first import has nothing to go on, so it spreads each track's past plays evenly from the start of 2010 up to its last play. After that, the more often the import runs, the smaller the gaps and the more accurate the history.
Add `WHERE NOT estimated` to a query to count only the plays that were actually observed.

## Playlists

Every view in the `playlists` schema is exported to Music. `setup` creates these to start with (see [`playlists.sql`](smarter_playlists/playlists.sql)), all in a "Smarter Playlists" folder:

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
CREATE VIEW playlists."Skipped" AS
SELECT track_id,
       ROW_NUMBER() OVER (ORDER BY skip_count DESC) AS position
  FROM track
 WHERE skip_count >= 5
   AND removed_at IS NULL
 ORDER BY position
 LIMIT 50;
```

Edit or drop the built-in views to change them. Dropping a view doesn't delete its playlists from Music.

To reset the built-in views, for example after updating Smarter Playlists, re-run `playlists.sql`. This drops and recreates the whole `playlists` schema, so any views you've added to it are lost:

```bash
psql music -f smarter_playlists/playlists.sql
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

## Running on a schedule

To keep the play history accurate and playlists up to date, import and export regularly. `smarter-playlists run` does both, and this sets it to run every 2 hours, and whenever you log in:

```bash
./venv/bin/smarter-playlists schedule install
```

It also runs straight away, so you can check it works:

```bash
./venv/bin/smarter-playlists schedule status
tail -f ~/Library/Logs/smarter-playlists.log
```

* Use `--every` to change how many hours apart runs are, and `--db` to use another database.
* It runs as a launchd agent (`~/Library/LaunchAgents/local.smarter-playlists.plist`), so only while you're logged in.
* The first scheduled run may ask again for permission to control Music, this time for Python. If the log shows "Not authorized to send Apple events to Music", allow it in System Settings > Privacy & Security > Automation.
* Any `PG*` environment variables are saved with the schedule, so run `install` again if they change.

To stop running on a schedule:

```bash
./venv/bin/smarter-playlists schedule uninstall
```

## Tests

The tests need Postgres installed (`initdb` or `pg_config` on the `PATH`) but not running: they start their own temporary server, so they never touch your database or Music library. They fake the Music library and app, so they run on Linux too.

```bash
./venv/bin/pip install -e '.[test]'
./venv/bin/pytest
```

`pytest --integration` also runs a few read-only checks against your real Music library and the Music app.
