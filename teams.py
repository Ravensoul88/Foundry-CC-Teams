import database
import discord
import config
import asyncio
import logging

bot_log = logging.getLogger('registration_bot')

async def _perform_assignment(channel: discord.TextChannel):
    bot_log.info("Starting team assignment process...")
    registrations = database.get_all_registrations()
    if not registrations:
        bot_log.info("No registrations found for team assignment.")
        await channel.send("No players are currently registered for any event slots. Team assignment skipped.")
        return

    assignment_details = {}

    for reg in registrations:
        event = reg['event']
        slot_type = reg['slot_type']
        slot_letter = reg['slot_letter']
        discord_user_id = reg['discord_user_id']

        slot_key = f"{event}_{slot_type}_Slot_{slot_letter}"

        if slot_key not in assignment_details:
            assignment_details[slot_key] = []

        assignment_details[slot_key].append(discord_user_id)

    bot_log.info(f"Assignment details compiled: {assignment_details}")

    for slot_key, user_ids in assignment_details.items():
        if not user_ids:
            continue

        event, slot_type, slot_letter = slot_key.split('_')
        team_name = f"{event}_{slot_type}_{slot_letter}" # Using slot letter to differentiate teams
        captain_id = None

        try:
            captain_id = database.get_player_role(user_ids[0]) # Assuming the first registered player in a slot is the captain for now
            if captain_id != 'Captain':
                 captain_id = None # Reset if the role isn't 'Captain'

        except Exception as e:
            bot_log.warning(f"Could not determine captain for {slot_key}: {e}")
            captain_id = None


        team_members_mentions = []
        for user_id in user_ids:
             try:
                 user = await channel.guild.fetch_member(user_id)
                 if user:
                     if captain_id and user.id == captain_id:
                         team_members_mentions.append(f"👑 **{user.mention}** (Captain)")
                     else:
                         team_members_mentions.append(user.mention)
                 else:
                     bot_log.warning(f"Could not fetch member with ID {user_id} for team {team_name}.")
                     team_members_mentions.append(f"Unknown User (ID: {user_id})") # Handle cases where user is not in guild
             except discord.errors.NotFound:
                 bot_log.warning(f"Member with ID {user_id} not found in guild for team {team_name}.")
                 team_members_mentions.append(f"Unknown User (ID: {user_id})")
             except Exception as e:
                 bot_log.error(f"Error fetching member {user_id} for team {team_name}: {e}")
                 team_members_mentions.append(f"Error Fetching User (ID: {user_id})")


        embed = discord.Embed(
            title=f"assigned to {team_name}",
            description="\n".join(team_members_mentions),
            color=discord.Color.green() if captain_id else discord.Color.orange()
        )
        if captain_id:
            embed.set_footer(text="Captain assigned")
        else:
            embed.set_footer(text="No Captain assigned")


        assignment_channel = discord.utils.get(channel.guild.channels, name="team-assignments") # Assuming a channel named "team-assignments"

        if assignment_channel and isinstance(assignment_channel, discord.TextChannel):
             await assignment_channel.send(embed=embed)
             bot_log.info(f"Sent team assignment for {team_name} to #{assignment_channel.name}")
        else:
             await channel.send(f"Could not find a channel named 'team-assignments'. Sending assignment for {team_name} here:\n{embed.description}", embed=embed)
             bot_log.warning("Channel 'team-assignments' not found. Sent assignment to command channel.")

    bot_log.info("Team assignment process completed.")


async def set_captain(user_id: int, event: str, slot_type: str, slot_letter: str):
    bot_log.info(f"Attempting to set user {user_id} as captain for {event}_{slot_type}_Slot_{slot_letter}")
    try:
        cursor = database.cursor
        conn = database.conn
        if cursor and conn:
            cursor.execute('''
                SELECT discord_user_id FROM registrations
                WHERE discord_user_id = ? AND event = ? AND slot_type = ? AND slot_letter = ?
            ''', (user_id, event, slot_type, slot_letter))
            is_registered = cursor.fetchone() is not None

            if is_registered:
                # First, ensure the user is in the player_roles table
                database.add_player_role(user_id, 'Captain')
                # Note: The actual 'Captain' role assignment on Discord is not handled here,
                # this only marks them as captain in the database.
                bot_log.info(f"User {user_id} marked as Captain in database for {event}_{slot_type}_Slot_{slot_letter}")
                return True
            else:
                bot_log.warning(f"User {user_id} is not registered for {event}_{slot_type}_Slot_{slot_letter}. Cannot set as captain.")
                return False
    except sqlite3.Error as e:
        bot_log.error(f"Database error setting captain for user {user_id}: {e}")
        return False
    except Exception as e:
        bot_log.error(f"Error setting captain for user {user_id}: {e}")
        return False
    return False