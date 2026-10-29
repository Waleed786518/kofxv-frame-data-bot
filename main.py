
import os
import re
import asyncio
from urllib.parse import quote

import discord
from discord.ext import commands
import requests
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
API = "https://www.dreamcancel.com/w/api.php"
ROSTER = [
    "Andy Bogard", "Ángel", "Antonov", "Ash Crimson",
    "Athena Asamiya", "B.Jenet", "Benimaru Nikaido", "Billy Kane",
    "Blue Mary", "Chizuru Kagura", "Chris", "Clark Still",
    "Darli Dagger", "Dolores", "Duo Lon", "Elisabeth Blanctorche",
    "Gato", "Geese Howard", "Goenitz", "Haohmaru", "Heidern",
    "Hinako Shijo", "Iori Yagami", "Isla", "Joe Higashi", "K'",
    "Kim Kaphwan", "King", "King of Dinosaurs", "Krohnen", "Kukri",
    "Kula Diamond", "Kyo Kusanagi", "Leona Heidern", "Luong",
    "Mai Shiranui", "Mature", "Maxima", "Meitenkun", "Nakoruru",
    "Najd", "Omega Rugal", "Orochi Chris", "Orochi Shermie",
    "Orochi Yashiro", "Ralf Jones", "Ramón", "Robert Garcia",
    "Rock Howard", "Ryo Sakazaki", "Shermie", "Shingo Yabuki",
    "Shun'ei", "Sylvie Paula Paula", "Terry Bogard", "Vanessa",
    "Vice", "Whip", "Ryuji Yamazaki", "Yashiro Nanakase",
    "Yuri Sakazaki",
]

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def api_get(params):
    response = requests.get(
        API,
        params=params,
        timeout=25,
        headers={"User-Agent": "KOFXVFrameDataDiscordBot/1.0"},
    )
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("info", "Dream Cancel API error"))
    return data


def page_title(character):
    return f"The King of Fighters XV/{character}/Data"


