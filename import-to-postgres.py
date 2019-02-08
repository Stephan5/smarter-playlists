#!/usr/bin/env python3

import argparse
import logging
import plistlib

import psycopg2

DEFAULT_LIBRARY_FILE_LOCATION = '/Users/stephan/Music/iTunes/iTunes Music Library.xml'
DEFAULT_DATABASE_NAME = 'music'
DEFAULT_SCHEMA_NAME = 'public'
DEFAULT_USER_NAME = 'postgres'
DEFAULT_PASSWORD = 'postgres'
DEFAULT_PORT = 5432

TEMPORARY_TABLE_NAME = 'itunes'
TEMPORARY_SCHEMA_NAME = 'itunes'


def main(arg_list=None):
    args = parse_args(arg_list)

    library_xml = args.library_xml
    db_name = args.database_name
    schema_name = args.schema_name
    port = args.port
    username = args.username
    password = args.password

    logging.warning("Connecting to database %s on port %s with username %s. Importing data to schema %s",
                    db_name, port, username, schema_name)

    logging.warning("Parsing library file at location: %s", library_xml.name)

    library = plistlib.load(library_xml)

    logging.warning("Extracting track data from library file...")
    tracks_table, tracks = process_tracks(library)

    # Import data 'as-is' to postgres
    conn, cur = open_db(db_name, port, username, password)
    logging.warning("Importing data into temp schema...")
    import_itunes_data(cur, tracks, tracks_table)
    close_db(conn, cur)

    # Create normalised data structure
    conn, cur = open_db(db_name, port, username, password)
    logging.warning("Creating the new tables...")
    create_normalised_tables(cur, schema_name)
    close_db(conn, cur)

    # Migrate data over to new structure
    conn, cur = open_db(db_name, port, username, password)
    logging.warning("Migrating data to new tables...")
    normalise_data(cur, schema_name)
    close_db(conn, cur)


def parse_args(arg_list):
    parser = argparse.ArgumentParser()
    parser.add_argument('--library',
                        help='Path to XML library file',
                        dest='library_xml',
                        type=argparse.FileType('rb'),
                        default=DEFAULT_LIBRARY_FILE_LOCATION)
    parser.add_argument('--db', '-d',
                        help='Name of postgres database [%(default)s]',
                        dest='database_name',
                        default=DEFAULT_DATABASE_NAME)
    parser.add_argument('--port', '-p',
                        help='Local database port [%(default)s]',
                        dest='port',
                        default=DEFAULT_PORT)
    parser.add_argument('--schema', '-s',
                        help='Name of database schema [%(default)s]',
                        dest='schema_name',
                        default=DEFAULT_SCHEMA_NAME)
    parser.add_argument('--user', '-u',
                        help='Postgres username [%(default)s]',
                        dest='username',
                        default=DEFAULT_USER_NAME)
    parser.add_argument('--pass', '-x',
                        help='Postgres password [%(default)s]',
                        dest='password',
                        default=DEFAULT_PASSWORD)
    args = parser.parse_args(arg_list)
    return args


def import_itunes_data(db, tracks, tracks_table):
    db.execute("DROP SCHEMA IF EXISTS itunes CASCADE")
    db.execute("CREATE SCHEMA itunes")
    db.execute("DROP TABLE IF EXISTS {0}.{1}".format(TEMPORARY_SCHEMA_NAME, TEMPORARY_TABLE_NAME))
    db.execute(tracks_table)
    db.execute("CREATE UNIQUE INDEX idx_itunes_itunes_id ON itunes.itunes (persistent_id);")

    for query in tracks:
        db.execute(query[0], list(query[1]))


def create_normalised_tables(db, schema_name):
    db.execute("CREATE SCHEMA IF NOT EXISTS {0}".format(schema_name))

    db.execute("CREATE TABLE IF NOT EXISTS {0}.artist ("
               "artist_id   BIGINT GENERATED BY DEFAULT AS IDENTITY,"
               "artist_name TEXT NOT NULL,"
               "CONSTRAINT pk_artist PRIMARY KEY (artist_id),"
               "CONSTRAINT uk_artist_name UNIQUE (artist_name)"
               "); "
               .format(schema_name))

    db.execute("CREATE TABLE IF NOT EXISTS {0}.album ("
               "album_id  BIGINT GENERATED BY DEFAULT AS IDENTITY,"
               "album_name TEXT NOT NULL,"
               "artist_id BIGINT NOT NULL,"
               "release_year INT,"
               "CONSTRAINT pk_album PRIMARY KEY (album_id),"
               "CONSTRAINT fk_album_artist_id FOREIGN KEY (artist_id) REFERENCES {0}.artist (artist_id),"
               "CONSTRAINT ck_album_release_year CHECK (release_year BETWEEN 1900 AND 2050),"
               "CONSTRAINT uk_album_artist UNIQUE (album_name, artist_id)"
               "); "
               .format(schema_name))

    db.execute("CREATE TABLE IF NOT EXISTS {0}.track ("
               "track_id BIGINT GENERATED BY DEFAULT AS IDENTITY, "
               "track_name TEXT NOT NULL, "
               "length BIGINT NOT NULL, "
               "album_id BIGINT NOT NULL, "
               "artist_id BIGINT NOT NULL, "
               "play_count INT NOT NULL, "
               "last_played TIMESTAMP, "
               "date_added TIMESTAMP NOT NULL, "
               "track_number TEXT NOT NULL, "
               "itunes_id VARCHAR(16) NOT NULL, "
               "CONSTRAINT pk_track PRIMARY KEY (track_id), "
               "CONSTRAINT fk_track_artist_id FOREIGN KEY (artist_id) REFERENCES {0}.artist (artist_id), "
               "CONSTRAINT fk_track_album_id FOREIGN KEY (album_id) REFERENCES {0}.album (album_id), "
               "CONSTRAINT ck_track_date_added CHECK (date_added <= dbrent_timestamp :: TIMESTAMP), "
               "CONSTRAINT ck_track_play_count CHECK (play_count >= 0), "
               "CONSTRAINT uk_track_artist_album UNIQUE (track_name, album_id, artist_id, track_number), "
               "CONSTRAINT uk_track_itunes_id UNIQUE (itunes_id));"
               .format(schema_name))

    db.execute("CREATE TABLE IF NOT EXISTS {0}.play ("
               "play_id BIGINT GENERATED BY DEFAULT AS IDENTITY,"
               "track_id BIGINT,"
               "played_at TIMESTAMP NOT NULL,"
               "CONSTRAINT pk_play PRIMARY KEY (play_id),"
               "CONSTRAINT fk_play_track_id FOREIGN KEY (track_id) REFERENCES {0}.track (track_id),"
               "CONSTRAINT uk_play_track_play_at UNIQUE (track_id, played_at)"
               ");"
               .format(schema_name))

    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_track_itunes_id ON {0}.track (itunes_id);".format(schema_name))


