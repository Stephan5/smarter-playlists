import importlib.resources
import logging
import re
import zlib

import psycopg
from psycopg import sql

from . import server

DATABASE = 'music'


def connect(database_name=DATABASE, **kwargs):
    """Connects to a database on smarter-playlists' own server, which must be running."""
    return psycopg.connect(**server.connection(database_name), **kwargs)


def exists(database_name=DATABASE):
    with connect('postgres') as admin:
        return admin.execute("SELECT EXISTS (SELECT FROM pg_database WHERE datname = %s)",
                             [database_name]).fetchone()[0]


def create(database_name=DATABASE):
    if not exists(database_name):
        with connect('postgres', autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {0}").format(sql.Identifier(database_name)))


def recreate(database_name=DATABASE):
    """Drops the database, if there is one, and creates it again, empty."""
    with connect('postgres', autocommit=True) as admin:
        name = sql.Identifier(database_name)
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {0} WITH (FORCE)").format(name))
        admin.execute(sql.SQL("CREATE DATABASE {0}").format(name))


def is_set_up(db):
    return db.execute("SELECT to_regclass('track')").fetchone()[0] is not None


def require_set_up(db):
    """Refuses to go on unless the database is set up and has every migration, as the code expects the newest schema."""
    if not is_set_up(db):
        raise SystemExit("The database isn't set up yet. Run `smarter-playlists setup` first")
    if pending_migrations(db) or pending_repeatables(db):
        raise SystemExit("The database needs migrating. Run `smarter-playlists migrate`")


def set_up(database_name=DATABASE):
    with connect(database_name) as db:
        if is_set_up(db):
            raise SystemExit("The database is already set up")

        for migration in pending_migrations(db):
            apply_migration(db, migration)
        for repeatable in pending_repeatables(db):
            apply_repeatable(db, repeatable)

    logging.info("Database set up. Run `smarter-playlists import` to import the Music library")


MIGRATION_NAME = re.compile(r'(\d+)__(\w+)\.sql')


def migrations():
    """The migrations that come with this version, as (version, name, sql), oldest first. A migration is a file in the
    migrations directory named like 02__AddSomething.sql, applied in order of the number."""
    found = {}
    for path in importlib.resources.files(__package__).joinpath('migrations').iterdir():
        match = MIGRATION_NAME.fullmatch(path.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in found:
            raise SystemExit("Migrations {0} and {1} have the same number".format(found[version][1], path.name))
        found[version] = (version, path.name.removesuffix('.sql'), path.read_text())
    return [found[version] for version in sorted(found)]


REPEATABLE_NAME = re.compile(r'R__\w+\.sql')


def repeatables():
    """The repeatable migrations that come with this version, as (name, sql), by name. A repeatable migration is a file
    in the migrations directory named like R__Something.sql. Unlike the numbered ones it's applied again whenever its
    contents change, after all the numbered ones, so it suits things that are dropped and recreated, like views."""
    found = [(path.name.removesuffix('.sql'), path.read_text())
             for path in importlib.resources.files(__package__).joinpath('migrations').iterdir()
             if REPEATABLE_NAME.fullmatch(path.name)]
    return sorted(found)


def checksum(sql_text):
    """Enough to tell that a migration has been edited, which isn't security. A CRC rather than hashlib, whose hashes
    aren't in the executable (see the spec)."""
    return '{0:08x}'.format(zlib.crc32(sql_text.encode()))


def recorded_migrations(db):
    """What has been applied, as {name: (version, checksum)}, version being None for a repeatable migration. A
    database from before migrations were recorded has the first, as that's the schema it was set up with."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migration (
            name       TEXT NOT NULL PRIMARY KEY,
            version    INTEGER UNIQUE, -- NULL for repeatables
            checksum   TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    recorded = {name: (version, sum_) for name, version, sum_
                in db.execute("SELECT name, version, checksum FROM schema_migration")}
    if not recorded and is_set_up(db):
        version, name, sql_text = migrations()[0]
        record(db, name, version, sql_text)
        recorded = {name: (version, checksum(sql_text))}
    return recorded


def record(db, name, version, sql_text):
    db.execute("""
        INSERT INTO schema_migration (name, version, checksum) VALUES (%(name)s, %(version)s, %(checksum)s)
            ON CONFLICT (name) DO UPDATE SET checksum = excluded.checksum, applied_at = now()
        """, {'name': name, 'version': version, 'checksum': checksum(sql_text)})


def pending_migrations(db):
    """The migrations the database doesn't have yet, oldest first. Refuses to go on if one that has been applied has
    changed since, as the database is then not what the migrations say it is."""
    available = migrations()
    recorded = recorded_migrations(db)
    applied = {version: (name, sum_) for name, (version, sum_) in recorded.items() if version is not None}
    unknown = set(applied) - {version for version, _, _ in available}
    if unknown:
        raise SystemExit("The database has migration {0}, which this version of Smarter Playlists doesn't know about. "
                         "Update Smarter Playlists".format(max(unknown)))
    for version, name, sql_text in available:
        if version in applied and applied[version][1] != checksum(sql_text):
            raise SystemExit("Migration {0} has changed since it was applied to the database. Migrations are never "
                             "edited once released: put the change in a new one".format(name))
    return [migration for migration in available if migration[0] not in applied]


def apply_migration(db, migration):
    """Applies a migration, and records that it has been. Nothing is kept until the transaction is committed."""
    version, name, sql_text = migration
    db.execute(sql_text)
    record(db, name, version, sql_text)


def pending_repeatables(db):
    """The repeatable migrations that have never been applied, or have changed since. Needs the numbered ones to have
    been applied first."""
    recorded = recorded_migrations(db)
    return [(name, sql_text) for name, sql_text in repeatables()
            if name not in recorded or recorded[name][1] != checksum(sql_text)]


def apply_repeatable(db, repeatable):
    """Applies a repeatable migration, and records its checksum. Nothing is kept until the transaction is committed."""
    name, sql_text = repeatable
    db.execute(sql_text)
    record(db, name, None, sql_text)


def migrate(database_name=DATABASE, before=None):
    """Brings the database up to date, each migration in a transaction of its own, so it's all or nothing. Calls
    before, if given, first if there's anything to apply. Returns how many migrations were applied."""
    with connect(database_name) as db:
        if not is_set_up(db):
            raise SystemExit("The database isn't set up yet. Run `smarter-playlists setup` first")
        pending = pending_migrations(db)
        if before and (pending or pending_repeatables(db)):
            before()
        for migration in pending:
            apply_migration(db, migration)
            db.commit()
            logging.info("Applied migration %s", migration[1])
        repeated = pending_repeatables(db)
        for repeatable in repeated:
            apply_repeatable(db, repeatable)
            db.commit()
            logging.info("Applied repeatable migration %s", repeatable[0])
    return len(pending) + len(repeated)


def read_sql(file_name):
    return importlib.resources.files(__package__).joinpath(file_name).read_text()
