import argparse
import logging

from . import database, library, playlists, schedule


def main(arg_list=None):
    args = parse_args(arg_list)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    args.command(args)


def parse_args(arg_list):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--db', '-d',
                        help='Postgres database name. The host, port, user and password come from the standard PG* '
                             'environment variables [%(default)s]',
                        dest='database',
                        default=database.DEFAULT_DATABASE)

    parser = argparse.ArgumentParser(prog='smarter-playlists',
                                     description='Build a play history from your Apple Music library in Postgres, and '
                                                 'make playlists from it with SQL')
    commands = parser.add_subparsers(title='commands', required=True, metavar='command')

    setup = commands.add_parser('setup', parents=[common], help='Create the tables and playlists in an empty database')
    setup.set_defaults(command=lambda args: database.set_up(args.database))

    import_ = commands.add_parser('import', parents=[common], help='Import the Music library')
    import_.set_defaults(command=lambda args: library.import_library(args.database))

    export = commands.add_parser('export', parents=[common], help='Export playlists to Music')
    add_export_arguments(export)
    export.set_defaults(command=lambda args: playlists.export_playlists(args.database, args.playlists, args.dry_run))

    run = commands.add_parser('run', parents=[common], help='Import the Music library, then export playlists')
    add_export_arguments(run)
    run.set_defaults(command=run_all)

    schedule_ = commands.add_parser('schedule', help='Run import and export every few hours')
    schedule_commands = schedule_.add_subparsers(title='commands', required=True, metavar='command')
    install = schedule_commands.add_parser('install', parents=[common],
                                           help='Run every few hours, and when you log in, starting now')
    install.add_argument('--every',
                         help='Hours between runs [%(default)s]',
                         type=float,
                         default=2)
    install.set_defaults(command=lambda args: schedule.install(args.database, args.every))
    uninstall = schedule_commands.add_parser('uninstall', help='Stop running on a schedule')
    uninstall.set_defaults(command=lambda args: schedule.uninstall())
    status = schedule_commands.add_parser('status', help='Show whether, and how often, it runs on a schedule')
    status.set_defaults(command=lambda args: schedule.status())

    return parser.parse_args(arg_list)


def add_export_arguments(parser):
    parser.add_argument('playlists',
                        help='Only export these playlists [all of them]',
                        metavar='playlist',
                        nargs='*')
    parser.add_argument('--dry-run',
                        help='Show what would change without modifying any playlists',
                        dest='dry_run',
                        action='store_true')


def run_all(args):
    library.import_library(args.database)
    playlists.export_playlists(args.database, args.playlists, args.dry_run)


if __name__ == '__main__':
    main()
