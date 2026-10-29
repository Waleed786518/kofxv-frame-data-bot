
import os
import re
import asyncio
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
API = "https://www.dreamcancel.com/w/api.php"

ROSTER = [
    "Andy Bogard", "Ángel", "Antonov", "Ash Crimson",
    "Athena Asamiya", "B.Jenet", "Benimaru Nikaido",
    "Billy Kane", "Blue Mary", "Chizuru Kagura", "Chris",
    "Clark Still", "Darli Dagger", "Dolores", "Duo Lon",
    "Elisabeth Blanctorche", "Gato", "Geese Howard",
    "Goenitz", "Haohmaru", "Heidern", "Hinako Shijo",
    "Iori Yagami", "Isla", "Joe Higashi", "K'",
    "Kim Kaphwan", "King", "King of Dinosaurs", "Krohnen",
    "Kukri", "Kula Diamond", "Kyo Kusanagi",
    "Leona Heidern", "Luong", "Mai Shiranui", "Mature",
    "Maxima", "Meitenkun", "Nakoruru", "Najd",
    "Omega Rugal", "Orochi Chris", "Orochi Shermie",
    "Orochi Yashiro", "Ralf Jones", "Ramón",
    "Robert Garcia", "Rock Howard", "Ryo Sakazaki",
    "Shermie", "Shingo Yabuki", "Shun'ei",
    "Sylvie Paula Paula", "Terry Bogard", "Vanessa",
    "Vice", "Whip", "Ryuji Yamazaki",
    "Yashiro Nanakase", "Yuri Sakazaki",
]

session = requests.Session()
session.headers.update({
    "User-Agent": "KOFXVFrameDataBot/1.0 (Discord bot)"
})


def api_get(params):
    response = session.get(API, params=params, timeout=25)
    response.raise_for_status()
    data = response.json()

    if "error" in data:
        raise RuntimeError(data["error"].get("info", "Dream Cancel API error"))

    return data


def character_page(character):
    return (
        "The_King_of_Fighters_XV/"
        + character.replace(" ", "_")
        + "/Data"
    )


def get_wikitext(character):
    data = api_get({
        "action": "query",
        "prop": "revisions",
        "titles": character_page(character),
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    })

    pages = data.get("query", {}).get("pages", [])

    if not pages or pages[0].get("missing") is not None:
        raise LookupError(
            f"Dream Cancel has no data page for {character}."
        )

    revisions = pages[0].get("revisions", [])
    if not revisions:
        raise LookupError("Dream Cancel returned no page revision.")

    revision = revisions[0]
    slots = revision.get("slots", {})
    main = slots.get("main", {})

    text = main.get("content", main.get("*", ""))

    if not text:
        raise LookupError("The Dream Cancel page contained no wikitext.")

    return text


def extract_move_templates(wikitext):
    """Extract MoveData-KOFXV templates, including nested templates."""
    results = []
    marker = "{{MoveData-KOFXV"
    search_from = 0

    while True:
        start = wikitext.find(marker, search_from)

        if start == -1:
            break

        depth = 0
        i = start
        end = None

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
                    end = i
                    break

                continue

            i += 1

        if end is None:
            break

        results.append(wikitext[start:end])
        search_from = end

    return results


def split_top_level(text, separator="|"):
    """Split template arguments without splitting nested templates."""
    parts = []
    current = []
    depth = 0
    i = 0

    while i < len(text):
        pair = text[i:i + 2]

        if pair == "{{":
            depth += 1
            current.append(pair)
            i += 2
            continue

        if pair == "}}":
            depth = max(0, depth - 1)
            current.append(pair)
            i += 2
            continue

        if text[i] == separator and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(text[i])

        i += 1

    parts.append("".join(current).strip())
    return parts


def parse_template(template):
    body = template[2:-2]
    parts = split_top_level(body)

    fields = {}

    for part in parts[1:]:
        if "=" not in part:
            continue

        key, value = part.split("=", 1)
        fields[key.strip().lower()] = value.strip()

    return fields


def clean_value(value):
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("'''", "").replace("''", "")
    value = value.replace("[[", "").replace("]]", "")
    return value.strip()


def parse_moves(wikitext):
    moves = []

    for template in extract_move_templates(wikitext):
        fields = parse_template(template)

        if not fields.get("name") and not fields.get("input"):
            continue

        moves.append(fields)

    return moves


def normalize_move(value):
    """Normalize common move notation for matching."""
    value = value.lower().strip()

    replacements = [
        (r"\bquarter\s*circle\s*forward\b", "236"),
        (r"\bquarter\s*circle\s*back(?:ward)?\b", "214"),
        (r"\bqcf\b", "236"),
        (r"\bqcb\b", "214"),
        (r"\bhcf\b", "41236"),
        (r"\bhcb\b", "63214"),
        (r"\bdp\b", "623"),
        (r"\bdragon\s*punch\b", "623"),
        (r"\bhalf\s*circle\s*forward\b", "41236"),
        (r"\bhalf\s*circle\s*back(?:ward)?\b", "63214"),
    ]

    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value)

    return re.sub(r"[^a-z0-9]", "", value)


def find_moves(moves, requested):
    query = normalize_move(requested)

    if not query:
        return []

    exact = []
    partial = []

    for move in moves:
        candidates = [
            clean_value(move.get("name", "")),
            clean_value(move.get("input", "")),
            clean_value(move.get("moveid", "")),
        ]

        normalized = [normalize_move(x) for x in candidates if x]

        if query in normalized:
            exact.append(move)
        elif any(
            query in candidate or candidate in query
            for candidate in normalized
            if candidate
        ):
            partial.append(move)

    return exact if exact else partial


