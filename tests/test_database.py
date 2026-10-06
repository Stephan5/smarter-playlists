import pytest

from conftest import make_track
from smarter_playlists import database


def test_creates_tables(database_name, query):
    assert query("""
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'public'
         ORDER BY 1
        """) == [('album',), ('artist',), ('play',), ('track',)]


def test_creates_only_the_playlist_schema(database_name, query):
    assert query("""
        SELECT nspname
          FROM pg_namespace
         WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'
         ORDER BY 1
        """) == [('playlist',), ('public',)]


def test_creates_builtin_playlists(database_name, query, run_import):
    run_import(make_track())

    views = query("SELECT table_name FROM information_schema.views WHERE table_schema = 'playlist' ORDER BY 1")
    assert views == [('All-Time Favourites',), ('Forgotten Favourites',), ('Last Month',), ('Rising',), ('monthly',),
                     ('top_artists',), ('yearly',)]
    for (view,) in views:
        query('SELECT track_id, position FROM playlist."{0}"'.format(view))


def test_refuses_to_run_twice(database_name):
    with pytest.raises(SystemExit, match='already set up'):
        database.set_up(database_name)


def test_connects_to_database_by_name(database_name):
    with database.connect(database_name) as db:
        assert db.execute("SELECT current_database()").fetchone() == (database_name,)


def test_creates_a_database_only_if_missing(empty_database):
    database.create(empty_database)
    with database.connect(empty_database) as db:
        db.execute("CREATE TABLE kept ()")

    database.create(empty_database)

    with database.connect(empty_database) as db:
        assert database.exists(empty_database)
        assert db.execute("SELECT to_regclass('kept')").fetchone()[0] is not None


def test_recreates_a_database_empty(database_name):
    database.recreate(database_name)

    with database.connect(database_name) as db:
        assert not database.is_set_up(db)


def test_only_optional_values_are_nullable(database_name, query):
    # Everything else must always have a value
    assert query("""
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND is_nullable = 'YES'
         ORDER BY 1, ordinal_position
        """) == [
        ('album', 'year'),
        ('track', 'year'),
        ('track', 'bpm'),
        ('track', 'last_played_at'),
        ('track', 'last_skipped_at'),
        ('track', 'added_at'),
        ('track', 'removed_at'),
    ]
