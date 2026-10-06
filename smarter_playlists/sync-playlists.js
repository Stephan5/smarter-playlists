// Replaces the contents of Music user playlists with the given tracks, creating playlists and folders as needed.
//
// Usage: osascript -l JavaScript sync-playlists.js < request.json
//
// The request is JSON on standard input:
//   {"dryRun": false,
//    "descriptionPrefix": "Made by Smarter Playlists",
//    "playlists": [{"name": "October 2026", "folder": ["Smarter Playlists", "2026"], "description": "...",
//                   "trackIds": ["C358A93CBB48422E", ...]}, ...]}
//
// Playlists are matched by name within their folder, so playlists of the same name elsewhere are left alone. The
// exception is a playlist this script made, recognised by its description, which is moved if its folder has changed.
//
// Prints a JSON array, in the same order as the request, with what was (or with dryRun, would be) changed for each
// playlist, or the error that stopped it from being changed.

ObjC.import('Foundation');

function run() {
  const { dryRun, descriptionPrefix, playlists } = JSON.parse(readStandardInput());

  const Music = Application('Music');
  const library = Music.libraryPlaylists[0];

  // Looking tracks up by database ID is much faster than a 'whose' query per track
  const libraryPersistentIds = library.tracks.persistentID();
  const libraryDatabaseIds = library.tracks.id();
  const databaseIds = {};
  libraryPersistentIds.forEach((persistentId, i) => { databaseIds[persistentId] = libraryDatabaseIds[i]; });

  const folders = new Folders(Music);

  return JSON.stringify(playlists.map(request => {
    try {
      return syncPlaylist(Music, library, databaseIds, folders, descriptionPrefix, request, dryRun);
    } catch (error) {
      return { error: error.message };
    }
  }));
}

function syncPlaylist(Music, library, databaseIds, folders, descriptionPrefix,
                      { name, folder, description, trackIds }, dryRun) {
  const found = trackIds.filter(trackId => trackId in databaseIds);
  const missing = trackIds.filter(trackId => !(trackId in databaseIds));

  // undefined when the folder doesn't exist yet, so nothing can be in it
  const parentId = folders.persistentId(folder);
  let playlist = parentId === undefined ? null : findUserPlaylist(Music, name, parentId);
  let moved = false;
  if (!playlist) {
    playlist = findOwnPlaylistElsewhere(Music, name, parentId, descriptionPrefix);
    moved = !!playlist;
  }

  const tracksChanged = !playlist || JSON.stringify(playlist.tracks.persistentID()) !== JSON.stringify(found);
  const descriptionChanged = !playlist || playlist.description() !== description;
  const result = { created: !playlist, moved, changed: tracksChanged || descriptionChanged || moved,
                   tracks: found.length, missing, dryRun };

  if (!result.changed || dryRun) {
    return result;
  }

  if (!playlist) {
    playlist = Music.make({ new: 'userPlaylist', withProperties: { name, description } });
    folders.moveInto(playlist, folder);
  } else {
    if (moved) {
      folders.moveInto(playlist, folder);
    }
    if (descriptionChanged) {
      playlist.description = description;
    }
    if (tracksChanged && playlist.tracks.length > 0) {
      Music.delete(playlist.tracks);
    }
  }

  if (tracksChanged) {
    for (const trackId of found) {
      Music.duplicate(library.tracks.byId(databaseIds[trackId]), { to: playlist });
    }
  }

  return result;
}

// The user playlist with this name in the given folder (null for the top level)
function findUserPlaylist(Music, name, parentId) {
  const playlists = Music.userPlaylists.whose({ name })().filter(playlist => parentPersistentId(playlist) === parentId);
  const editable = playlists.filter(isEditable);
  if (playlists.length > 0 && editable.length === 0) {
    throw new Error('"' + name + '" is a smart playlist or folder and cannot be replaced');
  }
  return editable[0] || null;
}

// A playlist with this name made by this script, but in another folder
function findOwnPlaylistElsewhere(Music, name, parentId, descriptionPrefix) {
  return Music.userPlaylists.whose({ name })().find(playlist =>
    isEditable(playlist) && parentPersistentId(playlist) !== parentId
    && (playlist.description() || '').startsWith(descriptionPrefix)) || null;
}

function isEditable(playlist) {
  return !playlist.smart() && playlist.specialKind() === 'none' && playlist.class() === 'userPlaylist';
}

function parentPersistentId(playlist) {
  try {
    return playlist.parent().persistentID();
  } catch (error) {
    // Playlists at the top level have no parent
    return null;
  }
}

// The folders in the library, by path, e.g. "Smarter Playlists/2026"
class Folders {
  constructor(Music) {
    this.Music = Music;
    this.byPath = {};

    // Fetching each property for every playlist at once is much faster than for one playlist at a time
    const classes = Music.playlists.class();
    const databaseIds = Music.playlists.id();
    const folders = {};
    classes.forEach((playlistClass, i) => {
      if (playlistClass === 'folderPlaylist') {
        const folder = Music.playlists.byId(databaseIds[i]);
        folders[folder.persistentID()] = { folder, name: folder.name(), parentId: parentPersistentId(folder) };
      }
    });

    const path = persistentId => {
      const { name, parentId } = folders[persistentId];
      return parentId === null ? name : path(parentId) + '/' + name;
    };
    for (const persistentId in folders) {
      this.byPath[path(persistentId)] = folders[persistentId].folder;
    }
  }

  // The folder's persistent ID, null for the top level, or undefined if it doesn't exist
  persistentId(folder) {
    if (folder.length === 0) {
      return null;
    }
    const existing = this.byPath[folder.join('/')];
    return existing ? existing.persistentID() : undefined;
  }

  // Moves a playlist or folder into the given folder, creating any folders that don't exist
  moveInto(item, folder) {
    if (folder.length === 0) {
      return;
    }
    this.Music.move(item, { to: this.findOrCreate(folder) });
  }

  findOrCreate(folder) {
    const path = folder.join('/');
    if (!this.byPath[path]) {
      const created = this.Music.make({ new: 'folderPlaylist', withProperties: { name: folder[folder.length - 1] } });
      this.moveInto(created, folder.slice(0, -1));
      this.byPath[path] = created;
    }
    return this.byPath[path];
  }
}

function readStandardInput() {
  const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
  return $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding).js;
}
