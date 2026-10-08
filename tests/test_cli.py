import contextlib
import datetime
import logging
import re
import signal
import subprocess

import pytest

from smarter_playlists import __main__ as cli
from smarter_playlists import backup, database, history, library, playlists, schedule, server, stats


@pytest.fixture
def calls(monkeypatch):
    """Records which commands run, with what arguments, instead of running them."""
    class Calls(list):
        """The calls made, in order, and the runs recorded."""

    recorded = Calls()

    def record(name, result=None):
        return lambda *args: recorded.append((name, *args)) or result

    @contextlib.contextmanager
    def context(name, value=None):
        recorded.append(name)
        yield value
        recorded.append('/' + name)

    monkeypatch.setattr(server, 'initialised', lambda: True)
    monkeypatch.setattr(server, 'init', record('init'))
    monkeypatch.setattr(server, 'work', lambda: context('work'))
    monkeypatch.setattr(server, 'running', lambda: context('running'))
    monkeypatch.setattr(server, 'start_and_keep_running', record('db start'))
    monkeypatch.setattr(server, 'stop_when_unused', record('db stop'))
    monkeypatch.setattr(database, 'create', record('create'))
    monkeypatch.setattr(database, 'set_up', record('setup'))
    monkeypatch.setattr(backup, 'take', record('backup'))
    monkeypatch.setattr(backup, 'restore', record('restore'))
    monkeypatch.setattr(backup, 'upgrade', record('upgrade'))
    runs = []
    monkeypatch.setattr(history, 'recorded', lambda scheduled, dry_run: context(
        'recorded{0}{1}'.format(' scheduled' if scheduled else '', ' dry run' if dry_run else ''),
        runs.append(history.Run()) or runs[-1]))
    recorded.runs = runs
    monkeypatch.setattr(history, 'log_summary', record('summary'))
    recorded.stale = None
    monkeypatch.setattr(history, 'stale_warning', lambda: recorded.stale)
    recorded.rise = None
    monkeypatch.setattr(history, 'estimated_rise_warning', lambda run: recorded.rise)
    monkeypatch.setattr(library, 'import_library', record('import', (3, 1)))
    monkeypatch.setattr(playlists, 'export_playlists', record('export', 2))
    monkeypatch.setattr(schedule, 'install', record('install'))
    monkeypatch.setattr(schedule, 'uninstall', record('uninstall'))
    monkeypatch.setattr(schedule, 'status', record('status'))
    return recorded


@pytest.mark.parametrize('arguments, expected', [
    (['setup'], ['work', 'running', ('create',), ('setup',), '/running', '/work']),
    (['import'], ['work', 'running', ('backup', None), ('import', 'music'), '/running', '/work']),
    (['import', '--backup-dir', '/backups'], ['work', 'running', ('backup', '/backups'), ('import', 'music'),
                                              '/running', '/work']),
    (['export'], ['work', 'running', ('export', 'music', [], False), '/running', '/work']),
    (['export', '--dry-run', 'October 2026', '2026'],
     ['work', 'running', ('export', 'music', ['October 2026', '2026'], True), '/running', '/work']),
    (['run', '-v'], ['work', 'running', 'recorded', ('backup', None), ('import', 'music'),
                     ('export', 'music', [], False), '/recorded', '/running', '/work']),
    (['run', '--scheduled', '--dry-run'], ['work', 'running', 'recorded scheduled dry run', ('backup', None),
                                           ('import', 'music'), ('export', 'music', [], True), '/recorded scheduled dry run',
                                           '/running', '/work']),
    (['backup'], ['work', 'running', ('backup', None), '/running', '/work']),
    (['restore', 'old.dump'], ['work', 'running', ('restore', 'old.dump', None), '/running', '/work']),
    (['upgrade'], ['work', ('upgrade',), '/work']),
    (['db', 'start'], [('db start', None)]),
    (['db', 'start', '--port', '5499'], [('db start', 5499)]),
    (['db', 'stop'], [('db stop',)]),
    (['schedule', 'install'], [('install', 2, None)]),
    (['schedule', 'install', '--every', '6', '--backup-dir', '/backups'], [('install', 6, '/backups')]),
    (['schedule', 'uninstall'], [('uninstall',)]),
    (['schedule', 'status'], [('status',), 'running', ('summary',), '/running']),
])
def test_commands(calls, arguments, expected):
    cli.main(arguments)

    assert calls == expected


