"""Creates music service playlists from the pages of a Notion database.

Expected Notion database:
  - a title property       -> playlist name
  - "Status" (select or status): "To create", "Created", "Error"
  - optional: "Added" and "Not found" (number), "Link" (URL)
Page content: one "Artist - Title" line per track (paragraphs, bullets,
numbered lists, to-dos or a code block). If the page title is empty, the
first line of the content is used as the name.

Only pages with the "To create" status are processed. The playlist is created,
or synced with the page if this tool created it (wrong or removed tracks are taken
out). The report (each line with the track picked) replaces the previous one at
the bottom of the page, and the status moves to "Created" or "Error". To fix a
track, edit its line and set the status back to "To create".

Database ID: the notion_lists_db key in config.txt (or the NOTION_DATABASE_ID
variable). Secrets: NOTION_TOKEN, MUSIC_EMAIL, MUSIC_PASSWORD.
"""

import os
import re
import sys
import time

import requests

import lists_sync as lq

NOTION = "https://api.notion.com/v1"
NOTION_VERSION = os.environ.get("NOTION_VERSION", "2022-06-28")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
CONFIG_KEY = "notion_lists_db"
STATUS_PROP = "Status"
TODO, DONE, ERROR = "To create", "Created", "Error"
REPORT_HEADING = "Sync report"
# Also recognizes reports written under the previous heading ("<name> report").
REPORT_HEADING_RE = re.compile(r"\w+ report", re.IGNORECASE)
TEXT_BLOCKS = ("paragraph", "bulleted_list_item", "numbered_list_item", "to_do",
               "quote", "callout", "toggle", "code")


class NotionError(RuntimeError):
    pass


class Notion:
    def __init__(self, token):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def call(self, method, path, body=None, params=None):
        for attempt in range(5):
            try:
                r = requests.request(method, NOTION + path, headers=self._headers,
                                     json=body, params=params, timeout=30)
            except requests.RequestException:
                if attempt == 4:
                    raise NotionError(f"Notion unreachable ({path.split('/')[1]})") from None
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json()
                    code, message = detail.get("code"), detail.get("message")
                except ValueError:
                    code, message = None, None
                raise NotionError(f"Notion refused the request (HTTP {r.status_code}, {code}): {message}")
            return r.json()
        raise NotionError("Notion unavailable")

    def paginate(self, method, path, body=None):
        cursor = None
        while True:
            if method == "POST":
                payload = dict(body or {}, page_size=100)
                if cursor:
                    payload["start_cursor"] = cursor
                data = self.call("POST", path, payload)
            else:
                params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
                data = self.call("GET", path, params=params)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")


def plain(rich):
    return "".join(part.get("plain_text", "") for part in rich or [])


def config_value(key):
    """Reads one 'key = value' line from config.txt (lines starting with # are ignored)."""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == key:
                        return v.strip()
    except FileNotFoundError:
        pass
    return ""


def database_id(raw):
    """Accepts the bare ID or the database's full URL."""
    path = raw.split("?")[0].split("#")[0].replace("-", "")
    found = re.findall(r"[0-9a-fA-F]{32,}", path)
    if not found:
        raise NotionError(f"Notion database ID ({CONFIG_KEY}) can't be read")
    return found[-1][-32:].lower()


def page_content(notion, page_id):
    """(text lines before the report, ids of the report blocks to replace)."""
    lines, report_ids, previous, in_report = [], [], None, False
    for block in notion.paginate("GET", f"/blocks/{page_id}/children"):
        kind = block.get("type")
        content = block.get(kind) or {}
        if in_report:
            report_ids.append(block["id"])
            continue
        if kind and kind.startswith("heading"):
            text = plain(content.get("rich_text"))
            if REPORT_HEADING_RE.fullmatch(text.strip()):
                in_report = True
                if previous and previous.get("type") == "divider":
                    report_ids.append(previous["id"])
                report_ids.append(block["id"])
                continue
            lines.append(text)
        elif kind in TEXT_BLOCKS:
            lines.extend(plain(content.get("rich_text")).split("\n"))
        previous = block
    return lines, report_ids


def text_block(kind, text):
    return {"object": "block", "type": kind,
            kind: {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}}


class Database:
    def __init__(self, notion, db_id):
        self.notion, self.id = notion, db_id
        schema = notion.call("GET", f"/databases/{db_id}").get("properties", {})
        self.props = {name: p.get("type") for name, p in schema.items()}
        self.title_prop = next((n for n, t in self.props.items() if t == "title"), None)
        self.status_type = self.props.get(STATUS_PROP)
        if self.status_type not in ("select", "status"):
            raise NotionError(f"The database needs a \"{STATUS_PROP}\" property "
                              "of type Select or Status")

    def pending(self):
        body = {"filter": {"property": STATUS_PROP, self.status_type: {"equals": TODO}},
                "sorts": [{"timestamp": "created_time", "direction": "ascending"}]}
        return list(self.notion.paginate("POST", f"/databases/{self.id}/query", body))

    def title_of(self, page):
        prop = (page.get("properties") or {}).get(self.title_prop) or {}
        return plain(prop.get("title")).strip()

    def finish(self, page_id, status, added=None, missing=None, link=None, blocks=(),
               old_report=()):
        props = {STATUS_PROP: {self.status_type: {"name": status}}}
        if added is not None and self.props.get("Added") == "number":
            props["Added"] = {"number": added}
        if missing is not None and self.props.get("Not found") == "number":
            props["Not found"] = {"number": missing}
        if link and self.props.get("Link") == "url":
            props["Link"] = {"url": link}
        if blocks:
            for block_id in old_report:   # the new report replaces the previous one
                self.notion.call("DELETE", f"/blocks/{block_id}")
            children = [{"object": "block", "type": "divider", "divider": {}},
                        text_block("heading_2", REPORT_HEADING), *blocks]
            for i in range(0, len(children), 100):
                self.notion.call("PATCH", f"/blocks/{page_id}/children",
                                 {"children": children[i:i + 100]})
        self.notion.call("PATCH", f"/pages/{page_id}", {"properties": props})


