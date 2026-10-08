"""Runs the standalone executable, as a user would, to check it works as built: that everything it needs is bundled, and
that the commands launchd runs for a schedule are ones it accepts. These only run with --binary, e.g.

    pytest --binary dist/smarter-playlists

Each command takes seconds to start, as the executable unpacks itself every time, so these are slow, and run once
against one throwaway database. Nothing here touches your real database, launchd or playlists: the database is in a
temporary folder, a schedule is only shown, and runs are dry runs. They need Postgres installed, but not a Music library:
without one, the run that launchd would make stops where it reads the library.
"""

import os
import pathlib
import plistlib
import shutil
import socket
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.binary

# A slow start, then however long Postgres, or reading the Music library, takes
TIMEOUT_SECONDS = 300


@pytest.fixture(scope='module')
def binary(request):
    path = request.config.getoption('--binary')
    if not path:
        pytest.skip('needs --binary')
    path = pathlib.Path(path).resolve()
    assert path.is_file(), '{0} is not a file. Build it with `make binary`'.format(path)
    return path


@pytest.fixture(scope='module')
def home():
    # Short, as the database's socket goes in it
    folder = pathlib.Path(tempfile.mkdtemp(prefix='sp-binary-', dir='/tmp'))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture(scope='module')
def environment(home):
    (home / 'user').mkdir()
    env = {name: value for name, value in os.environ.items() if not name.startswith(('PG', 'SMARTER_PLAYLISTS'))}
    # HOME, as that's where a schedule goes and Music logs, so nothing of the real one is touched
    env.update({'SMARTER_PLAYLISTS_HOME': str(home / 'data'), 'HOME': str(home / 'user')})
    return env


@pytest.fixture(scope='module')
def run(binary, environment):
    def run(*args, command=None, env=None, check=True):
        process = subprocess.run(command or [str(binary), *map(str, args)], env=env or environment,
                                 capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        process.output = process.stdout + process.stderr
        if check:
            assert process.returncode == 0, process.output
        return process
    return run


@pytest.fixture(scope='module')
def database(run):
    """The database after `setup`, which the tests after it use."""
    assert 'Database set up' in run('setup').output


def test_prints_help(run):
    assert 'usage: smarter-playlists' in run('--help').output


def test_setup_includes_the_tables_and_built_in_playlists(run, database):
    # Which come from SQL files bundled in the executable, and need psycopg
    counts = run('psql', '-At', '-c', "SELECT (SELECT count(*) FROM schema_migration), "
                                      "(SELECT count(*) FROM information_schema.views WHERE table_schema = 'playlist')")
    migrations, views = map(int, counts.stdout.strip().split('|'))
    assert migrations >= 1
    assert views >= 1


def test_setup_refuses_to_run_twice(run, database):
    process = run('setup', check=False)

    assert process.returncode != 0
    assert 'already set up' in process.output


def test_migrate_finds_nothing_to_do_on_a_new_database(run, database):
    assert 'The database is up to date' in run('migrate').output


def test_stats_work_on_an_empty_database(run, database):
    run('stats')


def test_backs_up_and_restores(run, database, home):
    run('backup', '--backup-dir', home / 'backups')
    [dump] = (home / 'backups').glob('music-*.dump')

    assert 'Restored' in run('restore', dump, '--backup-dir', home / 'backups').output
    assert 'The database is up to date' in run('migrate').output


def test_keeps_the_database_running_for_other_tools(run, database):
    with socket.socket() as s:
        s.bind(('localhost', 0))
        port = s.getsockname()[1]

    try:
        run('db', 'start', '--port', port)
        assert 'localhost:{0}'.format(port) in run('db', 'status').output
    finally:
        run('db', 'stop', check=False)
    assert 'not running' in run('db', 'status').output


def test_is_not_scheduled_unless_you_do_it(run):
    assert 'Not scheduled' in run('schedule', 'status').output


def test_scheduled_runs_use_commands_the_executable_accepts(run, binary, environment, database, home):
    shown = run('schedule', 'install', '--dry-run', '--every', '1', '--backup-dir', home / 'backups')
    agent = plistlib.loads(shown.stdout.encode())
    command = agent['ProgramArguments']

    # What launchd would run is the executable itself, with arguments it understands
    assert pathlib.Path(command[0]).resolve() == binary
    assert command[1:3] == ['run', '--scheduled']

    # Run it, as a dry run so that it never changes your playlists. With a Music library that completes. Without one
    # it stops after reading the library, which is later than it would if it didn't understand its arguments.
    process = run(command=command + ['--dry-run'], env={**environment, **agent.get('EnvironmentVariables', {})},
                  check=False)
    assert 'Run started' in process.output
    assert 'invalid choice' not in process.output
    assert 'usage:' not in process.output
