# Smarter Playlists

Import iTunes music data to PostgreSQL, build playlists with SQL, export playlists back out to iTunes

This repo consists of two separate Python scripts. One to move data from iTunes to Postgres and the other to go back the other way.

## Import to Postgres

To import your music data to a local postgres database you just need to follow a few simple steps and then run the `import-to-postgres.py` script.

### Prerequisites
* Python 3.7+
* Postgres running locally
* iTunes configured to "Share Library XML with other Applications" in Preferences > Advanced
* Basic SQL knowledge to create a playlist of your liking.

```bash
python3 ./import-to-postgres.py --library "/Users/stephan/Music/iTunes/iTunes Music Library.xml" --db "music" --port 5432 --schema "public" --user "postgres" --pass "postgres"
```

Once the script has successfully run, you should have `artist`, `album`, `track` and `play` tables in your choosen schema. 

The script can be re-run as many time as desired and simply updates the exisiting tables with any new tracks/play information.

The `play` table uses the "last played" data from itunes to create a history of plays - albeit inaccurate initially. 
The idea is that over time, running the script frequently will produce an accurate play history, enabling you to create **Smarter Playlists**.

## Export to Playlist

To later export a view or table from your postgres music database you simply run the other script, named `export-to-playlist.py`.
This produces a XML file that can be manually imported into your itunes library. 

For example, you might create a view such as the following to create a playlist for a month of the year
```sql
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
```

You would then run the export script with the desired name of the playlist and the name of the view...
```bash
python3 ./export-to-playlist.py -name "January 2019" -view "jan_2019" --library "/Users/stephan/Music/iTunes/iTunes Music Library.xml" --db "music" --port 5432 --schema "public" --user "postgres" --pass "postgres"
```

This would output an XML file labeled `January 2019.xml` which can be imported into iTunes using File > Library > Import Playlist... 


## Improvements
Unfortunately, I have not yet found a way to automatically import the generated XML playlist into iTunes and must be done manually every single time.
Initially I envisaged having a playlist that was kept up-to-date with new plays etc (like a smart playlist) but without being able to programatically import the playlist, this is impossible.
Any help on this would be greatly appreciated! (See [Issue #1](https://github.com/Stephan5/smarter-playlists/issues/1))


## Acknowledgements
These scripts borrow heavily from [itunes-to-sql](https://github.com/drien/itunes-to-sql) and [swinsian2itlxml](https://github.com/mhite/swinsian2itlxml) for inspiration. 
