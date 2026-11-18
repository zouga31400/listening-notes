"""Generates the theme covers used for playlists with no album art (covers/*.png).

A cover is a gradient with the theme's name, in the style of an album sleeve. They are
served from this public repository (raw.githubusercontent.com) and used as Notion page
covers by playlist_cover.py. Run: python tools/make_covers.py (needs Pillow)."""
import os

from PIL import Image, ImageDraw, ImageFont

SIZE = 600
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "covers")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# slug: (label, subtitle, top colour, bottom colour)
THEMES = {
    "60s": ("60s", "Années 60", "#f6b93b", "#e55039"),
    "70s": ("70s", "Années 70", "#e58e26", "#6a3d9a"),
    "80s": ("80s", "Années 80", "#ff5fa2", "#4834d4"),
    "90s": ("90s", "Années 90", "#00d2d3", "#341f97"),
    "2000s": ("2000s", "Années 2000", "#48dbfb", "#222f3e"),
    "2010s": ("2010s", "Années 2010", "#ff9ff3", "#576574"),
    "2020s": ("2020s", "Années 2020", "#1dd1a1", "#10ac84"),
    "soul": ("Soul", "Soul · Motown", "#f8c291", "#b71540"),
    "rock": ("Rock", "Rock", "#576574", "#c0392b"),
    "jazz": ("Jazz", "Jazz", "#273c75", "#fbc531"),
    "blues": ("Blues", "Blues", "#0c2461", "#4a69bd"),
    "funk": ("Funk", "Funk", "#e84118", "#fbc531"),
    "disco": ("Disco", "Disco", "#8e44ad", "#f368e0"),
    "pop": ("Pop", "Pop", "#ff6b81", "#a29bfe"),
    "hiphop": ("Hip-hop", "Hip-hop · Rap", "#2d3436", "#fdcb6e"),
    "reggae": ("Reggae", "Reggae", "#27ae60", "#f1c40f"),
    "electro": ("Électro", "Électro · Dance", "#0abde3", "#5f27cd"),
    "classique": ("Classique", "Musique classique", "#2c3e50", "#bdc3c7"),
    "metal": ("Metal", "Metal", "#1e1e1e", "#c0392b"),
    "country": ("Country", "Country · Folk", "#d35400", "#f6e58d"),
    "lounge": ("Lounge", "Lounge · Chill", "#16a085", "#2c3e50"),
    "chanson": ("Chanson", "Chanson française", "#1e3799", "#e55039"),
    "latino": ("Latino", "Musiques latines", "#f78fb3", "#e15f41"),
    "rnb": ("R&B", "R&B", "#6c5ce7", "#fd79a8"),
    "musique": ("Playlist", "Playlist", "#485460", "#1e272e"),
}


def gradient(top, bottom):
    img = Image.new("RGB", (SIZE, SIZE))
    px = img.load()
    t = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    for y in range(SIZE):
        for x in range(SIZE):
            k = (y * 0.75 + x * 0.25) / SIZE
            px[x, y] = tuple(int(t[c] + (b[c] - t[c]) * k) for c in range(3))
    return img


def make(slug, label, subtitle, top, bottom):
    img = gradient(top, bottom).convert("RGBA")
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for r, alpha in ((470, 34), (340, 34), (210, 34)):     # vinyl-like rings
        d.ellipse((SIZE - r * 0.6 - r, SIZE - r * 0.6 - r, SIZE - r * 0.6 + r, SIZE - r * 0.6 + r),
                  outline=(255, 255, 255, alpha), width=3)
    img = Image.alpha_composite(img, layer)
    d = ImageDraw.Draw(img)
    size = 150
    while size > 40:
        font = ImageFont.truetype(FONT, size)
        if d.textlength(label, font=font) <= SIZE - 90:
            break
        size -= 6
    top = SIZE - 270 if subtitle.lower() != label.lower() else SIZE - 215
    d.text((44, top), label, font=font, fill=(255, 255, 255, 255))
    if subtitle.lower() != label.lower():
        d.text((50, SIZE - 85), subtitle.upper(), font=ImageFont.truetype(FONT, 26),
               fill=(255, 255, 255, 215))
    img.convert("RGB").save(os.path.join(OUT, f"{slug}.png"), optimize=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for slug, spec in THEMES.items():
        make(slug, *spec)
    print(f"{len(THEMES)} covers written to {os.path.abspath(OUT)}")
