"""Backups of the database, taken before every import, and moving it to a newer Postgres."""

import datetime
import logging
import pathlib
import re
import tempfile

from . import database, server, timing

NAME_FORMAT = 'music-%Y-%m-%dT%H%M%S.dump'
NAME_PATTERN = re.compile(r'music-\d{4}-\d\d-\d\dT\d{6}\.dump')

# Keep this many of the newest backups, and the newest of each day for this many days, and of every month forever
KEEP_LATEST = 12
KEEP_DAYS = 30


def default_directory():
    return server.home() / 'backups'


def dump(path, database_name=database.DATABASE, pg_dump=None):
    """Dumps the database to a file, only putting it in place once it's complete."""
    path = pathlib.Path(path)
    partial = path.with_name(path.name + '.partial')
    server.run([pg_dump or server.program('pg_dump'), '--format=custom', '--file', partial],
               env=server.environment(database_name))
    partial.rename(path)


def take(directory=None, database_name=database.DATABASE, now=None):
    """Backs the database up, then deletes the backups that are no longer needed."""
    directory = pathlib.Path(directory or default_directory())
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.datetime.now()
    path = directory / now.strftime(NAME_FORMAT)
    logging.info("Backing up the database...")
    elapsed = timing.Stopwatch()
    dump(path, database_name)
    logging.info("Backed up to %s (%s) in %s", path, size(path), elapsed)
    prune(directory, now)
    return path


def size(path):
    size = pathlib.Path(path).stat().st_size
    return '{0:.1f} MB'.format(size / 1_000_000) if size >= 100_000 else '{0} KB'.format(-(-size // 1000))


def backups(directory):
    """The backups in a directory, by when they were taken."""
    directory = pathlib.Path(directory)
    if not directory.is_dir():
        return {}
    return {datetime.datetime.strptime(path.name, NAME_FORMAT): path
            for path in directory.iterdir() if NAME_PATTERN.fullmatch(path.name)}


def backups_to_keep(times, now):
    """The newest few, the newest of each recent day, and the newest of every month."""
    newest_first = sorted(times, reverse=True)
    days, months = {}, {}
    for time in newest_first:
        if now - time < datetime.timedelta(days=KEEP_DAYS):
            days.setdefault(time.date(), time)
        months.setdefault((time.year, time.month), time)
    return set(newest_first[:KEEP_LATEST]) | set(days.values()) | set(months.values())


def prune(directory, now):
    found = backups(directory)
    keep = backups_to_keep(found, now)
    old = [path for time, path in found.items() if time not in keep]
    for path in old:
        path.unlink()
    if old:
        logging.info("Deleted %d old backups", len(old))


def latest(directory=None):
    found = backups(directory or default_directory())
    return found[max(found)] if found else None


def restore(path, directory=None, database_name=database.DATABASE):
    """Replaces the database with a backup, backing up the one it replaces first."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise SystemExit("{0} doesn't exist".format(path))

    if database.exists(database_name):
        with database.connect(database_name) as db:
            set_up = database.is_set_up(db)
        if set_up:
            take(directory, database_name)

    database.recreate(database_name)
    load(path, database_name)
    logging.info("Restored %s", path)


def load(path, database_name=database.DATABASE, pg_restore=None):
    # Ownership and grants are left out, as dumps from other servers have their own users
    server.run([pg_restore or server.program('pg_restore'), '--no-owner', '--no-acl', '--single-transaction',
                '--exit-on-error', '--dbname', database_name, path], env=server.environment(database_name))


def upgrade():
    """Moves the database to the newest installed Postgres, by dumping it and loading it into a new cluster."""
    server.require_initialised()
    old = server.cluster_version()
    new_bin = server.bin_dir()
    new = server.major_version(new_bin)
    if old >= new:
        logging.info("The database already uses Postgres %d", old)
        return

    with server.lock('start.lock'):
        if server.is_running():
            raise SystemExit("The database is in use. Close anything using it, or run `smarter-playlists db stop`, "
                             "then try again")

        old_data = server.data_dir().with_name('postgres-{0}.old'.format(old))
        if old_data.exists():
            raise SystemExit("{0} is in the way. Delete it, or move it somewhere else, then try again".format(old_data))

        with tempfile.TemporaryDirectory() as temp:
            dump_path = pathlib.Path(temp) / 'upgrade.dump'
            # The newer pg_dump, as Postgres recommends for upgrading
            server.start()
            try:
                dump(dump_path, pg_dump=new_bin / 'pg_dump')
            finally:
                server.stop()

            server.data_dir().rename(old_data)
            server.init(new_bin)
            server.start()
            try:
                database.create()
                load(dump_path)
            finally:
                server.stop()

    logging.info("Upgraded the database from Postgres %d to %d. The old one is in %s, and can be deleted", old, new,
                 old_data)
