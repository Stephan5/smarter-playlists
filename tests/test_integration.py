"""Read-only checks against the real Music library and app. Run with: pytest --integration"""

import re

import pytest

from smarter_playlists import library, playlists

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def music_library():
    columns = [column for column, _ in library.LIBRARY_COLUMNS]
    return [dict(zip(columns, track)) for track in library.read_library()]


def test_reads_the_music_library(music_library):
    assert music_library
    for track in music_library:
        assert re.fullmatch('[0-9A-F]{16}', track['track_id'])
        assert re.fullmatch('[0-9A-F]{16}', track['artist_id'])
        assert re.fullmatch('[0-9A-F]{16}', track['album_id'])


def test_music_app_finds_library_tracks_by_persistent_id(music_library):
    track_ids = [track['track_id'] for track in music_library if not track['playlist_only']][:5]
    unknown = 'FFFFFFFFFFFFFFFF'
    playlist = playlists.Playlist('Smarter Playlists Integration Test', 'test', track_ids + [unknown])

    # A dry run never changes the library
    results = playlists.sync_playlists({playlist.name: playlist}, dry_run=True)

    assert results == [{
        'created': True,
        'moved': False,
        'changed': True,
        'tracks': len(track_ids),
        'missing': [unknown],
        'dryRun': True,
    }]


def test_smart_playlists_are_never_replaced():
    playlist = playlists.Playlist('Music', 'test', [])

    [result] = playlists.sync_playlists({playlist.name: playlist}, dry_run=True)

    assert result == {'error': '"Music" is a smart playlist or folder and cannot be replaced'}


def test_playlists_in_folders_that_dont_exist_yet_would_be_created():
    playlist = playlists.Playlist('Nested', 'test', [], ('Smarter Playlists Integration Test', 'Inner'))

    [result] = playlists.sync_playlists({playlist.path: playlist}, dry_run=True)

    assert result == {'created': True, 'moved': False, 'changed': True, 'tracks': 0, 'missing': [], 'dryRun': True}
