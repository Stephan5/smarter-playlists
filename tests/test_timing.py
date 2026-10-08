import pytest

from smarter_playlists import timing


@pytest.mark.parametrize('seconds, formatted', [
    (0, '0.0s'), (0.44, '0.4s'), (9.94, '9.9s'), (10, '10s'), (34.4, '34s'), (59.4, '59s'), (65, '1m 05s'),
    (600, '10m 00s'), (3725, '62m 05s'),
])
def test_formats_how_long_it_has_been(monkeypatch, seconds, formatted):
    now = [1000.0]
    monkeypatch.setattr(timing.time, 'monotonic', lambda: now[0])
    stopwatch = timing.Stopwatch()

    now[0] += seconds

    assert str(stopwatch) == formatted
