# Rules for this repository

When I ask "Music list: <request>" (or "Liste musique : <request>"), produce a list of tracks that fits the request (30 tracks unless I say otherwise), in original studio versions, with exact artist and track names, and no duplicates. Write it in the expected format to lists/<short-name-without-accents>.txt, with a clear playlist name on the first line. Commit and push. Do not modify any other file. To fix an error reported in a log, modify lists_sync.py or the workflow only.

## Expected format

```
Playlist name
Artist - Title
Artist - Title
```

Pushing the file triggers `.github/workflows/lists-sync.yml`, which runs `lists_sync.py` and creates the private playlist on the music service. Do not modify `radio_listen.py` or `.github/workflows/radio.yml`.

The list is the reference for playlists created by this tool (description "Created by lists_sync.py"): each run adds what is missing and removes tracks that are no longer chosen (wrong version, line removed) and duplicates. A playlist with the same name not created by the tool is only completed, never trimmed.

## Notion variant

`notion_lists.py` (workflow `.github/workflows/notion-lists.yml` on `main`, started every 2 min by an external call to the workflow_dispatch API, with a GitHub schedule as backup; runs with nothing to do delete themselves from the history on the next run) reads the "Playlist lists" Notion database, whose ID is the `notion_lists_db` key in `config.txt`. One page per playlist: title = playlist name, content = one "Artist - Title" line per track, "Status" property set to "To create". The script creates or syncs the playlist, replaces the "Sync report" at the bottom of the page (one line per track with the version and album picked, tracks not found, tracks removed) and sets the status to "Created" or "Error" (optional properties: "Added", "Not found", "Link"). Uses the `NOTION_TOKEN` secret, shared with the radio.

When the Notion connector is available:
- "Music list: <request>" can also go to Notion: create a page in "Playlist lists" (title = playlist name; content = one "Artist - Title" paragraph per track; Status = "To create"). Then, if GitHub tools are available, run the `notion-lists.yml` workflow on `main` right away instead of waiting for the schedule.
- "Check the playlist <name>" (or "Vérifie la playlist <nom>"): read the page's "Sync report". For each track that is not found or not the original studio version, correct or replace its line in the page content above the report (never edit the report itself), set Status back to "To create", and run the workflow as above. Summarize what was changed.
