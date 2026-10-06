"""Exports to the real Music app, in a test folder that's deleted afterwards. Run with: pytest --write-music"""

import subprocess
import time

import pytest

from smarter_playlists import library, playlists

pytestmark = pytest.mark.writes_music

ROOT = 'Smarter Playlists Integration Test'

SETTLE_SECONDS = 60

# Deletes everything the tests make, all named distinctively: playlists first, then folders, innermost first
DELETE_TEST_ITEMS = """
function run() {
  const Music = Application('Music');
  const isTest = name => name === '%s' || name.startsWith('SPIT ');
  const depth = playlist => { let n = 0; try { for (;;) { playlist = playlist.parent(); n++; } } catch (e) { return n; } };
  const ids = Music.playlists.id(), names = Music.playlists.name(), classes = Music.playlists.class();
  const items = ids.map((id, i) => ({ playlist: Music.playlists.byId(id), name: names[i], folder: classes[i] === 'folderPlaylist' }))
                   .filter(item => isTest(item.name));
  items.forEach(item => { item.depth = depth(item.playlist); });
  items.sort((a, b) => (a.folder - b.folder) || (b.depth - a.depth));
  items.forEach(item => { try { Music.delete(item.playlist); } catch (e) {} });
  return items.length;
}
""" % ROOT


def delete_test_items():
    subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', DELETE_TEST_ITEMS], capture_output=True, text=True,
                   check=True)


@pytest.fixture
def track_id():
    return next(track[0] for track in library.read_library() if not track[-1])


@pytest.fixture
def clean_music():
    delete_test_items()
    yield
    delete_test_items()


def sync(*playlist_list, dry_run=False):
    return playlists.sync_playlists({playlist.path: playlist for playlist in playlist_list}, dry_run)


def test_playlists_stay_in_the_folders_they_are_made_and_moved_into(track_id, clean_music):
    nested = playlists.Playlist('SPIT Nested', 'test', [track_id], (ROOT, 'SPIT Inner', 'SPIT Deeper'))
    moving = playlists.Playlist('SPIT Moving', 'test', [track_id], (ROOT, 'SPIT Before'))
    moved = playlists.Playlist('SPIT Moving', 'test', [track_id], (ROOT, 'SPIT After'))

    # iCloud Music Library puts anything moved straight after it was made back where it was made, within seconds. So
    # wait after each change to see that it sticks, including before moving, as exports are hours apart.
    assert [result['created'] for result in sync(nested, moving)] == [True, True]
    time.sleep(SETTLE_SECONDS)
    assert [result['moved'] for result in sync(moved)] == [True]
    time.sleep(SETTLE_SECONDS)

    assert [result['changed'] for result in sync(nested, moved, dry_run=True)] == [False, False]
