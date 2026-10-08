import datetime
import json
import re
import types

import pytest

from conftest import UTC, make_track
from smarter_playlists import database, playlists


@pytest.fixture
def tracks(run_import):
    run_import(*[make_track(track_id='A00000000000000{0}'.format(n), play_count=n) for n in range(1, 6)])


@pytest.fixture
def no_builtin_playlists(query):
    query("DROP SCHEMA playlist CASCADE; CREATE SCHEMA playlist")


@pytest.fixture
def music(monkeypatch):
    """Records requests to sync-playlists.js instead of running it, replying as it would."""
    requests = []
    errors = {}

    def run(request):
        request = json.loads(request)
        requests.append(request)
        return json.dumps([
            {'playlist': playlist['name'], 'error': errors[playlist['name']]} if playlist['name'] in errors else
            {'playlist': playlist['name'], 'created': True, 'changed': True, 'tracks': len(playlist['trackIds']),
             'missing': [], 'dryRun': request['dryRun']}
            for playlist in request['playlists']])

    monkeypatch.setattr(playlists, 'run_sync_script', run)
    return types.SimpleNamespace(requests=requests, errors=errors)


def exported(requests):
    """The playlists sent to Music, as {name: [track ID, ...]}."""
    return {playlist['name']: playlist['trackIds'] for request in requests for playlist in request['playlists']}


class TestExport:

    def test_exports_every_playlist_in_one_request(self, database_name, query, tracks, no_builtin_playlists, music):
        query("""CREATE VIEW playlist."Most Played" AS
                 SELECT track_id, ROW_NUMBER() OVER (ORDER BY play_count DESC) AS position FROM track""")
        query("""CREATE VIEW playlist."Least Played" AS
                 SELECT track_id, ROW_NUMBER() OVER (ORDER BY play_count) AS position FROM track LIMIT 2""")
        query("CREATE VIEW public.not_a_playlist AS SELECT track_id FROM track")

        playlists.export_playlists(database_name)

        assert len(music.requests) == 1
        assert exported(music.requests) == {
            'Least Played': ['A000000000000001', 'A000000000000002'],
            'Most Played': ['A000000000000005', 'A000000000000004', 'A000000000000003', 'A000000000000002',
                            'A000000000000001'],
        }

    def test_exports_only_named_playlists(self, database_name, query, tracks, no_builtin_playlists, music):
        query("CREATE VIEW playlist.\"One\" AS SELECT track_id FROM track")
        query("CREATE VIEW playlist.\"Two\" AS SELECT track_id FROM track")

        playlists.export_playlists(database_name, ['Two'])

        assert list(exported(music.requests)) == ['Two']

    def test_refuses_unknown_playlists(self, database_name, music):
        with pytest.raises(SystemExit, match="No playlist named 'Nope'"):
            playlists.export_playlists(database_name, ['Nope'])

        assert music.requests == []

    def test_dry_run_is_passed_to_music(self, database_name, tracks, music):
        playlists.export_playlists(database_name, dry_run=True)

        assert [request['dryRun'] for request in music.requests] == [True]

    def test_carries_on_when_a_playlist_fails(self, database_name, query, tracks, no_builtin_playlists, music):
        query("CREATE VIEW playlist.\"Music\" AS SELECT track_id FROM track")
        query("CREATE VIEW playlist.\"Mine\" AS SELECT track_id FROM track")
        music.errors['Music'] = '"Music" is a smart playlist or folder and cannot be replaced'

        with pytest.raises(SystemExit, match='Failed to export 1 of 2 playlists'):
            playlists.export_playlists(database_name)

        assert list(exported(music.requests)) == ['Mine', 'Music']

    def test_reports_music_failures(self, database_name, tracks, monkeypatch):
        monkeypatch.setattr(playlists.subprocess, 'run', lambda command, **kwargs: playlists.subprocess.CompletedProcess(
            command, 1, stdout='', stderr='Not authorized to send Apple events to Music.'))

        with pytest.raises(SystemExit, match='Failed to update playlists in Music: Not authorized'):
            playlists.export_playlists(database_name)

    def test_gives_up_when_music_takes_too_long(self, database_name, tracks, monkeypatch, caplog):
        timeouts = []

        def run(command, timeout, **kwargs):
            timeouts.append(timeout)
            raise playlists.subprocess.TimeoutExpired(command, timeout)
        monkeypatch.setattr(playlists.subprocess, 'run', run)

        with pytest.raises(SystemExit):
            playlists.export_playlists(database_name)

        assert timeouts == [15 * 60]
        assert [record.levelname for record in caplog.records if 'within 15 minutes' in record.getMessage()] == [
            'ERROR']

    def test_exports_playlists_chosen_by_path(self, database_name, query, tracks, no_builtin_playlists, music):
        query("CREATE VIEW playlist.v AS SELECT f AS folder, 'Same' AS playlist, track_id "
              "FROM track, (VALUES ('A'), ('B')) AS folders (f)")

        playlists.export_playlists(database_name, ['B/Same'])

        assert [(playlist['folder'], playlist['name']) for playlist in music.requests[0]['playlists']] == [
            (['B'], 'Same')]

    def test_exports_every_playlist_with_a_chosen_name(self, database_name, query, tracks, no_builtin_playlists,
                                                       music):
        query("CREATE VIEW playlist.v AS SELECT f AS folder, 'Same' AS playlist, track_id "
              "FROM track, (VALUES ('A'), ('B')) AS folders (f)")

        playlists.export_playlists(database_name, ['Same'])

        assert [(playlist['folder'], playlist['name']) for playlist in music.requests[0]['playlists']] == [
            (['A'], 'Same'), (['B'], 'Same')]

    def test_sends_folders_and_how_to_recognise_own_playlists(self, database_name, tracks, music):
        playlists.export_playlists(database_name, ['All-Time Favourites'])

        assert music.requests[0]['descriptionPrefix'] == 'Made by Smarter Playlists'
        assert music.requests[0]['playlists'][0]['folder'] == ['Smarter Playlists']

    def test_playlists_say_where_they_come_from(self, database_name, tracks, music):
        playlists.export_playlists(database_name, ['All-Time Favourites'])

        [playlist] = music.requests[0]['playlists']
        assert playlist['description'] == (
            'Made by Smarter Playlists from the All-Time Favourites view. Changes made here will be overwritten. '
            'https://github.com/Stephan5/smarter-playlists')

    def test_requires_database_setup(self, empty_database, music):
        with pytest.raises(SystemExit, match='Run `smarter-playlists setup` first'):
            playlists.export_playlists(empty_database)


