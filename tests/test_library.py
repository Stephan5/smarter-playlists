import datetime
import types

import pytest

from conftest import UTC, make_track
from smarter_playlists import library

LAST_PLAYED = datetime.datetime(2026, 8, 8, 19, 18, 46, tzinfo=UTC)


def plays(query, track_id='A000000000000001'):
    return query("SELECT played_at, estimated FROM play WHERE track_id = %s ORDER BY played_at", [track_id])


def gaps(rows):
    # In UTC, as Python ignores the offset when subtracting two times in the same time zone
    times = [played_at.astimezone(UTC) for played_at, _ in rows]
    return {later - earlier for earlier, later in zip(times, times[1:])}


class TestTracks:

    def test_imports_tracks_artists_and_albums(self, run_import, query):
        run_import(make_track(year=2007, genre='Alternative', track_number=4))

        assert query("SELECT track_id, title, artist_id, album_id, genre, year, track_number FROM track") == [
            ('A000000000000001', 'Weird Fishes / Arpeggi', 'B000000000000001', 'C000000000000001', 'Alternative',
             2007, 4)]
        assert query("SELECT artist_id, name FROM artist") == [('B000000000000001', 'Radiohead')]
        assert query("SELECT album_id, title, album_artist, compilation, year FROM album") == [
            ('C000000000000001', 'In Rainbows', 'Radiohead', False, 2007)]

    def test_updates_tracks_already_imported(self, run_import, query):
        run_import(make_track(title='Weird Fishes', play_count=1, last_played_at=LAST_PLAYED))
        run_import(make_track(title='Weird Fishes / Arpeggi', play_count=2,
                              last_played_at=LAST_PLAYED + datetime.timedelta(days=1)))

        assert query("SELECT title, play_count, last_played_at FROM track") == [
            ('Weird Fishes / Arpeggi', 2, LAST_PLAYED + datetime.timedelta(days=1))]

    def test_uses_most_common_spelling_of_artist_and_album(self, run_import, query):
        run_import(make_track(track_id='A000000000000001', artist_name='Band of Skulls', album_title='Sweet Sour'),
                   make_track(track_id='A000000000000002', artist_name='Band Of Skulls', album_title='Sweet Sour'),
                   make_track(track_id='A000000000000003', artist_name='Band of Skulls', album_title='Sweet sour'))

        assert query("SELECT name FROM artist") == [('Band of Skulls',)]
        assert query("SELECT title FROM album") == [('Sweet Sour',)]

    def test_album_artist_falls_back_to_most_common_track_artist(self, run_import, query):
        run_import(make_track(track_id='A000000000000001', album_id='C000000000000001', artist_id='B000000000000001',
                              artist_name='Gwen Stefani', album_artist=None),
                   make_track(track_id='A000000000000002', album_id='C000000000000001', artist_id='B000000000000002',
                              artist_name='No Doubt', album_artist=None),
                   make_track(track_id='A000000000000003', album_id='C000000000000001', artist_id='B000000000000002',
                              artist_name='No Doubt', album_artist=None),
                   make_track(track_id='A000000000000004', album_id='C000000000000002',
                              album_artist='Various Artists', compilation=True))

        assert query("SELECT album_id, album_artist, compilation FROM album ORDER BY album_id") == [
            ('C000000000000001', 'No Doubt', False),
            ('C000000000000002', 'Various Artists', True)]

    def test_album_year_is_latest_track_year(self, run_import, query):
        run_import(make_track(track_id='A000000000000001', year=1995),
                   make_track(track_id='A000000000000002', year=2009),
                   make_track(track_id='A000000000000003', year=None))

        assert query("SELECT year FROM album") == [(2009,)]

    def test_marks_tracks_no_longer_in_library_as_removed(self, run_import, query):
        played = make_track(track_id='A000000000000002', play_count=2, last_played_at=LAST_PLAYED)
        run_import(make_track(track_id='A000000000000001'), played)
        run_import(make_track(track_id='A000000000000001'))

        assert query("SELECT track_id FROM track WHERE removed_at IS NOT NULL") == [('A000000000000002',)]
        assert len(plays(query, 'A000000000000002')) == 2

    def test_tracks_added_back_are_no_longer_removed(self, run_import, query):
        run_import(make_track(track_id='A000000000000001'), make_track(track_id='A000000000000002'))
        run_import(make_track(track_id='A000000000000001'))
        run_import(make_track(track_id='A000000000000001'), make_track(track_id='A000000000000002'))

        assert query("SELECT count(*) FROM track WHERE removed_at IS NOT NULL") == [(0,)]

    def test_refuses_an_empty_library(self, run_import, query):
        run_import(make_track())

        with pytest.raises(SystemExit, match='No songs found'):
            run_import()

        assert query("SELECT removed_at FROM track") == [(None,)]

    def test_requires_database_setup(self, empty_database, monkeypatch):
        monkeypatch.setattr(library, 'read_library', lambda: [make_track()])

        with pytest.raises(SystemExit, match='Run `smarter-playlists setup` first'):
            library.import_library(empty_database)


