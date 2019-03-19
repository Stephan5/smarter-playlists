#!/usr/bin/env python3

import argparse
import collections
import datetime
import logging
import os
import plistlib
import random
import re
import subprocess
import sys
import time
from urllib.parse import unquote

import psycopg2

DEFAULT_ITUNES_MUSIC_FOLDER = os.path.expanduser('~/Music/iTunes/iTunes Music/')
DEFAULT_PLAYLIST_NAME = 'Top 2019'
DEFAULT_VIEW_NAME = 'year_2019'
DEFAULT_DATABASE_NAME = 'music'
DEFAULT_USER_NAME = 'postgres'
DEFAULT_PASSWORD = 'postgres'
DEFAULT_FORMAT = 'M3U'
DEFAULT_PORT = 4359


def main(arg_list=None):
    args = parse_args(arg_list)

    db_name = args.database_name
    playlist_name = args.playlist_name
    view_name = args.view_name
    username = args.username
    password = args.password
    port = args.port
    playlist_format = args.format

    if playlist_format == "XML":
        export_as_xml(db_name, password, playlist_name, port, username, view_name)
    elif playlist_format == "M3U":
        export_as_m3u(db_name, password, playlist_name, port, username, view_name)
    else:
        sys.exit("Unsupported format selected: " + playlist_format)


def export_as_m3u(db_name, password, playlist_name, port, username, view_name):
    base_file_name = playlist_name
    temp_file_name = base_file_name + ".txt"
    final_file_name = base_file_name + ".m3u8"

    m3u_file = open(temp_file_name, "w+")
    m3u_file.write("#EXTM3U\n")

    rows = fetch_m3u_tracks(db_name, password, port, username, view_name)

    for row in rows:
        if row[3] is None:
            logging.warning("No file location found for %s by %s", row[0], row[1])
            continue

        track_name = row[0]
        artist_name = row[1]
        time_in_seconds = int(round(float(row[2]) / 1000, 0))
        file_location = unquote(row[3]).replace("file://", "")

        m3u_file.write("#EXTINF:" + str(time_in_seconds) + "," + track_name + " - " + artist_name + "\n")
        m3u_file.write(file_location + "\n")

    m3u_file.close()
    base = os.path.splitext(temp_file_name)[0]
    os.rename(temp_file_name, base + ".m3u8")

    subprocess.call(["open", final_file_name])

    os.remove(final_file_name)


def fetch_m3u_tracks(db_name, password, port, username, view_name):
    conn = psycopg2.connect(host="localhost", database=db_name, port=port, user=username, password=password)
    cur = conn.cursor()

    cur.execute("SELECT name,"
                "       artist,"
                "       total_time,"
                "       location "
                "  FROM itunes.itunes a "
                "  JOIN {0} b ON (a.persistent_id = b.itunes_id)"
                " ORDER BY row_number ASC "
                .format(view_name))

    rows = cur.fetchall()
    conn.commit()
    cur.close()
    conn.close()
    return rows