def test_run_starts_with_a_header(calls, caplog, capsys):
    caplog.set_level('INFO')

    cli.main(['run'])
    cli.main(['run', '--dry-run'])

    assert [message for message in caplog.messages if message.startswith('=====')] == [
        '===== Run started =====', '===== Run started (dry run) =====']
    # With a blank line before each, to separate it from the run before
    assert capsys.readouterr().err == '\n\n'


def test_run_records_what_it_did(calls):
    cli.main(['run'])

    assert calls.runs == [history.Run(plays_recorded=3, plays_estimated=1, playlists_changed=2, playlists_failed=0)]


def test_run_records_failed_exports(calls, monkeypatch):
    def fail(*args):
        raise playlists.ExportFailed(changed=4, failed=1, total=9)
    monkeypatch.setattr(playlists, 'export_playlists', fail)

    with pytest.raises(SystemExit):
        cli.main(['run'])

    assert calls.runs == [history.Run(plays_recorded=3, plays_estimated=1, playlists_changed=4, playlists_failed=1)]


@pytest.mark.parametrize('arguments, notified', [
    (['run', '--scheduled'], ['Failed to export 1 of 9 playlists']),
    (['run'], []),
])
def test_only_scheduled_runs_notify_of_failures(calls, monkeypatch, arguments, notified):
    def fail(*args):
        raise playlists.ExportFailed(changed=4, failed=1, total=9)
    monkeypatch.setattr(playlists, 'export_playlists', fail)
    notifications = []
    monkeypatch.setattr(schedule, 'notify_failure', notifications.append)

    with pytest.raises(SystemExit):
        cli.main(arguments)

    assert notifications == notified


@pytest.mark.parametrize('arguments, notified', [
    (['run', '--scheduled'], [("No successful run since yesterday", "Runs have stopped")]),
    (['run'], []),
])
def test_runs_warn_when_there_has_been_no_successful_run_for_a_while(calls, monkeypatch, caplog, arguments, notified):
    calls.stale = "No successful run since yesterday"
    notifications = []
    monkeypatch.setattr(schedule, 'notify', lambda message, subtitle: notifications.append((message, subtitle)))

    cli.main(arguments)

    assert notifications == notified
    assert "No successful run since yesterday" in caplog.messages
    # The run goes ahead
    assert calls.runs


def test_runs_dont_warn_when_runs_are_recent(calls, monkeypatch, caplog):
    monkeypatch.setattr(schedule, 'notify', lambda *args: pytest.fail('notified'))

    cli.main(['run', '--scheduled'])

    assert not [record for record in caplog.records if record.levelname == 'WARNING']


@pytest.mark.parametrize('arguments, notified', [
    (['run', '--scheduled'], [("Estimated plays rose", "Play history is getting rougher")]),
    (['run'], []),
])
def test_runs_warn_when_estimated_plays_rise(calls, monkeypatch, caplog, arguments, notified):
    calls.rise = "Estimated plays rose"
    notifications = []
    monkeypatch.setattr(schedule, 'notify', lambda message, subtitle: notifications.append((message, subtitle)))

    cli.main(arguments)

    assert notifications == notified
    assert "Estimated plays rose" in caplog.messages


def test_runs_that_fail_dont_check_estimated_plays(calls, monkeypatch, caplog):
    calls.rise = "Estimated plays rose"
    monkeypatch.setattr(library, 'import_library', lambda *args: 1 / 0)

    with pytest.raises(SystemExit):
        cli.main(['run'])

    assert "Estimated plays rose" not in caplog.messages


def test_schedule_status_warns_when_there_has_been_no_successful_run_for_a_while(calls, caplog):
    calls.stale = "No successful run since yesterday"

    cli.main(['schedule', 'status'])

    assert "No successful run since yesterday. Is the schedule running?" in caplog.messages


