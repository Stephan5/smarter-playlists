"""A record of each `smarter-playlists run`, in the run table."""

import contextlib
import dataclasses
import logging
import signal

from . import database
from .library import plural

@dataclasses.dataclass
class Run:
    """How far a run got. Filled in as it goes."""
    plays_recorded: int = None
    plays_estimated: int = None
    playlists_changed: int = None
    playlists_failed: int = None


@contextlib.contextmanager
def recorded(scheduled, dry_run, database_name=database.DATABASE):
    """Records the run, and how it went, even if it fails. The database must be running."""
    run = Run()
    run_id = start(database_name, scheduled, dry_run)
    try:
        yield run
    except BaseException as error:
        finish(database_name, run_id, run, describe(error))
        raise
    finish(database_name, run_id, run, None)


def start(database_name, scheduled, dry_run):
    # In a transaction of its own, so it's kept whatever happens to the run
    with database.connect(database_name) as db:
        return db.execute("INSERT INTO run (started_at, scheduled, dry_run) VALUES (now(), %s, %s) RETURNING run_id",
                          [scheduled, dry_run]).fetchone()[0]


def finish(database_name, run_id, run, error):
    try:
        with database.connect(database_name) as db:
            db.execute("""
                UPDATE run
                   SET finished_at = now(),
                       plays_recorded = %s,
                       plays_estimated = %s,
                       playlists_changed = %s,
                       playlists_failed = %s,
                       error = %s
                 WHERE run_id = %s
                """, [run.plays_recorded, run.plays_estimated, run.playlists_changed, run.playlists_failed, error,
                      run_id])
    except Exception as failure:
        # Not to hide why the run failed, if it did
        logging.warning("Couldn't record how the run went: %s", failure)


def describe(error):
    """Why a run failed, in a few words."""
    if isinstance(error, SystemExit):
        if isinstance(error.code, str):
            return error.code
        if isinstance(error.code, int) and error.code > 128:
            return "Stopped by {0}".format(signal.Signals(error.code - 128).name)
        return "Exited with status {0}".format(error.code)
    if isinstance(error, KeyboardInterrupt):
        return "Interrupted"
    return "{0}: {1}".format(type(error).__name__, error)


def log_summary(database_name=database.DATABASE):
    """Logs how the last run went, and when the last successful one was if it wasn't."""
    with database.connect(database_name) as db:
        last = db.execute("""
            SELECT started_at, finished_at, scheduled, dry_run, plays_recorded, plays_estimated, playlists_changed,
                   error
              FROM run
             ORDER BY started_at DESC
             LIMIT 1
            """).fetchone()
        last_success = db.execute("""
            SELECT started_at
              FROM run
             WHERE finished_at IS NOT NULL
               AND error IS NULL
               AND NOT dry_run
             ORDER BY started_at DESC
             LIMIT 1
            """).fetchone()

    if not last:
        logging.info("No runs recorded yet")
        return

    started_at, finished_at, scheduled, dry_run, plays, estimated, changed, error = last
    run = "Last run{0}, {1} at {2},".format(" (dry run)" if dry_run else "", "scheduled" if scheduled else "by hand",
                                          local_time(started_at))
    if finished_at is None:
        logging.info("%s hasn't finished. It's either still running, or was stopped before it could", run)
    elif error is None:
        logging.info("%s recorded %s (%d estimated) and %s %s", run, plural(plays, 'new play'), estimated,
                     "would have changed" if dry_run else "changed", plural(changed, 'playlist'))
        return
    else:
        logging.info("%s failed: %s", run, error)

    if last_success:
        logging.info("Last successful run at %s", local_time(last_success[0]))
    else:
        logging.info("No successful runs yet")


def local_time(time):
    return time.astimezone().strftime('%Y-%m-%d %H:%M')
