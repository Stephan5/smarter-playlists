import fcntl
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

import psycopg
import pytest

from smarter_playlists import database, server


@pytest.fixture(scope='module')
def cluster_home():
    """A cluster of its own, separate from the one the other tests share, as these tests start and stop it."""
    home = tempfile.mkdtemp(prefix='smarter playlists ', dir='/tmp')
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('SMARTER_PLAYLISTS_HOME', home)
        server.init()
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def cluster(cluster_home, monkeypatch):
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', cluster_home)
    yield
    server.keep_running_path().unlink(missing_ok=True)
    if server.is_running():
        server.stop()


def free_port():
    with socket.socket() as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]


@pytest.fixture
def other_process(cluster_home):
    """Runs Python code that uses the server in another process, until .finish()."""
    class OtherProcess:
        def __init__(self, code):
            script = textwrap.dedent('''
                import sys
                from smarter_playlists import server
                with {0}:
                    print('ready', flush=True)
                    sys.stdin.readline()
            ''').format(code)
            self.process = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, text=True,
                                            env={**os.environ, 'SMARTER_PLAYLISTS_HOME': cluster_home})
            assert self.process.stdout.readline() == 'ready\n'

        def finish(self):
            self.process.communicate('\n', timeout=30)
            assert self.process.returncode == 0

    started = []
    yield lambda code: started.append(OtherProcess(code)) or started[-1]
    for process in started:
        process.process.kill()


def test_starts_and_stops_the_server(cluster):
    assert not server.is_running()

    with server.running():
        with database.connect('postgres') as db:
            assert db.execute("SELECT 1").fetchone() == (1,)

    assert not server.is_running()


def test_starts_without_a_locale_as_under_launchd(cluster, monkeypatch):
    for name in list(os.environ):
        if name == 'LANG' or name.startswith('LC_'):
            monkeypatch.delenv(name)

    with server.running():
        assert server.is_running()


def test_only_listens_on_its_socket(cluster):
    with server.running():
        assert server.tcp_address() is None
        assert (server.data_dir() / '.s.PGSQL.{0}'.format(server.DEFAULT_PORT)).is_socket()


def test_the_last_to_finish_stops_the_server(cluster):
    with server.running():
        with server.running():
            pass
        assert server.is_running()

    assert not server.is_running()


def test_leaves_the_server_running_for_another_process(cluster, other_process):
    with server.running():
        other = other_process('server.running()')
    assert server.is_running()

    other.finish()
    assert not server.is_running()


def test_keeps_running_after_db_start(cluster):
    server.start_and_keep_running()
    with server.running():
        pass
    assert server.is_running()

    server.stop_when_unused()
    assert not server.is_running()


def test_db_stop_waits_for_whatever_is_using_it(cluster, caplog):
    caplog.set_level('INFO')
    server.start_and_keep_running()

    with server.running():
        server.stop_when_unused()
        assert server.is_running()

    assert not server.is_running()
    assert "The database is in use, so it will stop when that finishes" in caplog.messages


def test_db_start_on_a_port(cluster):
    port = free_port()
    server.start_and_keep_running(port)

    assert server.tcp_address() == 'localhost:{0}'.format(port)
    with psycopg.connect(host='localhost', port=port, user=server.USER, dbname='postgres') as db:
        assert db.execute("SELECT 1").fetchone() == (1,)
    # Everything else still finds it on its socket
    with server.running(), database.connect('postgres') as db:
        assert db.execute("SELECT 1").fetchone() == (1,)


def test_db_start_refuses_to_change_the_port_of_a_running_server(cluster):
    server.start_and_keep_running()

    with pytest.raises(SystemExit, match='already running, but not on port'):
        server.start_and_keep_running(free_port())


def test_status(cluster, caplog):
    caplog.set_level('INFO')
    server.status()
    server.start_and_keep_running()
    server.status()

    version = server.cluster_version()
    assert caplog.messages[0] == "Postgres {0} database in {1}, not running".format(version, server.data_dir())
    assert caplog.messages[-1] == "Postgres {0} database in {1}, running until `smarter-playlists db stop`".format(
        version, server.data_dir())


def test_only_one_command_works_at_a_time(cluster):
    with server.work(), open(server.home() / 'work.lock', 'a') as other:
        with pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_says_when_it_is_waiting_for_another_command(cluster, caplog):
    caplog.set_level('INFO')
    with server.work():
        pass
    assert caplog.messages == []

    entered = threading.Event()

    def other_command():
        with server.work():
            entered.set()

    with server.work():
        thread = threading.Thread(target=other_command)
        thread.start()
        deadline = time.monotonic() + 5
        while not caplog.messages and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not entered.is_set()
    thread.join(5)

    assert entered.is_set()
    assert caplog.messages[0] == "Waiting for another Smarter Playlists command, e.g. a scheduled run, to finish..."
    assert re.fullmatch(r'Waited \d+\.\ds', caplog.messages[1])


def test_requires_a_database(tmp_path, monkeypatch):
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', str(tmp_path))

    with pytest.raises(SystemExit, match="There's no database yet"):
        with server.running():
            pass


def test_refuses_a_socket_path_that_is_too_long(tmp_path, monkeypatch):
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', str(tmp_path / ('x' * 100)))

    with pytest.raises(SystemExit, match='too long a path'):
        server.init()


def test_connects_programs_to_its_own_server_only(monkeypatch):
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', '/tmp/home')
    monkeypatch.setenv('PGHOST', 'db.example.com')
    monkeypatch.setenv('PGSERVICE', 'elsewhere')

    env = server.environment('music')

    assert {name: value for name, value in env.items() if name.startswith('PG')} == {
        'PGDATABASE': 'music', 'PGHOST': '/tmp/home/postgres', 'PGPORT': '5432', 'PGUSER': 'postgres'}


class TestBinDir:

    @pytest.fixture
    def installed(self, monkeypatch):
        """Pretends these Postgres versions are installed, the first on the PATH unless on_path is False."""
        def install(*versions, on_path=True):
            dirs = {pathlib.Path('/pg/{0}/bin'.format(version)): version for version in versions}
            monkeypatch.setattr(server, 'candidate_bin_dirs', lambda: list(dirs))
            monkeypatch.setattr(server, 'path_bin_dir', lambda: next(iter(dirs)) if on_path else None)
            monkeypatch.setattr(server, 'major_version', lambda path: dirs[path])
        return install

    def test_prefers_the_one_on_the_path(self, installed):
        installed(17, 18)

        assert server.bin_dir() == pathlib.Path('/pg/17/bin')

    def test_otherwise_the_newest(self, installed):
        installed(17, 18, 16, on_path=False)

        assert server.bin_dir() == pathlib.Path('/pg/18/bin')

    def test_finds_a_version(self, installed):
        installed(18, 17)

        assert server.bin_dir(17) == pathlib.Path('/pg/17/bin')

    def test_explains_a_missing_version(self, installed):
        installed(18)

        with pytest.raises(SystemExit, match='needs Postgres 17'):
            server.bin_dir(17)

    def test_explains_postgres_is_missing(self, installed):
        installed()

        with pytest.raises(SystemExit, match="Couldn't find Postgres"):
            server.bin_dir()

    def test_finds_the_real_postgres(self):
        assert (server.bin_dir() / 'pg_ctl').exists()
