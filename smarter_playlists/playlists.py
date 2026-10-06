import dataclasses
import importlib.resources
import json
import logging
import subprocess

from psycopg import sql

from . import database

PLAYLISTS_SCHEMA = 'playlist'

PROJECT_URL = 'https://github.com/Stephan5/smarter-playlists'

# Starts the description of every exported playlist, which is how playlists made by this project are recognised
DESCRIPTION_PREFIX = 'Made by Smarter Playlists'

SYNC_PLAYLISTS_SCRIPT = str(importlib.resources.files(__package__).joinpath('sync-playlists.js'))

# Updating many playlists takes Music a minute or two. Much longer and it's stuck, e.g. behind a dialog, and would
# otherwise hold up every scheduled run after this one.
SYNC_TIMEOUT_MINUTES = 15


@dataclasses.dataclass
class Playlist:
    name: str
    view: str
    track_ids: list
    # The folders the playlist is in, outermost first. Empty for the top level.
    folder: tuple = ()

    @property
    def path(self):
        return '/'.join(self.folder + (self.name,))

    @property
    def description(self):
        return ("{0} from the {1} view. Changes made here will be overwritten. {2}"
                .format(DESCRIPTION_PREFIX, self.view, PROJECT_URL))


class ExportFailed(SystemExit):
    """Some playlists couldn't be exported. Says how many were, for the run history."""

    def __init__(self, changed, failed, total):
        super().__init__("Failed to export {0} of {1} playlists".format(failed, total))
        self.changed = changed
        self.failed = failed


def export_playlists(database_name, names=(), dry_run=False):
    """Exports the playlists to Music, returning how many were (or with dry_run, would be) changed."""
    with database.connect(database_name) as db:
        database.require_set_up(db)
        playlists = fetch_playlists(db)

    if names:
        playlists = select_playlists(playlists, names)

    if not playlists:
        logging.warning("No playlists found in the %s schema", PLAYLISTS_SCHEMA)
        return 0

    # Music takes a while, a minute or more, to update many playlists
    logging.info("%s %d playlists in Music...", "Checking" if dry_run else "Updating", len(playlists))
    changed = failed = 0
    for playlist, result in zip(playlists.values(), sync_playlists(playlists, dry_run)):
        if 'error' in result:
            logging.error("Couldn't export playlist '%s': %s", playlist.path, result['error'])
            failed += 1
            continue

        for track_id in result['missing']:
            logging.warning("Track %s in '%s' was not found in the Music library", track_id, playlist.path)

        if not result['changed']:
            logging.debug("Playlist '%s' is already up to date", playlist.path)
        else:
            changed += 1
            if result['created']:
                action = 'Would create' if dry_run else 'Created'
            elif result['moved']:
                action = 'Would move and update' if dry_run else 'Moved and updated'
            else:
                action = 'Would update' if dry_run else 'Updated'
            logging.info("%s playlist '%s' with %d tracks", action, playlist.path, result['tracks'])

    if failed:
        raise ExportFailed(changed, failed, len(playlists))
    return changed


def select_playlists(playlists, names):
    """The playlists with the given names, or paths like "Smarter Playlists/2026/October 2026"."""
    selected = {path: playlist for path, playlist in playlists.items()
                if playlist.name in names or playlist.path in names}

    unknown = [name for name in names
               if not any(name in (playlist.name, playlist.path) for playlist in selected.values())]
    if unknown:
        raise SystemExit("No playlist named {0}".format(', '.join(map(repr, unknown))))

    return selected


def fetch_playlists(db):
    """Every playlist defined in the playlist schema, as {path: Playlist}.

    A view is one playlist named after the view, or many if it has a playlist column naming the playlist of each row.
    An optional folder column puts playlists in a folder, with / between nested folders.
    Tracks are ordered by the position column if there is one, otherwise as the view returns them.
    """
    views = db.execute("""
        SELECT c.relname
          FROM pg_class c
          JOIN pg_namespace n ON (n.oid = c.relnamespace)
         WHERE n.nspname = %s
           AND c.relkind IN ('v', 'm', 'r', 'p')
         ORDER BY c.relname
        """, [PLAYLISTS_SCHEMA]).fetchall()

    playlists = {}
    for (view,) in views:
        for playlist in fetch_view(db, view):
            if playlist.path in playlists:
                raise SystemExit("Playlist '{0}' is defined by both {1} and {2}".format(
                    playlist.path, playlists[playlist.path].view, view))
            playlists[playlist.path] = playlist

    return playlists


def fetch_view(db, view):
    cur = db.execute(sql.SQL("SELECT * FROM {0}").format(sql.Identifier(PLAYLISTS_SCHEMA, view)))
    columns = [column.name for column in cur.description]
    rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    if 'track_id' not in columns:
        raise SystemExit("View '{0}' has no track_id column".format(view))

    if 'position' in columns:
        rows.sort(key=lambda row: row['position'])

    track_ids = {}
    for row in rows:
        name = row.get('playlist', view)
        folder = tuple(part for part in (row.get('folder') or '').split('/') if part)
        if name is not None and row['track_id'] is not None:
            # A track can only appear once in a playlist, keeping its first position
            track_ids.setdefault((folder, name), {}).setdefault(row['track_id'])

    return [Playlist(name, view, list(ids), folder) for (folder, name), ids in track_ids.items()]


def sync_playlists(playlists, dry_run):
    """Updates the playlists in Music, returning what changed for each, in the same order."""
    request = {
        'dryRun': dry_run,
        'descriptionPrefix': DESCRIPTION_PREFIX,
        'playlists': [{'name': playlist.name, 'folder': list(playlist.folder), 'description': playlist.description,
                       'trackIds': playlist.track_ids}
                      for playlist in playlists.values()],
    }
    return json.loads(run_sync_script(json.dumps(request)))


def run_sync_script(request):
    try:
        process = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', SYNC_PLAYLISTS_SCRIPT],
                                 input=request, capture_output=True, text=True, timeout=SYNC_TIMEOUT_MINUTES * 60)
    except subprocess.TimeoutExpired:
        logging.error("Music didn't finish updating playlists within %d minutes, so gave up. Check Music isn't "
                      "showing a dialog. Any playlists left part-updated will be fixed by the next export",
                      SYNC_TIMEOUT_MINUTES)
        raise SystemExit(1)
    if process.returncode != 0:
        raise SystemExit("Failed to update playlists in Music: {0}".format(process.stderr.strip()))
    return process.stdout
