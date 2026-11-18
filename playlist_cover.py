"""Cover for a playlist that has no album art: an image of its main theme.

Order of preference:
  1. the film or series (Film property): its poster, from Wikipedia;
  2. the artist who makes up most of the list: their photo, from the music service,
     else from Wikipedia;
  3. the genre named in the playlist title (Soul, Rock, Jazz...): a theme cover;
  4. the period named in the title ("années 90", "1993-1996"): a theme cover;
  5. a neutral "Playlist" cover.
Theme covers are the PNG files of covers/ (made by tools/make_covers.py), served from this
public repository. Every lookup is best effort: a failure returns the next choice.
"""
import os
import re
import unicodedata
from collections import Counter
from urllib.parse import quote

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "").strip() or "zouga31400/listening-notes"
COVER_URL = f"https://raw.githubusercontent.com/{REPO}/main/covers/" + "{slug}.png"
USER_AGENT = f"listening-notes/1.0 (https://github.com/{REPO})"
WIKI_LANGS = ("en", "fr")
TIMEOUT = 15

# Genre words (accents removed, whole words) -> theme cover. The first one named in the title wins.
GENRES = {
    "soul": ("soul", "motown"), "rock": ("rock", "punk", "grunge", "indie"),
    "jazz": ("jazz", "swing", "bebop"), "blues": ("blues",), "funk": ("funk",),
    "disco": ("disco",), "hiphop": ("hip hop", "hiphop", "rap"),
    "reggae": ("reggae", "ska", "dub"),
    "electro": ("electro", "eurodance", "dance", "techno", "house", "trance", "edm"),
    "classique": ("classique", "classical", "opera", "symphonie", "symphony"),
    "metal": ("metal",), "country": ("country", "folk", "bluegrass"),
    "lounge": ("lounge", "chill", "ambient", "relax"),
    "chanson": ("chanson", "chansons francaises", "variete"),
    "latino": ("latino", "latin", "salsa", "reggaeton", "bossa"),
    "rnb": ("r and b", "rnb", "rhythm and blues"), "pop": ("pop",),
}
DECADES = {"60": "60s", "70": "70s", "80": "80s", "90": "90s", "00": "2000s", "10": "2010s",
           "20": "2020s"}
PERSON_RE = re.compile(r"singer|band|rapper|musician|group|duo|songwriter|vocalist|composer|"
                       r"\bdj\b|chanteu|groupe|musicien|compositeur|rappeu|trio|quartet", re.I)
SCREEN_RE = re.compile(r"film|television|series|miniseries|série|téléfilm|feuilleton", re.I)


def flat(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.replace("&", " and ")).split())


def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=TIMEOUT,
                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- Wikipedia

def wiki_titles(query, lang, limit=3):
    data = get_json(f"https://{lang}.wikipedia.org/w/api.php",
                    {"action": "query", "list": "search", "srsearch": query, "srlimit": limit,
                     "format": "json"})
    return [h["title"] for h in (data.get("query") or {}).get("search", [])]


def wiki_summary(title, lang):
    return get_json(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                    + quote(title.replace(" ", "_"), safe="()_,'"))


def wiki_image(query, lang, accept, name_words):
    """Thumbnail of the first search result whose summary is accepted by `accept` and whose
    title holds every word of `name_words`."""
    for title in wiki_titles(query, lang):
        if not set(name_words) <= set(flat(title).split()):
            continue
        summary = wiki_summary(title, lang)
        image = (summary.get("thumbnail") or {}).get("source")
        if image and accept(summary):
            return image
    return ""


def screen_title(film):
    """('Miami Vice', '1984', is_series) from 'Miami Vice (série TV, 1984-1990)'."""
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", film.strip())
    title, extra = (m.group(1), m.group(2)) if m else (film.strip(), "")
    year = re.search(r"\b(1[89]\d\d|20\d\d)\b", extra)
    series = bool(re.search(r"s[ée]rie|series|tv|t[ée]l[ée]", extra, re.I))
    return title.strip(), year.group(1) if year else "", series


def film_cover(film):
    title, year, series = screen_title(film)
    words = flat(title).split()
    if not words:
        return ""
    for lang in WIKI_LANGS:
        kind = {"en": "television series" if series else "film",
                "fr": "série télévisée" if series else "film"}[lang]
        try:
            def accept(summary):
                text = (summary.get("description") or "") + " " + (summary.get("extract") or "")[:300]
                return bool(SCREEN_RE.search(summary.get("description") or "")) and (
                    not year or year in text or year in (summary.get("title") or ""))
            image = wiki_image(f"{title} {year} {kind}".strip(), lang, accept, words)
        except (requests.RequestException, ValueError):
            continue
        if image:
            return image
    return ""


# ---------------------------------------------------------------- artists

def dominant_artist(artists):
    """The artist behind at least half of the list (and 3 tracks or more), else None."""
    names = [a.strip() for a in artists if a and a.strip()]
    if len(names) < 3:
        return None
    best, count = Counter(n.lower() for n in names).most_common(1)[0]
    if count < 3 or count * 2 < len(names):
        return None
    return next(n for n in names if n.lower() == best)


def service_artist_image(service, name):
    data = service.call("GET", "/artist/search", query=name, limit=5)
    for item in (data.get("artists") or {}).get("items", []):
        if flat(item.get("name")) != flat(name):
            continue
        image = item.get("image") or {}
        url = (image.get("large") or image.get("extralarge") or image.get("medium")
               or image.get("small") or item.get("picture") or "")
        if url:
            return url
    return ""


def artist_cover(name, service=None):
    if service is not None:
        try:
            image = service_artist_image(service, name)
            if image:
                return image
        except Exception:  # best effort: the music service may not offer the lookup
            pass
    words = flat(name).split()
    for lang in WIKI_LANGS:
        try:
            image = wiki_image(f"{name} musician", lang,
                               lambda s: bool(PERSON_RE.search(s.get("description") or "")), words)
        except (requests.RequestException, ValueError):
            continue
        if image:
            return image
    return ""


# ---------------------------------------------------------------- theme covers

def genre_of(name):
    text = f" {flat(name)} "
    found = []
    for slug, words in GENRES.items():
        for word in words:
            at = text.find(f" {word} ")
            if at >= 0:
                found.append((at, slug))
    return min(found)[1] if found else None


def decade_of(name):
    """'Eurodance 1993-1996' -> 90s; 'Rock années 80', 'Hits 80s' -> 80s; 'années 2000' -> 2000s."""
    text = flat(name)
    m = re.search(r"(?<!\d)((?:19[5-9]|20[0-2])\d)(?!\d)", text)          # a year, or a range of years
    if m:
        return DECADES.get(m.group(1)[2:3] + "0")
    m = re.search(r"\bannees? (\d{2})\b|\b(\d0) ?s\b", text)
    return DECADES.get(m.group(1) or m.group(2)) if m else None


def theme_cover(name):
    slug = genre_of(name) or decade_of(name) or "musique"
    return COVER_URL.format(slug=slug)


# ---------------------------------------------------------------- entry point

def find_cover(name, film=None, artists=(), service=None):
    """(url, source) of the best cover for a playlist without album art; source is one of
    'film', 'artist', 'theme'. Never raises."""
    try:
        if film:
            image = film_cover(film)
            if image:
                return image, "film"
        artist = dominant_artist(artists)
        if artist:
            image = artist_cover(artist, service)
            if image:
                return image, "artist"
    except Exception:  # a cover is a nicety: never fail a playlist because of it
        pass
    return theme_cover(name or ""), "theme"
