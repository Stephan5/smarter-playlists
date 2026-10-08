"""How long things take, for logging."""

import time


class Stopwatch:
    """Starts when it's made. Formats as how long it's been, e.g. 0.4s, 34s or 2m 05s."""

    def __init__(self):
        self.start = time.monotonic()

    def __str__(self):
        seconds = time.monotonic() - self.start
        if seconds < 10:
            return '{0:.1f}s'.format(seconds)
        if seconds < 60:
            return '{0:.0f}s'.format(seconds)
        return '{0:.0f}m {1:02.0f}s'.format(*divmod(seconds, 60))