def export_as_xml(db_name, password, playlist_name, port, username, view_name):
    # FIXME Don't hardcode library details
    plist_dict = collections.OrderedDict([('Major Version', 1),
                                          ('Minor Version', 1),
                                          ('Date', datetime.datetime.fromtimestamp(time.mktime(time.localtime()))),
                                          ('Features', 5),
                                          ('Show Content Ratings', True),
                                          ('Application Version', '12.1.2.27'),
                                          ('Music Folder', 'file:///Users/stephan/Music/iTunes/iTunes%20Media/'),
                                          ('Library Persistent ID', '23D7636E9EB97DA0')])
    conn = psycopg2.connect(host="localhost", database=db_name, port=port, user=username, password=password)
    cur = conn.cursor()

    cur.execute("SELECT track_id,"
                "       name,"
                "       artist,"
                "       album_artist,"
                "       album,"
                "       grouping,"
                "       genre,"
                "       size,"
                "       total_time,"
                "       track_number,"
                "       year,"
                "       bpm,"
                "       date_added,"
                "       bit_rate,"
                "       sample_rate,"
                "       comments,"
                "       play_count,"
                "       play_date,"
                "       play_date_utc,"
                "       compilation,"
                "       persistent_id,"
                "       location "
                "  FROM itunes.itunes a "
                "  JOIN {0} b ON (a.persistent_id = b.itunes_id)"
                " ORDER BY row_number "
                .format(view_name))
    rows = cur.fetchall()
    logging.warning("Rows returned by query retrieved")
    itunes_track = collections.OrderedDict()
    for row in rows:
        track_id = row[0]
        name = escape_xml_illegal_chars(row[1])
        artist = escape_xml_illegal_chars(row[2])
        album_artist = escape_xml_illegal_chars(row[3])
        album = escape_xml_illegal_chars(row[4])
        grouping = escape_xml_illegal_chars(row[5])
        genre = escape_xml_illegal_chars(row[6])
        size = row[7]
        total_time = row[8]
        track_number = row[9]
        year = row[10]
        bpm = row[11]
        date_added = row[12]
        bit_rate = row[13]
        sample_rate = row[14]
        comments = escape_xml_illegal_chars(row[15])
        play_count = row[16]
        play_date = row[17]
        play_date_utc = row[18]
        compilation = bool(row[19])
        persistent_id = row[20]
        location = row[21]

        track_dict = {'Track ID': track_id,
                      'Name': name,
                      'Artist': artist,
                      'Album Artist': album_artist,
                      'Album': album,
                      'Grouping': grouping,
                      'Genre': genre,
                      'Size': size,
                      'Total Time': total_time,
                      'Track Number': track_number,
                      'Year': year,
                      'BPM': bpm,
                      'Bit Rate': bit_rate,
                      'Sample Rate': sample_rate,
                      'Comments': comments,
                      'Play Count': play_count,
                      'Compilation': compilation,
                      'Persistent ID': persistent_id,
                      'Location': location,
                      'Kind': 'MPEG audio file',
                      'Date Added': date_added,
                      'Play Date': play_date,
                      'Play Date UTC': play_date_utc}

        track_dict = dict((k, v) for k, v in track_dict.items() if v is not None)
        itunes_track[str(track_id)] = track_dict
    conn.commit()
    cur.close()
    conn.close()
    plist_dict['Tracks'] = itunes_track
    playlist_array = []
    logging.info("Generating 'master' library hidden playlist...")
    playlist_id = random.randint(100000, 999999)
    hexdigits = "0123456789ABCDEF"
    playlist_persistent_id = "".join([hexdigits[random.randint(0, 0xF)] for _ in range(16)])
    playlist_items = []
    logging.warning(playlist_persistent_id)
    for x in plist_dict['Tracks']:
        playlist_track_dict = {'Track ID': int(x)}
        playlist_items.append(playlist_track_dict)
    playlist = {'Name': playlist_name,
                'Playlist ID': playlist_id,
                'All Items': True,
                'Playlist Items': playlist_items}
    playlist_array.append(playlist)
    plist_dict['Playlists'] = playlist_array
    qualified_playlist_name = playlist_name + '.xml'
    with open(qualified_playlist_name, 'wb') as fp:
        plistlib.dump(plist_dict, fp, sort_keys=False)


def parse_args(arg_list):
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', '-d',
                        help='Name of postgres database [%(default)s]',
                        dest='database_name',
                        default=DEFAULT_DATABASE_NAME)
    parser.add_argument('--name', '-n',
                        help='Playlist name [%(default)s]',
                        dest='playlist_name',
                        default=DEFAULT_PLAYLIST_NAME)
    parser.add_argument('--view', '-v',
                        help='Table or view to export as playlist [%(default)s]',
                        dest='view_name',
                        default=DEFAULT_VIEW_NAME)
    parser.add_argument('--port', '-p',
                        help='Database port [%(default)s]',
                        dest='port',
                        default=DEFAULT_PORT)
    parser.add_argument('--user', '-u',
                        help='Postgres username [%(default)s]',
                        dest='username',
                        default=DEFAULT_USER_NAME)
    parser.add_argument('--pass', '-x',
                        help='Postgres password [%(default)s]',
                        dest='password',
                        default=DEFAULT_PASSWORD)
    parser.add_argument('--format', '-f',
                        help='Playlist file format [%(default)s]',
                        dest='format',
                        default=DEFAULT_FORMAT)
    args = parser.parse_args(args=arg_list)
    return args


def escape_xml_illegal_chars(val, replacement='?'):
    _illegal_xml_chars_RE = re.compile(u'[\x00-\x08\x0b\x0c\x0e-\x1F\uD800-\uDFFF\uFFFE\uFFFF]')

    if val is not None:
        return _illegal_xml_chars_RE.sub(replacement, val)
    else:
        return None


if __name__ == '__main__':
    main()
