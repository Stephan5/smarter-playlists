import datetime
import shutil
import tempfile

import pytest

from conftest import make_track
from smarter_playlists import backup, database, server

NOW = datetime.datetime(2026, 10, 6, 14, 2, 0)


def every(hours, days, until=NOW):
    """Backup times every few hours, for some days up to the given time."""
    count = int(days * 24 / hours)
    return [until - datetime.timedelta(hours=hours * n) for n in range(count)]


class TestBackupsToKeep:

    def test_keeps_a_few(self):
        times = every(2, 1)[:5]

        assert backup.backups_to_keep(times, NOW) == set(times)

    def test_keeps_the_newest_twelve(self):
        times = every(2, 3)

        assert set(times[:12]) <= backup.backups_to_keep(times, NOW)

    def test_keeps_the_newest_of_each_of_the_last_30_days(self):
        times = every(2, 90)
        kept = backup.backups_to_keep(times, NOW) - set(times[:12])

        for day in range(1, 30):
            date = (NOW - datetime.timedelta(days=day)).date()
            on_day = [time for time in times if time.date() == date]
            assert [time for time in on_day if time in kept] == ([max(on_day)] if max(on_day) not in times[:12] else [])

    def test_keeps_the_newest_of_every_month(self):
        times = every(6, 400)
        kept = backup.backups_to_keep(times, NOW)

        newest_of_month = {}
        for time in times:
            month = (time.year, time.month)
            newest_of_month[month] = max(time, newest_of_month.get(month, time))
        a_month_ago = NOW - datetime.timedelta(days=31)
        assert {time for time in kept if time < a_month_ago} == {
            time for time in newest_of_month.values() if time < a_month_ago}

    def test_prunes_only_backups(self, tmp_path):
        for time in every(2, 10):
            (tmp_path / time.strftime(backup.NAME_FORMAT)).touch()
        (tmp_path / 'notes.txt').touch()

        backup.prune(tmp_path, NOW)

        assert len(backup.backups(tmp_path)) == len(backup.backups_to_keep(every(2, 10), NOW))
        assert (tmp_path / 'notes.txt').exists()


def test_takes_a_backup(database_name, tmp_path, caplog):
    caplog.set_level('INFO')

    path = backup.take(tmp_path, database_name, now=NOW)

    assert path == tmp_path / 'music-2026-10-06T140200.dump'
    assert path.stat().st_size > 0
    assert list(tmp_path.iterdir()) == [path]
    assert caplog.messages[-1].startswith('Backed up to {0} ('.format(path))


def test_latest(database_name, tmp_path):
    assert backup.latest(tmp_path) is None

    backup.take(tmp_path, database_name, now=NOW - datetime.timedelta(hours=2))
    newest = backup.take(tmp_path, database_name, now=NOW)

    assert backup.latest(tmp_path) == newest


def test_restores_a_backup(database_name, run_import, query, tmp_path):
    run_import(make_track(play_count=2, last_played_at=datetime.datetime(2026, 8, 8, tzinfo=datetime.UTC)))
    path = backup.take(tmp_path, database_name, now=NOW)
    query("DELETE FROM play")

    backup.restore(path, tmp_path, database_name)

    assert query("SELECT count(*) FROM play") == [(2,)]
    # And the database it replaced was backed up first
    assert len(backup.backups(tmp_path)) == 2


def test_restores_into_a_new_database(database_name, tmp_path):
    path = backup.take(tmp_path, database_name, now=NOW)
    new = database_name + '_restored'

    try:
        backup.restore(path, tmp_path, new)

        with database.connect(new) as db:
            assert database.is_set_up(db)
        # Nothing to back up first
        assert len(backup.backups(tmp_path)) == 1
    finally:
        with database.connect('postgres', autocommit=True) as admin:
            admin.execute('DROP DATABASE IF EXISTS {0} WITH (FORCE)'.format(new))


def test_backs_up_before_failing_to_restore_something_else(database_name, query, tmp_path):
    not_a_backup = tmp_path / 'music.dump'
    not_a_backup.write_text('nope')

    with pytest.raises(SystemExit, match='pg_restore failed'):
        backup.restore(not_a_backup, tmp_path, database_name)

    # It's empty now, but there's the backup taken first
    assert len(backup.backups(tmp_path)) == 1


def test_restore_needs_a_file(tmp_path):
    with pytest.raises(SystemExit, match="doesn't exist"):
        backup.restore(tmp_path / 'missing.dump', tmp_path)


def test_upgrade_does_nothing_on_the_newest_postgres(postgres, monkeypatch, caplog):
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', postgres)
    caplog.set_level('INFO')

    backup.upgrade()

    assert caplog.messages == ['The database already uses Postgres {0}'.format(server.cluster_version())]


def installed_versions():
    return sorted({server.major_version(path) for path in server.candidate_bin_dirs()})


@pytest.mark.skipif(len(installed_versions()) < 2 or installed_versions()[0] >= server.major_version(server.bin_dir()),
                    reason='needs an older Postgres installed, as well as the one on the PATH')
def test_upgrades_to_the_newest_postgres(monkeypatch):
    home = tempfile.mkdtemp(prefix='smarter playlists ', dir='/tmp')
    monkeypatch.setenv('SMARTER_PLAYLISTS_HOME', home)
    old = installed_versions()[0]
    try:
        server.init(server.bin_dir(old))
        with server.running():
            database.create()
            database.set_up()
            with database.connect() as db:
                db.execute("INSERT INTO artist VALUES ('B000000000000001', 'Radiohead')")

        backup.upgrade()

        assert server.cluster_version() == server.major_version(server.bin_dir())
        assert (server.home() / 'postgres-{0}.old'.format(old) / 'PG_VERSION').exists()
        with server.running(), database.connect() as db:
            assert db.execute("SELECT name FROM artist").fetchall() == [('Radiohead',)]
    finally:
        if server.is_running():
            server.stop()
        shutil.rmtree(home, ignore_errors=True)
