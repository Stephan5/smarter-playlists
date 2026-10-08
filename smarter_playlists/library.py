import datetime
import logging

try:
    import iTunesLibrary
except ImportError:
    # Only on macOS, but everything except reading the library works without it
    iTunesLibrary = None

from . import database, timing

# Plays from before the first import are spread evenly from here up to the track's last play
HISTORY_START = datetime.datetime(2010, 1, 1, tzinfo=datetime.timezone.utc)

# Each track's new plays are logged, unless more tracks than this have them
MAX_TRACKS_LOGGED = 100

LIBRARY_COLUMNS = [
    ('track_id', 'TEXT'),
    ('title', 'TEXT'),
    ('artist_id', 'TEXT'),
    ('artist_name', 'TEXT'),
    ('album_id', 'TEXT'),
    ('album_title', 'TEXT'),
    ('album_artist', 'TEXT'),
    ('compilation', 'BOOLEAN'),
    ('genre', 'TEXT'),
    ('year', 'INT'),
    ('disc_number', 'INT'),
    ('track_number', 'INT'),
    ('duration_ms', 'INT'),
    ('bpm', 'INT'),
    ('play_count', 'INT'),
    ('skip_count', 'INT'),
    ('last_played_at', 'TIMESTAMPTZ'),
    ('last_skipped_at', 'TIMESTAMPTZ'),
    ('added_at', 'TIMESTAMPTZ'),
    ('playlist_only', 'BOOLEAN'),
]


def import_library(database_name):
    """Imports the Music library, returning how many new plays were recorded, and how many of them were estimated."""
    logging.info("Reading the Music library, which can take a while...")
    elapsed = timing.Stopwatch()
    tracks = read_library()
    if not tracks:
        # Rather than marking every track as removed
        raise SystemExit("No songs found in the Music library")
    logging.info("Found %d songs in %s", len(tracks), elapsed)

    logging.info("Updating the database...")
    with database.connect(database_name) as db:
        database.require_set_up(db)
        load_library(db, tracks)
        update_tracks(db)
        return record_plays(db)


def read_library():
    if iTunesLibrary is None:
        raise SystemExit("Reading the Music library needs macOS, with pyobjc-framework-iTunesLibrary installed")

    library, error = iTunesLibrary.ITLibrary.libraryWithAPIVersion_error_('1.0', None)
    if library is None:
        raise SystemExit("Unable to read the Music library: {0}".format(error))

    tracks = []
    for item in library.allMediaItems():
        # Skips podcasts, audiobooks, music videos etc.
        if item.mediaKind() != iTunesLibrary.ITLibMediaItemMediaKindSong:
            continue

        album = item.album()
        track = {
            'track_id': format_persistent_id(item.persistentID()),
            'title': item.title() or '',
            'artist_id': format_persistent_id(item.artist().persistentID()),
            'artist_name': item.artist().name() or '',
            'album_id': format_persistent_id(album.persistentID()),
            'album_title': album.title() or '',
            'album_artist': album.albumArtist(),
            'compilation': bool(album.isCompilation()),
            'genre': item.genre() or '',
            'year': item.year() or None,
            'disc_number': album.discNumber() or 1,
            'track_number': item.trackNumber() or 1,
            'duration_ms': item.totalTime(),
            'bpm': item.beatsPerMinute() or None,
            'play_count': item.playCount(),
            'skip_count': item.skipCount(),
            'last_played_at': to_datetime(item.lastPlayedDate()),
            'last_skipped_at': to_datetime(item.skipDate()),
            'added_at': to_datetime(item.addedDate()),
            'playlist_only': bool(item.isPlaylistOnly()),
        }
        tracks.append(tuple(track[column] for column, _ in LIBRARY_COLUMNS))

    return tracks


def format_persistent_id(persistent_id):
    # The same 16 hex digit form the Music app uses for a track's persistent ID
    return '{0:016X}'.format(persistent_id & 0xFFFFFFFFFFFFFFFF)


# The library's dates count from here, and it reports some tracks that have never been played as last played then
MAC_EPOCH = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)


def to_datetime(ns_date):
    if ns_date is None:
        return None
    timestamp = datetime.datetime.fromtimestamp(ns_date.timeIntervalSince1970(), tz=datetime.timezone.utc)
    return timestamp if timestamp > MAC_EPOCH else None


def load_library(db, tracks):
    db.execute("CREATE TEMPORARY TABLE library ({0}) ON COMMIT DROP".format(
        ', '.join('{0} {1}'.format(column, column_type) for column, column_type in LIBRARY_COLUMNS)))

    with db.cursor().copy("COPY library FROM STDIN") as copy:
        for track in tracks:
            copy.write_row(track)


