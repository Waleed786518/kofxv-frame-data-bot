
import os
import asyncio

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from characters import ROSTER
import dream_cancel as dc

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from the hosting environment.")


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
    name = value_text(get_field(move, "name", "move", "title"))
    embed = discord.Embed(
        title=name,
        color=discord.Color.gold(),
        url=dc.source_page_url(character),
    )

    embed.description = (
        f"**Input:** {value_text(get_field(move, 'input'))}\n"
        f"**Character:** {character}"
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
    else:
        embed.set_footer(text="Source: Dream Cancel")

    return embed


async def send_in_batches(interaction, embeds):
    for start in range(0, len(embeds), 10):
        batch = embeds[start:start + 10]
        if start == 0:
            await interaction.followup.send(embeds=batch)
        else:
            await interaction.followup.send(embeds=batch)


@bot.tree.command(
    name="fd",
    description="Search KOF XV frame data from Dream Cancel.",
)
@app_commands.describe(
    character="Choose a KOF XV character",
    move="Enter a move name or input, such as cl.A, cr.B, 236+A or qcf+A",
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
            f"using `{move}`.\nTry notation such as `cl.A`, `cr.B`, "
            "`236+A`, or `qcf+A`."
        )
        return

    embeds = [
        make_move_embed(character, item, stale)
        for item in matches
    ]

    await send_in_batches(interaction, embeds)

    # Include every animation and hitbox image associated with
    # every matching move, not just the first image.
    image_embeds = []
    seen = set()

    for item in matches:
        move_name = value_text(
            get_field(item, "name", "move", "title")
        )

        for key in ("images", "hitboxes"):
            raw_images = item.get(key)
            if not raw_images:
                continue

            filenames = dc.image_filenames(raw_images)

            for filename in filenames:
                if filename in seen:
                    continue
                seen.add(filename)

                try:
                    image_url = await asyncio.to_thread(
                        dc.get_image_url, filename
                    )
                except Exception as exc:
                    print(f"Image lookup failed for {filename}: {exc}")
                    continue

                if not image_url:
                    continue

                image_embed = discord.Embed(
                    title=f"{move_name} — "
                          f"{'Animation' if key == 'images' else 'Hitbox'}",
                    color=discord.Color.dark_gold(),
                    url=dc.source_page_url(character),
                )
                image_embed.description = f"File: `{filename}`"
                image_embed.set_image(url=image_url)
                image_embeds.append(image_embed)

    if image_embeds:
        await send_in_batches(interaction, image_embeds)

    print(
        f"/fd: {interaction.user} searched "
        f"{character} for {move!r}; "
        f"{len(matches)} move(s), {len(image_embeds)} image(s)."
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
    print(f"Logged in as {bot.user} — KOF XV bot is online.")


bot.run(TOKEN)
