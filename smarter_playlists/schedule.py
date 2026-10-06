import logging
import os
import pathlib
import plistlib
import subprocess
import sys

LABEL = 'local.smarter-playlists'


def agent_path():
    return pathlib.Path.home() / 'Library' / 'LaunchAgents' / '{0}.plist'.format(LABEL)


def log_path():
    return pathlib.Path.home() / 'Library' / 'Logs' / 'smarter-playlists.log'


def agent(database_name, every_hours):
    """A launchd agent that imports the library and exports playlists every few hours, and when you log in."""
    definition = {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, '-m', 'smarter_playlists', 'run', '--db', database_name],
        'StartInterval': int(every_hours * 60 * 60),
        'RunAtLoad': True,
        'ProcessType': 'Background',
        'StandardOutPath': str(log_path()),
        'StandardErrorPath': str(log_path()),
    }
    # launchd jobs don't see your shell's environment, so keep any Postgres connection settings
    environment = {name: value for name, value in os.environ.items() if name.startswith('PG')}
    if environment:
        definition['EnvironmentVariables'] = environment
    return definition


def install(database_name, every_hours):
    path = agent_path()
    if path.exists():
        launchctl('bootout', service())

    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as file:
        plistlib.dump(agent(database_name, every_hours), file)

    launchctl('bootstrap', domain(), str(path), check=True)
    logging.info("Scheduled to run every %g hours, starting now. Logging to %s", every_hours, log_path())


def uninstall():
    path = agent_path()
    if not path.exists():
        logging.info("Not scheduled")
        return

    launchctl('bootout', service())
    path.unlink()
    logging.info("No longer scheduled")


def status():
    path = agent_path()
    if not path.exists():
        logging.info("Not scheduled")
        return

    with open(path, 'rb') as file:
        definition = plistlib.load(file)
    loaded = launchctl('print', service()).returncode == 0
    logging.info("Scheduled every %g hours%s. Logging to %s", definition['StartInterval'] / 60 / 60,
                 "" if loaded else ", but not loaded. Run `smarter-playlists schedule install` again",
                 definition['StandardOutPath'])


def domain():
    return 'gui/{0}'.format(os.getuid())


def service():
    return '{0}/{1}'.format(domain(), LABEL)


def launchctl(*args, check=False):
    process = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True)
    if check and process.returncode != 0:
        raise SystemExit("launchctl {0} failed: {1}".format(args[0], (process.stderr or process.stdout).strip()))
    return process
