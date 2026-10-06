"""The Postgres server smarter-playlists runs for itself. It only listens on a Unix socket in its data directory, and
only runs while a command needs it."""

import contextlib
import fcntl
import functools
import glob
import logging
import os
import pathlib
import re
import shutil
import subprocess

USER = 'postgres'
DEFAULT_PORT = 5432

# macOS limits Unix socket paths to 103 bytes, and the socket goes in the data directory
MAX_SOCKET_PATH = 103

# Where Postgres is installed, besides on the PATH: Homebrew on Apple silicon and Intel, Postgres.app and Debian/Ubuntu
INSTALL_PATTERNS = [
    '/opt/homebrew/opt/postgresql@*/bin',
    '/usr/local/opt/postgresql@*/bin',
    '/Applications/Postgres.app/Contents/Versions/*/bin',
    '/usr/lib/postgresql/*/bin',
]


def home():
    return pathlib.Path(os.environ.get('SMARTER_PLAYLISTS_HOME')
                        or pathlib.Path.home() / 'Library' / 'Application Support' / 'smarter-playlists')


def data_dir():
    return home() / 'postgres'


def log_path():
    return home() / 'postgres.log'


def keep_running_path():
    return home() / 'keep-running'


# Finding Postgres

def path_bin_dir():
    """The bin directory of the Postgres on the PATH, if there is one."""
    pg_config = shutil.which('pg_config')
    if pg_config:
        process = subprocess.run([pg_config, '--bindir'], capture_output=True, text=True)
        if process.returncode == 0:
            return pathlib.Path(process.stdout.strip())
    initdb = shutil.which('initdb')
    return pathlib.Path(initdb).parent if initdb else None


def candidate_bin_dirs():
    """Every Postgres bin directory found, the one on the PATH first."""
    found = [path_bin_dir()] + [pathlib.Path(path) for pattern in INSTALL_PATTERNS for path in sorted(glob.glob(pattern))]
    unique = {}
    for path in found:
        if path and (path / 'postgres').exists():
            unique.setdefault(path.resolve(), path)
    return list(unique.values())


@functools.cache
def major_version(bin_dir):
    output = subprocess.run([pathlib.Path(bin_dir) / 'postgres', '--version'], capture_output=True, text=True,
                            check=True).stdout
    return int(re.search(r'\) (\d+)', output).group(1))


def bin_dir(major=None):
    """The Postgres to use: for the given major version, or else the one on the PATH or the newest installed."""
    candidates = candidate_bin_dirs()
    if not candidates:
        raise SystemExit("Couldn't find Postgres. Install it, e.g. with `brew install postgresql@18`, and make sure "
                         "`pg_config` is on your PATH")

    if major is None:
        on_path = path_bin_dir()
        if on_path and on_path in candidates:
            return on_path
        return max(candidates, key=major_version)

    for candidate in candidates:
        if major_version(candidate) == major:
            return candidate
    raise SystemExit("The database needs Postgres {0}, which isn't installed. Install it, e.g. with "
                     "`brew install postgresql@{0}`, then run `smarter-playlists upgrade` to move to the newest "
                     "Postgres".format(major))


def program(name):
    """A Postgres program, from the version the database was made with."""
    return bin_dir(cluster_version()) / name


# The cluster

def initialised():
    return (data_dir() / 'PG_VERSION').exists()


def require_initialised():
    if not initialised():
        raise SystemExit("There's no database yet. Run `smarter-playlists setup`, or `smarter-playlists restore` "
                         "with a backup")


def cluster_version():
    return int((data_dir() / 'PG_VERSION').read_text().strip())


def init(bin=None):
    """Creates the cluster, set to only listen on a Unix socket in its data directory."""
    bin = bin or bin_dir()
    data = data_dir()
    socket = data / '.s.PGSQL.{0}'.format(DEFAULT_PORT)
    if len(str(socket).encode()) > MAX_SOCKET_PATH:
        raise SystemExit("{0} is too long a path for the database's socket. Set SMARTER_PLAYLISTS_HOME to somewhere "
                         "shorter".format(socket))

    data.parent.mkdir(parents=True, exist_ok=True)
    run([bin / 'initdb', '-D', data, '-U', USER, '--auth=trust', '-E', 'UTF8', '--locale=C'], env=server_environment())
    with open(data / 'postgresql.conf', 'a') as conf:
        # Here rather than as pg_ctl options, which would need the path quoting for a shell
        conf.write("\n# Set by smarter-playlists: only listen on a Unix socket in the data directory\n"
                   "listen_addresses = ''\n"
                   "unix_socket_directories = {0}\n".format(quote_setting(str(data))))
    logging.info("Created a Postgres %d database in %s", major_version(bin), data)


def quote_setting(value):
    return "'{0}'".format(value.replace("'", "''"))


# Starting and stopping

def is_running():
    if not initialised():
        return False
    return subprocess.run([program('pg_ctl'), 'status', '-D', data_dir()], capture_output=True).returncode == 0


