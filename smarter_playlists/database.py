import importlib.resources
import logging

import psycopg

DEFAULT_DATABASE = 'music'


def connect(database_name):
    # Host, port, user and password come from the standard PG* environment variables, or libpq's defaults
    return psycopg.connect(dbname=database_name)


def is_set_up(db):
    return db.execute("SELECT to_regclass('track')").fetchone()[0] is not None


def require_set_up(db):
    if not is_set_up(db):
        raise SystemExit("The database isn't set up yet. Run `smarter-playlists setup` first")


def set_up(database_name):
    with connect(database_name) as db:
        if is_set_up(db):
            raise SystemExit("The database is already set up")

        db.execute(read_sql('schema.sql'))
        db.execute(read_sql('playlists.sql'))

    logging.info("Database set up. Run `smarter-playlists import` to import the Music library")


def read_sql(file_name):
    return importlib.resources.files(__package__).joinpath(file_name).read_text()
