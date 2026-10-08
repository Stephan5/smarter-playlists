import datetime
import itertools
import shutil
import subprocess
import tempfile

import pytest

from smarter_playlists import database, library, server

UTC = datetime.timezone.utc

DATABASE_NUMBERS = itertools.count()


def pytest_addoption(parser):
    parser.addoption('--music', action='store_true',
                     help='Also run tests that read the real Music library and talk to the Music app (read-only)')
    parser.addoption('--binary',
                     help='Also run tests of this standalone executable, e.g. dist/smarter-playlists (slow)')
    parser.addoption('--music-write', action='store_true',
                     help='Also run tests that make test playlists and folders in the Music app, then delete them')


def pytest_collection_modifyitems(config, items):
    skips = []
    if not config.getoption('--music'):
        skips.append(('music', pytest.mark.skip(reason='needs --music')))
    if not config.getoption('--binary'):
        skips.append(('binary', pytest.mark.skip(reason='needs --binary')))
    if not config.getoption('--music-write'):
        skips.append(('music_write', pytest.mark.skip(reason='needs --music-write')))
    for item in items:
        for keyword, skip in skips:
            if keyword in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope='session')
def postgres():
    """A throwaway Postgres server, run as smarter-playlists runs its own, so tests never touch the real database."""
    # Short, as the socket goes in it, and with a space like ~/Library/Application Support
    home = tempfile.mkdtemp(prefix='smarter playlists ', dir='/tmp')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('SMARTER_PLAYLISTS_HOME', home)
        server.init()
        server.start(settings={'fsync': 'off'})
        try:
            yield home
        finally:
            subprocess.run([server.program('pg_ctl'), 'stop', '-D', server.data_dir(), '-m', 'immediate'],
                           capture_output=True)
            shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def empty_database(postgres, monkeypatch):
    """The name of a new, empty database for a single test."""
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', postgres)
    name = 'test_{0}'.format(next(DATABASE_NUMBERS))
    with database.connect('postgres', autocommit=True) as admin:
        admin.execute('DROP DATABASE IF EXISTS {0}'.format(name))
        admin.execute('CREATE DATABASE {0}'.format(name))
    yield name
    with database.connect('postgres', autocommit=True) as admin:
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