def report_blocks(name, result, bad, link):
    action = ("created" if result["created"] else
              "synced with the list" if result["synced"] else "completed")
    blocks = [
        text_block("paragraph",
                   f"Playlist \"{name}\" {action}: {result['added']} added, "
                   f"{result['already']} already present, {len(result['removed'])} removed, "
                   f"{len(result['missing'])} not found."),
        *([text_block("paragraph", f"Link: {link}")] if link else []),
        text_block("paragraph", "To fix a track: correct or replace its line above (exact artist "
                                "and title), then set Status back to \"To create\". Tracks no "
                                "longer chosen are removed from the playlist."),
        text_block("heading_3", "Tracks"),
    ]
    for row in result["tracks"]:
        if row["status"] == "not found":
            text = f"✗ {row['line']} — not found on the service"
        else:
            text = f"✓ {row['found']}"
            if row.get("album"):
                text += f" · album: {row['album']}"
            if row["status"] == "added":
                text += " (new)"
        blocks.append(text_block("bulleted_list_item", text))
    if result["removed"]:
        blocks.append(text_block("heading_3", "Removed from the playlist"))
        blocks += [text_block("bulleted_list_item", r) for r in result["removed"]]
    if bad:
        blocks.append(text_block("heading_3", "Unreadable lines"))
        blocks += [text_block("bulleted_list_item", t) for _, t in bad]
    return blocks


def handle(db, service, page):
    page_id = page["id"]
    name = db.title_of(page) or None
    lines, old_report = page_content(db.notion, page_id)
    name, entries, bad = lq.parse_lines(lines, name=name)
    lq.log(f"=== Notion page: {name or '(no name)'}")
    if not name or not entries:
        lq.log("  No readable track (expected one \"Artist - Title\" line per track).")
        db.finish(page_id, ERROR, old_report=old_report, blocks=[text_block(
            "paragraph", "No readable track. Expected format: one \"Artist - Title\" line per track.")])
        return False
    try:
        result = lq.build_playlist(service, name, entries, bad, "Source: Notion page")
    except lq.ServiceError as e:
        lq.log(f"  Error: {e}")
        db.finish(page_id, ERROR, old_report=old_report,
                  blocks=[text_block("paragraph", f"Service error: {e}")])
        return False

    web = os.environ.get("MUSIC_WEB_URL", "").strip().rstrip("/")
    link = f"{web}/playlist/{result['playlist_id']}" if web else None
    db.finish(page_id, DONE, added=result["added"], missing=len(result["missing"]), link=link,
              blocks=report_blocks(name, result, bad, link), old_report=old_report)
    return True


def count_pending():
    """--count mode: writes pending=<n> to GITHUB_OUTPUT (1 on a Notion error, so the
    processing job runs and reports it). Doesn't touch the music service."""
    token = os.environ.get("NOTION_TOKEN", "").strip()
    raw_db = config_value(CONFIG_KEY) or os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not token or not raw_db:
        count = 0
        lq.log(f"Notion lists: not configured (NOTION_TOKEN secret or {CONFIG_KEY} missing).")
    else:
        try:
            count = len(Database(Notion(token), database_id(raw_db)).pending())
            lq.log(f"{count} page(s) with the \"{TODO}\" status.")
        except NotionError as e:
            count = 1
            lq.log(f"{e} (the processing job will run and report it)")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"pending={count}\n")
    return 0


def main():
    token = os.environ.get("NOTION_TOKEN", "").strip()
    raw_db = config_value(CONFIG_KEY) or os.environ.get("NOTION_DATABASE_ID", "").strip()
    if not token or not raw_db:
        lq.log(f"Notion lists: not configured (NOTION_TOKEN secret or {CONFIG_KEY} missing).")
        return 0
    try:
        db = Database(Notion(token), database_id(raw_db))
        pages = db.pending()
    except NotionError as e:
        lq.log(str(e))
        return 1
    if not pages:
        lq.log(f"No page with the \"{TODO}\" status.")
        return 0
    lq.log(f"{len(pages)} page(s) to process.")

    service = lq.connect()
    if service is None:
        return 1

    failures = 0
    for page in pages:
        try:
            if not handle(db, service, page):
                failures += 1
        except NotionError as e:
            failures += 1
            lq.log(f"  Notion error: {e}")
        except Exception as e:  # generic message: no detail that could hold a secret
            failures += 1
            lq.log(f"  Unexpected error ({type(e).__name__}).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(count_pending() if "--count" in sys.argv[1:] else main())
