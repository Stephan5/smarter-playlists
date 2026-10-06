import plistlib
import subprocess
import sys

import pytest

from smarter_playlists import schedule


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    return tmp_path


@pytest.fixture
def launchctl(monkeypatch):
    """Records launchctl calls instead of running them. Set .loaded to control what `launchctl print` reports."""
    class Launchctl:
        def __init__(self):
            self.calls = []
            self.loaded = True

        def __call__(self, *args, check=False):
            self.calls.append(list(args))
            returncode = 0 if args[0] != 'print' or self.loaded else 113
            return subprocess.CompletedProcess(['launchctl', *args], returncode, stdout='', stderr='')

    fake = Launchctl()
    monkeypatch.setattr(schedule, 'launchctl', fake)
    return fake


def installed_agent(home):
    with open(home / 'Library' / 'LaunchAgents' / 'local.smarter-playlists.plist', 'rb') as file:
        return plistlib.load(file)


def test_install_writes_and_loads_an_agent(home, launchctl, monkeypatch):
    for name in ('PGHOST', 'PGPORT', 'PGUSER', 'PGPASSWORD'):
        monkeypatch.delenv(name, raising=False)

    schedule.install('music', 2)

    log = str(home / 'Library' / 'Logs' / 'smarter-playlists.log')
    assert installed_agent(home) == {
        'Label': 'local.smarter-playlists',
        'ProgramArguments': [sys.executable, '-m', 'smarter_playlists', 'run', '--db', 'music'],
        'StartInterval': 7200,
        'RunAtLoad': True,
        'ProcessType': 'Background',
        'StandardOutPath': log,
        'StandardErrorPath': log,
    }
    assert launchctl.calls == [['bootstrap', schedule.domain(), str(schedule.agent_path())]]


def test_agent_is_a_valid_launchd_plist(home, launchctl):
    schedule.install('music', 0.5)

    lint = subprocess.run(['plutil', '-lint', str(schedule.agent_path())], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout
    assert installed_agent(home)['StartInterval'] == 1800


def test_install_keeps_postgres_settings(home, launchctl, monkeypatch):
    monkeypatch.setenv('PGHOST', 'db.local')
    monkeypatch.setenv('PGPORT', '5433')

    schedule.install('music', 2)

    assert installed_agent(home)['EnvironmentVariables'] == {'PGHOST': 'db.local', 'PGPORT': '5433'}


def test_reinstall_replaces_the_agent(home, launchctl):
    schedule.install('music', 2)
    schedule.install('other', 4)

    assert installed_agent(home)['ProgramArguments'][-1] == 'other'
    assert [call[0] for call in launchctl.calls] == ['bootstrap', 'bootout', 'bootstrap']


def test_uninstall_unloads_and_removes_the_agent(home, launchctl):
    schedule.install('music', 2)
    schedule.uninstall()

    assert not schedule.agent_path().exists()
    assert launchctl.calls[-1] == ['bootout', schedule.service()]


def test_uninstall_when_not_installed(home, launchctl):
    schedule.uninstall()

    assert launchctl.calls == []


def test_status(home, launchctl, caplog):
    caplog.set_level('INFO')
    schedule.status()
    schedule.install('music', 3)
    schedule.status()
    launchctl.loaded = False
    schedule.status()

    messages = [record.getMessage() for record in caplog.records if 'Scheduled to run' not in record.getMessage()]
    log = home / 'Library' / 'Logs' / 'smarter-playlists.log'
    assert messages == [
        'Not scheduled',
        'Scheduled every 3 hours. Logging to {0}'.format(log),
        'Scheduled every 3 hours, but not loaded. Run `smarter-playlists schedule install` again. '
        'Logging to {0}'.format(log),
    ]