def image_filenames(value):
    """Read image filenames from comma-separated template values."""
    names = []

    for part in value.split(","):
        part = part.strip()
        part = re.sub(r"^\[\[(?:File|Image):", "", part,
                      flags=re.IGNORECASE)
        part = part.split("|", 1)[0]
        part = part.replace("]]", "").strip()

        match = re.search(
            r"([^/]+?\.(?:png|jpe?g|gif|webp|svg))$",
            part,
            flags=re.IGNORECASE,
        )

        if match:
            filename = match.group(1).strip()
            if filename not in names:
                names.append(filename)

    return names


def get_image_url(filename):
    """Resolve a real Dream Cancel image URL through MediaWiki."""
    data = api_get({
        "action": "query",
        "titles": "File:" + filename,
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "1000",
        "format": "json",
        "formatversion": "2",
    })

    pages = data.get("query", {}).get("pages", [])

    if not pages:
        return None

    imageinfo = pages[0].get("imageinfo", [])

    if not imageinfo:
        return None

    return (
        imageinfo[0].get("thumburl")
        or imageinfo[0].get("url")
    )


def field_value(fields, *keys):
    for key in keys:
        value = fields.get(key.lower())

        if value and value.strip():
            return clean_value(value)

    return "—"


def make_move_embed(character, move):
    name = field_value(move, "name")
    input_value = field_value(move, "input")

    embed = discord.Embed(
        title=f"{character} — {name}",
        description=f"**Input:** {input_value}",
        color=discord.Color.dark_red(),
        url="https://www.dreamcancel.com/wiki/"
            + quote(character_page(character), safe="/_"),
    )

    # Show all available frame-data properties.
    properties = [
        ("Damage", "damage"),
        ("Guard", "guard"),
        ("Cancel", "cancel"),
        ("Startup", "startup"),
        ("Active", "active"),
        ("Recovery", "recovery"),
        ("Hit advantage", "hitadv"),
        ("Block advantage", "blockadv"),
        ("Invulnerability", "invul"),
        ("Stun", "stun"),
        ("Guard damage", "guarddamage"),
        ("Version", "version"),
    ]

    for label, key in properties:
        value = move.get(key)

        if value and value.strip():
            embed.add_field(
                name=label,
                value=clean_value(value)[:1024],
                inline=True,
            )

    embed.set_footer(
        text="Source: Dream Cancel • Verify details against the source page"
    )

    return embed


intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}")
        print(f"Synced {len(synced)} global slash command(s).")
    except Exception as exc:
        print(f"Slash-command sync failed: {exc}")


async def character_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    query = current.casefold().strip()

    matches = [
        character for character in ROSTER
        if query in character.casefold()
    ]

    return [
        app_commands.Choice(name=character, value=character)
        for character in matches[:25]
    ]


@bot.tree.command(
    name="fd",
    description="Look up KOF XV move frame data from Dream Cancel.",
)
@app_commands.describe(
    character="Choose a KOF XV character",
    move="Move name or input, e.g. cl.A, cr.B, 236+A, qcf+A",
)
@app_commands.autocomplete(character=character_autocomplete)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str,
):
    await interaction.response.defer(thinking=True)

    if character not in ROSTER:
        await interaction.followup.send(
            "Please choose a character from the autocomplete list.",
            ephemeral=True,
        )
        return

    try:
        wikitext = await asyncio.to_thread(get_wikitext, character)
        moves = parse_moves(wikitext)

        if not moves:
            await interaction.followup.send(
                f"No MoveData-KOFXV entries were found for {character}. "
                "The Dream Cancel page format may have changed."
            )
            return

        matches = find_moves(moves, move)

        if not matches:
            await interaction.followup.send(
                f"No matching move found for **{character}** "
                f"with input/name `{move}`.\n"
                "Try the move name or a different notation, "
                "such as `cl.A`, `cr.B`, or `236+A`."
            )
            return

        # Show every exact match, or the best available partial matches.
        for index, result in enumerate(matches[:5]):
            embed = make_move_embed(character, result)

            if index == 0 and len(matches) > 1:
                embed.description = (
                    (embed.description or "")
                    + f"\n**Matches found:** {len(matches)}"
                )

            await interaction.followup.send(embed=embed)

            # Show all resolved animation and hitbox images.
            for field, label in [
                ("images", "Animation"),
                ("hitboxes", "Hitbox"),
            ]:
                filenames = image_filenames(result.get(field, ""))

                for filename in filenames:
                    try:
                        url = await asyncio.to_thread(
                            get_image_url, filename
                        )

                        if not url:
                            continue

                        image_embed = discord.Embed(
                            title=f"{label}: {filename}",
                            color=discord.Color.dark_red(),
                        )
                        image_embed.set_image(url=url)

                        await interaction.followup.send(
                            embed=image_embed
                        )

                    except Exception as image_error:
                        print(
                            f"Could not resolve image {filename}: "
                            f"{image_error}"
                        )

    except requests.RequestException as exc:
        print(f"Dream Cancel request failed: {exc}")
        await interaction.followup.send(
            "I couldn't reach Dream Cancel right now. Please try again."
        )

    except LookupError as exc:
        await interaction.followup.send(str(exc))

    except Exception as exc:
        print(f"/fd error for {character}, {move}: {exc}")
        await interaction.followup.send(
            "Something went wrong while reading the move data. "
            "The error has been logged for troubleshooting."
        )


if not TOKEN:
    raise RuntimeError(
        "Missing DISCORD_TOKEN environment variable. "
        "Set it in the hosting panel's environment variables."
    )

bot.run(TOKEN)