class TestPlays:

    def test_first_import_spreads_plays_evenly_from_2010(self, run_import, query):
        run_import(make_track(play_count=4, last_played_at=LAST_PLAYED))

        rows = plays(query)
        assert [estimated for _, estimated in rows] == [True, True, True, False]
        assert rows[-1][0] == LAST_PLAYED
        gap = (LAST_PLAYED - library.HISTORY_START) / 4
        assert rows[0][0] == library.HISTORY_START + gap
        assert gaps(rows) == {gap}

    def test_single_play_is_recorded_at_last_played_time(self, run_import, query):
        run_import(make_track(play_count=1, last_played_at=LAST_PLAYED))

        assert plays(query) == [(LAST_PLAYED, False)]

    def test_unplayed_tracks_have_no_plays(self, run_import, query):
        run_import(make_track(play_count=0, last_played_at=None))

        assert plays(query) == []

    def test_plays_without_last_played_time_are_not_recorded(self, run_import, query):
        run_import(make_track(play_count=3, last_played_at=None))

        assert plays(query) == []

    def test_reimporting_records_nothing_new(self, run_import, query):
        track = make_track(play_count=5, last_played_at=LAST_PLAYED)
        run_import(track)
        before = plays(query)
        run_import(track)

        assert plays(query) == before

    def test_new_plays_are_spread_since_the_previous_play(self, run_import, query):
        run_import(make_track(play_count=2, last_played_at=LAST_PLAYED))
        later = LAST_PLAYED + datetime.timedelta(days=4)
        run_import(make_track(play_count=6, last_played_at=later))

        rows = plays(query)
        assert len(rows) == 6
        new = [row for row in rows if row[0] > LAST_PLAYED]
        assert new == [(LAST_PLAYED + datetime.timedelta(days=day), True) for day in (1, 2, 3)] + [(later, False)]

    def test_a_new_play_with_the_same_count_is_not_recorded(self, run_import, query):
        # The library reports last played times an hour out after the clocks change
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED))
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED + datetime.timedelta(hours=1)))

        assert len(plays(query)) == 3
        assert plays(query)[-1] == (LAST_PLAYED, False)

    def test_plays_are_never_removed(self, run_import, query):
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED))
        run_import(make_track(play_count=1, last_played_at=LAST_PLAYED))

        assert len(plays(query)) == 3

    def test_every_track_has_a_play_per_play_count(self, run_import, query):
        run_import(*[make_track(track_id='A{0:015X}'.format(n), play_count=n,
                                last_played_at=LAST_PLAYED - datetime.timedelta(days=n)) for n in range(1, 30)])

        assert query("""
            SELECT count(*)
              FROM track t
             WHERE play_count <> (SELECT count(*) FROM play p WHERE p.track_id = t.track_id)
            """) == [(0,)]


