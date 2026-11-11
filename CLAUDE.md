# Rules for this repository

When a message starts with "Playlist", "Liste musique", "Liste Qobuz" or "Music list" (with or without a colon), the rest of the message is a playlist request, as short as "Playlist film Titanic" or "Playlist rock 90s, 15 titres". For it, produce a list of tracks that fits the request (30 tracks unless I say otherwise), in original studio versions, with exact artist and track names, and no duplicates. Write it in the expected format to lists/<short-name-without-accents>.txt, with a clear playlist name on the first line. Commit and push. Do not modify any other file. To fix an error reported in a log, modify lists_sync.py or the workflow only.

When the request is the music of a film (or a series), e.g. "film Titanic", list only the songs and existing tracks heard in it (licensed songs, original songs written for it, and existing instrumental tracks such as "Misirlou" in Pulp Fiction). Leave out the score composed for the film (orchestral themes and cues). If the film has few such tracks, give a shorter list with only those and say why it is short, instead of filling it with the score. Playlists get the description "Created by <author>": the default author is `playlist_author` in `config.txt` (fsyl20); when my request names another author (e.g. "Playlist rock 90s par Marie", "auteur Marie"), write it on an "Author: <name>" line right after the playlist name in a `lists/` file, or in the "Author" property of the Notion page. Name the film for the soundtrack check: a "Film: <title> (<year>)" line right after the playlist name in a `lists/` file, or the "Film" property of the Notion page. When the music service already has a soundtrack album with every song of the list, and those songs are at least half of the album, no playlist is created: the Notion page gets the status "Album" with the album link, and the notification opens the album. Otherwise the playlist is created as usual, with the album link added when the album holds at least half of the songs.

## Expected format

```
Playlist name
Artist - Title
Artist - Title
```

Pushing the file triggers `.github/workflows/lists-sync.yml`, which runs `lists_sync.py` and creates the private playlist on the music service. Do not modify `radio_listen.py` or `.github/workflows/radio.yml`.

The list is the reference for playlists created by this tool (description "Created by <author>", or the older "Created by lists_sync.py"): each run adds what is missing and removes tracks that are no longer chosen (wrong version, line removed) and duplicates. A playlist with the same name not created by the tool is only completed, never trimmed.

## Notion variant

`notion_lists.py` (workflow `.github/workflows/notion-lists.yml` on `main`, started every 2 min by an external call to the workflow_dispatch API, with a GitHub schedule as backup; runs with nothing to do delete themselves from the history on the next run) reads the "Playlist lists" Notion database, whose ID is the `notion_lists_db` key in `config.txt`. One page per playlist: title = playlist name, content = one "Artist - Title" line per track, "Status" property set to "To create". The script creates or syncs the playlist, replaces the "Sync report" at the bottom of the page (one line per track with the version and album picked, tracks not found, tracks removed) and sets the status to "Created" or "Error" (optional properties: "Added", "Not found", "Link"). Uses the `NOTION_TOKEN` secret, shared with the radio. At the end of each playlist (Notion page or `lists/` file), a push notification is sent through ntfy on the `NTFY_LISTS_TOPIC` secret, separate from the radio's `NTFY_TOPIC` (nothing is sent if it is not set).

When the Notion connector is available:
- These playlist requests can also go to Notion: create a page in "Playlist lists" (title = playlist name; content = one "Artist - Title" paragraph per track; Status = "To create"; for a film or series, Film = its title and year; Author = the author only when my request names one). Then, if GitHub tools are available, run the `notion-lists.yml` workflow on `main` right away instead of waiting for the schedule.
- "Check the playlist <name>" (or "Vérifie la playlist <nom>"): read the page's "Sync report". For each track that is not found or not the original studio version, correct or replace its line in the page content above the report (never edit the report itself), set Status back to "To create", and run the workflow as above. Summarize what was changed.
