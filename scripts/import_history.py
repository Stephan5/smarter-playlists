#!/usr/bin/env python3
"""Starts a new database from the play history of the original iTunes version of Smarter Playlists (2018-2021).

    ./venv/bin/python scripts/import_history.py --history "dbname=music_2021"

The old database is on another Postgres server, e.g. Postgres.app, and `--history` is how to connect to it, as a libpq
connection string. Run on a database that's set up but hasn't been imported into yet. It imports the Music library as
`smarter-playlists import` does, but puts the plays from the old database underneath:

* Real plays are kept. Plays recorded twice an hour apart when the clocks changed, and plays from 1904, are dropped.
* Estimated plays from the old monthly backfills, on the 1st of the month at 00:00 or 06:00, are kept as estimates.
* Plays from the old year-end backfill, stacked a second apart at 2018-12-31 23:59, are dropped. Instead, the plays
  each track's old play count says are missing are estimated, spaced evenly from 2010 up to its first remaining play.
* Plays since then are estimated by the import as usual, spaced evenly from the last old play up to the last play.

The old database's tracks have different persistent IDs, as the library was rebuilt in 2021, so they're matched to
the library by title, artist and album, or by title and artist where that's unambiguous.
"""

import argparse
import datetime
import logging

import psycopg

from smarter_playlists import database, library, server
from smarter_playlists.__main__ import LOG_DATE_FORMAT, LOG_FORMAT

UTC = datetime.timezone.utc

# Old plays, without the ones that weren't real, as (old track ID, played at, estimated)
HISTORY_PLAYS = """
    WITH ordered AS (
        SELECT track_id,
               played_at,
               played_at - LAG(played_at) OVER (PARTITION BY track_id ORDER BY played_at) AS gap_before,
               LEAD(played_at) OVER (PARTITION BY track_id ORDER BY played_at) - played_at AS gap_after
          FROM play
    ),
    classified AS (
        SELECT *,
               CASE WHEN played_at < '1990-01-01' THEN 'bogus'
                    WHEN played_at >= '2018-12-31 23:59' AND played_at < '2019-01-01' THEN 'year end backfill'
                    WHEN gap_before = INTERVAL '1 hour' THEN 'clock change duplicate'
                    WHEN gap_before <= INTERVAL '1 second'
                      OR gap_after <= INTERVAL '1 second'
                      OR (EXTRACT(DAY FROM played_at) = 1
                          AND (played_at::time < '00:01' OR played_at::time >= '06:00' AND played_at::time < '06:01'))
                    THEN 'month backfill'
                    ELSE 'real'
               END AS kind
          FROM ordered
    )
    SELECT track_id, played_at, kind = 'month backfill' AS estimated
      FROM classified
     WHERE kind IN ('real', 'month backfill')
"""

HISTORY_TRACKS = """
    SELECT t.track_id, t.track_name, ar.artist_name, al.album_name, t.play_count, t.last_played
      FROM track t
      JOIN artist ar USING (artist_id)
      JOIN album al USING (album_id)
     WHERE t.play_count > 0
        OR EXISTS (SELECT FROM play p WHERE p.track_id = t.track_id)
"""


def main(arg_list=None):
    args = parse_args(arg_list)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    with server.work(), server.running():
        import_with_history(database.DATABASE, args.history)


def parse_args(arg_list):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--history',
                        help='How to connect to the database made by the original iTunes version of Smarter '
                             'Playlists, e.g. "dbname=music_2021"',
                        required=True)
    return parser.parse_args(arg_list)


def import_with_history(database_name, history_conninfo):
    tracks, plays = read_history(history_conninfo)
    logging.info("Read %d tracks and %d plays from the old database", len(tracks), len(plays))

    logging.info("Reading the Music library...")
    library_tracks = library.read_library()
    if not library_tracks:
        raise SystemExit("No songs found in the Music library")
    logging.info("Found %d songs", len(library_tracks))

    with database.connect(database_name) as db:
        database.require_set_up(db)
        if db.execute("SELECT EXISTS (SELECT FROM play)").fetchone()[0]:
            raise SystemExit("The history has to go in before the first import, but {0} already has plays".format(
                database_name))

        library.load_library(db, library_tracks)
        library.update_tracks(db)
        load_history(db, tracks, plays)
        match_tracks(db)
        record_history(db)
        library.record_plays(db)


def read_history(history_conninfo):
    with psycopg.connect(history_conninfo) as history:
        tracks = history.execute(HISTORY_TRACKS).fetchall()
        plays = history.execute(HISTORY_PLAYS).fetchall()

    # The old database stored times in UTC, without a time zone
    tracks = [track[:5] + (utc(track[5]),) for track in tracks]
    plays = [(track_id, utc(played_at), estimated) for track_id, played_at, estimated in plays]
    return tracks, plays


def utc(timestamp):
    return timestamp.replace(tzinfo=UTC) if timestamp is not None else None


