CREATE OR REPLACE VIEW jan_2019 AS
 SELECT playlist_tracks.itunes_id,
        ROW_NUMBER() OVER (ORDER BY playlist_tracks.count DESC) AS row_number,
        playlist_tracks.count,
        playlist_tracks.track_name
   FROM (SELECT play.track_id,
                track.itunes_id,
                track.track_name,
                COUNT(*) AS count,
                ROW_NUMBER() OVER (PARTITION BY track.album_id ORDER BY (COUNT(*)) DESC) AS album_rows,
                ROW_NUMBER() OVER (PARTITION BY track.artist_id ORDER BY (COUNT(*)) DESC) AS artist_rows
           FROM play
           JOIN track USING (track_id)
           JOIN artist USING (artist_id)
           JOIN album USING (album_id, artist_id)
          WHERE DATE_PART('month'::text, play.played_at) = '1':: DOUBLE PRECISION
            AND DATE_PART('year'::text, play.played_at) = '2019':: DOUBLE PRECISION
          GROUP BY play.track_id, track.track_name, track.itunes_id, track.album_id, track.artist_id) AS playlist_tracks
  WHERE playlist_tracks.album_rows <= 2
    AND playlist_tracks.artist_rows <= 5
    AND playlist_tracks.count > 1
  GROUP BY playlist_tracks.track_id, playlist_tracks.track_name, playlist_tracks.itunes_id, playlist_tracks.count
  ORDER BY playlist_tracks.count DESC
  LIMIT 50;