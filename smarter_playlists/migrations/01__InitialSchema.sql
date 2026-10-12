-- The database as first set up, applied by `smarter-playlists setup`. IDs are Music's persistent IDs as 16 hex digits,
-- the same IDs the Music app uses for scripting, so a track can always be found again when exporting a playlist.
--
-- Changes to the schema after this go in new files here, named like 02__AddSomething.sql. They're applied in order, each
-- once, and never edited after they're released. See database.py.

CREATE TABLE artist (
    artist_id TEXT NOT NULL,
    name      TEXT NOT NULL,

    CONSTRAINT pk_artist PRIMARY KEY (artist_id)
);

CREATE TABLE album (
    album_id     TEXT NOT NULL,
    title        TEXT NOT NULL,
    -- Falls back to the album's most common track artist when no album artist is set, as the Music app does
    album_artist TEXT NOT NULL,
    compilation  BOOLEAN NOT NULL,
    year         INT,

    CONSTRAINT pk_album PRIMARY KEY (album_id)
);

CREATE TABLE track (
    track_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    artist_id       TEXT NOT NULL,
    album_id        TEXT NOT NULL,
    genre           TEXT NOT NULL,
    year            INT,
    disc_number     INT NOT NULL,
    track_number    INT NOT NULL,
    duration_ms     INT NOT NULL,
    bpm             INT,
    play_count      INT NOT NULL,
    skip_count      INT NOT NULL,
    last_played_at  TIMESTAMPTZ,
    last_skipped_at TIMESTAMPTZ,
    added_at        TIMESTAMPTZ,
    -- Apple Music tracks added to a playlist but not the library. These can't be added to exported playlists.
    playlist_only   BOOLEAN NOT NULL,
    -- Set when the track is no longer in the Music library. Its plays are kept.
    removed_at      TIMESTAMPTZ,

    CONSTRAINT pk_track PRIMARY KEY (track_id),
    CONSTRAINT fk_track_1 FOREIGN KEY (artist_id) REFERENCES artist,
    CONSTRAINT fk_track_2 FOREIGN KEY (album_id) REFERENCES album,
    CONSTRAINT ck_track_play_count CHECK (play_count >= 0),
    CONSTRAINT ck_track_skip_count CHECK (skip_count >= 0)
);

-- One row per play. Plays that happened between imports are estimated and evenly spaced, see library.py
CREATE TABLE play (
    play_id   BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    track_id  TEXT NOT NULL,
    played_at TIMESTAMPTZ NOT NULL,
    estimated BOOLEAN NOT NULL,

    CONSTRAINT pk_play PRIMARY KEY (play_id),
    CONSTRAINT fk_play_1 FOREIGN KEY (track_id) REFERENCES track,
    CONSTRAINT uk_play_track_played_at UNIQUE (track_id, played_at)
);

CREATE INDEX idx_play_played_at ON play (played_at);

-- One row for each `smarter-playlists run`, for `schedule status` and to look back on how runs have gone
CREATE TABLE run (
    run_id            BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY,
    started_at        TIMESTAMPTZ NOT NULL,
    -- Not set while it's running, or if it was killed before it could say how it went
    finished_at       TIMESTAMPTZ,
    scheduled         BOOLEAN NOT NULL,
    dry_run           BOOLEAN NOT NULL,
    -- How far it got: not set if it stopped before importing, or before exporting
    plays_recorded    INT,
    plays_estimated   INT,
    playlists_changed INT,
    playlists_failed  INT,
    -- Why it failed, if it did
    error             TEXT,

    CONSTRAINT pk_run PRIMARY KEY (run_id)
);

CREATE INDEX idx_run_started_at ON run (started_at);
