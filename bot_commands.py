import discord
from discord.ext import commands
# Import the app_commands module and the Choice class
import discord.app_commands as app_commands
from discord.app_commands import Choice

import config
import database
import state
import lookup
import registration
import ui_components
import logging
import asyncio
import io
import pandas as pd
from tabulate import tabulate
from thefuzz import fuzz, process
import functools
import utils # Import the utils module

# Configure logging for this module
bot_log = logging.getLogger('registration_bot')

# Assuming ADMIN_ROLE_ID is defined in config.py
ADMIN_ROLE_ID = config.ADMIN_ROLE_ID

# Define the admin check function outside the class
# This function is used by the @app_commands.check decorator
async def is_admin(interaction: discord.Interaction) -> bool:
    """Checks if the user interacting has the configured admin role."""
    if ADMIN_ROLE_ID is None:
        # Check if interaction has a response that's not yet done
        if not interaction.response.is_done():
             await interaction.response.send_message("Admin role is not configured in config.py.", ephemeral=True)
        else:
             # If already deferred or responded, try followup (though check failures often aren't deferred)
             try: await interaction.followup.send("Admin role is not configured in config.py.", ephemeral=True)
             except discord.HTTPException: pass # Ignore if followup fails
        return False
    if interaction.guild is None:
         if not interaction.response.is_done():
             await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
         else:
             try: await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
             except discord.HTTPException: pass
         return False
    admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
    if admin_role and admin_role in interaction.user.roles:
        return True
    # If user doesn't have the role, send the denial message
    if not interaction.response.is_done():
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
    else:
        try: await interaction.followup.send("You do not have permission to use this command.", ephemeral=True)
        except discord.HTTPException: pass
    return False

# Assuming teams module exists and has toggle_captain_status and _perform_assignment
try:
    import teams
except ImportError:
    bot_log.warning("Teams module not found. Captain/Assignment functions may fail.")
    # Define placeholder functions if teams module is critical
    class teams:
        @staticmethod
        async def toggle_captain_status(interaction, chief_name, event_name, time_slot, team):
            bot_log.error("teams.toggle_captain_status not implemented (teams module missing?)")
            if not interaction.response.is_done(): await interaction.response.send_message(f"{config.EMOJI_ERROR} Captain toggle functionality is unavailable.", ephemeral=True)
            else: await interaction.followup.send(f"{config.EMOJI_ERROR} Captain toggle functionality is unavailable.", ephemeral=True)
        @staticmethod
        async def _perform_assignment(interaction, event, slot):
             bot_log.error("teams._perform_assignment not implemented (teams module missing?)")
             if not interaction.response.is_done(): await interaction.response.send_message(f"{config.EMOJI_ERROR} Team assignment functionality is unavailable.", ephemeral=True)
             else: await interaction.followup.send(f"{config.EMOJI_ERROR} Team assignment functionality is unavailable.", ephemeral=True)


class BotCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ensure the active_events list exists on the bot if not already present
        if not hasattr(self.bot, 'active_events'):
             self.bot.active_events = config.DEFAULT_ACTIVE_EVENTS

    async def get_guild(self, interaction: discord.Interaction) -> discord.Guild | None:
        if config.GUILD_ID is None:
            # If GUILD_ID is not set, rely on interaction.guild if possible
            if interaction.guild:
                return interaction.guild
            await interaction.response.send_message("Server (Guild) ID is not configured and interaction did not provide one.", ephemeral=True)
            return None

        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild:
            await interaction.response.send_message(f"Could not find guild with ID {config.GUILD_ID}. Is the bot in this server?", ephemeral=True)
        return guild


    @app_commands.command(name="ping", description="Checks bot latency")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Pong! {round(self.bot.latency * 1000)}ms")

    @app_commands.command(name="settings", description="Administrator settings menu")
    @app_commands.check(is_admin) # Checks are back
    async def settings_menu(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="⚙️ Administrator Settings Menu",
            description="Please select a category:",
            color=config.COLOR_INFO
        )
        # Assuming SettingsMenuView exists in ui_components.py
        view = ui_components.SettingsMenuView()

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


    @app_commands.command(name="viewregs", description="View current registrations for an event.")
    @app_commands.choices(event=[
        Choice(name=event_name, value=event_name) for event_name in config.DEFAULT_ACTIVE_EVENTS
    ])
    @app_commands.check(is_admin) # Checks are back
    async def viewregs(self, interaction: discord.Interaction, event: Choice[str]):
        event_name = event.value
        await interaction.response.defer(thinking=True)

        registrations_data = database.get_registrations_for_viewregs(event_name)

        if not registrations_data:
            await interaction.followup.send(f"{config.EMOJI_INFO} No registrations found for **{event_name}**.", ephemeral=True)
            return

        df = pd.DataFrame(registrations_data)

        df['Role'] = df.apply(lambda row: f"{config.EMOJI_CAPTAIN} Captain" if row['is_captain'] else f"{config.EMOJI_FUEL} Fuel Mgr" if row['fuel_mgr_status'] else "Member", axis=1)
        df['Slot Type'] = df['substitute'].apply(lambda x: 'Sub' if x else 'Main')
        # Use verified_fc_display or calculate from raw furnace_level
        df['FC Level'] = df.apply(lambda row: row['verified_fc_display'] or utils.get_display_level(row['furnace_level']), axis=1)
        df['Chief Name'] = df['chief_name']
        df['Team'] = df['team_assignment'].fillna('Unassigned')
        df['Time Slot'] = df['time_slot']
        df['FID'] = df['player_fid'].fillna('N/A')
        df['Kingdom ID'] = df['kingdom_id'].fillna('N/A') # Ensure Kingdom ID is handled

        columns_to_display = ['Time Slot', 'Team', 'Role', 'Chief Name', 'FC Level', 'FID', 'Kingdom ID', 'date']
        display_df = df[columns_to_display].sort_values(by=['Time Slot', 'substitute', 'Team', 'Role', 'FC Level'], ascending=[True, True, True, False, False])

        if display_df.empty:
             await interaction.followup.send(f"{config.EMOJI_INFO} No registrations found for **{event_name}** with sufficient data to display.", ephemeral=True)
             return

        output = tabulate(display_df, headers='keys', tablefmt='pretty')
        output = f"```\n{output}\n```"

        if len(output) > 1990:
            try:
                with io.StringIO(output) as outfile:
                    await interaction.followup.send(f"{config.EMOJI_INFO} Registrations for **{event_name}**:", file=discord.File(outfile, filename=f'{event_name}_registrations.txt'))
            except Exception as e:
                bot_log.error(f"Failed to send registration list as file: {e}", exc_info=True)
                await interaction.followup.send(f"{config.EMOJI_WARNING} Registration list is too long for a message and failed to send as a file.", ephemeral=True)
        else:
            await interaction.followup.send(f"{config.EMOJI_INFO} Registrations for **{event_name}**:\n{output}")


    @app_commands.command(name="fuelme", description="Links your Discord account to your game FID for Fuel Manager role.")
    async def fuelme(self, interaction: discord.Interaction):
        # Assuming FuelMeModal is defined in ui_components.py
        try:
            await interaction.response.send_modal(ui_components.FuelMeModal())
        except AttributeError:
             bot_log.error("FuelMeModal not found in ui_components.py")
             await interaction.response.send_message(f"{config.EMOJI_ERROR} FuelMe command is not fully implemented (Modal not found).", ephemeral=True)


    @app_commands.command(name="unfuelme", description="Unlinks your Discord account from your game FID.")
    async def unfuelme(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        discord_id = interaction.user.id

        linked_fid = database.get_linked_fid(discord_id)

        if not linked_fid:
            await interaction.followup.send(f"{config.EMOJI_INFO} Your Discord account is not currently linked to a game FID.", ephemeral=True)
            return

        success = database.unlink_discord_fid(discord_id)

        if success:
            guild = await self.get_guild(interaction)
            if guild:
                 fuel_manager_role = discord.utils.get(guild.roles, name="Fuel Manager") # Replace "Fuel Manager" with actual role name
                 if fuel_manager_role:
                      member = guild.get_member(discord_id)
                      if member and fuel_manager_role in member.roles:
                           try:
                               await member.remove_roles(fuel_manager_role)
                               bot_log.info(f"Removed Fuel Manager role from {interaction.user.name} ({discord_id})")
                           except discord.Forbidden:
                                bot_log.error(f"Missing permissions to remove Fuel Manager role from {member.display_name}")
                           except Exception as e:
                                bot_log.error(f"Error removing Fuel Manager role from {member.display_name}: {e}")
                 else:
                      bot_log.warning("Fuel Manager role not found in guild.")

            await interaction.followup.send(f"{config.EMOJI_SUCCESS} Your Discord account has been unlinked from FID `{linked_fid}`.", ephemeral=True)
            bot_log.info(f"Discord ID {discord_id} unlinked from FID {linked_fid}")
        else:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Failed to unlink your account. Database error?", ephemeral=True)
             bot_log.error(f"Failed to unlink Discord ID {discord_id} from FID {linked_fid} in DB.")


    @app_commands.command(name="fuelmanagers", description="Lists current Fuel Managers.")
    @app_commands.check(is_admin) # Checks are back
    async def fuelmanagers(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        fuel_manager_fids = database.get_fuel_managers()
        if not fuel_manager_fids:
            await interaction.followup.send(f"{config.EMOJI_INFO} No Fuel Managers currently registered.", ephemeral=True)
            return

        guild = await self.get_guild(interaction)
        if not guild:
            await interaction.followup.send(f"{config.EMOJI_WARNING} Could not retrieve server info to list Discord users.", ephemeral=True)
            return

        lines = [f"{config.EMOJI_FUEL} **Current Fuel Managers:**"]
        for fid in fuel_manager_fids:
            discord_id = database.get_linked_discord_user(fid)
            if discord_id:
                member = guild.get_member(discord_id)
                if member:
                    lines.append(f"- {member.display_name} (Discord ID: {discord_id}, FID: {fid})")
                else:
                    lines.append(f"- User not found in server (Discord ID: {discord_id}, FID: {fid})")
            else:
                lines.append(f"- No linked Discord user found for FID: {fid}")

        output = "\n".join(lines)
        if len(output) > 1990:
             try:
                with io.StringIO(output) as outfile:
                    await interaction.followup.send(f"{config.EMOJI_INFO} Fuel Managers List:", file=discord.File(outfile, filename='fuel_managers.txt'), ephemeral=True)
             except Exception as e:
                bot_log.error(f"Failed to send fuel manager list as file: {e}", exc_info=True)
                await interaction.followup.send(f"{config.EMOJI_WARNING} Fuel Manager list is too long for a message and failed to send as a file.", ephemeral=True)
        else:
             await interaction.followup.send(output, ephemeral=True)


    @app_commands.command(name="addfuelmanager", description="Adds a player to the Fuel Managers list by FID.")
    @app_commands.check(is_admin) # Checks are back
    async def addfuelmanager(self, interaction: discord.Interaction, fid: int):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if fid <= 0:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FID provided.", ephemeral=True)
             return

        success = database.add_fuel_manager_role(fid)

        if success:
            discord_id = database.get_linked_discord_user(fid)
            guild = await self.get_guild(interaction)
            role_added_msg = ""
            if discord_id and guild:
                fuel_manager_role = discord.utils.get(guild.roles, name="Fuel Manager") # Replace "Fuel Manager"
                if fuel_manager_role:
                    member = guild.get_member(discord_id)
                    if member:
                         try:
                             await member.add_roles(fuel_manager_role)
                             bot_log.info(f"Added Fuel Manager role to {member.display_name} ({discord_id}) for FID {fid}")
                             role_added_msg = f" and granted the Fuel Manager role."
                         except discord.Forbidden:
                             bot_log.error(f"Missing permissions to add Fuel Manager role to {member.display_name}")
                             role_added_msg = f" but could not grant the Fuel Manager role (permissions issue?)."
                         except Exception as e:
                             bot_log.error(f"Error adding Fuel Manager role to {member.display_name}: {e}")
                             role_added_msg = f" but encountered an error granting the Fuel Manager role."
                    else:
                         role_added_msg = f" but the linked Discord user is not in this server."
                else:
                     role_added_msg = f" but the Fuel Manager role was not found in the server."
            elif discord_id:
                 role_added_msg = f" but no server information was available to grant the role."
            else:
                 role_added_msg = f"."

            await interaction.followup.send(f"{config.EMOJI_SUCCESS} FID `{fid}` has been added to the Fuel Managers list{role_added_msg}", ephemeral=True)
            bot_log.info(f"FID {fid} added to Fuel Managers list.")

        else:
            await interaction.followup.send(f"{config.EMOJI_INFO} FID `{fid}` is already in the Fuel Managers list.", ephemeral=True)

    @app_commands.command(name="removefuelmanager", description="Removes a player from the Fuel Managers list by FID.")
    @app_commands.check(is_admin) # Checks are back
    async def removefuelmanager(self, interaction: discord.Interaction, fid: int):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if fid <= 0:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FID provided.", ephemeral=True)
             return

        success = database.remove_fuel_manager_role(fid)

        if success:
            discord_id = database.get_linked_discord_user(fid)
            guild = await self.get_guild(interaction)
            role_removed_msg = ""
            if discord_id and guild:
                fuel_manager_role = discord.utils.get(guild.roles, name="Fuel Manager") # Replace "Fuel Manager"
                if fuel_manager_role:
                    member = guild.get_member(discord_id)
                    if member and fuel_manager_role in member.roles:
                         try:
                             await member.remove_roles(fuel_manager_role)
                             bot_log.info(f"Removed Fuel Manager role from {member.display_name} ({discord_id}) for FID {fid}")
                             role_removed_msg = f" and removed the Fuel Manager role."
                         except discord.Forbidden:
                             bot_log.error(f"Missing permissions to remove Fuel Manager role from {member.display_name}")
                             role_removed_msg = f" but could not remove the Fuel Manager role (permissions issue?)."
                         except Exception as e:
                             bot_log.error(f"Error removing Fuel Manager role from {member.display_name}: {e}")
                             role_removed_msg = f" but encountered an error removing the Fuel Manager role."
                    elif member:
                         role_removed_msg = f", but the user did not have the role."
                    else:
                         role_removed_msg = f", but the linked Discord user is not in this server."
                else:
                     role_removed_msg = f", but the Fuel Manager role was not found in the server."
            elif discord_id:
                 role_removed_msg = f", but no server information was available to remove the role."
            else:
                 role_removed_msg = f"."

            await interaction.followup.send(f"{config.EMOJI_SUCCESS} FID `{fid}` has been removed from the Fuel Managers list{role_removed_msg}", ephemeral=True)
            bot_log.info(f"FID {fid} removed from Fuel Managers list.")

        else:
            await interaction.followup.send(f"{config.EMOJI_INFO} FID `{fid}` was not found in the Fuel Managers list.", ephemeral=True)

    @app_commands.command(name="settings", description="Configure and manage bot settings")
    @app_commands.check(checks.is_admin)
    async def settings_command(self, interaction: discord.Interaction):
    """Configure and manage bot settings and preferences."""
    
    # Create the settings embed
    embed = discord.Embed(
        title=f"{config.EMOJI_MANAGE} Bot Settings",
        description="Configure bot settings and active events.",
        color=config.COLOR_MANAGE
    )
    
    # Add fields for current settings
    active_events = ", ".join(self.bot.active_events) if self.bot.active_events else "None"
    embed.add_field(
        name=f"{config.EMOJI_EVENT} Active Events", 
        value=active_events, 
        inline=False
    )
    
    # Create a view with settings controls
    view = ui_components.SettingsView(self.bot)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    # =========================================================================
    # Command Handlers for UI Interactions (called from ui_components)
    # =========================================================================

    async def handle_setup_register_from_ui(self, interaction: discord.Interaction):
         guild = await self.get_guild(interaction)
         if not guild: return

         bot_log.info(f"Admin Settings UI: 'Setup Message' clicked by {interaction.user.name} in channel {interaction.channel.name}")
         await interaction.response.defer(thinking=True, ephemeral=True) # Defer ephemerally

         # --- Update Active Events State (Assuming default 'both' for UI trigger unless a modal is added) ---
         # If you need the admin to select the mode (Foundry/Canyon/Both) via UI, we would add a modal or select here
         # For now, defaulting to all active events from config, similar to original 'both' mode
         self.bot.active_events = config.DEFAULT_ACTIVE_EVENTS
         bot_log.info(f"Set active events to: {self.bot.active_events}")

         # Save the new event state immediately to persistence (This might not be needed if state is only bot attribute)
         # state.save_persistent_message_id() # Removed this call as it only saves message/channel IDs

         # --- Delete Old Message (if tracked and accessible) ---
         if self.bot.persistent_message_id and self.bot.persistent_channel_id:
            bot_log.info(f"Attempting to delete previous persistent message {self.bot.persistent_message_id} in channel {self.bot.persistent_channel_id}")
            try:
                old_ch = self.bot.get_channel(self.bot.persistent_channel_id)
                if old_ch and isinstance(old_ch, discord.TextChannel) and old_ch.guild == interaction.guild:
                    old_msg = await old_ch.fetch_message(self.bot.persistent_message_id)
                    await old_msg.delete()
                    bot_log.info(f"Successfully deleted old persistent message {self.bot.persistent_message_id} in channel {old_ch.id}")
                elif not old_ch:
                    bot_log.warning(f"Old channel {self.bot.persistent_channel_id} not found or accessible. Cannot delete old message.")
                elif old_ch.guild != interaction.guild:
                    bot_log.warning(f"Old channel {self.bot.persistent_channel_id} is in a different guild ({old_ch.guild.id} vs {interaction.guild.id}). Cannot delete old message.")
                else: # Not a TextChannel
                    bot_log.warning(f"Old channel {self.bot.persistent_channel_id} is not a text channel ({old_ch.type}). Cannot delete old message.")
            except discord.NotFound:
                bot_log.warning(f"Old persistent message {self.bot.persistent_message_id} not found (already deleted?).")
            except discord.Forbidden:
                bot_log.warning(f"Lacked permissions ('Manage Messages') to delete old message {self.bot.persistent_message_id} in channel {self.bot.persistent_channel_id}.")
            except Exception as find_err:
                bot_log.warning(f"Error accessing/deleting old persistent message: {find_err}")
            # Clear the IDs regardless of deletion success to avoid retrying on error
            self.bot.persistent_message_id = None
            self.bot.persistent_channel_id = None
            state.save_registration_message_ids(None, None) # Save the cleared state

         # --- Send New Message ---
         message = None
         try:
            # Ensure recalculation happens before building the new embed
            await state.recalculate_all_counters(self.bot)
            # Build the embed and view - This should match your original build_registration_embed_and_view output
            # Assumes build_registration_embed_and_view returned an embed and EventSelectionView
            embed = state.build_registration_embed(self.bot) # Get the embed
            # Use active_events from bot attribute to initialize the view
            view = ui_components.EventSelectionView(self.bot.active_events) # Get the view (Dropdown + Manage)

            if isinstance(interaction.channel, discord.TextChannel):
                message = await interaction.channel.send(embed=embed, view=view) # Send publicly
                self.bot.persistent_message_id = message.id
                self.bot.persistent_channel_id = message.channel.id
                state.save_registration_message_ids(message.channel.id, message.id) # Save new state
                bot_log.info(f"New persistent message created: {message.id} in {message.channel.id} with events: {self.bot.active_events}")
                await interaction.followup.send(f"{config.EMOJI_SETUP} Registration message posted/updated in {interaction.channel.mention}: {message.jump_url}", ephemeral=True)
            else:
                bot_log.error(f"Attempted Admin UI setup in non-text channel: {interaction.channel.type}")
                await interaction.followup.send(f"{config.EMOJI_ERROR} The setup message can only be posted in standard text channels.", ephemeral=True)

         except discord.Forbidden:
            bot_log.error(f"No permissions to send message/embed in channel {interaction.channel.id}.")
            await interaction.followup.send(f"{config.EMOJI_ERROR} Setup failed. The bot lacks permissions to send messages or embeds in this channel.", ephemeral=True)
            # Ensure IDs are cleared if setup fails
            self.bot.persistent_message_id = None
            self.bot.persistent_channel_id = None
            state.save_registration_message_ids(None, None)
         except Exception as e:
            bot_log.error(f"Error posting new registration message via Admin UI: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An unexpected error occurred while posting the registration message. Check logs.", ephemeral=True)
            # Ensure IDs are cleared if setup fails
            self.bot.persistent_message_id = None
            self.bot.persistent_channel_id = None
            state.save_registration_message_ids(None, None)


    async def handle_clear_from_ui(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            deleted_count = database.clear_all_registrations()
            await state.recalculate_all_counters(self.bot)
            asyncio.create_task(state.update_registration_embed(self.bot))
            await interaction.followup.send(f"{config.EMOJI_SUCCESS} Cleared all registrations ({deleted_count} records deleted).", ephemeral=True)
            bot_log.info(f"Admin {interaction.user.name} cleared all registrations.")
        except Exception as e:
            bot_log.error(f"Error clearing registrations: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An error occurred while clearing registrations.", ephemeral=True)

    async def handle_register_from_ui(self, interaction: discord.Interaction, event_str: str, slot_type_str: str, slot_letter_str: str, fid_str: str, chief_name_str: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            event = event_str.strip()
            slot_type = slot_type_str.strip().lower()
            slot_letter = slot_letter_str.strip().upper()
            chief_name = chief_name_str.strip()
            player_fid = int(fid_str.strip()) if fid_str.strip().isdigit() else None

            if slot_type not in ['main', 'sub']:
                await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid slot type '{slot_type}'. Must be 'Main' or 'Sub'.", ephemeral=True)
                return

            if not chief_name:
                await interaction.followup.send(f"{config.EMOJI_ERROR} Chief Name cannot be empty.", ephemeral=True)
                return

            if player_fid is None:
                 await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FID '{fid_str}'. Please enter a valid number.", ephemeral=True)
                 return

            event_parts = event_str.strip().split('_')
            if len(event_parts) != 2:
                 await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid Event format '{event_str}'. Expected format like 'Foundry_14UTC'.", ephemeral=True)
                 return
            event_name = event_parts[0]
            time_slot_name = event_parts[1]
            is_substitute = slot_type == 'sub'

            api_data = None
            if player_fid:
                 api_result = await registration.call_player_api(self.bot.api_session, player_fid)
                 if api_result and not api_result.get("error"):
                      api_data = api_result
                 else:
                      error_msg = api_result.get("msg", "API error") if api_result else "API call failed"
                      bot_log.warning(f"Admin Register Modal: API lookup failed for FID {player_fid}: {error_msg}")
                      await interaction.followup.send(f"{config.EMOJI_WARNING} Could not verify player data via API for FID `{player_fid}` ({error_msg}). Proceeding with provided Name.", ephemeral=True)
            elif fid_str.strip():
                await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FID format '{fid_str}'. Please enter a number.", ephemeral=True)
                return


            await registration._process_registration(
                 bot=self.bot,
                 interaction=interaction,
                 event=event_name,
                 time_slot=time_slot_name,
                 is_substitute=is_substitute,
                 chief_name_input=chief_name,
                 entered_fc_level=None, # Admin modal doesn't require FC level input here based on current modal def
                 registration_target='other', # Assume admin is registering someone else
                 confirmed_player_fid=player_fid,
                 existing_api_data=api_data
            )


        except ValueError as e:
            bot_log.warning(f"Admin Register Modal Input Error: {e}")
            await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid input format: {e}", ephemeral=True)
        except Exception as e:
            bot_log.error(f"Error processing Admin Register Modal: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An unexpected error occurred while processing the registration.", ephemeral=True)


    async def handle_unregister_from_ui(self, interaction: discord.Interaction, event_str: str, slot_type_str: str, slot_letter_str: str):
         await interaction.response.defer(thinking=True, ephemeral=True)
         try:
            # This modal handler appears to be missing the Chief Name input from the UI definition.
            # Assuming Chief Name is needed for unregistration...
            await interaction.followup.send(f"{config.EMOJI_ERROR} The unregister modal is missing the 'Chief Name' field. Please update the modal definition in `ui_components.py` to include a `chief_name_input` field.", ephemeral=True)
            bot_log.error("Admin Unregister Modal handler called, but modal definition in ui_components.py is missing Chief Name field.")
            return

         except Exception as e:
            bot_log.error(f"Error processing Admin Unregister Modal: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An unexpected error occurred while processing the unregistration.", ephemeral=True)


    async def handle_assign_from_ui(self, interaction: discord.Interaction, event: str, time_slot: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        bot_log.info(f"Attempting to assign teams for {event} {time_slot}...")

        if event == "Foundry":
             teams_list = config.FOUNDRY_TEAMS
        elif event == "Canyon":
             teams_list = config.CANYON_TEAMS
        else:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Cannot assign teams for unknown event type: {event}", ephemeral=True)
             bot_log.warning(f"Attempted to assign teams for unknown event type: {event}")
             return

        await interaction.followup.send(f"{config.EMOJI_WAIT} Assigning teams for **{event} {time_slot}**...", ephemeral=True)


        try:
            bot_log.info(f"Clearing existing assignments for {event} {time_slot}...")
            database.clear_team_assignments_and_captains(event, time_slot)
            bot_log.info("Existing assignments cleared.")

            assignable_players = database.get_assignable_players(event, time_slot)
            unassignable_names = database.get_unassignable_players_names(event, time_slot)


            if not assignable_players:
                msg = f"{config.EMOJI_WARNING} No players found for **{event} {time_slot}** with verified FC levels to assign teams."
                if unassignable_names:
                     msg += f"\nPlayers without verified FC level (not assigned): {', '.join(unassignable_names[:10])}{'...' if len(unassignable_names)>10 else ''}"
                await interaction.followup.send(msg, ephemeral=True)
                bot_log.info(f"No assignable players found for {event} {time_slot}.")
                return

            bot_log.info(f"Found {len(assignable_players)} assignable players for {event} {time_slot}. Performing assignment...")


            team_assignments = {team: [] for team in teams_list}
            current_team_index = 0

            # Simple round-robin assignment
            for player in assignable_players:
                team_name = teams_list[current_team_index]
                team_assignments[team_name].append(player)
                current_team_index = (current_team_index + 1) % len(teams_list)

            assignment_results = []
            assigned_count = 0

            for team_name, players in team_assignments.items():
                 if players:
                      # Assign the first player in the list as captain for this team
                      captain_set = False
                      for i, player in enumerate(players):
                           database.update_player_team_assignment(player['chief_name'], event, time_slot, team_name)
                           if i == 0: # First player in the assigned list for this team
                                database.update_player_captain_status(player['chief_name'], event, time_slot, team_name, 1)
                                captain_set = True
                           assigned_count += 1

                      captain_name = players[0]['chief_name'] if captain_set else "No Captain Set"
                      assignment_results.append(f"**{team_name}:** {captain_name} ({config.EMOJI_CAPTAIN}) + {len(players)-1} members")
                 else:
                      assignment_results.append(f"**{team_name}:** (Empty)")


            success_msg = f"{config.EMOJI_SUCCESS} Team assignment complete for **{event} {time_slot}** ({assigned_count} players assigned)."
            if unassignable_names:
                 success_msg += f"\n{config.EMOJI_WARNING} Players without verified FC level (not assigned): {', '.join(unassignable_names[:10])}{'...' if len(unassignable_names)>10 else ''}"

            assignment_embed = discord.Embed(
                title=f"{config.EMOJI_TEAM} {event} {time_slot} Team Assignments",
                description="\n".join(assignment_results),
                color=config.COLOR_SUCCESS
            )

            await interaction.followup.send(success_msg, embed=assignment_embed, ephemeral=True)
            bot_log.info(f"Team assignment successfully completed for {event} {time_slot}.")

            # Recalculate and update embed after assignment
            await state.recalculate_all_counters(self.bot)
            asyncio.create_task(state.update_registration_embed(self.bot))


        except Exception as e:
            bot_log.error(f"Error during team assignment for {event} {time_slot}: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An error occurred during team assignment.", ephemeral=True)



    async def handle_setcaptain_from_ui(self, interaction: discord.Interaction, user_id: int, event_name: str, slot_type: str, slot_letter: str):
        await interaction.response.defer(thinking=True, ephemeral=True)

        time_slot = event_name.split('_')[-1]
        base_event = event_name.split('_')[0]

        team_assignment = None
        if base_event == "Foundry":
             if slot_type.lower() == 'main':
                  if slot_letter == 'A': team_assignment = 'A1'
                  elif slot_letter == 'B': team_assignment = 'A2'
             elif slot_type.lower() == 'sub':
                  if slot_letter == 'C': team_assignment = 'D1'
                  elif slot_letter == 'D': team_assignment = 'D2'
        elif base_event == "Canyon":
             # Canyon teams are just the letter A, B, C, G, R, etc.
             if slot_letter in config.CANYON_TEAMS: # Check if the letter is a valid canyon team
                  team_assignment = slot_letter


        if team_assignment is None:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Could not determine team assignment from input: Event='{event_name}', Slot Type='{slot_type}', Slot Letter='{slot_letter}'.", ephemeral=True)
             bot_log.warning(f"Could not map modal input to team: Event='{event_name}', Slot Type='{slot_type}', Slot Letter='{slot_letter}'")
             return


        reg_info = database.get_registration_by_user_event_slot_team(user_id, event_name, time_slot, team_assignment)

        if not reg_info:
             await interaction.followup.send(f"{config.EMOJI_WARNING} Could not find a registration for User ID `{user_id}` in **{event_name} {time_slot} Team {team_assignment}**.", ephemeral=True)
             bot_log.warning(f"Set Captain: No registration found for user {user_id} in {event_name} {time_slot} Team {team_assignment}.")
             return

        chief_name = reg_info.get('chief_name')
        current_status = reg_info.get('is_captain', 0)
        new_status = 1 if current_status == 0 else 0

        if new_status == 1:
            # If setting a new captain, clear the captain status for all other players in that specific team/slot/event
            database.clear_other_captains_in_team(event_name, time_slot, team_assignment, chief_name)
            bot_log.info(f"   Cleared other captains in {event_name} {time_slot} Team {team_assignment} before setting {chief_name}.")


        success = database.update_player_captain_status(chief_name, event_name, time_slot, team_assignment, new_status)

        if success:
             action = "assigned as" if new_status == 1 else "removed as"
             await interaction.followup.send(f"{config.EMOJI_SUCCESS} **{chief_name}** ({user_id}) has been {action} {config.EMOJI_CAPTAIN} captain for **{event_name} {time_slot} Team {team_assignment}**.", ephemeral=True)
             bot_log.info(f"Admin {interaction.user.name} toggled captain status for '{chief_name}' ({user_id}) in {event_name} {time_slot} Team {team_assignment} to {new_status}.")
        else:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Failed to update captain status for **{chief_name}** ({user_id}). Database error?", ephemeral=True)
             bot_log.error(f"Failed to update captain status for user {user_id} in {event_name} {time_slot} Team {team_assignment} in DB.")


    async def handle_export_from_ui(self, interaction: discord.Interaction):
         guild = await self.get_guild(interaction)
         if not guild: return

         await interaction.response.defer(thinking=True, ephemeral=True)

         options = [Choice(name=event_name, value=event_name) for event_name in config.DEFAULT_ACTIVE_EVENTS]

         if not options:
             await interaction.followup.send(f"{config.EMOJI_INFO} No active events configured for export.", ephemeral=True)
             return

         select = discord.ui.Select(
             placeholder="Select Event to export...",
             options=options,
             custom_id="export_select_event"
         )
         select.callback = functools.partial(self.export_event_select_callback, original_interaction=interaction)

         view = discord.ui.View(timeout=180)
         view.add_item(select)

         await interaction.followup.send(f"{config.EMOJI_INFO} Select an event to export registrations:", view=view, ephemeral=True)


    async def export_event_select_callback(self, interaction: discord.Interaction, original_interaction: discord.Interaction):
        if not interaction.data or 'values' not in interaction.data or not interaction.data['values']:
            await interaction.response.send_message(f"{config.EMOJI_ERROR} Invalid selection data received.", ephemeral=True)
            return

        event_name = interaction.data['values'][0]
        await interaction.response.defer(thinking=True, ephemeral=True)
        bot_log.info(f"Admin {original_interaction.user.name} selected event '{event_name}' for export.")

        registrations_data = database.get_registrations_for_export(event_name)

        if not registrations_data:
            await interaction.followup.send(f"{config.EMOJI_INFO} No registrations found for **{event_name}** to export.", ephemeral=True)
            return

        try:
            df = pd.DataFrame(registrations_data)

            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, sheet_name='Registrations', index=False)
            output.seek(0)

            file = discord.File(output, filename=f'{event_name}_registrations_export.xlsx')

            await interaction.followup.send(f"{config.EMOJI_SUCCESS} Here are the registrations for **{event_name}**:", file=file, ephemeral=True)
            bot_log.info(f"Exported registrations for {event_name} via UI for admin {original_interaction.user.name}.")

        except Exception as e:
            bot_log.error(f"Error exporting registrations for {event_name}: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An error occurred while generating the export file.", ephemeral=True)


    async def handle_reload_lookup_from_ui(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            initial_count = len(self.bot.fid_lookup_data)
            lookup.load_lookup_data(self.bot)
            new_count = len(self.bot.fid_lookup_data)
            await interaction.followup.send(f"{config.EMOJI_SUCCESS} Lookup data reloaded from `{config.FID_LOOKUP_CSV}`. {new_count} entries loaded (previously {initial_count}).", ephemeral=True)
            bot_log.info(f"Admin {interaction.user.name} reloaded lookup data. {new_count} entries loaded.")
        except FileNotFoundError:
            await interaction.followup.send(f"{config.EMOJI_ERROR} Lookup file `{config.FID_LOOKUP_CSV}` not found.", ephemeral=True)
            bot_log.error(f"Admin {interaction.user.name} attempted to reload lookup, but `{config.FID_LOOKUP_CSV}` was not found.")
        except Exception as e:
            bot_log.error(f"Error reloading lookup data: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An error occurred while reloading lookup data.", ephemeral=True)

    async def handle_view_lookup_from_ui(self, interaction: discord.Interaction):
         await interaction.response.defer(thinking=True, ephemeral=True)
         lookup_entries = lookup.get_all_lookup_entries(self.bot) # Use lookup function that gets from bot cache

         if not lookup_entries:
             await interaction.followup.send(f"{config.EMOJI_INFO} No lookup entries currently loaded.", ephemeral=True)
             return

         # Assuming lookup.get_all_lookup_entries returns a list of tuples (chief_name, fid)
         table_data = [{"Chief Name": name, "FID": fid} for name, fid in sorted(lookup_entries, key=lambda item: item[0].lower())]


         output = tabulate(table_data, headers='keys', tablefmt='pretty')
         output = f"```\n{output}\n```"

         if len(output) > 1990:
             try:
                 with io.StringIO(output) as outfile:
                     await interaction.followup.send(f"{config.EMOJI_INFO} Loaded Lookup Entries:", file=discord.File(outfile, filename='lookup_data.txt'), ephemeral=True)
             except Exception as e:
                 bot_log.error(f"Failed to send lookup list as file: {e}", exc_info=True)
                 await interaction.followup.send(f"{config.EMOJI_WARNING} Lookup list is too long for a message and failed to send as a file.", ephemeral=True)
         else:
             await interaction.followup.send(f"{config.EMOJI_INFO} Loaded Lookup Entries:\n{output}", ephemeral=True)


    async def handle_lookup_add_from_ui(self, interaction: discord.Interaction, chief_name: str, fid_str: str):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            chief_name = chief_name.strip()
            if not chief_name:
                 await interaction.followup.send(f"{config.EMOJI_ERROR} Chief Name cannot be empty.", ephemeral=True)
                 return
            try:
                fid = int(fid_str.strip())
                if fid <= 0: raise ValueError("FID must be positive.")
            except ValueError:
                await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FID format '{fid_str}'. Please enter a positive number.", ephemeral=True)
                return

            # Use lookup function that updates the bot cache and saves the file
            success = lookup.add_lookup_entry(self.bot, chief_name, fid)

            if success:
                 await interaction.followup.send(f"{config.EMOJI_SUCCESS} Added/Updated lookup entry: **{chief_name}** (FID: `{fid}`).", ephemeral=True)
                 bot_log.info(f"Admin {interaction.user.name} added/updated lookup entry: Chief='{chief_name}', FID={fid}")
            else:
                 await interaction.followup.send(f"{config.EMOJI_INFO} Lookup entry for **{chief_name}** (FID: `{fid}`) already existed and is unchanged.", ephemeral=True)
                 bot_log.info(f"Admin {interaction.user.name} attempted to add existing lookup entry: Chief='{chief_name}', FID={fid}")

        except Exception as e:
            bot_log.error(f"Error adding lookup entry: {e}", exc_info=True)
            await interaction.followup.send(f"{config.EMOJI_ERROR} An error occurred while adding the lookup entry.", ephemeral=True)

    async def handle_lookup_find_from_ui(self, interaction: discord.Interaction, chief_name_search: str):
         await interaction.response.defer(thinking=True, ephemeral=True)
         chief_name_search = chief_name_search.strip()

         if not chief_name_search:
              await interaction.followup.send(f"{config.EMOJI_ERROR} Chief Name cannot be empty for search.", ephemeral=True)
              return

         # Use lookup function that searches the bot cache
         all_entries = lookup.get_all_lookup_entries(self.bot) # Assuming this returns list of (name, fid) tuples
         if not all_entries:
              await interaction.followup.send(f"{config.EMOJI_INFO} Lookup data is empty. Cannot search.", ephemeral=True)
              return

         all_names = [name for name, fid in all_entries]

         matches = process.extract(chief_name_search, all_names, scorer=fuzz.token_sort_ratio, limit=config.FUZZY_MATCH_LIMIT + 5)

         found_matches = []
         for name_match, score in matches:
             if score >= config.FUZZY_MATCH_THRESHOLD:
                 matched_fid = None
                 # Find the FID for the matched name from the original entries
                 for orig_name, fid_val in all_entries:
                      if orig_name == name_match:
                           matched_fid = fid_val
                           break
                 if matched_fid is not None:
                      found_matches.append({"Chief Name": name_match, "FID": matched_fid, "Score": score})


         if not found_matches:
              await interaction.followup.send(f"{config.EMOJI_INFO} No close matches found for '{chief_name_search}' in the lookup data.", ephemeral=True)
              bot_log.info(f"Admin {interaction.user.name} searched lookup for '{chief_name_search}', no matches found.")
              return

         found_matches.sort(key=lambda x: x['Score'], reverse=True)

         table_data = [{"Chief Name": m['Chief Name'], "FID": m['FID'], "Score": f"{m['Score']}%"} for m in found_matches[:config.FUZZY_MATCH_LIMIT]]

         output = tabulate(table_data, headers='keys', tablefmt='pretty')
         output = f"```\n{output}\n```"

         await interaction.followup.send(f"{config.EMOJI_INFO} Found potential matches for '{chief_name_search}':\n{output}", ephemeral=True)
         bot_log.info(f"Admin {interaction.user.name} searched lookup for '{chief_name_search}'. Found {len(found_matches)} matches.")


# The setup function registers the cog
async def setup(bot: commands.Bot):
    # Create and add the cog instance without specifying the guild here.
    # Manual addition in on_ready will associate commands with the guild.
    cog_instance = BotCommands(bot)
    await bot.add_cog(cog_instance)
    bot_log.info("BotCommands cog added to bot.")

    # Manual command addition to tree will be done in the on_ready event in bot.py.

    bot_log.info("Cog setup complete.")
