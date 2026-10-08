import argparse
import datetime
import logging
import signal
import subprocess
import sys

from . import backup, database, history, library, playlists, schedule, server, stats, timing

LOG_FORMAT = '%(asctime)s %(levelname)s %(message)s'
LOG_DATE_FORMAT = '%Y-%m-%d %H:%M:%S'


def main(arg_list=None):
    args = parse_args(arg_list)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format=LOG_FORMAT,
                        datefmt=LOG_DATE_FORMAT)
    # launchd stops jobs with SIGTERM, which would otherwise leave the database running
    signal.signal(signal.SIGTERM, exit_on_signal)
    try:
        args.command(args)
    except SystemExit as exit:
        if exit.code in (None, 0):
            raise
        # Log why it stopped, with a timestamp like everything else, rather than leave Python to print it
        if isinstance(exit.code, str):
            logging.error("%s", exit.code)
        notify_if_scheduled(args, history.describe(exit))
        raise SystemExit(1 if isinstance(exit.code, str) else exit.code) from None
    except Exception as error:
        logging.exception("Failed")
        notify_if_scheduled(args, history.describe(error))
        raise SystemExit(1) from None


def notify_if_scheduled(args, message):
    # Runs you start yourself show their errors in the terminal
    if getattr(args, 'scheduled', False):
        schedule.notify_failure(message)


def exit_on_signal(signum, frame):
    logging.warning("Stopping, after %s", signal.Signals(signum).name)
    raise SystemExit(128 + signum)


def parse_args(arg_list):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--verbose', '-v',
                        help='Log more, e.g. every track played, even on the first import',
                        action='store_true')
    backups = argparse.ArgumentParser(add_help=False)
    backups.add_argument('--backup-dir',
                         help='Where to keep backups [{0}]'.format(backup.default_directory()),
                         dest='backup_dir')

    parser = argparse.ArgumentParser(prog='smarter-playlists',
                                     description='Build a play history from your Apple Music library in Postgres, and '
                                                 'make playlists from it with SQL')
    parser.set_defaults(verbose=False)
    commands = parser.add_subparsers(title='commands', required=True, metavar='command')

    setup_ = commands.add_parser('setup', parents=[common], help='Create the database')
    setup_.set_defaults(command=setup)

    import_ = commands.add_parser('import', parents=[common, backups],
                                  help='Back up the database, then import the Music library')
    import_.set_defaults(command=import_library)

    migrate_ = commands.add_parser('migrate', parents=[common, backups],
                                   help='Bring the database up to date with this version, after backing it up. '
                                        'Import and run do this too')
    migrate_.set_defaults(command=migrate)

    export_ = commands.add_parser('export', parents=[common], help='Export playlists to Music')
    add_export_arguments(export_)
    export_.set_defaults(command=export)

    run_ = commands.add_parser('run', parents=[common, backups],
                               help='Back up the database, import the Music library, then export playlists')
    add_export_arguments(run_)
    run_.add_argument('--scheduled',
                      help='Record the run as scheduled, and show a notification if it fails',
                      action='store_true')
    run_.set_defaults(command=run)

    stats_ = commands.add_parser('stats', parents=[common], help='Show a year of listening')
    stats_.add_argument('year',
                        help='[this year]',
                        type=int,
                        nargs='?',
                        default=datetime.date.today().year)
    stats_.add_argument('--top',
                        help='How many artists, tracks and albums to list [%(default)s]',
                        type=int,
                        default=10)
    stats_.set_defaults(command=show_stats)

    backup_ = commands.add_parser('backup', parents=[common, backups], help='Back up the database')
    backup_.set_defaults(command=take_backup)

    restore_ = commands.add_parser('restore', parents=[common, backups],
                                   help='Replace the database with a backup, backing it up first')
    restore_.add_argument('dump', help='A backup, or any dump made with `pg_dump --format=custom`')
    restore_.set_defaults(command=restore)

    # Everything after `psql` goes to psql, so it doesn't take --verbose, which would steal psql's -v
    psql_ = commands.add_parser('psql', help='Open psql on the database. Any arguments are passed to psql',
                                add_help=False)
    psql_.set_defaults(command=psql, psql_args=[])

    db = commands.add_parser('db', help='Keep the database running, e.g. for other database tools')
    db_commands = db.add_subparsers(title='commands', required=True, metavar='command')
    db_start = db_commands.add_parser('start', parents=[common], help='Start the database, and keep it running')
    db_start.add_argument('--port',
                          help='Also listen on this port on localhost, for tools that need TCP',
                          type=int)
    db_start.set_defaults(command=lambda args: server.start_and_keep_running(args.port))
    db_stop = db_commands.add_parser('stop', parents=[common],
                                     help='Stop the database, now or when whatever is using it finishes')
    db_stop.set_defaults(command=lambda args: server.stop_when_unused())
    db_status = db_commands.add_parser('status', parents=[common, backups],
                                       help='Show where the database is, whether it is running, and the latest backup')
    db_status.set_defaults(command=status)

    upgrade_ = commands.add_parser('upgrade', parents=[common],
                                   help='Move the database to the newest installed Postgres')
    upgrade_.set_defaults(command=upgrade)

    schedule_ = commands.add_parser('schedule', help='Run every few hours')
    schedule_commands = schedule_.add_subparsers(title='commands', required=True, metavar='command')
    install = schedule_commands.add_parser('install', parents=[common, backups],
                                           help='Run every few hours, and when you log in, starting now')
    install.add_argument('--every',
                         help='Hours between runs [%(default)s]',
                         type=float,
                         default=2)
    install.add_argument('--dry-run',
                         help='Show the launchd agent that would be installed, without installing it',
                         dest='dry_run',
                         action='store_true')
    install.set_defaults(command=lambda args: schedule.install(args.every, args.backup_dir, args.dry_run))
    uninstall = schedule_commands.add_parser('uninstall', parents=[common], help='Stop running on a schedule')
    uninstall.set_defaults(command=lambda args: schedule.uninstall())
    status_ = schedule_commands.add_parser('status', parents=[common],
                                           help='Show whether, and how often, it runs on a schedule, and how the '
                                                'last run went')
    status_.set_defaults(command=schedule_status)

    args, extra = parser.parse_known_args(arg_list)
    if args.command is psql:
        args.psql_args = extra
    elif extra:
        parser.error('unrecognized arguments: {0}'.format(' '.join(extra)))
    return args


