import discord
import config
import database
import logging
import os
import asyncio

bot_log = logging.getLogger('registration_bot')

PERSISTENCE_FILE_PATH = config.PERSISTENCE_FILE

def save_registration_message_ids(channel_id: int, message_id: int):
    try:
        with open(PERSISTENCE_FILE_PATH, 'w') as f:
            f.write(f"{channel_id},{message_id}")
        bot_log.info(f"Saved persistent message ID: Channel {channel_id}, Message {message_id}")
    except IOError as e:
        bot_log.error(f"Failed to save persistent message IDs to {PERSISTENCE_FILE_PATH}: {e}")

def load_registration_message_ids() -> tuple[int | None, int | None]:
    if not os.path.exists(PERSISTENCE_FILE_PATH):
        return None, None
    try:
        with open(PERSISTENCE_FILE_PATH, 'r') as f:
            line = f.readline().strip()
            if not line:
                return None, None
            channel_id_str, message_id_str = line.split(',')
            channel_id = int(channel_id_str)
            message_id = int(message_id_str)
            bot_log.info(f"Loaded persistent message ID: Channel {channel_id}, Message {message_id}")
            return channel_id, message_id
    except (IOError, ValueError, IndexError) as e:
        bot_log.error(f"Failed to load persistent message IDs from {PERSISTENCE_FILE_PATH}: {e}")
        return None, None

async def recalculate_all_counters(bot: discord.Client):
    bot_log.info("Recalculating all registration counters...")
    bot.time_slot_counts = {}
    bot.substitute_counts = {}
    time_slots = ["14UTC", "19UTC"]

    try:
        all_regs = database.get_all_registrations()
        for reg in all_regs:
            event = reg['event']
            slot = reg['time_slot']
            is_sub = bool(reg['substitute'])

            if event not in bot.time_slot_counts:
                bot.time_slot_counts[event] = {}
            if event not in bot.substitute_counts:
                bot.substitute_counts[event] = {}

            if slot not in bot.time_slot_counts[event]:
                 bot.time_slot_counts[event][slot] = 0
            if slot not in bot.substitute_counts[event]:
                 bot.substitute_counts[event][slot] = 0

            if not is_sub:
                 bot.time_slot_counts[event][slot] += 1
            else:
                 bot.substitute_counts[event][slot] += 1

        bot_log.info(f"Finished recalculating counters. Main: {bot.time_slot_counts}, Sub: {bot.substitute_counts}")

    except Exception as e:
        bot_log.error(f"Error recalculating counters: {e}", exc_info=True)


def build_registration_embed(bot: discord.Client) -> discord.Embed:
    embed = discord.Embed(
        title=f"{config.EMOJI_EVENT} Event Registration",
        description=f"Use the menu below to register for upcoming events. Click '{config.EMOJI_MANAGE} Manage' to see or cancel your current registrations.",
        color=config.COLOR_DEFAULT
    )

    time_slots = ["14UTC", "19UTC"]

    for event in config.DEFAULT_ACTIVE_EVENTS:
        main_count_total = 0
        sub_count_total = 0
        field_value = ""

        for slot in time_slots:
            main_count = bot.time_slot_counts.get(event, {}).get(slot, 0)
            sub_count = bot.substitute_counts.get(event, {}).get(slot, 0)
            main_count_total += main_count
            sub_count_total += sub_count
            field_value += f"{config.EMOJI_SLOT} **{slot}:** {main_count} Main / {sub_count} Sub\n"

        embed.add_field(
            name=f"{event} Registrations ({main_count_total} Main / {sub_count_total} Sub)",
            value=field_value or "No registrations yet.",
            inline=False
        )

    embed.set_footer(text="Last updated")
    embed.timestamp = discord.utils.utcnow()

    return embed


async def update_registration_embed(bot: discord.Client):
    channel_id, message_id = load_registration_message_ids()

    if not channel_id or not message_id:
        bot_log.warning("No persistent message IDs found. Cannot update registration embed.")
        return

    await recalculate_all_counters(bot)

    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            bot_log.error(f"Persistent message channel not found (ID: {channel_id}).")
            return
        message = await channel.fetch_message(message_id)
        if not message:
            bot_log.error(f"Persistent message not found (ID: {message_id}) in channel {channel_id}.")
            return

        new_embed = build_registration_embed(bot)
        view = ui_components.EventSelectionView(config.DEFAULT_ACTIVE_EVENTS) # Need ui_components here

        await message.edit(embed=new_embed, view=view)
        bot_log.info(f"Updated persistent registration embed in channel {channel_id}.")

    except discord.NotFound:
        bot_log.error(f"Persistent message or channel not found (Channel ID: {channel_id}, Message ID: {message_id}). Was it deleted?")
        # Consider clearing persistence file here
    except discord.Forbidden:
        bot_log.error(f"Missing permissions to fetch or edit persistent message in channel {channel_id}.")
    except Exception as e:
        bot_log.error(f"Unexpected error updating persistent registration embed: {e}", exc_info=True)

