import importlib.resources
import logging

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
    if not is_set_up(db):
        raise SystemExit("The database isn't set up yet. Run `smarter-playlists setup` first")


def set_up(database_name=DATABASE):
    with connect(database_name) as db:
        if is_set_up(db):
            raise SystemExit("The database is already set up")

        db.execute(read_sql('schema.sql'))
        db.execute(read_sql('playlists.sql'))

    logging.info("Database set up. Run `smarter-playlists import` to import the Music library")


def read_sql(file_name):
    return importlib.resources.files(__package__).joinpath(file_name).read_text()