class TestPlayLog:

    @pytest.fixture
    def messages(self, caplog):
        caplog.set_level('DEBUG')
        return lambda level='INFO': [record.getMessage() for record in caplog.records
                                     if record.levelname == level and record.getMessage().startswith(('Played',
                                                                                                      'Recorded'))]

    def local(self, time, date=True):
        return time.astimezone().strftime('%Y-%m-%d %H:%M' if date else '%H:%M')

    def test_logs_each_track_played(self, run_import, messages):
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED),
                   make_track(track_id='A000000000000002', title='Reckoner', play_count=1,
                              last_played_at=LAST_PLAYED - datetime.timedelta(hours=1)))

        assert messages() == [
            "Played 'Reckoner' by Radiohead at {0}".format(self.local(LAST_PLAYED - datetime.timedelta(hours=1))),
            "Played 'Weird Fishes / Arpeggi' by Radiohead at {0} (+2 estimated)".format(self.local(LAST_PLAYED)),
            "Recorded 4 new plays (2 estimated) of 2 tracks",
        ]

    def test_counts_every_track_s_estimated_plays(self, run_import, messages):
        # The track played last has none estimated
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED - datetime.timedelta(hours=1)),
                   make_track(track_id='A000000000000002', title='Reckoner', play_count=1, last_played_at=LAST_PLAYED))

        assert messages()[-1] == "Recorded 4 new plays (2 estimated) of 2 tracks"

    def test_leaves_out_the_date_for_plays_today(self, run_import, messages):
        now = datetime.datetime.now(UTC).replace(microsecond=0)
        run_import(make_track(play_count=1, last_played_at=now))

        assert messages()[0] == "Played 'Weird Fishes / Arpeggi' by Radiohead at {0}".format(
            self.local(now, date=False))

    def test_logs_plays_only_estimated(self, run_import, query, messages):
        run_import(make_track(play_count=1, last_played_at=LAST_PLAYED))
        # Two more plays, but the same last play, so there's nothing observed to log a time for
        run_import(make_track(play_count=3, last_played_at=LAST_PLAYED))

        assert messages()[-2:] == ["Played 'Weird Fishes / Arpeggi' by Radiohead 2 times (estimated)",
                                   "Recorded 2 new plays (2 estimated) of 1 track"]

    def test_logs_when_nothing_was_played(self, run_import, messages, caplog):
        run_import(make_track())

        assert "No new plays" in caplog.messages

    def test_only_logs_how_many_when_too_many_tracks_were_played(self, run_import, messages, monkeypatch):
        monkeypatch.setattr(library, 'MAX_TRACKS_LOGGED', 1)
        run_import(make_track(play_count=1, last_played_at=LAST_PLAYED),
                   make_track(track_id='A000000000000002', title='Reckoner', play_count=1, last_played_at=LAST_PLAYED))

        assert messages() == ["Recorded 2 new plays (0 estimated) of 2 tracks. Use --verbose to list them"]
        assert len(messages('DEBUG')) == 2


class FakeObject:
    """Stands in for iTunesLibrary objects, whose properties are all methods."""

    def __init__(self, **values):
        self._values = values

    def __getattr__(self, name):
        try:
            value = self._values[name]
        except KeyError:
            raise AttributeError(name)
        return lambda: value


def fake_date(when):
    return FakeObject(timeIntervalSince1970=when.timestamp())


def fake_item(**values):
    item = {
        'mediaKind': 2,
        'persistentID': 0xC358A93CBB48422E,
        'title': 'Weird Fishes / Arpeggi',
        'artist': FakeObject(persistentID=0x5E8250F5D07FE2B4, name='Radiohead'),
        'album': FakeObject(persistentID=0x8EC2B0B8B0D3F43B, title='In Rainbows', albumArtist='Radiohead',
                            isCompilation=False, discNumber=1),
        'genre': 'Alternative',
        'year': 2007,
        'trackNumber': 4,
        'totalTime': 318187,
        'beatsPerMinute': 76,
        'playCount': 196,
        'skipCount': 8,
        'lastPlayedDate': fake_date(LAST_PLAYED),
        'skipDate': None,
        'addedDate': fake_date(datetime.datetime(2019, 8, 13, 21, 14, 28, tzinfo=UTC)),
        'isPlaylistOnly': False,
    }
    item.update(values)
    return FakeObject(**item)