def postmaster_line(number):
    """A line of postmaster.pid, which the server writes while it's running, or None."""
    try:
        return (data_dir() / 'postmaster.pid').read_text().splitlines()[number]
    except (FileNotFoundError, IndexError):
        return None


def port():
    """The port the server is on, which names its socket, as `db start --port` changes it."""
    value = postmaster_line(3)
    return int(value) if value and value.strip().isdigit() else DEFAULT_PORT


def tcp_address():
    """Where the server is listening for TCP connections, if it is."""
    host = (postmaster_line(5) or '').strip()
    return '{0}:{1}'.format(host, port()) if host else None


def start(tcp_port=None, settings=None):
    """Starts the server. With a port, it also listens on localhost, for other database tools."""
    warn_if_outdated()
    options = []
    if tcp_port:
        options += ['-p {0}'.format(tcp_port), "-c listen_addresses=localhost"]
    options += ['-c {0}={1}'.format(name, value) for name, value in (settings or {}).items()]
    run([program('pg_ctl'), 'start', '-D', data_dir(), '-l', log_path(), '-w',
         *(['-o', ' '.join(options)] if options else [])], env=server_environment())
    logging.info("Started Postgres %d%s", cluster_version(), " on port {0}".format(tcp_port) if tcp_port else "")


def stop():
    run([program('pg_ctl'), 'stop', '-D', data_dir(), '-m', 'fast', '-w'])
    logging.info("Stopped Postgres")


def warn_if_outdated():
    installed = major_version(bin_dir())
    if cluster_version() < installed:
        logging.warning("The database uses Postgres %d, but %d is installed. Run `smarter-playlists upgrade` to move "
                        "to it", cluster_version(), installed)


@contextlib.contextmanager
def lock(name, operation=fcntl.LOCK_EX):
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / name, 'a') as file:
        fcntl.flock(file, operation)
        yield file


@contextlib.contextmanager
def running():
    """Starts the server if it isn't running, and stops it afterwards if nothing else is using it.

    Everything using the server holds a shared lock on server.lock, so whichever finishes last stops it, whoever
    started it. Unless `db start` has asked for it to keep running.
    """
    require_initialised()
    with lock('start.lock'):
        if not is_running():
            start()
        users = open(home() / 'server.lock', 'a')
        fcntl.flock(users, fcntl.LOCK_SH)
    try:
        yield
    finally:
        with lock('start.lock'):
            last = try_exclusive(users)
            users.close()
            if last and not keep_running_path().exists() and is_running():
                stop()


def try_exclusive(file):
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


@contextlib.contextmanager
def work():
    """Only one command changing the database, or the Music app, runs at a time."""
    with lock('work.lock'):
        yield


def start_and_keep_running(tcp_port=None):
    """`db start`: leaves the server running, until `db stop`."""
    require_initialised()
    with lock('start.lock'):
        if is_running():
            if tcp_port and tcp_address() != 'localhost:{0}'.format(tcp_port):
                raise SystemExit("The database is already running, but not on port {0}. Run `smarter-playlists db "
                                 "stop` first".format(tcp_port))
            logging.info("The database is already running")
        else:
            start(tcp_port)
        keep_running_path().touch()


def stop_when_unused():
    """`db stop`: stops the server now, or when whatever is using it finishes."""
    with lock('start.lock'):
        keep_running_path().unlink(missing_ok=True)
        if not is_running():
            logging.info("The database isn't running")
            return
        with open(home() / 'server.lock', 'a') as users:
            if not try_exclusive(users):
                logging.info("The database is in use, so it will stop when that finishes")
                return
        stop()


def status():
    if not initialised():
        logging.info("There's no database yet. Run `smarter-playlists setup`")
        return
    if is_running():
        state = "running on {0}".format(tcp_address()) if tcp_address() else "running"
    else:
        state = "not running"
    logging.info("Postgres %d database in %s, %s%s", cluster_version(), data_dir(), state,
                 " until `smarter-playlists db stop`" if keep_running_path().exists() else "")


# Connecting

def connection(database_name):
    """Settings for psycopg.connect, to connect to a database on the server."""
    return {'dbname': database_name, 'host': str(data_dir()), 'port': port(), 'user': USER}


def environment(database_name):
    """The environment for a Postgres program, e.g. psql or pg_dump, to connect to a database on the server."""
    env = {name: value for name, value in os.environ.items() if not name.startswith('PG')}
    env.update({'PGDATABASE': database_name, 'PGHOST': str(data_dir()), 'PGPORT': str(port()), 'PGUSER': USER})
    return env


def server_environment():
    """The environment to run the server in. Postgres on macOS won't start without a locale set, and launchd jobs
    don't have one. The database's locale is C whatever the environment's."""
    return {**os.environ, 'LC_ALL': 'C'}


def run(args, **kwargs):
    process = subprocess.run([str(arg) for arg in args], capture_output=True, text=True, **kwargs)
    if process.returncode != 0:
        raise SystemExit("{0} failed: {1}".format(pathlib.Path(args[0]).name,
                                                  (process.stderr or process.stdout).strip()))
    return process
