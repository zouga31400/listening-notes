"""Copies of a playlist sorted by mood (tempo and energy) and period.

For each chosen playlist (config.txt: sort_playlists, or the workflow input), every
track gets the same classification as the radio tracks (radio_listen.py):
  - its original year (oldest release among the relevant versions), giving the period
    60-80s / 90-2000s / 2010s+ and the decade;
  - its tempo (BPM) and energy measured on Deezer's 30-second preview, giving the
    mood Calme / Groove / Energique (thresholds in config.txt).
The track then goes to the copies named by sort_playlist_name, e.g. "Moto Groove 90-2000s".
The original playlist is never modified.

Copies created by this script (description "Created by sort_playlist.py") are kept in
sync with the original: each run adds what is missing and removes tracks that left the
original or changed category. A playlist with the same name not created by the script
is only completed, never trimmed.
"""
import os
import sys
import time

from analyze_playlists import MusicService, flat, load_config, log
from radio_listen import (decade_label, log_detail, mood_of, norm, period_label, pick,
                          preview_features, search_title)

MARKER = "Created by sort_playlist.py"
BATCH = 50


def entries(service, playlist_id):
    """(track id, playlist_track_id) of each entry of a playlist."""
    found, offset = [], 0
    while True:
        data = service.call("GET", "/playlist/get", playlist_id=playlist_id,
                            extra="tracks", limit=500, offset=offset)
        items = (data.get("tracks") or {}).get("items", [])
        found.extend((t["id"], t.get("playlist_track_id")) for t in items if t.get("id"))
        if len(items) < 500:
            return found
        offset += 500


def remove(service, playlist_id, playlist_track_ids):
    for i in range(0, len(playlist_track_ids), BATCH):
        service.call("POST", "/playlist/deleteTracks", playlist_id=playlist_id,
                     playlist_track_ids=",".join(str(t) for t in playlist_track_ids[i:i + BATCH]))


def original_year(service, track):
    """Oldest release year among the relevant versions of the track (a remaster counts
    in its original decade), as in radio_listen.classify_existing."""
    artist = (track.get("performer") or {}).get("name") or ""
    title = track.get("title") or ""
    try:
        versions = service.call("GET", "/catalog/search", query=f"{artist} {search_title(title)}",
                                limit=30)
        versions = (versions.get("tracks") or {}).get("items", [])
    except Exception:
        versions = []
    best = pick(versions + [track], norm(artist), norm(title))
    year = (best or {}).get("_original_year")
    if not year:
        date = ((track.get("album") or {}).get("release_date_original") or "")[:4]
        year = int(date) if date.isdigit() else None
    return year


def copy_name(pattern, source, year, mood):
    values = {"{playlist}": source.strip(), "{mood}": mood or "", "{period}": period_label(year),
              "{decade}": decade_label(year), "{year}": str(year or "")}
    used = [k for k in values if k in pattern]
    if any(not values[k] for k in used):
        return None
    for k in used:
        pattern = pattern.replace(k, values[k])
    return " ".join(pattern.split())


def sort_one(service, source, playlists, config, pattern):
    tracks = service.playlist_tracks(source["id"])
    log(f"Sorting '{source['name']}': {len(tracks)} tracks")
    groups, unsorted, no_audio = {}, 0, 0
    for n, t in enumerate(tracks, 1):
        artist = (t.get("performer") or {}).get("name") or ""
        title = t.get("title") or ""
        year = original_year(service, t)
        feats = preview_features((t.get("isrc") or "").strip().upper() or None, artist, title)
        mood = mood_of(feats, config) if feats else ""
        if not feats:
            no_audio += 1
        name = copy_name(pattern, source["name"], year, mood)
        if name is None:
            unsorted += 1
            log_detail(f"   ? {artist} - {title}: year {year or '?'}, no category")
        else:
            groups.setdefault(name, []).append(t["id"])
            log_detail(f"   {artist} - {title}: {year}, {feats['bpm']} BPM, "
                       f"energy {feats['energy']} -> {name}")
        if n % 25 == 0:
            log(f"   {n}/{len(tracks)} tracks analyzed")
        time.sleep(0.2)

    by_name = {flat(p.get("name")): p for p in playlists}
    added = removed = created = 0
    for name in sorted(groups):
        wanted = list(dict.fromkeys(groups[name]))
        target = by_name.get(flat(name))
        if target is None:
            playlist_id = service.create_playlist(
                name, f"{MARKER} from the playlist {source['name']}")
            created += 1
            current = []
            managed = True
        else:
            playlist_id = target["id"]
            current = entries(service, playlist_id)
            managed = MARKER.lower() in (target.get("description") or "").lower()
        present = {tid for tid, _ in current}
        missing = [tid for tid in wanted if tid not in present]
        for i in range(0, len(missing), BATCH):
            service.call("POST", "/playlist/addTracks", playlist_id=playlist_id,
                         track_ids=",".join(str(x) for x in missing[i:i + BATCH]),
                         no_duplicate="true")
        added += len(missing)
        extra = []
        if managed:
            keep, seen = set(wanted), set()
            for tid, entry in current:
                if (tid not in keep or tid in seen) and entry:
                    extra.append(entry)
                seen.add(tid)
            if extra:
                remove(service, playlist_id, extra)
            removed += len(extra)
        log(f"   {name}: {len(wanted)} tracks"
            + (f", +{len(missing)}" if missing else "") + (f", -{len(extra)}" if extra else "")
            + ("" if managed else " (not created by this script: only completed)"))

    # Copies of this playlist that no longer get any track are emptied.
    prefix = MARKER.lower() + " from the playlist " + source["name"].strip().lower()
    for p in playlists:
        if (p.get("description") or "").strip().lower() == prefix and flat(p.get("name")) \
                not in {flat(g) for g in groups}:
            old = [e for _, e in entries(service, p["id"]) if e]
            if old:
                remove(service, p["id"], old)
                removed += len(old)
                log(f"   {p['name']}: emptied (no track left in this category)")
    log(f"   Done: {len(groups)} copies, {created} created, {added} additions, {removed} removals, "
        f"{unsorted} tracks without year or mood ({no_audio} without audio analysis)")


def main():
    config = load_config()
    if not os.environ.get("MUSIC_EMAIL") or not os.environ.get("MUSIC_PASSWORD"):
        raise SystemExit("MUSIC_EMAIL / MUSIC_PASSWORD secrets missing")
    wanted = os.environ.get("PLAYLISTS", "").strip() or config.get("sort_playlists", "")
    pattern = config.get("sort_playlist_name") or "{playlist} {mood} {period}"
    if not wanted:
        raise SystemExit("No playlist to sort (config.txt: sort_playlists)")
    service = MusicService(os.environ["MUSIC_EMAIL"].strip(), os.environ["MUSIC_PASSWORD"].strip())
    service.login()
    log("Service login OK")
    playlists = service.list_playlists()
    mine = [p for p in playlists if service.user_id is None
            or (p.get("owner") or {}).get("id") in (None, service.user_id)]
    by_name = {flat(p.get("name")): p for p in mine}
    for name in (n.strip() for n in wanted.split(",")):
        source = by_name.get(flat(name))
        if source is None:
            log(f"Playlist '{name}' not found among your playlists")
            continue
        sort_one(service, source, mine, config, pattern)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(str(e))
        sys.exit(1)
