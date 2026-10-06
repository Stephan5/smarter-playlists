-- Playlists created by `smarter-playlists setup`. Every view in the playlist schema is exported to Music.
--
-- A view needs a track_id column, and is ordered by its position column if it has one. It's exported as a playlist
-- named after the view, or, if it has a playlist column, as one playlist for each distinct value of that column.
-- A folder column puts playlists in a folder, with / between nested folders, e.g. 'Smarter Playlists/2026'.
--
-- Months and years follow the database's time zone. Start dates are compared as dates rather than timestamps, which
-- would be fixed to the time zone the view was created in and could disagree with how plays are grouped.

-- The most played tracks of each month since October 2026, named like "October 2026", with at most 2 tracks from any
-- album and 5 from any artist. A new playlist appears each month, in a folder for its year.

DROP SCHEMA IF EXISTS playlist CASCADE;
CREATE SCHEMA playlist;

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
                         WHERE played_at::date >= DATE '2026-10-01'
                           AND removed_at IS NULL
                           AND NOT playlist_only) AS plays
                 GROUP BY month, track_id, album_id, artist_id) AS track_plays
         WHERE album_rank <= 2
           AND artist_rank <= 5) AS ranked
 WHERE position <= 50;

-- The most played tracks of each year since 2026, named like "2026", with at most 5 tracks from any album and 10 from
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
                         WHERE played_at::date >= DATE '2026-01-01'
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
 WHERE artist_rank <= 5
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