def add_export_arguments(parser):
    parser.add_argument('playlists',
                        help='Only export these playlists [all of them]',
                        metavar='playlist',
                        nargs='*')
    parser.add_argument('--dry-run',
                        help='Show what would change without modifying any playlists',
                        dest='dry_run',
                        action='store_true')


def setup(args):
    if not server.initialised():
        server.init()
    with server.work(), server.running():
        database.create()
        database.set_up()


def import_library(args):
    with server.work(), server.running():
        backup.take(args.backup_dir)
        database.migrate()
        library.import_library(database.DATABASE)


def migrate(args):
    with server.work(), server.running():
        # Backed up first, as a migration can't always be undone
        if not database.migrate(before=lambda: backup.take(args.backup_dir)):
            logging.info("The database is up to date")


def export(args):
    with server.work(), server.running():
        playlists.export_playlists(database.DATABASE, args.playlists, args.dry_run)


def run(args):
    # Runs are added one after another to the schedule's log, so mark where each starts
    print(file=sys.stderr)
    logging.info("===== Run started%s =====", " (dry run)" if args.dry_run else "")
    elapsed = timing.Stopwatch()
    with server.work(), server.running():
        warn_if_stale(args)
        with history.recorded(args.scheduled, args.dry_run) as record:
            backup.take(args.backup_dir)
            database.migrate()
            record.plays_recorded, record.plays_estimated = library.import_library(database.DATABASE)
            try:
                record.playlists_changed = playlists.export_playlists(database.DATABASE, args.playlists, args.dry_run)
                record.playlists_failed = 0
            except playlists.ExportFailed as failure:
                record.playlists_changed, record.playlists_failed = failure.changed, failure.failed
                raise
        warn_if_estimates_rising(args, record)
    logging.info("===== Run finished in %s =====", elapsed)


def warn_if_estimates_rising(args, record):
    """A rise means imports are too far apart, e.g. a run was missed, which makes the play history rougher."""
    warning = history.estimated_rise_warning(record)
    if warning:
        logging.warning("%s", warning)
        if args.scheduled:
            schedule.notify(warning, "Play history is getting rougher")


def warn_if_stale(args):
    """Scheduled runs are frequent, so a long gap since the last good one means launchd wasn't running them."""
    warning = history.stale_warning()
    if warning:
        logging.warning("%s", warning)
        # Runs you start yourself show it in the terminal
        if args.scheduled:
            schedule.notify(warning, "Runs have stopped")


def show_stats(args):
    with server.running():
        print('\n'.join(stats.report(args.year, args.top)))


def take_backup(args):
    with server.work(), server.running():
        backup.take(args.backup_dir)


def restore(args):
    if not server.initialised():
        server.init()
    with server.work(), server.running():
        backup.restore(args.dump, args.backup_dir)


def upgrade(args):
    with server.work():
        backup.upgrade()


def psql(args):
    with server.running():
        # Ctrl-C is for psql, to cancel a query, so it mustn't stop us before the database is stopped
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            returncode = subprocess.run([server.program('psql'), *args.psql_args],
                                        env=server.environment(database.DATABASE)).returncode
        finally:
            signal.signal(signal.SIGINT, previous)
    if returncode:
        raise SystemExit(returncode)


def schedule_status(args):
    schedule.status()
    if server.initialised():
        with server.running():
            history.log_summary()
            warning = history.stale_warning()
            if warning:
                logging.warning("%s. Is the schedule running?", warning)


def status(args):
    server.status()
    latest = backup.latest(args.backup_dir)
    logging.info("Latest backup: %s", latest or "none yet")


if __name__ == '__main__':
    main()