def normalise_data(db, schema_name):
    db.execute("INSERT INTO {0}.artist (artist_name) "
               "SELECT artist "
               "  FROM itunes.itunes "
               " WHERE artist IS NOT NULL "
               " GROUP BY artist "
               "    ON CONFLICT (artist_name) "
               "    DO NOTHING;"
               .format(schema_name))

    db.execute("INSERT INTO {0}.album (album_name, artist_id, release_year) "
               "SELECT album, "
               "       (SELECT artist_id "
               "          FROM {0}.artist "
               "         WHERE artist_name = artist), "
               "       MAX(year :: INT) "
               "  FROM itunes.itunes "
               " WHERE album IS NOT NULL "
               "   AND year IS NOT NULL "
               " GROUP BY album, artist "
               "    ON CONFLICT (album_name, artist_id)"
               "    DO NOTHING;"
               .format(schema_name))

    db.execute("INSERT INTO {0}.track (track_name, length, album_id, "
               "                   artist_id, play_count, last_played, "
               "                   date_added, track_number, itunes_id) "
               "SELECT name, "
               "       total_time :: BIGINT, "
               "       al.album_id, "
               "       ar.artist_id, "
               "       COALESCE(play_count :: INT, 0), "
               "       play_date_utc :: TIMESTAMP, "
               "       date_added :: TIMESTAMP, "
               "       COALESCE(track_number :: INT, 1), "
               "       persistent_id "
               "  FROM itunes.itunes t "
               "       JOIN {0}.artist ar "
               "            ON (artist_name = artist) "
               "       JOIN {0}.album al "
               "            ON (album_name = album "
               "           AND release_year = year :: int "
               "           AND al.artist_id = ar.artist_id)"
               "    ON CONFLICT "
               "    ON CONSTRAINT uk_track_artist_album"
               "    DO UPDATE "
               "   SET play_count = EXCLUDED.play_count, "
               "       last_played = EXCLUDED.last_played, "
               "       itunes_id = EXCLUDED.itunes_id;"
               .format(schema_name))

    db.execute("INSERT INTO {0}.play (track_id, played_at)"
               "SELECT track_id,"
               "       last_played"
               "  FROM {0}.track"
               " WHERE last_played IS NOT NULL"
               "   AND NOT EXISTS (SELECT "
               "                     FROM {0}.play"
               "                    WHERE track_id = track_id"
               "                      AND played_at = last_played);"
               .format(schema_name))


def process_tracks(library):
    all_keys = set()
    inserts = []

    for track_id in library['Tracks'].keys():
        track = library['Tracks'][track_id]
        track_keys = list(map(slugify, track.keys()))

        if 'Podcast' in track and track['Podcast']:
            track.pop('Podcast')
            continue
        if 'Playlist Only' in track:
            track.pop('Playlist Only')
            continue
        if 'Music Video' in track:
            track.pop('Music Video')
            continue

        if 'Artist' not in track:
            continue
        if 'Album' not in track:
            continue
        if 'Year' not in track:
            continue

        track_values = track.values()

        all_keys = all_keys.union(set(track_keys))

        inserts.append(get_parameterized(track_keys, track_values))

    all_keys = list(map(slugify, all_keys))
    all_keys_with_type = (key + " TEXT" for key in all_keys)

    return "CREATE TABLE IF NOT EXISTS {0}.{1} ({2})".format(TEMPORARY_SCHEMA_NAME, TEMPORARY_TABLE_NAME,
                                                             ', '.join(all_keys_with_type)), inserts


def get_parameterized(keys, values):
    return (
        "INSERT INTO {0}.{1} ({2}) VALUES ({3})".format(
            TEMPORARY_SCHEMA_NAME,
            TEMPORARY_TABLE_NAME,
            ', '.join(map(str, keys)),
            ', '.join(['%s'] * len(values))
        ),
        [value for value in values]
    )


def slugify(name):
    return name.lower().replace(' ', '_')


def open_db(name, port, user, password):
    conn = psycopg2.connect(host="localhost", port=port, database=name, user=user, password=password)
    cur = conn.cursor()
    return conn, cur


def close_db(conn, cur):
    conn.commit()
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