def fetch_wikitext(character):
    data = api_get({
        "action": "query",
        "prop": "revisions",
        "titles": page_title(character),
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") is not None:
        raise ValueError(f"Dream Cancel has no data page for {character}.")
    page = pages[0]
    revision = page["revisions"][0]
    content = revision["slots"]["main"].get("content", "")
    if not content:
        raise ValueError("The page returned no readable wikitext.")
    return content, revision


def split_template_fields(body):
    """Split template parameters without splitting inside nested templates."""
    fields = {}
    parts = []
    start = 0
    depth = 0
    i = 0
    while i < len(body):
        if body.startswith("{{", i):
            depth += 1
            i += 2
            continue
        if body.startswith("}}", i) and depth:
            depth -= 1
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append(body[start:i])
            start = i + 1
        i += 1
    parts.append(body[start:])

    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        fields[key.strip().lower()] = value.strip()
    return fields


def extract_templates(wikitext):
    """Find MoveData-KOFXV templates, including nested parameter markup."""
    found = []
    i = 0
    while i < len(wikitext):
        if not wikitext.startswith("{{", i):
            i += 1
            continue
        start = i
        depth = 0
        j = i
        while j < len(wikitext):
            if wikitext.startswith("{{", j):
                depth += 1
                j += 2
                continue
            if wikitext.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
                continue
            j += 1
        if depth != 0:
            break

        raw = wikitext[start + 2:j - 2]
        name = raw.split("|", 1)[0].strip().lower()
        if name == "movedata-kofxv":
            fields = split_template_fields(raw[len(raw.split("|", 1)[0]):].lstrip("|"))
            found.append((start, fields))
        i = j
    return found


def clean_markup(value):
    value = re.sub(r"<ref[^>]*>.*?</ref>", "", value, flags=re.I | re.S)
    value = re.sub(r"<ref[^/]*/\s*>", "", value, flags=re.I)
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = re.sub(r"'''?", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    return value.strip()


def headings_before(wikitext, position):
    headings = re.findall(
        r"(?m)^(={2,6})\s*(.*?)\s*\1\s*$",
        wikitext[:position],
    )
    return [clean_markup(title) for _, title in headings]


def parse_moves(character, wikitext):
    moves = []
    templates = extract_templates(wikitext)
    for index, (position, fields) in enumerate(templates):
        end = templates[index + 1][0] if index + 1 < len(templates) else len(wikitext)
        nearby = wikitext[position:end]
        fields = {key: clean_markup(value) for key, value in fields.items()}
        name = fields.get("name", "").strip()
        move_input = fields.get("input", "").strip()
        if not name and not move_input:
            continue

        moves.append({
            "character": character,
            "name": name or move_input,
            "input": move_input,
            "headings": headings_before(wikitext, position),
            "fields": fields,
            "images": split_filenames(fields.get("images", "")),
            "hitboxes": split_filenames(fields.get("hitboxes", "")),
            "source_section": clean_markup(nearby[:500]),
        })
    return moves


def split_filenames(value):
    return [
        item.strip().replace("File:", "").replace("Image:", "")
        for item in re.split(r"[,;\n]", value)
        if item.strip()
    ]


def normalize_input(value):
    value = value.lower().replace(" ", "")
    value = value.replace("qcf", "236").replace("qcb", "214")
    value = value.replace("hcf", "41236").replace("hcb", "63214")
    value = value.replace("dp", "623").replace("charge", "")
    value = value.replace("+", "")
    return value


def find_moves(moves, query):
    wanted = normalize_input(query)
    results = []
    for move in moves:
        candidates = [move["name"], move["input"]]
        if any(
            wanted == normalize_input(candidate)
            or wanted in normalize_input(candidate)
            for candidate in candidates if candidate
        ):
            results.append(move)
    return results


def image_urls(filenames):
    if not filenames:
        return []
    params = {
        "action": "query",
        "titles": "|".join("File:" + name for name in filenames),
        "prop": "imageinfo",
        "iiprop": "url",
        "format": "json",
    }
    data = api_get(params)
    output = []
    for page in data.get("query", {}).get("pages", {}).values():
        info = page.get("imageinfo", [])
        if info:
            output.append((page.get("title", "Image"), info[0]["url"]))
    return output


def make_embed(character, move):
    fields = move["fields"]
    embed = discord.Embed(
        title=f"{character} — {move['name']}",
        description=f"**Input:** {move['input'] or 'Not listed'}",
        color=discord.Color.gold(),
        url="https://www.dreamcancel.com/wiki/"
            + quote(page_title(character).replace(" ", "_"), safe="/_"),
    )
    if move["headings"]:
        embed.add_field(
            name="Page headings",
            value="\n".join(move["headings"][-5:])[:1024],
            inline=False,
        )

    # Keep all available move fields; do not collapse bracketed values.
    skip = {"name", "input", "images", "hitboxes", "character"}
    for key, value in fields.items():
        if key not in skip and value:
            label = key.replace("_", " ").title()
            embed.add_field(name=label[:256], value=value[:1024], inline=True)

    embed.set_footer(text="Frame data source: Dream Cancel")
    return embed


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} — KOF XV bot is online.")


@bot.command(name="characters")
async def characters(ctx):
    await ctx.send("**KOF XV roster (Dream Cancel order):**\n" +
                   "\n".join(f"{i}. {name}" for i, name in enumerate(ROSTER, 1)))


@bot.command(name="move")
async def move_command(ctx, *, search: str):
    """
    Usage: !move Andy Bogard | cl.A
    Separate character and move with a vertical bar.
    """
    if "|" not in search:
        await ctx.send(
            "Use `!move Character Name | move input`\n"
            "Example: `!move Andy Bogard | cl.A`\n"
            "Notation examples: `236+A`, `qcf+A`, `j.C`."
        )
        return

    character_text, move_query = [part.strip() for part in search.split("|", 1)]
    character = next(
        (name for name in ROSTER if name.casefold() == character_text.casefold()),
        None,
    )
    if not character:
        await ctx.send(
            "I couldn't match that character to the roster. "
            "Use `!characters` to see the exact names."
        )
        return

    if not move_query:
        await ctx.send("Please include a move input, such as `cl.A` or `236+A`.")
        return

    await ctx.typing()
    try:
        wikitext, revision = await asyncio.to_thread(fetch_wikitext, character)
        moves = await asyncio.to_thread(parse_moves, character, wikitext)
        matches = find_moves(moves, move_query)

        if not matches:
            await ctx.send(
                f"No exact move match found for **{character} — {move_query}**. "
                "Try the notation shown on Dream Cancel."
            )
            return

        await ctx.send(
            f"Found **{len(matches)}** matching move section(s) for **{character}**. "
            f"Source revision: `{revision.get('revid', 'unknown')}`."
        )

        for move in matches[:5]:
            await ctx.send(embed=make_embed(character, move))
            all_files = move["images"] + move["hitboxes"]
            if all_files:
                try:
                    resolved = await asyncio.to_thread(image_urls, all_files)
                    for title, url in resolved:
                        await ctx.send(f"**{title}**\n{url}")
                except Exception as exc:
                    await ctx.send(
                        "Some image URLs could not be resolved: "
                        + str(exc)[:300]
                    )

        if len(matches) > 5:
            await ctx.send(
                f"There are {len(matches)} matches; showing the first 5. "
                "Narrow the move input to get a smaller result."
            )

    except requests.RequestException as exc:
        await ctx.send(
            "I couldn't reach Dream Cancel right now. Please try again later.\n"
            f"`{str(exc)[:250]}`"
        )
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        await ctx.send(f"Couldn't read that Dream Cancel page: `{str(exc)[:300]}`")


if not TOKEN:
    raise RuntimeError(
        "Missing DISCORD_TOKEN environment variable. "
        "Set it in the hosting panel's secret/environment settings."
    )

bot.run(TOKEN)
