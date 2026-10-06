import datetime
import itertools
import os
import socket
import subprocess

import psycopg
import pytest

from smarter_playlists import database, library

UTC = datetime.timezone.utc

DATABASE_NUMBERS = itertools.count()


def pytest_addoption(parser):
    parser.addoption('--integration', action='store_true',
                     help='Also run tests that read the real Music library and talk to the Music app (read-only)')


def pytest_collection_modifyitems(config, items):
    if config.getoption('--integration'):
        return
    skip = pytest.mark.skip(reason='needs --integration')
    for item in items:
        if 'integration' in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope='session')
def postgres(tmp_path_factory):
    """A throwaway Postgres server, so tests never touch a real database."""
    bin_dir = subprocess.run(['pg_config', '--bindir'], capture_output=True, text=True, check=True).stdout.strip()
    data_dir = tmp_path_factory.mktemp('postgres')
    port = free_port()

    subprocess.run([os.path.join(bin_dir, 'initdb'), '-D', data_dir, '-U', 'postgres', '--auth=trust',
                    '-E', 'UTF8', '--locale=C'], capture_output=True, check=True)
    # Temporary paths are too long for a Unix socket, so only listen on TCP
    subprocess.run([os.path.join(bin_dir, 'pg_ctl'), '-D', data_dir, '-l', data_dir / 'postgres.log', '-w',
                    '-o', "-p {0} -c listen_addresses=localhost -c unix_socket_directories='' -c fsync=off".format(port),
                    'start'], capture_output=True, check=True)
    try:
        yield {'PGHOST': 'localhost', 'PGPORT': str(port), 'PGUSER': 'postgres'}
    finally:
        subprocess.run([os.path.join(bin_dir, 'pg_ctl'), '-D', data_dir, '-m', 'immediate', 'stop'],
                       capture_output=True)


def free_port():
    with socket.socket() as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]


@pytest.fixture
def empty_database(postgres, monkeypatch):
    """The name of a new, empty database for a single test, with PG* variables set to connect to it."""
    for name in ('PGPASSWORD', 'PGDATABASE', 'PGSERVICE', 'PGOPTIONS'):
        monkeypatch.delenv(name, raising=False)
    for name, value in postgres.items():
        monkeypatch.setenv(name, value)

    name = 'test_{0}'.format(next(DATABASE_NUMBERS))
    with psycopg.connect(dbname='postgres', autocommit=True) as admin:
        admin.execute('DROP DATABASE IF EXISTS {0}'.format(name))
        admin.execute('CREATE DATABASE {0}'.format(name))
    yield name
    with psycopg.connect(dbname='postgres', autocommit=True) as admin:
        admin.execute('DROP DATABASE {0} WITH (FORCE)'.format(name))


@pytest.fixture
def database_name(empty_database):
    """The name of a new database, set up and ready to import into."""
    database.set_up(empty_database)
    return empty_database


@pytest.fixture
def query(database_name):
    def run(statement, params=None):
        with database.connect(database_name) as conn:
            cur = conn.execute(statement, params)
            return cur.fetchall() if cur.description else None
    return run


def make_track(**values):
    """A track as read_library returns it, with sensible defaults for anything not given."""
    track = {
        'track_id': 'A000000000000001',
        'title': 'Weird Fishes / Arpeggi',
        'artist_id': 'B000000000000001',
        'artist_name': 'Radiohead',
        'album_id': 'C000000000000001',
        'album_title': 'In Rainbows',
        'album_artist': None,
        'compilation': False,
        'genre': 'Alternative',
        'year': 2007,
        'disc_number': 1,
        'track_number': 4,
        'duration_ms': 318187,
        'bpm': None,
        'play_count': 0,
        'skip_count': 0,
        'last_played_at': None,
        'last_skipped_at': None,
        'added_at': datetime.datetime(2019, 8, 13, 21, 14, 28, tzinfo=UTC),
        'playlist_only': False,
    }
    unknown = set(values) - set(track)
    assert not unknown, 'Unknown track fields: {0}'.format(unknown)
    track.update(values)
    return tuple(track[column] for column, _ in library.LIBRARY_COLUMNS)


@pytest.fixture
def run_import(database_name, monkeypatch):
    """Imports the given tracks as if they were the whole Music library."""
    def run(*tracks):
        monkeypatch.setattr(library, 'read_library', lambda: list(tracks))
        library.import_library(database_name)
    return run
