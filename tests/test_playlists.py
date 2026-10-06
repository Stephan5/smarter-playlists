import datetime
import json
import types

import pytest

from conftest import UTC, make_track
from smarter_playlists import database, playlists


@pytest.fixture
def tracks(run_import):
    run_import(*[make_track(track_id='A00000000000000{0}'.format(n), play_count=n) for n in range(1, 6)])


@pytest.fixture
def no_builtin_playlists(query):
    query("""DROP VIEW playlists.monthly, playlists.yearly, playlists."All-Time Favourites",
                       playlists."Forgotten Favourites" """)


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
        query("""CREATE VIEW playlists."Most Played" AS
                 SELECT track_id, ROW_NUMBER() OVER (ORDER BY play_count DESC) AS position FROM track""")
        query("""CREATE VIEW playlists."Least Played" AS
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
        query("CREATE VIEW playlists.\"One\" AS SELECT track_id FROM track")
        query("CREATE VIEW playlists.\"Two\" AS SELECT track_id FROM track")

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
        query("CREATE VIEW playlists.\"Music\" AS SELECT track_id FROM track")
        query("CREATE VIEW playlists.\"Mine\" AS SELECT track_id FROM track")
        music.errors['Music'] = '"Music" is a smart playlist or folder and cannot be replaced'

        with pytest.raises(SystemExit, match='Failed to export 1 of 2 playlists'):
            playlists.export_playlists(database_name)

        assert list(exported(music.requests)) == ['Mine', 'Music']

    def test_reports_music_failures(self, database_name, tracks, monkeypatch):
        monkeypatch.setattr(playlists.subprocess, 'run', lambda command, **kwargs: playlists.subprocess.CompletedProcess(
            command, 1, stdout='', stderr='Not authorized to send Apple events to Music.'))

        with pytest.raises(SystemExit, match='Failed to update playlists in Music: Not authorized'):
            playlists.export_playlists(database_name)

    def test_exports_playlists_chosen_by_path(self, database_name, query, tracks, no_builtin_playlists, music):
        query("CREATE VIEW playlists.v AS SELECT f AS folder, 'Same' AS playlist, track_id "
              "FROM track, (VALUES ('A'), ('B')) AS folders (f)")

        playlists.export_playlists(database_name, ['B/Same'])

        assert [(playlist['folder'], playlist['name']) for playlist in music.requests[0]['playlists']] == [
            (['B'], 'Same')]

    def test_exports_every_playlist_with_a_chosen_name(self, database_name, query, tracks, no_builtin_playlists,
                                                       music):
        query("CREATE VIEW playlists.v AS SELECT f AS folder, 'Same' AS playlist, track_id "
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
            'Made by Smarter Playlists from the playlists."All-Time Favourites" view. Changes made here will be '
            'overwritten. https://github.com/Stephan5/smarter-playlists')

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
        query("""CREATE VIEW playlists.v AS
                 SELECT track_id, 6 - play_count AS position FROM track ORDER BY track_id""")

        assert fetch() == {'v': ['A000000000000005', 'A000000000000004', 'A000000000000003', 'A000000000000002',
                                 'A000000000000001']}

    def test_keeps_view_order_without_position(self, query, fetch):
        query("CREATE VIEW playlists.v AS SELECT track_id FROM track ORDER BY play_count DESC LIMIT 2")

        assert fetch() == {'v': ['A000000000000005', 'A000000000000004']}

    def test_keeps_first_position_of_duplicate_tracks(self, query, fetch):
        query("""CREATE VIEW playlists.v AS
                 SELECT * FROM (VALUES ('A000000000000002', 1), ('A000000000000001', 2), ('A000000000000002', 3),
                                       (NULL, 4)) AS v (track_id, position)""")

        assert fetch() == {'v': ['A000000000000002', 'A000000000000001']}

    def test_playlist_column_makes_a_playlist_per_value(self, query, fetch):
        query("""CREATE VIEW playlists.v AS
                 SELECT * FROM (VALUES ('Odd', 'A000000000000003', 2), ('Even', 'A000000000000002', 1),
                                       ('Odd', 'A000000000000001', 1), (NULL, 'A000000000000004', 1))
                                AS v (playlist, track_id, position)""")

        assert fetch() == {'Even': ['A000000000000002'], 'Odd': ['A000000000000001', 'A000000000000003']}

    def test_folder_column_puts_playlists_in_folders(self, query, fetch):
        query("""CREATE VIEW playlists.v AS
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
        query("CREATE VIEW playlists.a AS SELECT 'F' AS folder, 'Mine' AS playlist, track_id FROM track")
        query("CREATE VIEW playlists.b AS SELECT 'F' AS folder, 'Mine' AS playlist, track_id FROM track")

        with pytest.raises(SystemExit, match="Playlist 'F/Mine' is defined by both a and b"):
            fetch()

    def test_requires_a_track_id_column(self, query, fetch):
        query("CREATE VIEW playlists.v AS SELECT title FROM track")

        with pytest.raises(SystemExit, match="View 'v' has no track_id column"):
            fetch()


def at(year, month, day):
    return datetime.datetime(year, month, day, 12, tzinfo=UTC)


class TestBuiltinPlaylists:

    @pytest.fixture
    def plays(self, run_import, query):
        """Imports tracks, then records the given plays as {track number: [time, ...]}."""
        def record(plays_by_track, track_values=None):
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

    def test_monthly_playlists_start_october_2026(self, database_name, plays):
        plays({1: [at(2026, 9, 15), at(2026, 10, 2), at(2026, 11, 3), at(2027, 1, 20)],
               2: [at(2026, 10, 5), at(2026, 10, 6)]})

        assert self.fetch(database_name, 'monthly') == {
            'Smarter Playlists/2026/October 2026': ['A000000000000002', 'A000000000000001'],
            'Smarter Playlists/2026/November 2026': ['A000000000000001'],
            'Smarter Playlists/2027/January 2027': ['A000000000000001'],
        }

    def test_yearly_playlists_start_2026(self, database_name, plays):
        plays({1: [at(2025, 6, 1), at(2026, 2, 1), at(2027, 3, 1)],
               2: [at(2026, 5, 1), at(2026, 6, 1)]})

        assert self.fetch(database_name, 'yearly') == {
            'Smarter Playlists/2026/2026': ['A000000000000002', 'A000000000000001'],
            'Smarter Playlists/2027/2027': ['A000000000000001'],
        }

    def test_monthly_playlists_limit_tracks_per_album(self, database_name, plays, query):
        plays({n: [at(2026, 10, day) for day in range(1, n + 2)] for n in range(1, 5)})
        query("UPDATE track SET album_id = 'C000000000000001'")

        assert self.fetch(database_name, 'monthly') == {'Smarter Playlists/2026/October 2026': ['A000000000000004', 'A000000000000003']}

    def test_monthly_playlists_skip_removed_and_playlist_only_tracks(self, database_name, plays, query):
        plays({1: [at(2026, 10, 1)], 2: [at(2026, 10, 1)], 3: [at(2026, 10, 1)]},
              {2: {'playlist_only': True}})
        query("UPDATE track SET removed_at = now() WHERE track_id = 'A000000000000003'")

        assert self.fetch(database_name, 'monthly') == {'Smarter Playlists/2026/October 2026': ['A000000000000001']}


@pytest.mark.parametrize('view, written_as', [
    ('monthly', 'playlists.monthly'),
    ('All-Time Favourites', 'playlists."All-Time Favourites"'),
    ('say "hi"', 'playlists."say ""hi"""'),
])
def test_description_names_the_view_as_written_in_sql(view, written_as):
    assert playlists.Playlist('Name', view, []).description.startswith(
        'Made by Smarter Playlists from the {0} view.'.format(written_as))


@pytest.mark.parametrize('result, dry_run, message', [
    ({'created': True, 'moved': False, 'changed': True}, True, "Would create playlist 'F/P' with 1 tracks"),
    ({'created': True, 'moved': False, 'changed': True}, False, "Created playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': True, 'changed': True}, True, "Would move and update playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': True, 'changed': True}, False, "Moved and updated playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': False, 'changed': True}, False, "Updated playlist 'F/P' with 1 tracks"),
    ({'created': False, 'moved': False, 'changed': False}, False, "Playlist 'F/P' is already up to date"),
])
def test_reports_what_changed(database_name, query, tracks, no_builtin_playlists, monkeypatch, caplog, result,
                              dry_run, message):
    query("CREATE VIEW playlists.v AS SELECT 'F' AS folder, 'P' AS playlist, track_id FROM track LIMIT 1")
    monkeypatch.setattr(playlists, 'run_sync_script', lambda request: json.dumps(
        [dict(result, tracks=1, missing=[], dryRun=dry_run)]))
    caplog.set_level('INFO')

    playlists.export_playlists(database_name, dry_run=dry_run)

    assert [record.getMessage() for record in caplog.records] == [message]


@pytest.mark.parametrize('time_zone, expected', [
    # 00:30 on 1 October in London is still 30 September in UTC and Los Angeles, which is before monthly playlists start
    ('Europe/London', {'Smarter Playlists/2026/October 2026': ['A000000000000001']}),
    ('UTC', {}),
    ('America/Los_Angeles', {}),
])
def test_months_follow_the_database_time_zone(database_name, run_import, query, monkeypatch, time_zone, expected):
    run_import(make_track())
    query("INSERT INTO play (track_id, played_at, estimated) VALUES ('A000000000000001', %s, FALSE)",
          [datetime.datetime(2026, 9, 30, 23, 30, tzinfo=UTC)])
    monkeypatch.setenv('PGTZ', time_zone)

    with database.connect(database_name) as db:
        monthly = {playlist.path: playlist.track_ids for playlist in playlists.fetch_view(db, 'monthly')}

    assert monthly == expected
