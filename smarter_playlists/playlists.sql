-- Playlists created by `smarter-playlists setup`. Every view in the playlists schema is exported to Music.
--
-- A view needs a track_id column, and is ordered by its position column if it has one. It's exported as a playlist
-- named after the view, or, if it has a playlist column, as one playlist for each distinct value of that column.
-- A folder column puts playlists in a folder, with / between nested folders, e.g. 'Smarter Playlists/2026'.
--
-- Months and years follow the database's time zone. Start dates are compared as dates rather than timestamps, which
-- would be fixed to the time zone the view was created in and could disagree with how plays are grouped.

-- The most played tracks of each month since October 2026, named like "October 2026", with at most 2 tracks from any
-- album and 5 from any artist. A new playlist appears each month, in a folder for its year.

DROP SCHEMA IF EXISTS playlists CASCADE;
CREATE SCHEMA playlists;

CREATE VIEW playlists.monthly AS
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

-- The most played tracks of each year since 2026, named like "2026", with at most 3 tracks from any album and 5 from
-- any artist. A new playlist appears each year, in a folder for its year alongside its monthly playlists.
CREATE VIEW playlists.yearly AS
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
         WHERE album_rank <= 3
           AND artist_rank <= 5) AS ranked
 WHERE position <= 100;

-- The most played tracks of all time, with at most 3 tracks per artist
CREATE VIEW playlists."All-Time Favourites" AS
SELECT 'Smarter Playlists' AS folder,
       track_id,
       ROW_NUMBER() OVER (ORDER BY play_count DESC, track_id) AS position
  FROM (SELECT track_id,
               play_count,
               ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY play_count DESC, track_id) AS artist_rank
          FROM track
         WHERE removed_at IS NULL
           AND NOT playlist_only) AS ranked
 WHERE artist_rank <= 3
 ORDER BY position
 LIMIT 100;

-- Well loved tracks that haven't been played for a year
CREATE VIEW playlists."Forgotten Favourites" AS
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