class TestFetchPlaylists:

    @pytest.fixture
    def fetch(self, database_name, tracks, no_builtin_playlists):
        def run():
            with database.connect(database_name) as db:
                return {name: playlist.track_ids for name, playlist in playlists.fetch_playlists(db).items()}
        return run

    def test_orders_by_position(self, query, fetch):
        query("""CREATE VIEW playlist.v AS
                 SELECT track_id, 6 - play_count AS position FROM track ORDER BY track_id""")

        assert fetch() == {'v': ['A000000000000005', 'A000000000000004', 'A000000000000003', 'A000000000000002',
                                 'A000000000000001']}

    def test_keeps_view_order_without_position(self, query, fetch):
        query("CREATE VIEW playlist.v AS SELECT track_id FROM track ORDER BY play_count DESC LIMIT 2")

        assert fetch() == {'v': ['A000000000000005', 'A000000000000004']}

    def test_keeps_first_position_of_duplicate_tracks(self, query, fetch):
        query("""CREATE VIEW playlist.v AS
                 SELECT * FROM (VALUES ('A000000000000002', 1), ('A000000000000001', 2), ('A000000000000002', 3),
                                       (NULL, 4)) AS v (track_id, position)""")

        assert fetch() == {'v': ['A000000000000002', 'A000000000000001']}

    def test_playlist_column_makes_a_playlist_per_value(self, query, fetch):
        query("""CREATE VIEW playlist.v AS
                 SELECT * FROM (VALUES ('Odd', 'A000000000000003', 2), ('Even', 'A000000000000002', 1),
                                       ('Odd', 'A000000000000001', 1), (NULL, 'A000000000000004', 1))
                                AS v (playlist, track_id, position)""")

        assert fetch() == {'Even': ['A000000000000002'], 'Odd': ['A000000000000001', 'A000000000000003']}

    def test_folder_column_puts_playlists_in_folders(self, query, fetch):
        query("""CREATE VIEW playlist.v AS
                 SELECT * FROM (VALUES ('Outer/Inner', 'Nested', 'A000000000000001'),
                                       ('/Outer//', 'Outer', 'A000000000000002'),
                                       (NULL, 'Top', 'A000000000000003'),
                                       ('Elsewhere', 'Nested', 'A000000000000004'))
                                AS v (folder, playlist, track_id)""")

        assert fetch() == {
            'Outer/Inner/Nested': ['A000000000000001'],
            'Outer/Outer': ['A000000000000002'],
            'Top': ['A000000000000003'],
            'Elsewhere/Nested': ['A000000000000004'],
        }

    def test_playlists_must_have_unique_names(self, query, fetch):
        query("CREATE VIEW playlist.a AS SELECT 'F' AS folder, 'Mine' AS playlist, track_id FROM track")
        query("CREATE VIEW playlist.b AS SELECT 'F' AS folder, 'Mine' AS playlist, track_id FROM track")

        with pytest.raises(SystemExit, match="Playlist 'F/Mine' is defined by both a and b"):
            fetch()

    def test_requires_a_track_id_column(self, query, fetch):
        query("CREATE VIEW playlist.v AS SELECT title FROM track")

        with pytest.raises(SystemExit, match="View 'v' has no track_id column"):
            fetch()


