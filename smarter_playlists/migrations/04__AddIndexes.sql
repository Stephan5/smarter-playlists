-- Indexes for looking tracks, albums and artists up by name, and for the joins from a track to its artist and album.
CREATE INDEX idx_artist_name ON artist (name);
CREATE INDEX idx_album_title ON album (title);
CREATE INDEX idx_track_title ON track (title);
CREATE INDEX idx_track_artist_id ON track (artist_id);
CREATE INDEX idx_track_album_id ON track (album_id);
