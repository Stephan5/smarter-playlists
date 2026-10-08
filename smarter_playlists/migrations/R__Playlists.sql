-- The built-in playlists, reapplied whenever this file changes. Only the views made here are dropped and recreated, so
-- views you've added to the playlist schema are kept. Plays before the history_start setting are ignored.

CREATE SCHEMA IF NOT EXISTS playlist;

DROP VIEW IF EXISTS playlist.monthly, playlist.yearly, playlist."Last Month", playlist."Rising", playlist.top_artists,
                    playlist."All-Time Favourites", playlist."Forgotten Favourites", playlist."New and Unplayed",
                    playlist.time_of_day, playlist.seasons;

-- The most played tracks of each month since history_start, named like "October 2026", with at most 2 tracks from any
-- album and 5 from any artist. A new playlist appears each month, in a folder for its year. Months and years follow the
-- database's time zone, so history_start is compared as a date rather than a timestamp.
CREATE VIEW playlist.monthly AS
SELECT 'Smarter Playlists/' || TO_CHAR(month, 'YYYY') AS folder,
       TO_CHAR(month, 'FMMonth YYYY') AS playlist,
       track_id,
       position
  FROM (SELECT month,
               track_id,
               ROW_NUMBER() OVER (PARTITION BY month ORDER BY plays DESC, last_played_at DESC, track_id) AS position
          FROM (SELECT month,
                       track_id,
                       COUNT(*) AS plays,
                       MAX(played_at) AS last_played_at,
                       ROW_NUMBER() OVER (PARTITION BY month, album_id
                                              ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS album_rank,
                       ROW_NUMBER() OVER (PARTITION BY month, artist_id
                                              ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS artist_rank
                  FROM (SELECT DATE_TRUNC('month', played_at) AS month, track_id, album_id, artist_id, played_at
                          FROM play
                          JOIN track USING (track_id)
                         WHERE played_at::date >= (SELECT history_start FROM setting)
                           AND removed_at IS NULL
                           AND NOT playlist_only) AS plays
                 GROUP BY month, track_id, album_id, artist_id) AS track_plays
         WHERE album_rank <= 2
           AND artist_rank <= 5) AS ranked
 WHERE position <= 50;

-- The most played tracks of each year since history_start, named like "2026", with at most 5 tracks from any album and 10 from
-- any artist. A new playlist appears each year, in a folder for its year alongside its monthly playlists.
CREATE VIEW playlist.yearly AS
SELECT 'Smarter Playlists/' || TO_CHAR(year, 'YYYY') AS folder,
       TO_CHAR(year, 'YYYY') AS playlist,
       track_id,
       position
  FROM (SELECT year,
               track_id,
               ROW_NUMBER() OVER (PARTITION BY year ORDER BY plays DESC, last_played_at DESC, track_id) AS position
          FROM (SELECT year,
                       track_id,
                       COUNT(*) AS plays,
                       MAX(played_at) AS last_played_at,
                       ROW_NUMBER() OVER (PARTITION BY year, album_id
                                              ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS album_rank,
                       ROW_NUMBER() OVER (PARTITION BY year, artist_id
                                              ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS artist_rank
                  FROM (SELECT DATE_TRUNC('year', played_at) AS year, track_id, album_id, artist_id, played_at
                          FROM play
                          JOIN track USING (track_id)
                         WHERE played_at::date >= (SELECT history_start FROM setting)
                           AND removed_at IS NULL
                           AND NOT playlist_only) AS plays
                 GROUP BY year, track_id, album_id, artist_id) AS track_plays
         WHERE album_rank <= 5
           AND artist_rank <= 10) AS ranked
 WHERE position <= 100;

-- The most played tracks of the last 30 days, with at most 2 tracks from any album and 5 from any artist
CREATE VIEW playlist."Last Month" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY plays DESC, last_played_at DESC, track_id) AS position
  FROM (SELECT track_id,
               COUNT(*) AS plays,
               MAX(played_at) AS last_played_at,
               ROW_NUMBER() OVER (PARTITION BY album_id
                                      ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS album_rank,
               ROW_NUMBER() OVER (PARTITION BY artist_id
                                      ORDER BY COUNT(*) DESC, MAX(played_at) DESC, track_id) AS artist_rank
          FROM play
          JOIN track USING (track_id)
         WHERE played_at >= now() - INTERVAL '30 days'
           AND played_at::date >= (SELECT history_start FROM setting)
           AND removed_at IS NULL
           AND NOT playlist_only
         GROUP BY track_id, album_id, artist_id) AS track_plays
 WHERE album_rank <= 2
   AND artist_rank <= 5
 ORDER BY position
 LIMIT 50;

-- What you're getting into: tracks played more in the last 30 days than in the 90 days before, at least twice, ordered
-- by how much more. At most 2 tracks from any album and 3 from any artist.
CREATE VIEW playlist."Rising" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY recent_plays - earlier_plays DESC, recent_plays DESC, track_id) AS position
  FROM (SELECT track_id,
               recent_plays,
               earlier_plays,
               ROW_NUMBER() OVER (PARTITION BY album_id
                                      ORDER BY recent_plays - earlier_plays DESC, recent_plays DESC, track_id)
                   AS album_rank,
               ROW_NUMBER() OVER (PARTITION BY artist_id
                                      ORDER BY recent_plays - earlier_plays DESC, recent_plays DESC, track_id)
                   AS artist_rank
          FROM (SELECT track_id,
                       album_id,
                       artist_id,
                       COUNT(*) FILTER (WHERE played_at >= now() - INTERVAL '30 days') AS recent_plays,
                       COUNT(*) FILTER (WHERE played_at < now() - INTERVAL '30 days') AS earlier_plays
                  FROM play
                  JOIN track USING (track_id)
                 WHERE played_at >= now() - INTERVAL '120 days'
                   AND played_at::date >= (SELECT history_start FROM setting)
                   AND removed_at IS NULL
                   AND NOT playlist_only
                 GROUP BY track_id, album_id, artist_id) AS track_plays
         WHERE recent_plays > earlier_plays
           AND recent_plays >= 2) AS rising
 WHERE album_rank <= 2
   AND artist_rank <= 3
 ORDER BY position
 LIMIT 50;

-- A playlist for each of your 10 most played artists, named after them, with their 25 most played tracks
CREATE VIEW playlist.top_artists AS
SELECT 'Smarter Playlists/Top Artists' AS folder,
       artist.name AS playlist,
       track_id,
       position
  FROM (SELECT artist_id,
               track_id,
               ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY play_count DESC, track_id) AS position
          FROM track
         WHERE play_count > 0
           AND removed_at IS NULL
           AND NOT playlist_only) AS artist_tracks
  JOIN (SELECT artist_id
          FROM track
         WHERE removed_at IS NULL
           AND NOT playlist_only
         GROUP BY artist_id
         ORDER BY SUM(play_count) DESC, artist_id
         LIMIT 10) AS top_artists USING (artist_id)
  JOIN artist USING (artist_id)
 WHERE position <= 25;

-- The most played tracks of all time, with at most 5 tracks per artist
CREATE VIEW playlist."All-Time Favourites" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY play_count DESC, track_id) AS position
  FROM (SELECT track_id,
               play_count,
               ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY play_count DESC, track_id) AS artist_rank
          FROM track
         WHERE removed_at IS NULL
           AND NOT playlist_only) AS ranked
 WHERE artist_rank <= 10
 ORDER BY position
 LIMIT 100;

