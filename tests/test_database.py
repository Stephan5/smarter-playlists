import pytest

from conftest import make_track
from smarter_playlists import database


def test_creates_tables(database_name, query):
    assert query("""
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'public'
         ORDER BY 1
        """) == [('album',), ('artist',), ('play',), ('run',), ('schema_migration',), ('setting',), ('track',)]


def test_creates_only_the_playlist_schema(database_name, query):
    assert query("""
        SELECT nspname
          FROM pg_namespace
         WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'
         ORDER BY 1
        """) == [('playlist',), ('public',)]


def test_creates_builtin_playlists(database_name, query, run_import):
    run_import(make_track())

    views = query("SELECT table_name FROM information_schema.views WHERE table_schema = 'playlist' ORDER BY 1")
    assert {'monthly', 'yearly', 'Rising'} <= {view for (view,) in views}
    for (view,) in views:
        query('SELECT track_id, position FROM playlist."{0}"'.format(view))


def test_refuses_to_run_twice(database_name):
    with pytest.raises(SystemExit, match='already set up'):
        database.set_up(database_name)


def test_connects_to_database_by_name(database_name):
    with database.connect(database_name) as db:
        assert db.execute("SELECT current_database()").fetchone() == (database_name,)


def test_creates_a_database_only_if_missing(empty_database):
    database.create(empty_database)
    with database.connect(empty_database) as db:
        db.execute("CREATE TABLE kept ()")

    database.create(empty_database)

    with database.connect(empty_database) as db:
        assert database.exists(empty_database)
        assert db.execute("SELECT to_regclass('kept')").fetchone()[0] is not None


def test_recreates_a_database_empty(database_name):
    database.recreate(database_name)

    with database.connect(database_name) as db:
        assert not database.is_set_up(db)


def test_only_optional_values_are_nullable(database_name, query):
    # Everything else must always have a value
    assert query("""
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND is_nullable = 'YES'
         ORDER BY 1, ordinal_position
        """) == [
        ('album', 'year'),
        ('run', 'finished_at'),
        ('run', 'plays_recorded'),
        ('run', 'plays_estimated'),
        ('run', 'playlists_changed'),
        ('run', 'playlists_failed'),
        ('run', 'error'),
        ('track', 'year'),
        ('track', 'bpm'),
        ('track', 'last_played_at'),
        ('track', 'last_skipped_at'),
        ('track', 'added_at'),
        ('track', 'removed_at'),
    ]


class TestMigrations:

    @pytest.fixture
    def newer(self, monkeypatch):
        """Adds migrations that come after the real ones, as {number: SQL}."""
        def add(**sql_by_name):
            real = database.migrations()
            first = real[-1][0]
            extra = [(first + n, name, sql) for n, (name, sql) in enumerate(sql_by_name.items(), 1)]
            monkeypatch.setattr(database, 'migrations', lambda: real + extra)
            return [name for _, name, _ in extra]
        return add

    def applied(self, query):
        return [name for (name,) in query("SELECT name FROM schema_migration ORDER BY version")]

    def test_set_up_records_the_migrations(self, database_name, query):
        assert self.applied(query) == [name for _, name, _ in database.migrations()]
        assert self.applied(query)[0] == '01__InitialSchema'

    def test_migrations_are_numbered_from_1_without_gaps(self):
        assert [version for version, _, _ in database.migrations()] == list(range(1, len(database.migrations()) + 1))

    def test_migrations_are_found_in_order_of_their_number(self, tmp_path, monkeypatch):
        directory = tmp_path / 'migrations'
        directory.mkdir()
        for name in ('10__Ten.sql', '02__Two.sql', '01__One.sql', 'notes.txt', 'README.sql', '3_Bad.sql'):
            (directory / name).write_text('-- ' + name)
        monkeypatch.setattr(database.importlib.resources, 'files', lambda package: tmp_path)

        assert database.migrations() == [(1, '01__One', '-- 01__One.sql'), (2, '02__Two', '-- 02__Two.sql'),
                                         (10, '10__Ten', '-- 10__Ten.sql')]

    def test_migrations_cant_share_a_number(self, tmp_path, monkeypatch):
        directory = tmp_path / 'migrations'
        directory.mkdir()
        (directory / '01__One.sql').write_text('')
        (directory / '1__Again.sql').write_text('')
        monkeypatch.setattr(database.importlib.resources, 'files', lambda package: tmp_path)

        with pytest.raises(SystemExit, match='have the same number'):
            database.migrations()

    def test_migrates_once_in_order(self, database_name, query, newer, caplog):
        caplog.set_level('INFO')
        two, three = newer(**{'02__AddNote': "CREATE TABLE note (note_id INT)",
                              '03__FillNote': "INSERT INTO note VALUES (1)"})

        assert database.migrate(database_name) == 2
        assert database.migrate(database_name) == 0

        assert self.applied(query)[-2:] == [two, three]
        assert query("SELECT note_id FROM note") == [(1,)]
        assert [m for m in caplog.messages if m.startswith('Applied')] == [
            'Applied migration ' + two, 'Applied migration ' + three]

    def test_a_failed_migration_is_undone_but_earlier_ones_are_kept(self, database_name, query, newer):
        ok, _ = newer(**{'02__AddNote': "CREATE TABLE note (note_id INT)",
                         '03__Broken': "CREATE TABLE half (id INT); SELECT 1 / 0"})

        with pytest.raises(Exception, match='division by zero'):
            database.migrate(database_name)

        assert self.applied(query)[-1] == ok
        assert query("SELECT to_regclass('note'), to_regclass('half')") == [('note', None)]

    def test_calls_before_only_if_there_is_something_to_apply(self, database_name, newer):
        calls = []
        database.migrate(database_name, before=lambda: calls.append('before'))
        assert calls == []

        newer(**{'02__AddNote': "CREATE TABLE note (note_id INT)"})
        database.migrate(database_name, before=lambda: calls.append('before'))
        assert calls == ['before']

    def test_commands_refuse_to_run_on_an_old_schema(self, database_name, newer):
        newer(**{'02__AddNote': "CREATE TABLE note (note_id INT)"})

        with database.connect(database_name) as db:
            with pytest.raises(SystemExit, match='needs migrating. Run `smarter-playlists migrate`'):
                database.require_set_up(db)

    def test_a_database_from_before_migrations_were_recorded_has_the_first(self, database_name, query, newer):
        query("DROP TABLE schema_migration, setting CASCADE")
        newer(**{'03__AddNote': "CREATE TABLE note (note_id INT)"})

        assert database.migrate(database_name) == 2
        assert self.applied(query) == ['01__InitialSchema', '02__AddHistoryStart', '03__AddNote']

    def test_a_database_from_a_newer_version_is_refused(self, database_name, query):
        query("INSERT INTO schema_migration (version, name) VALUES (99, '99__FromTheFuture')")

        with pytest.raises(SystemExit, match='migration 99, which this version .* doesn.t know about'):
            database.migrate(database_name)

    def test_migrating_a_database_that_is_not_set_up(self, empty_database):
        with pytest.raises(SystemExit, match="isn't set up yet"):
            database.migrate(empty_database)
