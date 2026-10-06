import pytest

from smarter_playlists import __main__ as cli
from smarter_playlists import database, library, playlists, schedule


@pytest.fixture
def calls(monkeypatch):
    """Records which commands run, with what arguments, instead of running them."""
    recorded = []

    def record(name):
        return lambda *args: recorded.append((name, *args))

    monkeypatch.setattr(database, 'set_up', record('setup'))
    monkeypatch.setattr(library, 'import_library', record('import'))
    monkeypatch.setattr(playlists, 'export_playlists', record('export'))
    monkeypatch.setattr(schedule, 'install', record('install'))
    monkeypatch.setattr(schedule, 'uninstall', record('uninstall'))
    monkeypatch.setattr(schedule, 'status', record('status'))
    return recorded


@pytest.mark.parametrize('arguments, expected', [
    (['setup'], [('setup', 'music')]),
    (['import'], [('import', 'music')]),
    (['import', '--db', 'other'], [('import', 'other')]),
    (['export'], [('export', 'music', [], False)]),
    (['export', '--dry-run', 'October 2026', '2026'], [('export', 'music', ['October 2026', '2026'], True)]),
    (['run', '-d', 'other'], [('import', 'other'), ('export', 'other', [], False)]),
    (['schedule', 'install'], [('install', 'music', 2)]),
    (['schedule', 'install', '--every', '6', '--db', 'other'], [('install', 'other', 6)]),
    (['schedule', 'uninstall'], [('uninstall',)]),
    (['schedule', 'status'], [('status',)]),
])
def test_commands(calls, arguments, expected):
    cli.main(arguments)

    assert calls == expected


def test_requires_a_command(capsys):
    with pytest.raises(SystemExit):
        cli.main([])

    assert 'required: command' in capsys.readouterr().err