-- Well loved tracks that haven't been played for a year
CREATE VIEW playlist."Forgotten Favourites" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY play_count DESC, track_id) AS position
  FROM track
 WHERE play_count >= 10
   AND last_played_at < now() - INTERVAL '1 year'
   AND removed_at IS NULL
   AND NOT playlist_only
 ORDER BY position
 LIMIT 100;

-- Recently added tracks you haven't played yet, newest first. The date added isn't always when a track first arrived, as
-- tracks get added to the library again, but a track that was played before wouldn't be unplayed.
CREATE VIEW playlist."New and Unplayed" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY added_at DESC, track_id) AS position
  FROM track
 WHERE added_at >= now() - INTERVAL '60 days'
   AND play_count = 0
   AND removed_at IS NULL
   AND NOT playlist_only
 ORDER BY position
 LIMIT 100;

-- What you play at different times of the day over the last year: Morning (5am to noon), Afternoon (noon to 5pm),
-- Evening (5pm to 10pm) and Late Night (10pm to 5am), by the database's time zone. A playlist for each, with at most
-- 2 tracks from any album and 5 from any artist. Estimated plays are left out, as they have no real time of day.
CREATE VIEW playlist.time_of_day AS
SELECT 'Smarter Playlists/Time of Day' AS folder,
       period AS playlist,
       track_id,
       position
  FROM (SELECT period,
               track_id,
               ROW_NUMBER() OVER (PARTITION BY period ORDER BY plays DESC, last_played_at DESC, track_id) AS position
          FROM (SELECT period,
                       track_id,
                       plays,
                       last_played_at,
                       ROW_NUMBER() OVER (PARTITION BY period, album_id
                                              ORDER BY plays DESC, last_played_at DESC, track_id) AS album_rank,
                       ROW_NUMBER() OVER (PARTITION BY period, artist_id
                                              ORDER BY plays DESC, last_played_at DESC, track_id) AS artist_rank
                  FROM (SELECT CASE WHEN EXTRACT(HOUR FROM played_at) BETWEEN 5 AND 11 THEN 'Morning'
                                    WHEN EXTRACT(HOUR FROM played_at) BETWEEN 12 AND 16 THEN 'Afternoon'
                                    WHEN EXTRACT(HOUR FROM played_at) BETWEEN 17 AND 21 THEN 'Evening'
                                    ELSE 'Late Night' END AS period,
                               track_id,
                               album_id,
                               artist_id,
                               COUNT(*) AS plays,
                               MAX(played_at) AS last_played_at
                          FROM play
                          JOIN track USING (track_id)
                         WHERE played_at >= now() - INTERVAL '1 year'
                           AND played_at::date >= (SELECT history_start FROM setting)
                           AND NOT estimated
                           AND removed_at IS NULL
                           AND NOT playlist_only
                         GROUP BY 1, track_id, album_id, artist_id) AS track_plays) AS ranked
         WHERE album_rank <= 2
           AND artist_rank <= 5) AS limited
 WHERE position <= 50;