def load_history(db, tracks, plays):
    db.execute("""
        CREATE TEMPORARY TABLE history_track (
            history_id   BIGINT PRIMARY KEY,
            title        TEXT NOT NULL,
            artist       TEXT NOT NULL,
            album        TEXT NOT NULL,
            play_count   INT NOT NULL,
            last_played  TIMESTAMPTZ
        ) ON COMMIT DROP""")
    db.execute("""
        CREATE TEMPORARY TABLE history_play (
            history_id   BIGINT NOT NULL,
            played_at    TIMESTAMPTZ NOT NULL,
            estimated    BOOLEAN NOT NULL
        ) ON COMMIT DROP""")

    cur = db.cursor()
    with cur.copy("COPY history_track FROM STDIN") as copy:
        for track in tracks:
            copy.write_row(track)
    with cur.copy("COPY history_play FROM STDIN") as copy:
        for play in plays:
            copy.write_row(play)


def match_tracks(db):
    db.execute("""
        CREATE TEMPORARY TABLE library_track ON COMMIT DROP AS
        SELECT t.track_id, LOWER(t.title) AS title, LOWER(ar.name) AS artist, LOWER(al.title) AS album, t.play_count
          FROM track t
          JOIN artist ar USING (artist_id)
          JOIN album al USING (album_id)
        """)

    # By title, artist and album, choosing the most played copy if the library has the track more than once
    db.execute("""
        CREATE TEMPORARY TABLE history_match ON COMMIT DROP AS
        SELECT DISTINCT ON (h.history_id) h.history_id, l.track_id
          FROM history_track h
          JOIN library_track l ON ((l.title, l.artist, l.album) = (LOWER(h.title), LOWER(h.artist), LOWER(h.album)))
         ORDER BY h.history_id, l.play_count DESC, l.track_id
        """)

    # Then by title and artist, where only one track matches, e.g. when the album has been renamed
    db.execute("""
        INSERT INTO history_match (history_id, track_id)
        SELECT h.history_id, MIN(l.track_id)
          FROM history_track h
          JOIN library_track l ON ((l.title, l.artist) = (LOWER(h.title), LOWER(h.artist)))
         WHERE NOT EXISTS (SELECT FROM history_match m WHERE m.history_id = h.history_id)
         GROUP BY h.history_id
        HAVING COUNT(*) = 1
        """)

    matched, total, matched_plays, total_plays = db.execute("""
        SELECT COUNT(m.history_id), COUNT(*), COALESCE(SUM(h.play_count) FILTER (WHERE m.history_id IS NOT NULL), 0),
               COALESCE(SUM(h.play_count), 0)
          FROM history_track h
          LEFT JOIN history_match m USING (history_id)
        """).fetchone()
    logging.info("Matched %d of %d played tracks from the history to the library, with %d of their %d plays",
                 matched, total, matched_plays, total_plays)


def record_history(db):
    real, kept = db.execute("""
        WITH inserted AS (
            INSERT INTO play (track_id, played_at, estimated)
            SELECT m.track_id, p.played_at, p.estimated
              FROM history_play p
              JOIN history_match m USING (history_id)
                ON CONFLICT (track_id, played_at) DO NOTHING
            RETURNING estimated
        )
        SELECT COUNT(*) FILTER (WHERE NOT estimated), COUNT(*) FILTER (WHERE estimated) FROM inserted
        """).fetchone()

    # The plays each track's old play count says are missing, spread evenly from HISTORY_START to its first play.
    # Counted per library track, as the old library could have the same track more than once.
    estimated = db.execute("""
        WITH history AS (
            SELECT m.track_id, SUM(h.play_count) AS play_count, MAX(h.last_played) AS last_played
              FROM history_track h
              JOIN history_match m USING (history_id)
             GROUP BY m.track_id
        ),
        missing AS (
            SELECT h.track_id,
                   %(history_start)s::timestamptz AS window_start,
                   COALESCE(MIN(p.played_at), h.last_played, TIMESTAMPTZ '2019-01-01 00:00:00+00') AS window_end,
                   h.play_count - COUNT(p.play_id) AS estimates
              FROM history h
              LEFT JOIN play p USING (track_id)
             GROUP BY h.track_id, h.play_count, h.last_played
            HAVING h.play_count > COUNT(p.play_id)
        ),
        inserted AS (
            INSERT INTO play (track_id, played_at, estimated)
            SELECT track_id,
                   window_start + EXTRACT(EPOCH FROM window_end - window_start) * n / (estimates + 1)
                                  * INTERVAL '1 second',
                   TRUE
              FROM missing,
                   generate_series(1, estimates) AS n
             WHERE window_end > window_start
                ON CONFLICT (track_id, played_at) DO NOTHING
            RETURNING 1
        )
        SELECT COUNT(*) FROM inserted
        """, {'history_start': library.HISTORY_START}).fetchone()[0]

    logging.info("Recorded %d real plays from the history, kept %d of its estimates and estimated %d more",
                 real, kept, estimated)


if __name__ == '__main__':
    main()
