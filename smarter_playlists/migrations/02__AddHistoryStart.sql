CREATE TABLE setting (
    -- Plays before this date are ignored by the playlist views and `stats`. Plays before the first import are estimated
    -- and spread evenly from long ago, so only the plays from when real listening was being recorded are rich enough
    -- to trust. Compared with the date of each play in the database's time zone. '-infinity' ignores nothing.
    history_start DATE NOT NULL
);

-- restrict to single row
CREATE UNIQUE INDEX ui_setting ON setting ((TRUE));

INSERT INTO setting (history_start) VALUES (CURRENT_DATE);
