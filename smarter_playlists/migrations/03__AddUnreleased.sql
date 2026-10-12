-- Tracks in the library that aren't out yet, like an upcoming album's tracks added early, and when a track came out, if
-- Music knows. Both are set on every import.
ALTER TABLE track ADD COLUMN unreleased BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE track ADD COLUMN released_at TIMESTAMPTZ;
