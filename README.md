# Smarter Playlists

[![Tests](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml/badge.svg)](https://github.com/Stephan5/smarter-playlists/actions/workflows/tests.yml)

Smarter Playlists lets you get the playlists you want by collecting your Apple Music library data over time, building a real play history, and writing playlists in SQL.
It imports your Music library into PostgreSQL, keeps a history of every play, and exports SQL views back out to playlists in the Music app, including a playlist of your most played tracks for every month and year.

## Why
Apple Music only stores two pieces of play information about tracks in your library: the total play count and the last played time. 
This isn't enough to create time-based playlists, and so creating a playlist like 'Most Played October 2026' is impossible.

Smarter Playlists fixes this by snapshotting this limited play data for every track in your library on a schedule, and builds it's own history of which tracks where played when.
This enables it to create temporally concerned playlists like those for months, years, seasons, days of the week or even times of day.

## Setup

There are two ways to install it. Either way you need macOS with the Music app, and PostgreSQL, e.g. `brew install postgresql@18`. Postgres only needs to be installed, not running: Smarter Playlists runs its own server (see [Database](#database)).

|                                                       | Binary                                           | Clone                                     |
|-------------------------------------------------------|--------------------------------------------------|-------------------------------------------|
| Needs                                                 | Apple Silicon Mac. No Python                     | Python 3.14+. Any Mac                     |
| Start up                                              | About 6 seconds for every command                | About 0.3 seconds                         |
| Scheduling                                            | Yes                                              | Yes                                       |
| Updating                                              | Download the new release                         | `git pull`, then `pip install -e .` again |

Both can [run on a schedule](#running-on-a-schedule). The clone is the better choice if you can use it, as it starts much faster. The binary is for if you'd rather not install Python.

### Clone

Install into a virtual environment, then set up the database:

```bash
git clone https://github.com/Stephan5/smarter-playlists.git
cd smarter-playlists
python3 -m venv venv
./venv/bin/pip install -e .
./venv/bin/smarter-playlists setup
```

### Binary

Download `smarter-playlists-<version>-macos-arm64.zip` from the [latest release](https://github.com/Stephan5/smarter-playlists/releases/latest), unzip it, and set up the database:

```bash
unzip smarter-playlists-*-macos-arm64.zip
cd smarter-playlists-*-macos-arm64
./smarter-playlists setup
```

It's a single file, so move it anywhere on your `PATH` to run it as `smarter-playlists`. If macOS won't open it because it isn't notarized, clear the quarantine mark it got when downloaded: `xattr -d com.apple.quarantine smarter-playlists`. It's for Apple silicon only, and every command takes about 6 seconds to start, as it unpacks itself into a temporary folder each time and macOS scans everything it unpacks.

`setup` creates a database of its own, with the tables and the built-in playlists, and only needs running once.

## Import

```bash
smarter-playlists import
```

The library is read with Apple's [iTunesLibrary framework](https://developer.apple.com/documentation/ituneslibrary), so there's no need to export a library file and the Music app doesn't need to be running.

Each import brings the database up to date with the library (see [`01__InitialSchema.sql`](smarter_playlists/migrations/01__InitialSchema.sql)):

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

### History start

Plays from before you started importing are only guesses, so the playlist views and `stats` ignore any before the `history_start` date in the `setting` table, which `setup` sets to the day it ran. That covers `monthly`, `yearly`, `"Last Month"`, `"Rising"`, `time_of_day` and `seasons`. Views built from each track's play count, like `"All-Time Favourites"`, aren't affected, as Music keeps no dates for those plays.

To use a different date, e.g. the day your install started recording real plays, change it and export again. The date is compared with the date of each play in the database's time zone. `'-infinity'` ignores nothing:

```bash
smarter-playlists psql -c "UPDATE setting SET history_start = DATE '2026-10-06'"
smarter-playlists export
```

Playlists from months before the new date stay in Music, as playlists are never deleted.

## Playlists

Every view in the `playlist` schema is exported to Music. `setup` creates these to start with (see [`R__Playlists.sql`](smarter_playlists/migrations/R__Playlists.sql)), all in a "Smarter Playlists" folder:

| View                     | Playlists                                                                                                                                                                                                                    |
|--------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `monthly`                | The 50 most played tracks of each month since the [history start](#history-start), named like "October 2026", at most 2 per album and 5 per artist. A new one appears each month, in a folder for its year.                                         |
| `yearly`                 | The 100 most played tracks of each year since the history start, named like "2026", at most 5 per album and 10 per artist. A new one appears each year, in the folder for that year.                                                      |
| `"Last Month"`           | The 50 most played tracks of the last 30 days, at most 2 per album and 5 per artist.                                                                                                                                         |
| `"Rising"`               | What you're getting into: up to 50 tracks played more in the last 30 days than in the 90 days before, and at least twice, ordered by how much more. At most 2 per album and 3 per artist.                                    |
| `top_artists`            | A playlist for each of your 10 most played artists, named after them, with their 25 most played tracks, in a "Top Artists" folder.                                                                                           |
| `"All-Time Favourites"`  | The 100 most played tracks of all time, at most 5 per artist.                                                                                                                                                                |
| `"Forgotten Favourites"` | Up to 100 tracks played at least 10 times, but not for a year.                                                                                                                                                               |
| `"New and Unplayed"`     | Up to 100 tracks added to the library in the last 60 days that you haven't played yet, newest first.                                                                                                                         |
| `time_of_day`            | What you play in the Morning (5am to noon), Afternoon (noon to 5pm), Evening (5pm to 10pm) and Late Night (10pm to 5am) over the last year, in a "Time of Day" folder. 50 tracks each, at most 2 per album and 5 per artist. |
| `seasons`                | What you play in Winter, Spring, Summer and Autumn across every year (northern hemisphere, by month), in a "Seasons" folder. 50 tracks each, at most 2 per album and 5 per artist.                                           |

```
Smarter Playlists
├── All-Time Favourites
├── Forgotten Favourites
├── Last Month
├── New and Unplayed
├── Rising
├── Seasons
│   ├── Autumn
│   └── ...
├── Time of Day
│   ├── Morning
│   └── ...
├── Top Artists
│   ├── Radiohead
│   └── ...
└── 2026
    ├── 2026
    └── October 2026
```

"Last Month" and "Rising" include plays estimated between imports. "Time of Day" and "Seasons" leave them out, as an estimated play has no real time of day, and they fill up as real plays build up. Rising stays empty until imports have built up a few weeks of real listening, as the first import spreads past plays evenly. Playlists are never deleted, so when an artist drops out of your top 10, their Top Artists playlist stays as it was.

A view needs a `track_id` column, and the playlist follows its `position` column if it has one. It's exported as a playlist named after the view or, if it has a `playlist` column, as one playlist for each value of that column. That's how `monthly` makes a playlist for every month.

A `folder` column puts playlists in a folder, with `/` between nested folders, e.g. `'Smarter Playlists/2026'`. Without one, playlists go at the top level.

To add your own, create a view in the `playlist` schema with `smarter-playlists psql`, which works the same for the binary and a clone, or put the SQL in a file and run `smarter-playlists psql -f my_views.sql`. For example, a playlist of tracks you keep skipping:

```sql
CREATE VIEW playlist."Skipped" AS
SELECT track_id,
       ROW_NUMBER() OVER (ORDER BY skip_count DESC) AS position
  FROM track
 WHERE skip_count >= 5
   AND played_at::date >= (SELECT history_start FROM setting)
   AND removed_at IS NULL
 ORDER BY position
 LIMIT 50;
```

Build your views from the tables (`play`, `track`, `artist` and `album`) rather than from other views in the `playlist` schema. To ignore plays before the [history start](#history-start), add `AND played_at::date >= (SELECT history_start FROM setting)`.

You can edit or drop the built-in views, but not for long. They're recreated whenever [`R__Playlists.sql`](smarter_playlists/migrations/R__Playlists.sql) changes, e.g. after updating Smarter Playlists (see [Schema changes](#schema-changes)), so edits are lost and dropped ones come back. To change one for good, copy it under a new name. Views you've added are kept, but one that selects from a built-in view stops that recreating them, as Postgres won't drop a view something else uses, and then every command refuses to run until you drop it. Dropping a view doesn't delete its playlists from Music.

To reset the built-in views now, forget that the file was applied and migrate:

```bash
smarter-playlists psql -c "DELETE FROM schema_migration WHERE version IS NULL"
smarter-playlists migrate
```

### Export

```bash
smarter-playlists export
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
smarter-playlists stats
smarter-playlists stats 2025 --top 20
```

A year of listening from the [history start](#history-start), this year unless you name another: how many plays and hours, hours by month, and your top artists (with how much you played them each month), tracks, and albums, both of any year and released that year. Plays estimated between imports count too, so it says what share of plays are estimated. The more of them there are, the rougher the months are.

## Running on a schedule

To keep the play history accurate and playlists up to date, import and export regularly. `smarter-playlists run` backs up, imports and exports, and this sets it to run every 2 hours, and whenever you log in:

```bash
smarter-playlists schedule install
```

It also runs straight away, so you can check it works:

```bash
smarter-playlists schedule status
tail -f ~/Library/Logs/smarter-playlists.log
```

* Use `--every` to change how many hours apart runs are, and `--backup-dir` to keep backups somewhere else. `--dry-run` shows the launchd agent that would be installed, without installing it.
* It runs as a launchd agent (`~/Library/LaunchAgents/local.smarter-playlists.plist`), so only while you're logged in.
* The first scheduled run may ask again for permission to control Music, this time for Python. If the log shows "Not authorized to send Apple events to Music", allow it in System Settings > Privacy & Security > Automation.
* Your `PATH`, to find the same Postgres, and `SMARTER_PLAYLISTS_HOME`, if set, are saved with the schedule, so run `install` again if they change.

To stop running on a schedule:

```bash
smarter-playlists schedule uninstall
```

### Run history

Every `run` is recorded in the `run` table: when it started and finished, whether it was scheduled, the plays it recorded, the playlists it changed, and why it failed if it did. `schedule status` says how the last run went, and what share of all plays are estimated, and when the last successful one was if it failed. If a scheduled run fails, you also get a notification. You're also told, by a notification at the start of a scheduled run and by `schedule status`, if it's been over 24 hours since the last successful run, which is what happens when nothing has been running, e.g. after days logged out. A run also warns, and notifies if scheduled, if it raised the share of plays that are estimated, as it falls while real plays build up, so a rise means imports are too far apart to keep up with your listening.

## Database

Smarter Playlists keeps its database in `~/Library/Application Support/smarter-playlists`, on a Postgres server of its own. The server only runs while a command needs it, and only listens on a socket in that folder, so it doesn't get in the way of any other Postgres. Set `SMARTER_PLAYLISTS_HOME` to keep it somewhere else, though not too deep, as the socket's path has to fit in 103 characters.

To query it, or edit the playlist views, `psql` starts the server, opens psql on the database, and stops the server again when you quit. Any arguments are passed to psql:

```bash
smarter-playlists psql
smarter-playlists psql -c 'SELECT count(*) FROM play'
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

### Schema changes

The database schema is built up by the files in [`smarter_playlists/migrations`](smarter_playlists/migrations), named like `02__AddSomething.sql`. `setup` applies them all, and each one is recorded in the `schema_migration` table once applied, with a checksum of its SQL. A numbered migration that no longer matches its checksum has been edited since it was applied, and every command refuses to run until that's put right.

After updating Smarter Playlists, `import` and `run` apply any new ones, right after backing up the database. To do it without importing, or to see that it's up to date:

```bash
./venv/bin/smarter-playlists migrate
```

Files named like `R__Something.sql` are repeatable migrations. They're applied after the numbered ones, and again whenever their contents change, and recorded with a checksum in the same `schema_migration` table, with no version. The built-in playlist views are one: it drops and recreates only the views it makes.

Other commands refuse to run on an out of date database, and on one made by a newer version than the one installed. Each migration is applied in a transaction of its own, so one that fails changes nothing, and the ones before it are kept.

To change the schema, add a migration with the next number rather than editing one that's been released. It's plain SQL, and doesn't need to touch the `playlist` views, which are reset by editing `R__Playlists.sql`.

## Development

Common development tasks are available through a Makefile:

```bash
make test              # Run unit tests
make music-test        # Run read-only tests against your real Music library
make music-write-test  # Test exporting to the Music app for real, in playlists it then deletes
make binary-test       # Build the standalone executable, and test it by running it
make binary            # Build standalone executable (macOS only)
make clean             # Remove venv, build artifacts, and cache
```

### Releasing

To release, set `version` in `pyproject.toml` and add a section at the top of [`RELEASE_NOTES.md`](RELEASE_NOTES.md), headed `## <version>`, and commit them. Older versions' sections stay below as the history. Then push a tag that matches the version. That builds the executable on macOS, tests it, and attaches it to a GitHub release, with that version's section as its description (see [`release.yml`](.github/workflows/release.yml)). The workflow refuses to release if the tag doesn't match the version, or the notes have no section for it:

```bash
git tag v4.5.0
git push origin v4.5.0
```

The executable is for Apple Silicon (arm64) Macs only, and is slow to start. See [Setup](#setup) for how it compares with a clone.

## Tests

Postgres needs to be installed (`initdb` or `pg_config` on the `PATH`), but not running: every test starts its own temporary server in `/tmp`, so none of them touch your database.

* **`make test`** runs the tests. They fake the Music library and app, so they also run on Linux, and take about 20 seconds. GitHub runs them on every push and pull request. They touch nothing of yours.
* **`make music-test`** runs those, and a few more that read your real Music library and the Music app (`pytest --music`). It only reads.
* **`make music-write-test`** checks exporting to the Music app for real (`pytest --music-write`). It makes playlists and folders named `SPIT …` in a "Smarter Playlists Integration Test" folder, and deletes them again. It takes a couple of minutes, as it waits to see that iCloud Music Library leaves them where they were put.
* **`make binary-test`** builds the standalone executable (`make binary`), then runs it the way a user would (`pytest --binary dist/smarter-playlists`): setting up a database, backing up and restoring it, and checking that what a schedule runs is something it accepts. It uses a temporary database, only shows the schedule rather than installing it, and makes dry runs, so it doesn't change your database, launchd or playlists. Each command takes seconds to start, so it takes a couple of minutes. The release workflow runs it before publishing.