def test_scheduled_runs_notify_of_unexpected_errors(calls, monkeypatch):
    def fail(*args):
        raise ValueError('oops')
    monkeypatch.setattr(library, 'import_library', fail)
    notifications = []
    monkeypatch.setattr(schedule, 'notify_failure', notifications.append)

    with pytest.raises(SystemExit):
        cli.main(['run', '--scheduled'])

    assert notifications == ['ValueError: oops']


@pytest.mark.parametrize('arguments, expected', [
    (['stats'], (datetime.date.today().year, 10)),
    (['stats', '2025', '--top', '5'], (2025, 5)),
])
def test_stats(calls, monkeypatch, capsys, arguments, expected):
    monkeypatch.setattr(stats, 'report', lambda year, top: calls.append(('stats', year, top)) or ['2025 in review'])

    cli.main(arguments)

    assert calls == ['running', ('stats', *expected), '/running']
    assert capsys.readouterr().out == '2025 in review\n'


def test_setup_creates_the_cluster_first(calls, monkeypatch):
    monkeypatch.setattr(server, 'initialised', lambda: False)

    cli.main(['setup'])

    assert calls[0] == ('init',)


def test_restore_creates_the_cluster_first(calls, monkeypatch):
    monkeypatch.setattr(server, 'initialised', lambda: False)

    cli.main(['restore', 'old.dump'])

    assert calls[0] == ('init',)


def test_psql_passes_its_arguments_on(calls, monkeypatch):
    ran = []
    monkeypatch.setattr(server, 'program', lambda name: '/pg/bin/' + name)
    monkeypatch.setattr(server, 'environment', lambda name: {'PGDATABASE': name})

    def fake_run(args, env):
        ran.append((args, env, signal.getsignal(signal.SIGINT)))
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(subprocess, 'run', fake_run)

    cli.main(['psql', '-v', 'ON_ERROR_STOP=1', '-c', 'SELECT 1'])

    assert ran == [(['/pg/bin/psql', '-v', 'ON_ERROR_STOP=1', '-c', 'SELECT 1'], {'PGDATABASE': 'music'},
                    signal.SIG_IGN)]
    assert calls == ['running', '/running']
    assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN


def test_psql_exits_as_psql_did(calls, monkeypatch):
    monkeypatch.setattr(server, 'program', lambda name: name)
    monkeypatch.setattr(subprocess, 'run', lambda args, env: subprocess.CompletedProcess(args, 3))

    with pytest.raises(SystemExit) as exit:
        cli.main(['psql'])

    assert exit.value.code == 3
    assert calls == ['running', '/running']


def test_refuses_unknown_arguments(calls, capsys):
    with pytest.raises(SystemExit):
        cli.main(['import', '--nope'])

    assert 'unrecognized arguments: --nope' in capsys.readouterr().err
    assert calls == []


def test_requires_a_command(capsys):
    with pytest.raises(SystemExit):
        cli.main([])

    assert 'required: command' in capsys.readouterr().err


def test_logs_why_a_command_failed(calls, monkeypatch, caplog):
    def fail(*args):
        raise SystemExit("The database isn't set up yet")
    monkeypatch.setattr(backup, 'take', fail)

    with pytest.raises(SystemExit) as exit:
        cli.main(['backup'])

    assert exit.value.code == 1
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ('ERROR', "The database isn't set up yet")]


def test_logs_unexpected_errors(calls, monkeypatch, caplog):
    def fail(*args):
        raise ValueError('oops')
    monkeypatch.setattr(backup, 'take', fail)

    with pytest.raises(SystemExit) as exit:
        cli.main(['backup'])

    assert exit.value.code == 1
    assert caplog.records[-1].levelname == 'ERROR'
    assert 'ValueError: oops' in caplog.text


def test_sigterm_exits_so_the_database_is_stopped():
    with pytest.raises(SystemExit) as exit:
        cli.exit_on_signal(signal.SIGTERM, None)

    assert exit.value.code == 128 + signal.SIGTERM


def test_logs_have_timestamps():
    record = logging.LogRecord('smarter_playlists', logging.INFO, __file__, 1, "Recorded %d new plays", (3,), None)

    line = logging.Formatter(cli.LOG_FORMAT, cli.LOG_DATE_FORMAT).format(record)

    assert re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d INFO Recorded 3 new plays', line)