-- What you play in each season, across every year: Winter (December to February), Spring (March to May), Summer
-- (June to August) and Autumn (September to November). For the southern hemisphere, swap the names around. A playlist
-- for each, of up to 50 tracks with at most 2 from any album and 5 from any artist. Estimated plays are left out, as
-- they aren't at a real time of year.
CREATE VIEW playlist.seasons AS
SELECT 'Smarter Playlists/Seasons' AS folder,
       season AS playlist,
       track_id,
       position
  FROM (SELECT season,
               track_id,
               ROW_NUMBER() OVER (PARTITION BY season ORDER BY plays DESC, last_played_at DESC, track_id) AS position
          FROM (SELECT season,
                       track_id,
                       plays,
                       last_played_at,
                       ROW_NUMBER() OVER (PARTITION BY season, album_id
                                              ORDER BY plays DESC, last_played_at DESC, track_id) AS album_rank,
                       ROW_NUMBER() OVER (PARTITION BY season, artist_id
                                              ORDER BY plays DESC, last_played_at DESC, track_id) AS artist_rank
                  FROM (SELECT CASE WHEN EXTRACT(MONTH FROM played_at) IN (12, 1, 2) THEN 'Winter'
                                    WHEN EXTRACT(MONTH FROM played_at) IN (3, 4, 5) THEN 'Spring'
                                    WHEN EXTRACT(MONTH FROM played_at) IN (6, 7, 8) THEN 'Summer'
                                    ELSE 'Autumn' END AS season,
                               track_id,
                               album_id,
                               artist_id,
                               COUNT(*) AS plays,
                               MAX(played_at) AS last_played_at
                          FROM play
                          JOIN track USING (track_id)
                         WHERE played_at::date >= (SELECT history_start FROM setting)
                           AND NOT estimated
                           AND removed_at IS NULL
                           AND NOT playlist_only
                         GROUP BY 1, track_id, album_id, artist_id) AS track_plays) AS ranked
         WHERE album_rank <= 2
           AND artist_rank <= 5) AS limited
 WHERE position <= 50;
