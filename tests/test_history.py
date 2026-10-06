import datetime
import signal
import subprocess

import pytest

from smarter_playlists import history, schedule

UTC = datetime.timezone.utc


def runs(query):
    return query("""
        SELECT scheduled, dry_run, finished_at IS NOT NULL, plays_recorded, plays_estimated, playlists_changed,
               playlists_failed, error
          FROM run
         ORDER BY run_id
        """)


def test_records_a_run(database_name, query):
    with history.recorded(True, False, database_name) as run:
        run.plays_recorded, run.plays_estimated = 3, 1
        run.playlists_changed, run.playlists_failed = 2, 0

    assert runs(query) == [(True, False, True, 3, 1, 2, 0, None)]


def test_records_why_a_run_failed(database_name, query):
    with pytest.raises(SystemExit):
        with history.recorded(False, True, database_name) as run:
            run.plays_recorded, run.plays_estimated = 3, 1
            raise SystemExit("Music didn't finish")

    assert runs(query) == [(False, True, True, 3, 1, None, None, "Music didn't finish")]


def test_a_run_is_recorded_as_soon_as_it_starts(database_name, query):
    with history.recorded(True, False, database_name):
        assert runs(query) == [(True, False, False, None, None, None, None, None)]


@pytest.mark.parametrize('error, described', [
    (SystemExit("No songs found in the Music library"), "No songs found in the Music library"),
    (SystemExit(128 + signal.SIGTERM), "Stopped by SIGTERM"),
    (SystemExit(3), "Exited with status 3"),
    (KeyboardInterrupt(), "Interrupted"),
    (ValueError("oops"), "ValueError: oops"),
])
def test_describes_failures(error, described):
    assert history.describe(error) == described


class TestSummary:

    @pytest.fixture
    def summary(self, database_name, caplog):
        def run():
            caplog.clear()
            caplog.set_level('INFO')
            history.log_summary(database_name)
            return caplog.messages
        return run

    def add_run(self, query, started_at, finished=True, error=None, dry_run=False, scheduled=True):
        query("""
            INSERT INTO run (started_at, finished_at, scheduled, dry_run, plays_recorded, plays_estimated,
                             playlists_changed, playlists_failed, error)
            VALUES (%s, %s, %s, %s, 3, 1, 2, 0, %s)
            """, [started_at, started_at + datetime.timedelta(minutes=2) if finished else None, scheduled, dry_run,
                  error])

    def at(self, hour):
        return datetime.datetime(2026, 10, 6, hour, 0, tzinfo=UTC)

    def local(self, hour):
        return self.at(hour).astimezone().strftime('%Y-%m-%d %H:%M')

    def test_no_runs(self, summary):
        assert summary() == ["No runs recorded yet"]

    def test_last_run_succeeded(self, summary, query):
        self.add_run(query, self.at(18))

        assert summary() == ["Last run, scheduled at {0}, recorded 3 new plays (1 estimated) and changed 2 playlists"
                             .format(self.local(18))]

    def test_last_run_failed(self, summary, query):
        self.add_run(query, self.at(18))
        self.add_run(query, self.at(20), error="Music didn't finish", scheduled=False)

        assert summary() == [
            "Last run, by hand at {0}, failed: Music didn't finish".format(self.local(20)),
            "Last successful run at {0}".format(self.local(18)),
        ]

    def test_dry_runs_are_not_successful_runs(self, summary, query):
        self.add_run(query, self.at(18), dry_run=True)
        self.add_run(query, self.at(20), error="Music didn't finish")

        assert summary()[-1] == "No successful runs yet"

    def test_last_run_did_not_finish(self, summary, query):
        self.add_run(query, self.at(20), finished=False)

        assert summary()[0] == ("Last run, scheduled at {0}, hasn't finished. It's either still running, or was "
                                "stopped before it could".format(self.local(20)))


def test_notifies_of_failures(monkeypatch):
    commands = []
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append(command)
                        or subprocess.CompletedProcess(command, 0, '', ''))

    schedule.notify_failure('Music didn\'t "finish"')

    # The message is an argument, so it needn't be quoted for AppleScript
    assert commands[0][0] == '/usr/bin/osascript'
    assert commands[0][-1] == 'Music didn\'t "finish"'


def test_a_failure_to_notify_is_only_logged(monkeypatch, caplog):
    def fail(command, **kwargs):
        raise FileNotFoundError('/usr/bin/osascript')
    monkeypatch.setattr(subprocess, 'run', fail)

    schedule.notify_failure('Oops')

    assert "Couldn't show a notification" in caplog.text