def at(year, month, day):
    return datetime.datetime(year, month, day, 12, tzinfo=UTC)


class TestBuiltinPlaylists:

    @pytest.fixture
    def plays(self, run_import, query):
        """Imports tracks, then records the given plays as {track number: [time, ...]}."""
        def record(plays_by_track, track_values=None):
            # Not to ignore any plays, unless a test says
            query("UPDATE setting SET history_start = '-infinity'")
            run_import(*[make_track(track_id='A{0:015X}'.format(n), album_id='C{0:015X}'.format(n),
                                    artist_id='B{0:015X}'.format(n), **(track_values or {}).get(n, {}))
                         for n in plays_by_track])
            for n, times in plays_by_track.items():
                for played_at in times:
                    query("INSERT INTO play (track_id, played_at, estimated) VALUES (%s, %s, FALSE)",
                          ['A{0:015X}'.format(n), played_at])
        return record

    def fetch(self, database_name, view):
        with database.connect(database_name) as db:
            return {playlist.path: playlist.track_ids for playlist in playlists.fetch_view(db, view)}

    def test_monthly_playlists_start_at_history_start(self, database_name, plays, query):
        plays({1: [at(2026, 9, 15), at(2026, 10, 2), at(2026, 11, 3), at(2027, 1, 20)],
               2: [at(2026, 10, 5), at(2026, 10, 6)]})
        query("UPDATE setting SET history_start = DATE '2026-10-06'")

        assert self.fetch(database_name, 'monthly') == {
            'Smarter Playlists/2026/October 2026': ['A000000000000002'],
            'Smarter Playlists/2026/November 2026': ['A000000000000001'],
            'Smarter Playlists/2027/January 2027': ['A000000000000001'],
        }

    def test_yearly_playlists_start_at_history_start(self, database_name, plays, query):
        plays({1: [at(2025, 6, 1), at(2026, 2, 1), at(2027, 3, 1)],
               2: [at(2026, 5, 1), at(2026, 6, 1)]})
        query("UPDATE setting SET history_start = DATE '2026-05-15'")

        assert self.fetch(database_name, 'yearly') == {
            'Smarter Playlists/2026/2026': ['A000000000000002'],
            'Smarter Playlists/2027/2027': ['A000000000000001'],
        }

    def test_history_start_applies_to_every_view_built_from_plays(self, database_name, plays, query):
        now = datetime.datetime.now(UTC)
        days_ago = lambda *days: [now - datetime.timedelta(days=day) for day in days]
        plays({1: days_ago(2, 3, 4)})
        query("UPDATE setting SET history_start = %s", [(now - datetime.timedelta(days=1)).date()])

        for view in ['Last Month', 'Rising', 'time_of_day', 'seasons', 'monthly', 'yearly']:
            assert self.fetch(database_name, view) == {}, view

    def test_history_start_is_the_day_it_starts_on(self, database_name, plays, query):
        plays({1: [at(2026, 10, 5), at(2026, 10, 6)], 2: [at(2026, 10, 5)]})
        query("UPDATE setting SET history_start = DATE '2026-10-06'")

        assert self.fetch(database_name, 'monthly') == {'Smarter Playlists/2026/October 2026': ['A000000000000001']}

    def test_monthly_playlists_limit_tracks_per_album(self, database_name, plays, query):
        plays({n: [at(2026, 10, day) for day in range(1, n + 2)] for n in range(1, 5)})
        query("UPDATE track SET album_id = 'C000000000000001'")

        assert self.fetch(database_name, 'monthly') == {'Smarter Playlists/2026/October 2026': ['A000000000000004', 'A000000000000003']}

    def test_last_month_is_the_last_30_days(self, database_name, plays):
        now = datetime.datetime.now(UTC)
        days_ago = lambda days: now - datetime.timedelta(days=days)
        plays({1: [days_ago(1)],
               2: [days_ago(5), days_ago(29)],
               3: [days_ago(31), days_ago(40), days_ago(45)]})

        assert self.fetch(database_name, 'Last Month') == {
            'Smarter Playlists/Last Month': ['A000000000000002', 'A000000000000001']}

    def test_rising_is_tracks_played_more_recently(self, database_name, plays):
        now = datetime.datetime.now(UTC)
        days_ago = lambda *days: [now - datetime.timedelta(days=day) for day in days]
        plays({1: days_ago(1, 2, 3),                    # 3 recent, none before: up 3
               2: days_ago(1, 2) + days_ago(40, 50, 60), # 2 recent, 3 before: down
               3: days_ago(1),                           # only played once recently
               4: days_ago(1, 2, 3, 4) + days_ago(100),  # 4 recent, 1 before: up 3, and more recent plays
               5: days_ago(1, 2) + days_ago(130, 140)})  # older plays are outside the 90 days before

        assert self.fetch(database_name, 'Rising') == {
            'Smarter Playlists/Rising': ['A000000000000004', 'A000000000000001', 'A000000000000005']}

    def test_new_and_unplayed_is_recent_tracks_without_plays(self, database_name, run_import):
        now = datetime.datetime.now(UTC)
        days_ago = lambda days: now - datetime.timedelta(days=days)
        run_import(make_track(track_id='A000000000000001', album_id='C000000000000001', play_count=0,
                              added_at=days_ago(30)),
                   make_track(track_id='A000000000000002', album_id='C000000000000002', play_count=0,
                              added_at=days_ago(3)),
                   make_track(track_id='A000000000000003', album_id='C000000000000003', play_count=1,
                              added_at=days_ago(3)),
                   make_track(track_id='A000000000000004', album_id='C000000000000004', play_count=0,
                              added_at=days_ago(90)))

        assert self.fetch(database_name, 'New and Unplayed') == {
            'Smarter Playlists/New and Unplayed': ['A000000000000002', 'A000000000000001']}

    def test_time_of_day_playlists_are_what_is_played_at_each_time(self, database_name, plays, query):
        query("ALTER DATABASE {0} SET timezone = 'UTC'".format(database_name))
        now = datetime.datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        hour = lambda hour, days_ago=1: (now - datetime.timedelta(days=days_ago)).replace(hour=hour)
        plays({1: [hour(5), hour(11), hour(8, 2)],
               2: [hour(12), hour(16)],
               3: [hour(17), hour(21)],
               4: [hour(22), hour(4)],
               5: [hour(23, 400)],  # over a year ago
               6: [hour(9)]})
        query("UPDATE play SET estimated = TRUE WHERE track_id = 'A000000000000006'")

        assert self.fetch(database_name, 'time_of_day') == {
            'Smarter Playlists/Time of Day/Morning': ['A000000000000001'],
            'Smarter Playlists/Time of Day/Afternoon': ['A000000000000002'],
            'Smarter Playlists/Time of Day/Evening': ['A000000000000003'],
            'Smarter Playlists/Time of Day/Late Night': ['A000000000000004'],
        }

    def test_seasons_are_what_is_played_in_each_season_of_any_year(self, database_name, plays, query):
        query("ALTER DATABASE {0} SET timezone = 'UTC'".format(database_name))
        plays({1: [at(2024, 12, 25), at(2026, 1, 5), at(2026, 2, 28)],
               2: [at(2025, 3, 1), at(2026, 5, 31)],
               3: [at(2025, 6, 1), at(2026, 8, 31)],
               4: [at(2025, 9, 1), at(2026, 11, 30)],
               5: [at(2025, 6, 15)]})
        query("UPDATE play SET estimated = TRUE WHERE track_id = 'A000000000000005'")

        assert self.fetch(database_name, 'seasons') == {
            'Smarter Playlists/Seasons/Winter': ['A000000000000001'],
            'Smarter Playlists/Seasons/Spring': ['A000000000000002'],
            'Smarter Playlists/Seasons/Summer': ['A000000000000003'],
            'Smarter Playlists/Seasons/Autumn': ['A000000000000004'],
        }

    def test_top_artists_are_playlists_of_their_top_tracks(self, database_name, run_import):
        big = [make_track(track_id='A1{0:014X}'.format(n), artist_id='B000000000000BIG', artist_name='Big',
                          album_id='C000000000000BIG', play_count=100 + n) for n in range(30)]
        small = [make_track(track_id='A2{0:014X}'.format(n), artist_id='B{0:015X}'.format(n),
                            artist_name='Small {0}'.format(n), album_id='C{0:015X}'.format(n), play_count=n)
                 for n in range(1, 22)]
        run_import(*big, *small)

        top_artists = self.fetch(database_name, 'top_artists')

        # Big, and the 9 most played of the 21 smaller artists
        assert len(top_artists) == 10
        assert 'Smarter Playlists/Top Artists/Small 12' not in top_artists
        assert top_artists['Smarter Playlists/Top Artists/Small 13'] == ['A2{0:014X}'.format(13)]
        assert top_artists['Smarter Playlists/Top Artists/Big'] == ['A1{0:014X}'.format(n) for n in range(29, 4, -1)]

    def test_monthly_playlists_skip_removed_and_playlist_only_tracks(self, database_name, plays, query):
        plays({1: [at(2026, 10, 1)], 2: [at(2026, 10, 1)], 3: [at(2026, 10, 1)]},
              {2: {'playlist_only': True}})
        query("UPDATE track SET removed_at = now() WHERE track_id = 'A000000000000003'")

        assert self.fetch(database_name, 'monthly') == {'Smarter Playlists/2026/October 2026': ['A000000000000001']}