@pytest.fixture
def fake_library(monkeypatch):
    def use(*items):
        music_library = FakeObject(allMediaItems=list(items))
        monkeypatch.setattr(library, 'iTunesLibrary', types.SimpleNamespace(
            ITLibrary=types.SimpleNamespace(libraryWithAPIVersion_error_=lambda version, error: (music_library, None)),
            ITLibMediaItemMediaKindSong=2))
    return use


def read_library_as_dicts():
    columns = [column for column, _ in library.LIBRARY_COLUMNS]
    return [dict(zip(columns, track)) for track in library.read_library()]


class TestReadLibrary:

    def test_reads_songs(self, fake_library):
        fake_library(fake_item())

        assert read_library_as_dicts() == [{
            'track_id': 'C358A93CBB48422E',
            'title': 'Weird Fishes / Arpeggi',
            'artist_id': '5E8250F5D07FE2B4',
            'artist_name': 'Radiohead',
            'album_id': '8EC2B0B8B0D3F43B',
            'album_title': 'In Rainbows',
            'album_artist': 'Radiohead',
            'compilation': False,
            'genre': 'Alternative',
            'year': 2007,
            'disc_number': 1,
            'track_number': 4,
            'duration_ms': 318187,
            'bpm': 76,
            'play_count': 196,
            'skip_count': 8,
            'last_played_at': LAST_PLAYED,
            'last_skipped_at': None,
            'added_at': datetime.datetime(2019, 8, 13, 21, 14, 28, tzinfo=UTC),
            'playlist_only': False,
        }]

    def test_skips_everything_but_songs(self, fake_library):
        podcast, music_video = 4, 7
        fake_library(fake_item(mediaKind=podcast), fake_item(mediaKind=music_video))

        assert read_library_as_dicts() == []

    def test_fills_in_missing_values(self, fake_library):
        fake_library(fake_item(title=None, genre=None, year=0, trackNumber=0, beatsPerMinute=0,
                               album=FakeObject(persistentID=1, title=None, albumArtist=None, isCompilation=False,
                                                discNumber=0)))

        [track] = read_library_as_dicts()
        assert (track['title'], track['genre'], track['year'], track['track_number'], track['bpm']) == (
            '', '', None, 1, None)
        assert (track['album_title'], track['album_artist'], track['disc_number']) == ('', None, 1)

    def test_never_played_tracks_have_no_last_played_time(self, fake_library):
        fake_library(fake_item(playCount=0, lastPlayedDate=fake_date(datetime.datetime(1904, 1, 1, tzinfo=UTC))))

        assert read_library_as_dicts()[0]['last_played_at'] is None

    @pytest.mark.parametrize('persistent_id, expected', [
        (0x1, '0000000000000001'),
        (0xC358A93CBB48422E, 'C358A93CBB48422E'),
        (-1, 'FFFFFFFFFFFFFFFF'),
    ])
    def test_formats_persistent_ids_as_music_does(self, fake_library, persistent_id, expected):
        fake_library(fake_item(persistentID=persistent_id))

        assert read_library_as_dicts()[0]['track_id'] == expected

    def test_reports_reading_needs_macos(self, monkeypatch):
        monkeypatch.setattr(library, 'iTunesLibrary', None)

        with pytest.raises(SystemExit, match='needs macOS'):
            library.read_library()

    def test_reports_an_unreadable_library(self, monkeypatch):
        monkeypatch.setattr(library, 'iTunesLibrary', types.SimpleNamespace(
            ITLibrary=types.SimpleNamespace(libraryWithAPIVersion_error_=lambda version, error: (None, 'denied'))))

        with pytest.raises(SystemExit, match='Unable to read the Music library: denied'):
            library.read_library()
