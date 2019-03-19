#!/bin/bash
set -e;
if [ -z "$1" ]
  then
    echo "No playlist name supplied"
fi
if [ -z "$2" ]
  then
    echo "No view name supplied"
fi

/usr/bin/osascript /Users/stephan/Development/smarter-playlists/applescript/delete-playlist-tracks.scpt "$1"

/usr/local/bin/python3 /Users/stephan/Development/smarter-playlists/export-to-playlist.py -n "$1" -v $2 -p 4359

/usr/bin/osascript /Users/stephan/Development/smarter-playlists/applescript/pause.scpt
