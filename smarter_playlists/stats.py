"""`smarter-playlists stats`: a year of listening, as text."""

import datetime
import math

from . import database

MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
SPARKS = '▁▂▃▄▅▆▇█'
BAR_WIDTH = 40
NAME_WIDTH = 60

# Every play in the year, by the database's time zone, as the playlists group them, from the history_start setting.
# Plays of tracks since removed from the library still count.
YEAR_PLAYS = """
    WITH year_plays AS (
        SELECT play.track_id, play.played_at, play.estimated, track.duration_ms, track.artist_id, track.album_id
          FROM play
          JOIN track USING (track_id)
         WHERE play.played_at >= %(start)s
           AND play.played_at < %(end)s
           AND play.played_at::date >= (SELECT history_start FROM setting)
    )
"""


def report(year, top=10, database_name=database.DATABASE):
    """The year in review, as lines of text."""
    params = {'start': datetime.date(year, 1, 1), 'end': datetime.date(year + 1, 1, 1), 'year': year, 'top': top}
    with database.connect(database_name) as db:
        database.require_set_up(db)

        def query(statement):
            return db.execute(YEAR_PLAYS + statement, params).fetchall()

        [(plays, estimated, duration_ms, tracks, artists, albums)] = query("""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE estimated), COALESCE(SUM(duration_ms), 0),
                   COUNT(DISTINCT track_id), COUNT(DISTINCT artist_id), COUNT(DISTINCT album_id)
              FROM year_plays
            """)
        if not plays:
            return ["No plays in {0}".format(year)]

        hours_by_month = {month: ms / 3_600_000 for month, ms in query("""
            SELECT EXTRACT(MONTH FROM played_at)::INT, SUM(duration_ms)
              FROM year_plays
             GROUP BY 1
            """)}
        top_artists = query("""
            SELECT artist_id, artist.name, COUNT(*)
              FROM year_plays
              JOIN artist USING (artist_id)
             GROUP BY artist_id, artist.name
             ORDER BY 3 DESC, 2
             LIMIT %(top)s
            """)
        artist_months = {}
        for artist_id, month, count in query("""
                SELECT artist_id, EXTRACT(MONTH FROM played_at)::INT, COUNT(*)
                  FROM year_plays
                 GROUP BY 1, 2
                """):
            artist_months.setdefault(artist_id, {})[month] = count
        top_tracks = query("""
            SELECT track.title || ' — ' || artist.name, COUNT(*)
              FROM year_plays
              JOIN track USING (track_id)
              JOIN artist ON artist.artist_id = track.artist_id
             GROUP BY track.track_id, track.title, artist.name
             ORDER BY 2 DESC, 1
             LIMIT %(top)s
            """)
        album_query = """
            SELECT album.title || ' — ' || album.album_artist || COALESCE(' (' || album.year || ')', ''), COUNT(*)
              FROM year_plays
              JOIN album USING (album_id)
             {0}
             GROUP BY album.album_id, album.title, album.album_artist, album.year
             ORDER BY 2 DESC, 1
             LIMIT %(top)s
            """
        top_albums = query(album_query.format(''))
        new_albums = query(album_query.format('WHERE album.year = %(year)s'))

    months = months_so_far(year)
    lines = [
        "{0} in review".format(year),
        "",
        "{0:,} plays, {1} of listening".format(plays, hours(duration_ms / 3_600_000)),
        "{0:,} tracks by {1:,} artists, from {2:,} albums".format(tracks, artists, albums),
    ]
    if estimated:
        lines.append("{0:.0%} of plays are estimated, spread evenly between imports, so plays by month are "
                     "approximate".format(estimated / plays))

    lines += ["", "Listening by month"]
    most = max(hours_by_month.values())
    for month in months:
        value = hours_by_month.get(month, 0)
        lines.append("  {0}  {1:>8}  {2}".format(MONTHS[month - 1], hours(value), bar(value, most)).rstrip())

    lines += [""] + ranked("Top artists", [(name, count, sparkline([artist_months[artist_id].get(month, 0)
                                                                     for month in months]))
                                            for artist_id, name, count in top_artists],
                           "{0}–{1}".format(MONTHS[months[0] - 1], MONTHS[months[-1] - 1]))
    lines += [""] + ranked("Top tracks", [(label, count, '') for label, count in top_tracks])
    lines += [""] + ranked("Most played albums", [(label, count, '') for label, count in top_albums])
    lines += [""] + ranked("Albums released in {0}".format(year), [(label, count, '') for label, count in new_albums])
    return lines


def months_so_far(year, today=None):
    """The months of the year to show: all of them, or up to this month for this year."""
    today = today or datetime.date.today()
    return list(range(1, (12 if year < today.year else today.month) + 1))


def hours(value):
    return "{0:.1f} h".format(value) if value < 10 else "{0:,.0f} h".format(value)


def bar(value, most, width=BAR_WIDTH):
    if not value:
        return ''
    return '█' * max(1, round(value / most * width))


def sparkline(values):
    """A character per value, taller for larger values, and blank for none."""
    most = max(values, default=0)
    return ''.join(SPARKS[math.ceil(value / most * len(SPARKS)) - 1] if value else ' ' for value in values)


def ranked(title, items, extra_title=''):
    """A numbered table of (label, plays, extra) items."""
    if not items:
        return [title, "  None"]
    width = min(max(len(label) for label, _, _ in items), NAME_WIDTH)
    lines = ["{0:<{1}}  {2:>5}  {3}".format(title, width + 6, 'plays', extra_title).rstrip()]
    for rank, (label, plays, extra) in enumerate(items, 1):
        lines.append("{0:>4}  {1:<{2}}  {3:>5,}  {4}".format(rank, truncate(label, width), width, plays, extra)
                     .rstrip())
    return lines


def truncate(text, width):
    return text if len(text) <= width else text[:width - 1] + '…'
