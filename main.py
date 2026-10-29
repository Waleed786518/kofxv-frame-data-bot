
import os
import asyncio
import io
import re

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image

from characters import ROSTER
import dream_cancel as dc


load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from the hosting environment."
    )


class KOFBot(commands.Bot):
    async def setup_hook(self):
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")


intents = discord.Intents.default()
bot = KOFBot(command_prefix="!", intents=intents)


def value_text(value):
    if value is None:
        return "Not listed"

    text = dc.clean_value(value)
    return text if text else "Not listed"


def get_field(move, *names):
    for name in names:
        value = move.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def make_move_embed(character, move, stale):
    move_input = value_text(get_field(move, "input"))

    embed = discord.Embed(
        title=f"{character} — {move_input}",
        color=discord.Color.gold(),
        url=dc.source_page_url(character),
    )

    fields = [
        ("Damage", ("damage",)),
        ("Guard", ("guard",)),
        ("Cancel", ("cancel",)),
        ("Startup", ("startup",)),
        ("Active", ("active",)),
        ("Recovery", ("recovery",)),
        ("On Hit", ("hitadv", "hitAdv")),
        ("On Block", ("blockadv", "blockAdv")),
        ("Invulnerability", ("invul",)),
        ("Stun", ("stun",)),
        ("Guard Damage", ("guardDamage", "guarddamage")),
    ]

    for label, keys in fields:
        value = get_field(move, *keys)

        if value is not None:
            embed.add_field(
                name=label,
                value=value_text(value),
                inline=True,
            )

    if stale:
        embed.set_footer(
            text="Using saved data: Dream Cancel refresh failed."
        )

    return embed


def download_image(image_url):
    """Download the original image bytes without resizing."""
    response = dc.SESSION.get(
        image_url,
        timeout=dc.REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/"):
        raise ValueError(
            f"Source did not return an image: {content_type}"
        )

    return response.content


def add_image_margin(image_bytes, margin_ratio=0.10):
    """
    Add a 10% margin on each side while preserving the original
    image proportions and artwork. The background color is sampled
    from visible pixels along the image's outer edges.
    """
    with Image.open(io.BytesIO(image_bytes)) as source:
        # Create a consistent image format and avoid modifying
        # the original downloaded bytes.
        image = source.convert("RGBA")
        width, height = image.size

        if width < 1 or height < 1:
            raise ValueError("The source image has invalid dimensions.")

        pixels = image.load()
        edge_colors = []

        # Sample the top and bottom edges.
        for x in range(width):
            for y in {0, height - 1}:
                red, green, blue, alpha = pixels[x, y]
                if alpha > 16:
                    edge_colors.append((red, green, blue))

        # Sample the left and right edges.
        for y in range(height):
            for x in {0, width - 1}:
                red, green, blue, alpha = pixels[x, y]
                if alpha > 16:
                    edge_colors.append((red, green, blue))

        if edge_colors:
            count = len(edge_colors)
            background = tuple(
                round(sum(color[channel] for color in edge_colors) / count)
                for channel in range(3)
            )
        else:
            # Neutral dark background if the edges are fully transparent.
            background = (32, 32, 32)

        pad_x = max(1, round(width * margin_ratio))
        pad_y = max(1, round(height * margin_ratio))

        canvas = Image.new(
            "RGBA",
            (width + 2 * pad_x, height + 2 * pad_y),
            (*background, 255),
        )

        # Keep the original artwork at its original size.
        canvas.alpha_composite(image, (pad_x, pad_y))

        output = io.BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()


def safe_attachment_name(filename, number):
    filename = filename.rsplit("/", 1)[-1]
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    if not stem:
        stem = "image"

    return f"image_{number}_{stem}_padded.png"


async def send_in_batches(interaction, embeds):
    for start in range(0, len(embeds), 10):
        await interaction.followup.send(
            embeds=embeds[start:start + 10]
        )


async def send_image_pairs(interaction, image_items):
    """Send at most two padded image embeds per message."""
    for start in range(0, len(image_items), 2):
        batch = image_items[start:start + 2]

        embeds = [item[0] for item in batch]
        files = [item[1] for item in batch]

        await interaction.followup.send(
            embeds=embeds,
            files=files,
        )


@bot.tree.command(
    name="fd",
    description="Search KOF XV frame data from Dream Cancel.",
)
@app_commands.describe(
    character="Choose a KOF XV character",
    move=(
        "Enter a move name or input, such as cl.A, cr.B, "
        "236+A or qcf+A"
    ),
)
async def fd(
    interaction: discord.Interaction,
    character: str,
    move: str,
):
    if character not in ROSTER:
        await interaction.response.send_message(
            "Please choose a character from the autocomplete suggestions.",
            ephemeral=True,
        )
        return

    if not move.strip():
        await interaction.response.send_message(
            "Enter a move name or input notation.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        moves, stale = await asyncio.to_thread(
            dc.get_character_moves, character
        )
        matches = dc.find_moves(moves, move.strip())
    except Exception as exc:
        print(f"Frame-data lookup failed for {character}: {exc}")
        await interaction.followup.send(
            "I couldn't retrieve this character's frame data. "
            "Please try again shortly."
        )
        return

    if not matches:
        await interaction.followup.send(
            f"No matching moves found for **{character}** "
            f"using `{move}`.\n"
            "Try notation such as `cl.A`, `cr.B`, `236+A`, "
            "or `qcf+A`."
        )
        return

    move_embeds = [
        make_move_embed(character, item, stale)
        for item in matches
    ]
    await send_in_batches(interaction, move_embeds)

    image_items = []
    seen = set()
    image_number = 0

    for item in matches:
        move_input = value_text(get_field(item, "input"))
        heading = f"{character} — {move_input}"

        for image_key in ("images", "hitboxes"):
            raw_images = item.get(image_key)
            if not raw_images:
                continue

            for filename in dc.image_filenames(raw_images):
                if filename in seen:
                    continue

                seen.add(filename)

                try:
                    image_url = await asyncio.to_thread(
                        dc.get_image_url, filename
                    )

                    if not image_url:
                        print(
                            f"No source image URL found for {filename}"
                        )
                        continue

                    original_bytes = await asyncio.to_thread(
                        download_image, image_url
                    )

                    padded_bytes = await asyncio.to_thread(
                        add_image_margin, original_bytes
                    )

                    image_number += 1
                    attachment_name = safe_attachment_name(
                        filename, image_number
                    )

                    image_embed = discord.Embed(
                        title=heading,
                        color=discord.Color.dark_gold(),
                        url=dc.source_page_url(character),
                    )
                    image_embed.set_image(
                        url=f"attachment://{attachment_name}"
                    )

                    image_file = discord.File(
                        io.BytesIO(padded_bytes),
                        filename=attachment_name,
                    )

                    image_items.append((image_embed, image_file))

                except Exception as exc:
                    print(
                        f"Image processing failed for {filename}: {exc}"
                    )

    if image_items:
        await send_image_pairs(interaction, image_items)

    print(
        f"/fd: {interaction.user} searched "
        f"{character} for {move!r}; "
        f"{len(matches)} move(s), "
        f"{len(image_items)} padded image(s), "
        f"{(len(image_items) + 1) // 2} image message(s)."
    )


@fd.autocomplete("character")
async def character_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    current = current.casefold()
    results = [
        app_commands.Choice(name=name, value=name)
        for name in ROSTER
        if current in name.casefold()
    ]

    return results[:25]


@bot.event
async def on_ready():
    print(
        f"Logged in as {bot.user} — KOF XV bot is online."
    )


bot.run(TOKEN)