@pytest.mark.parametrize('view', ['monthly', 'All-Time Favourites', 'say "hi"'])
def test_description_names_the_view(view):
    assert playlists.Playlist('Name', view, []).description.startswith(
        'Made by Smarter Playlists from the {0} view.'.format(view))


@pytest.mark.parametrize('result, dry_run, level, message', [
    ({'created': True, 'moved': False, 'changed': True}, True, 'INFO', "Would create playlist 'F/P' with 1 tracks"),
    ({'created': True, 'moved': False, 'changed': True}, False, 'INFO', "Created playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': True, 'changed': True}, True, 'INFO',
     "Would move and update playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': True, 'changed': True}, False, 'INFO', "Moved and updated playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': False, 'changed': True}, False, 'INFO', "Updated playlist 'F/P' with 1 tracks"),
    # Only with --verbose, as most playlists are unchanged on most runs
    ({'created': False, 'moved': False, 'changed': False}, False, 'DEBUG', "Playlist 'F/P' is already up to date"),
])
def test_reports_what_changed(database_name, query, tracks, no_builtin_playlists, monkeypatch, caplog, result,
                              dry_run, level, message):
    query("CREATE VIEW playlist.v AS SELECT 'F' AS folder, 'P' AS playlist, track_id FROM track LIMIT 1")
    monkeypatch.setattr(playlists, 'run_sync_script', lambda request: json.dumps(
        [dict(result, tracks=1, missing=[], dryRun=dry_run)]))
    caplog.set_level('DEBUG')

    playlists.export_playlists(database_name, dry_run=dry_run)

    *logged, summary = [(record.levelname, record.getMessage()) for record in caplog.records]
    assert logged == [
        ('INFO', "{0} 1 playlists in Music, which can take a minute...".format("Checking" if dry_run else "Updating")),
        (level, message)]
    changed = int(result['changed'])
    assert summary[0] == 'INFO'
    assert re.fullmatch(r"{0} 1 playlists in \d+\.\ds, {1} {2}".format(
        "Checked" if dry_run else "Updated", changed, "would change" if dry_run else "changed"), summary[1])


@pytest.mark.parametrize('time_zone, expected', [
    # 00:30 on 1 October in London is still 30 September in UTC and Los Angeles, which is before history starts
    ('Europe/London', {'Smarter Playlists/2026/October 2026': ['A000000000000001']}),
    ('UTC', {}),
    ('America/Los_Angeles', {}),
])
def test_months_follow_the_database_time_zone(database_name, run_import, query, monkeypatch, time_zone, expected):
    run_import(make_track())
    query("UPDATE setting SET history_start = DATE '2026-10-01'")
    query("INSERT INTO play (track_id, played_at, estimated) VALUES ('A000000000000001', %s, FALSE)",
          [datetime.datetime(2026, 9, 30, 23, 30, tzinfo=UTC)])
    monkeypatch.setenv('PGTZ', time_zone)

    with database.connect(database_name) as db:
        monthly = {playlist.path: playlist.track_ids for playlist in playlists.fetch_view(db, 'monthly')}

    assert monthly == expected
