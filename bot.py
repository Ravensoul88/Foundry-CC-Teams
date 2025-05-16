import discord
from discord.ext import commands
import config
import logging
import database
import state
import lookup
import ui_components
import asyncio
import aiohttp
import os

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[logging.FileHandler("bot.log"),
                              logging.StreamHandler()])

bot_log = logging.getLogger('registration_bot')

# Define bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize bot attributes
bot.api_session: aiohttp.ClientSession | None = None
bot.fid_lookup_data = {}
bot.persistent_channel_id: int | None = None
bot.persistent_message_id: int | None = None
bot.time_slot_counts = {} # Ensure these are initialized
bot.substitute_counts = {} # Ensure these are initialized
# Add active_events attribute to the bot instance
bot.active_events = config.DEFAULT_ACTIVE_EVENTS


# Define a simple test command directly on the bot's command tree
# This command should appear in the tree regardless of the cog loading status
@bot.tree.command(name="test", description="A simple test command to check command registration")
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message("Test command successful!", ephemeral=True)


@bot.event
async def on_ready():
    bot_log.info(f'Logged in as {bot.user.name} ({bot.user.id})')

    # Initialize database
    database.initialize_databases()

    # Load lookup data
    lookup.load_lookup_data(bot)

    # Load persistent message IDs
    bot.persistent_channel_id, bot.persistent_message_id = state.load_registration_message_ids()

    # Initialize aiohttp session for API calls
    if bot.api_session is None or bot.api_session.closed:
       bot.api_session = aiohttp.ClientSession()

    # --- Debugging: Cog Loading and Command Inspection ---
    bot_log.info("--- Debugging: Attempting to load cogs and inspect commands ---")
    try:
        # Load the cog extension. Its setup function will *only* add the cog instance to the bot.
        await bot.load_extension('cogs.bot_commands')
        bot_log.info("Finished loading cogs.bot_commands extension.")

        # Get the cog instance after it's loaded
        actual_cog = bot.get_cog("BotCommands")

        if actual_cog and config.GUILD_ID:
            guild_obj = discord.Object(id=config.GUILD_ID)
            bot_log.info(f"Manually adding application commands from cog '{actual_cog.qualified_name}' to bot.tree for guild {config.GUILD_ID}.")
            commands_manually_added_count = 0
            # Iterate through all application commands defined within the cog instance
            for command in actual_cog.walk_app_commands():
                bot_log.debug(f"  Attempting to manually add command /{command.name} from cog to tree...")
                try:
                    # Add the command to the bot's tree, EXPLICITLY setting its guild.
                    # This is the crucial step to ensure guild association in the tree before syncing.
                    bot.tree.add_command(command, guild=guild_obj) # Add with guild parameter
                    bot_log.info(f"  Successfully manually added /{command.name} to tree for guild {config.GUILD_ID}")
                    commands_manually_added_count += 1
                except Exception as e:
                     bot_log.error(f"  Failed to manually add command /{command.name} to tree: {e}", exc_info=True)

            bot_log.info(f"Finished manually adding {commands_manually_added_count} application commands from cog to tree in on_ready.")

        elif not actual_cog:
             bot_log.error("BotCommands cog instance NOT found after loading extension. Cannot manually add commands.")
             # If cog instance is not found, cannot proceed with manual command addition
             return
        else: # config.GUILD_ID is None
             bot_log.warning("config.GUILD_ID is not set. Skipping manual guild command addition in on_ready.")


    except Exception as e:
        bot_log.critical(f"Failed to load cogs.bot_commands or manually add commands: {e}", exc_info=True)
        return


    # --- Debugging: Commands registered to bot.tree AFTER manual add (before sync) ---
    # Log the state after manual adding. We EXPECT Guild IDs: [GUILD_ID] for cog commands now.
    registered_commands_post_manual_add = bot.tree.get_commands()
    bot_log.info(f"--- Debugging: Commands registered to bot.tree AFTER manual add but BEFORE sync ({len(registered_commands_post_manual_add)} total) ---")
    if not registered_commands_post_manual_add:
         bot_log.warning("No commands found registered to bot.tree after manual add but before sync.")
    else:
        for command in registered_commands_post_manual_add:
            command_guild_ids = "N/A (AttributeError accessing guild_ids)" # Default value
            try:
                command_guild_ids = command.guild_ids
            except AttributeError:
                # Try accessing the potentially private attribute if the public one fails
                try:
                     command_guild_ids = command._guild_ids
                except AttributeError:
                     pass # Still not found

            bot_log.info(f"  Command: /{command.name}, Type: {type(command).__name__}, Description: {command.description}, Guild IDs: {command_guild_ids}")

    bot_log.info("--- End Debugging: AFTER manual add but BEFORE sync ---")


    # Sync slash commands - Perform Guild Sync
    guild_synced = False
    guild = None # Define guild object outside the try block
    if config.GUILD_ID:
        guild = discord.Object(id=config.GUILD_ID)
        bot_log.info(f"Attempting to clear and sync slash commands for guild {config.GUILD_ID}")
        try:
            # Clear existing commands in the guild first (good practice for development)
            # This should clear any outdated commands synced to the guild.
            # And prepare for the newly manually added commands associated with the guild.
            bot.tree.clear_commands(guild=guild)
            bot_log.info(f"Cleared commands for guild {config.GUILD_ID}")

            # Sync specifically to the guild
            # This call communicates with the Discord API to update commands in the guild.
            # It should now sync the commands that were manually added to bot.tree with guild=...
            await bot.tree.sync(guild=guild)

            bot_log.info(f"Successfully synced slash commands to guild {config.GUILD_ID}")
            guild_synced = True # Mark as synced if the sync call completes without exception
        except Exception as e:
            bot_log.error(f"Failed to sync slash commands to guild {config.GUILD_ID}: {e}", exc_info=True)
    else:
        bot_log.warning("GUILD_ID not set. Skipping guild-specific command sync.")


    # --- Debugging: Fetch and inspect guild commands from API *AFTER* sync attempt ---
    # Keep this block to see if the fetching errors persist even after successful manual add+sync.
    if guild_synced and guild: # Only attempt to fetch if sync was attempted for a guild AND guild object is valid
        bot_log.info(f"--- Debugging: Attempting to fetch guild commands from API for guild {config.GUILD_ID} AFTER sync ---")
        api_commands = []
        fetch_method_used = "None"
        fetch_success = False
        try:
            # Try the documented public method first
            # Note: This method was causing AttributeError before. Keep to see if manual add/sync changes anything.
            api_commands = await bot.tree.fetch_guild_commands(guild=guild)
            fetch_method_used = "fetch_guild_commands"
            fetch_success = True
            bot_log.info(f"Successfully used {fetch_method_used}.")
        except AttributeError:
             # If public method fails, try the suggested private attribute
            bot_log.warning("fetch_guild_commands not found, trying _guild_commands.")
            try:
                 # Note: Accessing underscored attributes is not guaranteed stable.
                 # We saw TypeError ('dict' not callable) before when trying to call _guild_commands.
                 # Trying again in case context changes behavior.
                 api_commands = await bot.tree._guild_commands(guild.id)
                 fetch_method_used = "_guild_commands"
                 fetch_success = True
                 bot_log.warning(f"Successfully used potentially private method {fetch_method_used}.")
            except AttributeError: # Corrected indentation
                 bot_log.error("Neither fetch_guild_commands nor _guild_commands found on CommandTree.")
            except TypeError: # Corrected indentation
                 bot_log.error("_guild_commands found but is not callable (TypeError).")
            except Exception as e: # Corrected indentation
                 bot_log.error(f"Error using _guild_commands: {e}", exc_info=True)
                 fetch_method_used = f"Error with _guild_commands: {e}"


        if fetch_success:
            bot_log.info(f"Fetched {len(api_commands)} commands from API for guild {config.GUILD_ID} using {fetch_method_used}.")
            if not api_commands:
                bot_log.warning(f"No commands fetched from API for guild {config.GUILD_ID}. This might indicate an issue with syncing despite the log message, or a significant API delay.")
            else:
                for command in api_commands:
                    # Need to be careful accessing attributes on commands fetched via API - they might differ slightly
                    # Fetched commands are often dict-like or have attributes
                    command_name = getattr(command, 'name', 'N/A')
                    command_description = getattr(command, 'description', 'N/A')
                    command_guild_id = getattr(command, 'guild_id', 'N/A') # This should be present for guild commands

                    # Alternative access if the fetched command is a dictionary (can sometimes happen with raw API responses)
                    if isinstance(command, dict):
                        command_name = command.get('name', 'N/A')
                        command_description = command.get('description', 'N/A')
                        command_guild_id = command.get('guild_id', 'N/A')


                    bot_log.info(f"  API Fetched Command: /{command_name}, Type: {type(command).__name__}, Description: {command_description}, Guild ID: {command_guild_id}")

        else:
             bot_log.error("Could not fetch commands from API using available methods.")

    else:
        bot_log.info("Skipping API command fetch as guild sync was skipped or failed or guild object invalid.")

    bot_log.info("--- End Debugging: AFTER sync ---")


    # Recalculate initial counters from DB
    await state.recalculate_all_counters(bot)
    bot_log.info("Recalculated initial registration counters.")

    # Update the persistent message embed on startup
    asyncio.create_task(state.update_registration_embed(bot))
    bot_log.info("Scheduled initial persistent embed update task.")

    # Add persistent views back to message if found
    if bot.persistent_channel_id and bot.persistent_message_id:
        try:
            channel = bot.get_channel(bot.persistent_channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(bot.persistent_message_id)
                    # Pass bot.active_events to the view constructor
                    view = ui_components.EventSelectionView(bot.active_events)
                    await message.edit(view=view) # Re-attaches the view
                    bot_log.info(f"Re-added persistent view to message {bot.persistent_message_id} in channel {bot.persistent_channel_id}.")
                except discord.NotFound:
                    bot_log.warning(f"Persistent message {bot.persistent_message_id} not found. Cannot re-add view.")
                except discord.Forbidden:
                    bot_log.warning(f"Missing permissions to fetch or edit persistent message {bot.persistent_message_id}.")
                except Exception as e:
                    bot_log.error(f"Error re-adding persistent view to message {bot.persistent_message_id}: {e}", exc_info=True)
            else:
                bot_log.warning(f"Persistent channel {bot.persistent_channel_id} not found. Cannot re-add view.")
        except Exception as e:
            bot_log.error(f"Error fetching persistent message or channel: {e}", exc_info=True)


    bot_log.info(f'{bot.user.name} is fully ready!')


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    # bot_log.debug(f"Message from {message.author}: {message.content}")
    # await bot.process_commands(message)


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # Ensure interaction is not already responded to before attempting to send message
    if interaction.response.is_done():
        bot_log.error(f"Interaction already acknowledged, cannot send error message for: {error}", exc_info=True)
        # If acknowledged, try editing the original response if possible, or just log
        try:
             original_error_msg = str(error.original) if isinstance(error, discord.app_commands.CommandInvokeError) and error.original else str(error)
             # Check if original response exists before editing
             try:
                 await interaction.edit_original_response(content=f"{config.EMOJI_ERROR} An error occurred: {original_error_msg}", view=None, embed=None)
             except discord.NotFound:
                  bot_log.debug("Original interaction response not found, cannot edit.")
        except discord.HTTPException:
             bot_log.debug("Failed to edit original response for error in acknowledged interaction.")
        return # Exit the error handler

    if isinstance(error, discord.app_commands.CheckFailure):
        bot_log.warning(f"Check failure for user {interaction.user.name} on command '{interaction.command.name if interaction.command else 'Unknown' }': {error}")
        await interaction.response.send_message(str(error), ephemeral=True)
    elif isinstance(error, discord.app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"This command is on cooldown. Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
    elif isinstance(error, discord.app_commands.CommandInvokeError):
        bot_log.error(f"Error invoking command '{interaction.command.name if interaction.command else 'Unknown'}' by {interaction.user.name}: {error.original}", exc_info=True)
        user_error_msg = f"{config.EMOJI_ERROR} An unexpected error occurred while running this command."
        # Try sending the original error detail if it's a string and not too long/sensitive
        if isinstance(error.original, discord.HTTPException):
             user_error_msg += f"\nDetails: {error.original}"
        elif isinstance(error.original, Exception) and not isinstance(error.original, (TypeError, ValueError, AttributeError, KeyError)):
             detail_msg = str(error.original)
             if len(detail_msg) < 100 and '\n' not in detail_msg:
                  user_error_msg += f"\nDetails: {type(error.original).__name__}: {detail_msg}"
             else:
                  user_error_msg += f"\nCheck bot logs for details."
        else:
             user_error_msg += f"\nCheck bot logs for details."

        await interaction.response.send_message(user_error_msg, ephemeral=True)

    elif isinstance(error, discord.app_commands.CommandNotFound):
         # This will now log the guild ID where the command was attempted
         bot_log.error(f"Application command '{error.name}' not found. Interaction Guild ID: {interaction.guild_id}")
         user_error_msg = f"{config.EMOJI_ERROR} Command '{error.name}' not found. It might not be synced correctly in this server. Please try again later or contact an admin if the issue persists."
         # Use followup if the interaction is already acknowledged (often the case for CommandNotFound)
         if interaction.response.is_done():
              try:
                await interaction.followup.send(user_error_msg, ephemeral=True)
              except discord.HTTPException:
                 bot_log.debug("Failed to send CommandNotFound followup message.")
         else:
              await interaction.response.send_message(user_error_msg, ephemeral=True)

    else:
        bot_log.error(f"Unhandled application command error: {error}", exc_info=True)
        user_error_msg = f"{config.EMOJI_ERROR} An unhandled error occurred."
        if interaction.response.is_done():
            try: await interaction.followup.send(user_error_msg, ephemeral=True)
            except discord.HTTPException: bot_log.debug("Failed to send unhandled error followup.")
        else:
            await interaction.response.send_message(user_error_msg, ephemeral=True)


# Run the bot
if __name__ == "__main__":
    # Corrected the check to use config.BOT_TOKEN
    if not config.BOT_TOKEN:
        bot_log.critical("FATAL: BOT_TOKEN is not set in config.py or environment variables.")
        exit(1)

    # Ensure the token passed to bot.run is config.BOT_TOKEN
    bot.run(config.BOT_TOKEN, reconnect=True)
