import datetime

import pytest

from conftest import UTC, make_track
from smarter_playlists import stats


def test_sparkline():
    assert stats.sparkline([0, 1, 4, 8]) == ' ▁▄█'


def test_sparkline_with_nothing():
    assert stats.sparkline([0, 0]) == '  '


def test_bar():
    assert stats.bar(10, 10, width=4) == '████'
    assert stats.bar(1, 100, width=4) == '█'
    assert stats.bar(0, 100, width=4) == ''


def test_hours():
    assert stats.hours(4.24) == '4.2 h'
    assert stats.hours(1234.4) == '1,234 h'


@pytest.mark.parametrize('year, expected', [(2025, 12), (2026, 10)])
def test_shows_months_up_to_now(year, expected):
    assert stats.months_so_far(year, today=datetime.date(2026, 10, 6)) == list(range(1, expected + 1))


def test_ranked_truncates_long_names():
    lines = stats.ranked('Top', [('x' * 70, 3, '')])

    assert lines[1] == '   1  {0}…      3'.format('x' * 59)


def test_ranked_with_nothing():
    assert stats.ranked('Albums released in 2025', []) == ['Albums released in 2025', '  None']


class TestReport:

    @pytest.fixture
    def plays(self, run_import, query):
        """Two Radiohead tracks and one by Warpaint, with these plays in 2025 (and some in 2024, which don't count)."""
        run_import(
            make_track(track_id='A000000000000001', title='Weird Fishes', duration_ms=6 * 60_000),
            make_track(track_id='A000000000000002', title='Reckoner', duration_ms=5 * 60_000,
                       album_id='C000000000000002', album_title='Reckoner Single', year=2025),
            make_track(track_id='A000000000000003', title='Undertow', duration_ms=4 * 60_000,
                       artist_id='B000000000000002', artist_name='Warpaint', album_id='C000000000000003',
                       album_title='The Fool', year=2010))

        def play(track, month, day, estimated=False, year=2025):
            query("INSERT INTO play (track_id, played_at, estimated) VALUES (%s, %s, %s)",
                  ['A00000000000000{0}'.format(track), datetime.datetime(year, month, day, 12, tzinfo=UTC), estimated])

        for day in range(1, 5):
            play(1, 1, day)
        play(1, 3, 1, estimated=True)
        play(2, 3, 2)
        play(2, 3, 3)
        play(3, 12, 1)
        play(3, 12, 2)
        play(1, 6, 1, year=2024)

    def test_report(self, database_name, plays):
        assert stats.report(2025, database_name=database_name) == [
            "2025 in review",
            "",
            # 5 plays of 6 minutes, 2 of 5 and 2 of 4: 48 minutes
            "9 plays, 0.8 h of listening",
            "3 tracks by 2 artists, from 3 albums",
            "11% of plays are estimated, spread evenly between imports, so plays by month are approximate",
            "",
            "Listening by month",
            # 24 minutes in January, 16 in March and 8 in December
            "  Jan     0.4 h  ████████████████████████████████████████",
            "  Feb     0.0 h",
            "  Mar     0.3 h  ███████████████████████████",
            "  Apr     0.0 h",
            "  May     0.0 h",
            "  Jun     0.0 h",
            "  Jul     0.0 h",
            "  Aug     0.0 h",
            "  Sep     0.0 h",
            "  Oct     0.0 h",
            "  Nov     0.0 h",
            "  Dec     0.1 h  █████████████",
            "",
            "Top artists      plays  Jan–Dec",
            # 4 plays in January, and 3 in March
            "   1  Radiohead      7  █ ▆",
            "   2  Warpaint       2             █",
            "",
            "Top tracks                      plays",
            "   1  Weird Fishes — Radiohead      5",
            "   2  Reckoner — Radiohead          2",
            "   3  Undertow — Warpaint           2",
            "",
            "Most played albums                        plays",
            "   1  In Rainbows — Radiohead (2007)          5",
            "   2  Reckoner Single — Radiohead (2025)      2",
            "   3  The Fool — Warpaint (2010)              2",
            "",
            "Albums released in 2025                   plays",
            "   1  Reckoner Single — Radiohead (2025)      2",
        ]

    def test_no_plays(self, database_name, plays):
        assert stats.report(2023, database_name=database_name) == ["No plays in 2023"]

    def test_lists_only_the_top(self, database_name, plays):
        lines = stats.report(2025, top=1, database_name=database_name)

        artists = lines.index("Top artists      plays  Jan–Dec")
        assert lines[artists + 1:artists + 3] == ["   1  Radiohead      7  █ ▆", ""]
