```python
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

API = "https://www.dreamcancel.com/w/api.php"
BASE_URL = "https://www.dreamcancel.com"

CACHE_DIR = Path("data")
CACHE_TTL = 5 * 60
REQUEST_TIMEOUT = 20

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "KOFXVFrameDataDiscordBot/1.0"
})

CACHE_DIR.mkdir(parents=True, exist_ok=True)


def clean_value(value):
    """Remove basic wiki markup while preserving useful frame-data text."""
    if value is None:
        return ""

    value = str(value).strip()
    value = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[(https?://\S+)\s+([^\]]+)\]", r"\2", value)
    value = re.sub(r"'''?", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


def normalize_move(value):
    """
    Normalize alternate spellings for searching only.

    Examples:
      cl.A, cl+A, cla       -> cla
      j.AC, jA+C, jAC       -> jac
      4/6.C, 4/6+C, 4/6c   -> 46c
      236+A, qcf+A          -> qcfa
      214+B, qcb+B          -> qcbb
    """
    value = clean_value(value).casefold().strip()

    # Remove common directional arrows and whitespace.
    value = value.replace("→", "")
    value = re.sub(r"\s+", "", value)

    # Convert numeric motion notation to named shorthand before
    # removing punctuation.
    motion_aliases = (
        ("41236", "hcf"),
        ("63214", "hcb"),
        ("236", "qcf"),
        ("214", "qcb"),
        ("623", "dp"),
    )

    for numeric, shorthand in motion_aliases:
        value = re.sub(
            rf"(?<!\d){numeric}(?=[+.]?[a-z])",
            shorthand,
            value,
        )

    # Normalize separators and punctuation. This makes cl.A, cl+A,
    # cla and j.AC, jA+C, jAC searchable with the same key.
    value = re.sub(r"[^a-z0-9]", "", value)

    return value


def character_page(character):
    """Construct a Dream Cancel character data page title."""
    slug = character.replace(" ", "_")
    return f"The_King_of_Fighters_XV/{slug}/Data"


def source_page_url(character):
    return (
        f"{BASE_URL}/wiki/"
        f"{quote(character_page(character), safe='/')}"
    )


def fetch_wikitext(character):
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": character_page(character),
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    }

    response = SESSION.get(
        API, params=params, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()

    pages = payload.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") is not None:
        raise ValueError(
            f"Dream Cancel data page not found for {character}"
        )

    page = pages[0]
    revisions = page.get("revisions", [])
    if not revisions:
        raise ValueError(f"No revisions returned for {character}")

    revision = revisions[0]
    slots = revision.get("slots", {})
    main = slots.get("main", {})
    wikitext = main.get("content", main.get("*", ""))

    if not wikitext:
        raise ValueError(f"Empty data page for {character}")

    return {
        "wikitext": wikitext,
        "revid": revision.get("revid"),
        "timestamp": revision.get("timestamp"),
        "pageid": page.get("pageid"),
    }


def extract_templates(wikitext):
    """Extract complete MoveData-KOFXV templates, including nested braces."""
    templates = []
    marker = "{{MoveData-KOFXV"
    position = 0

    while True:
        start = wikitext.find(marker, position)
        if start == -1:
            break

        depth = 0
        i = start

        while i < len(wikitext) - 1:
            pair = wikitext[i:i + 2]

            if pair == "{{":
                depth += 1
                i += 2
                continue

            if pair == "}}":
                depth -= 1
                i += 2

                if depth == 0:
                    templates.append(wikitext[start:i])
                    position = i
                    break

                continue

            i += 1
        else:
            break

    return templates


def split_arguments(body):
    """Split template arguments without splitting nested templates."""
    parts = []
    current = []
    depth = 0
    i = 0

    while i < len(body):
        pair = body[i:i + 2]

        if pair == "{{":
            depth += 1
            current.extend(pair)
            i += 2
            continue

        if pair == "}}":
            depth = max(0, depth - 1)
            current.extend(pair)
            i += 2
            continue

        if body[i] == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(body[i])

        i += 1

    if current:
        parts.append("".join(current).strip())

    return parts


def parse_template(template):
    body = template[2:-2]
    parts = split_arguments(body)

    if not parts:
        return None

    if parts[0].strip().casefold() != "movedata-kofxv":
        return None

    move = {}

    for part in parts[1:]:
        if "=" not in part:
            continue

        key, value = part.split("=", 1)
        move[key.strip()] = clean_value(value)

    if not any(
        move.get(field)
        for field in ("name", "input", "moveId")
    ):
        return None

    return move


def parse_moves(wikitext):
    moves = []

    for template in extract_templates(wikitext):
        move = parse_template(template)
        if move:
            moves.append(move)

    return moves


def cache_path(character):
    safe_name = re.sub(
        r"[^A-Za-z0-9_-]+", "_", character
    ).strip("_")
    return CACHE_DIR / f"{safe_name}.json"


def read_cache(character):
    path = cache_path(character)

    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            cached = json.load(file)

        if not isinstance(cached.get("moves"), list):
            return None

        return cached
    except (OSError, ValueError, TypeError):
        return None


def write_cache(character, data):
    path = cache_path(character)
    temporary = path.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)

    temporary.replace(path)


def get_character_moves(character):
    """
    Return (moves, stale).

    Each character has one shared disk cache. After five minutes,
    the next request tries to refresh it. If the source is unavailable,
    an existing cached copy is returned as stale.
    """
    cached = read_cache(character)
    now = time.time()

    if cached and now - cached.get("fetched_at", 0) < CACHE_TTL:
        return cached["moves"], False

    try:
        page = fetch_wikitext(character)
        moves = parse_moves(page["wikitext"])

        if not moves:
            raise ValueError(
                f"No moves parsed for {character}; keeping existing cache"
            )

        data = {
            "character": character,
            "fetched_at": now,
            "source_timestamp": page.get("timestamp"),
            "revision_id": page.get("revid"),
            "moves": moves,
        }

        write_cache(character, data)
        return moves, False

    except Exception as exc:
        print(f"Dream Cancel refresh failed for {character}: {exc}")

        if cached:
            return cached["moves"], True

        raise


def find_moves(moves, query):
    """
    Find moves by name, input, move ID, or flexible input spelling.

    Exact normalized matches take priority over partial matches.
    Original move data is returned unchanged.
    """
    needle = normalize_move(query)

    if not needle:
        return []

    exact = []
    partial = []

    for move in moves:
        searchable = [
            normalize_move(move.get("name", "")),
            normalize_move(move.get("input", "")),
            normalize_move(move.get("moveId", "")),
        ]

        if needle in searchable:
            exact.append(move)
        elif any(
            needle in value
            for value in searchable
            if value
        ):
            partial.append(move)

    return exact or partial


def image_filenames(raw_images):
    """Extract image filenames from comma-separated wiki fields."""
    if not raw_images:
        return []

    if isinstance(raw_images, list):
        values = raw_images
    else:
        values = str(raw_images).split(",")

    result = []

    for value in values:
        filename = clean_value(value)
        filename = re.sub(
            r"^\s*(?:File|Image)\s*:\s*",
            "",
            filename,
            flags=re.IGNORECASE,
        )
        filename = filename.strip()

        if filename:
            result.append(filename)

    return result


def get_image_url(filename):
    """Resolve an actual image URL through MediaWiki imageinfo."""
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }

    response = SESSION.get(
        API, params=params, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload = response.json()

    pages = payload.get("query", {}).get("pages", {})

    if isinstance(pages, list):
        page_values = pages
    else:
        page_values = list(pages.values())

    for page in page_values:
        imageinfo = page.get("imageinfo", [])
        if imageinfo:
            return imageinfo[0].get("url")

    return None
```