def update_tracks(db):
    # Artists and albums can be spelt differently across their tracks, so take the most common spelling
    db.execute("""
        INSERT INTO artist (artist_id, name)
        SELECT artist_id,
               MODE() WITHIN GROUP (ORDER BY artist_name)
          FROM library
         GROUP BY artist_id
            ON CONFLICT (artist_id) DO UPDATE
           SET name = excluded.name
        """)

    db.execute("""
        INSERT INTO album (album_id, title, album_artist, compilation, year)
        SELECT album_id,
               MODE() WITHIN GROUP (ORDER BY album_title),
               COALESCE(MODE() WITHIN GROUP (ORDER BY album_artist), MODE() WITHIN GROUP (ORDER BY artist_name)),
               BOOL_OR(compilation),
               MAX(year)
          FROM library
         GROUP BY album_id
            ON CONFLICT (album_id) DO UPDATE
           SET title = excluded.title,
               album_artist = excluded.album_artist,
               compilation = excluded.compilation,
               year = excluded.year
        """)

    columns = [column for column, _ in LIBRARY_COLUMNS
               if column not in ('artist_name', 'album_title', 'album_artist', 'compilation')]
    db.execute("""
        INSERT INTO track ({0}, removed_at)
        SELECT {0}, NULL
          FROM library
            ON CONFLICT (track_id) DO UPDATE
           SET {1}
        """.format(', '.join(columns),
                   ', '.join('{0} = excluded.{0}'.format(column) for column in columns + ['removed_at'])))

    cur = db.execute("""
        UPDATE track
           SET removed_at = now()
         WHERE removed_at IS NULL
           AND NOT EXISTS (SELECT FROM library WHERE library.track_id = track.track_id)
        """)
    if cur.rowcount:
        removed = db.execute("""
            SELECT title, name
              FROM track
              JOIN artist USING (artist_id)
             WHERE removed_at IS NOT NULL
             ORDER BY removed_at DESC
             LIMIT %s
            """, (cur.rowcount,)).fetchall()
        logging.warning("%d tracks are no longer in the library", cur.rowcount)
        for title, artist in removed:
            logging.warning("  - '%s' by %s", title, artist)


def record_plays(db):
    # Tops up each track's plays to its play count. The newest play is recorded at the track's last played time and
    # any others since its previous recorded play (or HISTORY_START) are estimated, evenly spaced before it.
    #
    # Only adding plays when the count goes up also stops the same play being recorded twice when the library reports
    # a slightly different last played time, as it does after the clocks change.
    rows = db.execute("""
        WITH missing AS (
            SELECT t.track_id,
                   t.last_played_at,
                   COALESCE(MAX(p.played_at) FILTER (WHERE p.played_at < t.last_played_at),
                            %(history_start)s) AS window_start,
                   COALESCE(BOOL_OR(p.played_at = t.last_played_at), FALSE) AS has_last_play,
                   t.play_count - COUNT(p.play_id) AS missing_plays
              FROM track t
              LEFT JOIN play p USING (track_id)
             WHERE t.last_played_at IS NOT NULL
             GROUP BY t.track_id
            HAVING t.play_count > COUNT(p.play_id)
        ),
        recorded AS (
            INSERT INTO play (track_id, played_at, estimated)
            SELECT track_id,
                   last_played_at,
                   FALSE
              FROM missing
             WHERE NOT has_last_play
             UNION ALL
            SELECT track_id,
                   -- In seconds rather than days, which would shift by an hour across a clock change
                   window_start + EXTRACT(EPOCH FROM last_played_at - window_start) * n / (estimates + 1)
                                  * INTERVAL '1 second',
                   TRUE
              FROM (SELECT *,
                           missing_plays - CASE WHEN has_last_play THEN 0 ELSE 1 END AS estimates
                      FROM missing) AS m,
                   generate_series(1, m.estimates) AS n
                ON CONFLICT (track_id, played_at) DO NOTHING
            RETURNING track_id, played_at, estimated
        )
        -- What was recorded for each track, for the log
        SELECT t.title,
               a.name,
               MAX(r.played_at) FILTER (WHERE NOT r.estimated) AS played_at,
               COUNT(*) FILTER (WHERE r.estimated) AS estimated
          FROM recorded r
          JOIN track t USING (track_id)
          JOIN artist a USING (artist_id)
         GROUP BY r.track_id, t.title, a.name
         ORDER BY MAX(r.played_at), t.title
        """, {'history_start': HISTORY_START}).fetchall()

    estimated = sum(row[3] for row in rows)
    plays = sum(1 for _, _, played_at, _ in rows if played_at) + estimated
    log_plays(rows, plays, estimated)
    return plays, estimated


def log_plays(rows, plays, estimated):
    """Logs each track's new plays, unless there are too many to read, as on the first import."""
    level = logging.INFO if len(rows) <= MAX_TRACKS_LOGGED else logging.DEBUG
    today = datetime.date.today()
    for title, artist, played_at, track_estimated in rows:
        if played_at:
            played_at = played_at.astimezone()
            when = played_at.strftime('%H:%M' if played_at.date() == today else '%Y-%m-%d %H:%M')
            logging.log(level, "Played '%s' by %s at %s%s", title, artist, when,
                        " (+{0} estimated)".format(track_estimated) if track_estimated else "")
        else:
            logging.log(level, "Played '%s' by %s %d %s (estimated)", title, artist, track_estimated,
                        "time" if track_estimated == 1 else "times")

    if not rows:
        logging.info("No new plays")
        return
    logging.info("Recorded %s (%d estimated) of %s%s", plural(plays, 'new play'), estimated,
                 plural(len(rows), 'track'), "" if level == logging.INFO else ". Use --verbose to list them")


def plural(count, noun):
    return '{0} {1}{2}'.format(count, noun, '' if count == 1 else 's')

