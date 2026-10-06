import contextlib
import logging
import re
import signal
import subprocess

import pytest

from smarter_playlists import __main__ as cli
from smarter_playlists import backup, database, library, playlists, schedule, server


@pytest.fixture
def calls(monkeypatch):
    """Records which commands run, with what arguments, instead of running them."""
    recorded = []

    def record(name):
        return lambda *args: recorded.append((name, *args))

    @contextlib.contextmanager
    def context(name):
        recorded.append(name)
        yield
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
    monkeypatch.setattr(library, 'import_library', record('import'))
    monkeypatch.setattr(playlists, 'export_playlists', record('export'))
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
    (['run', '-v'], ['work', 'running', ('backup', None), ('import', 'music'), ('export', 'music', [], False),
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
    (['schedule', 'status'], [('status',)]),
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
