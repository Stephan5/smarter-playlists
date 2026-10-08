import importlib.resources
import logging
import re

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
    if pending_migrations(db):
        raise SystemExit("The database needs migrating. Run `smarter-playlists migrate`")


def set_up(database_name=DATABASE):
    with connect(database_name) as db:
        if is_set_up(db):
            raise SystemExit("The database is already set up")

        for migration in pending_migrations(db):
            apply_migration(db, migration)
        db.execute(read_sql('playlists.sql'))

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


def applied_migrations(db):
    """The versions of the migrations the database already has. A database from before migrations were recorded has the
    first, as that's the schema it was set up with."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migration (
            version    INT NOT NULL PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    applied = {version for (version,) in db.execute("SELECT version FROM schema_migration")}
    if not applied and is_set_up(db):
        first = migrations()[0]
        db.execute("INSERT INTO schema_migration (version, name) VALUES (%s, %s)", first[:2])
        applied = {first[0]}
    return applied


def pending_migrations(db):
    """The migrations the database doesn't have yet, oldest first."""
    available = migrations()
    applied = applied_migrations(db)
    unknown = applied - {version for version, _, _ in available}
    if unknown:
        raise SystemExit("The database has migration {0}, which this version of Smarter Playlists doesn't know about. "
                         "Update Smarter Playlists".format(max(unknown)))
    return [migration for migration in available if migration[0] not in applied]


def apply_migration(db, migration):
    """Applies a migration, and records that it has been. Nothing is kept until the transaction is committed."""
    version, name, sql_text = migration
    db.execute(sql_text)
    db.execute("INSERT INTO schema_migration (version, name) VALUES (%s, %s)", [version, name])


def migrate(database_name=DATABASE, before=None):
    """Brings the database up to date, each migration in a transaction of its own, so it's all or nothing. Calls
    before, if given, first if there's anything to apply. Returns how many migrations were applied."""
    with connect(database_name) as db:
        if not is_set_up(db):
            raise SystemExit("The database isn't set up yet. Run `smarter-playlists setup` first")
        pending = pending_migrations(db)
        if pending and before:
            before()
        for migration in pending:
            apply_migration(db, migration)
            db.commit()
            logging.info("Applied migration %s", migration[1])
    return len(pending)


def read_sql(file_name):
    return importlib.resources.files(__package__).joinpath(file_name).read_text()
