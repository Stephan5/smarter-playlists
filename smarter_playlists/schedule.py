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


def agent(every_hours, backup_dir=None):
    """A launchd agent that imports the library and exports playlists every few hours, and when you log in."""
    if getattr(sys, 'frozen', False):
        # The standalone executable is the program itself, not a Python to run the package with
        arguments = [sys.executable, 'run', '--scheduled']
    else:
        arguments = [sys.executable, '-m', 'smarter_playlists', 'run', '--scheduled']
    if backup_dir:
        arguments += ['--backup-dir', str(pathlib.Path(backup_dir).resolve())]
    definition = {
        'Label': LABEL,
        'ProgramArguments': arguments,
        'StartInterval': int(every_hours * 60 * 60),
        'RunAtLoad': True,
        'ProcessType': 'Background',
        'StandardOutPath': str(log_path()),
        'StandardErrorPath': str(log_path()),
    }
    # launchd jobs don't see your shell's environment, so keep the PATH, to find the same Postgres, and where the
    # database is if it's been moved
    environment = {name: os.environ[name] for name in ('PATH', 'SMARTER_PLAYLISTS_HOME') if os.environ.get(name)}
    if environment:
        definition['EnvironmentVariables'] = environment
    return definition


def install(every_hours, backup_dir=None, dry_run=False):
    if dry_run:
        print(plistlib.dumps(agent(every_hours, backup_dir)).decode(), end='')
        return

    path = agent_path()
    if path.exists():
        launchctl('bootout', service())

    path.parent.mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as file:
        plistlib.dump(agent(every_hours, backup_dir), file)

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


def notify_failure(message):
    """Shows a macOS notification that a scheduled run failed. If that fails too, it's only logged."""
    notify(message, "The scheduled run failed")


def notify(message, subtitle):
    """Shows a macOS notification. If that fails, it's only logged."""
    script = ['-e', 'on run argv',
              '-e', 'display notification (item 1 of argv) with title "Smarter Playlists" '
                    'subtitle (item 2 of argv)',
              '-e', 'end run']
    try:
        process = subprocess.run(['/usr/bin/osascript', *script, message, subtitle], capture_output=True, text=True)
    except OSError as error:
        logging.warning("Couldn't show a notification: %s", error)
        return
    if process.returncode != 0:
        logging.warning("Couldn't show a notification: %s", process.stderr.strip())


def domain():
    return 'gui/{0}'.format(os.getuid())


def service():
    return '{0}/{1}'.format(domain(), LABEL)


def launchctl(*args, check=False):
    process = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True)
    if check and process.returncode != 0:
        raise SystemExit("launchctl {0} failed: {1}".format(args[0], (process.stderr or process.stdout).strip()))
    return process
