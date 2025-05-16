import discord
# from discord import app_commands # Not needed in ui_components
from discord.ui import Button, View, Select, Modal, TextInput, button
import functools
import config
import lookup
import registration
import teams # Assuming teams module handles captain toggle logic
import database
# import utils # Assuming utils module contains get_display_level
# import logger # Removed as it caused AttributeError
import logging # Import standard logging

# Configure logging for this module
bot_log = logging.getLogger('registration_bot')


# Assuming utils module exists and has get_display_level, otherwise define a placeholder or integrate
try:
    import utils
    get_display_level = utils.get_display_level
except ImportError:
     bot_log.warning("Utils module not found, using placeholder for get_display_level.")
     def get_display_level(fc_level):
         return f"FC{fc_level-30}" if fc_level is not None else '?' # Placeholder

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
            await interaction.followup.send(f"{config.EMOJI_ERROR} Captain toggle functionality is unavailable.", ephemeral=True)
        @staticmethod
        async def _perform_assignment(interaction, event, slot):
             bot_log.error("teams._perform_assignment not implemented (teams module missing?)")
             await interaction.followup.send(f"{config.EMOJI_ERROR} Team assignment functionality is unavailable.", ephemeral=True)


class PossibleNameSelect(Select):
    def __init__(self, possible_matches, original_chief_name_input, event, time_slot, is_substitute, registration_target, entered_fc_level):
        # original_interaction is problematic to pass directly due to pickling for persistence.
        # We need to rethink how the modal submit or the fuzzy select gets access to the original modal inputs.
        # The current ChiefNameModal approach retrieves values directly from its own inputs in on_submit,
        # which is better. Let's pass the original modal input values instead of the interaction object.
        # original_chief_name_input is now passed directly
        self.original_chief_name_input = original_chief_name_input
        self.entered_fc_level = entered_fc_level # Already passed

        self.event = event
        self.time_slot = time_slot
        self.is_substitute = is_substitute
        self.registration_target = registration_target
        self.entered_fc_level = entered_fc_level


        options = []
        if not possible_matches:
            options.append(discord.SelectOption(label="Error: No matches found", value="-1", emoji=config.EMOJI_ERROR))
        else:
            options.append(discord.SelectOption(
                label="None of these (Use my typed name)", value="-1",
                description="Select this if your name isn't listed.", emoji=config.EMOJI_CANCEL
            ))
            for name, fid, score in possible_matches[:24]:
                options.append(discord.SelectOption(
                    label=name[:100],
                    value=str(fid),
                    description=f"Similarity: {score}%"
                ))
        super().__init__(placeholder="Did you mean one of these?", min_values=1, max_values=1, options=options, custom_id="confirm_fuzzy_name")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        selected_value = interaction.data['values'][0]
        bot_log.info(f"Fuzzy match selection: User {interaction.user.name} selected '{selected_value}'")

        # Retrieve the original typed name from the stored attribute
        original_typed_name = self.original_chief_name_input
        confirmed_chief_name = original_typed_name
        player_fid = None

        if selected_value == "-1":
            bot_log.info("User chose 'None of these'. Proceeding with typed name, no verification.")
            # Use the _process_registration from the main registration module
            await registration._process_registration(
                bot=interaction.client, # Pass bot instance
                interaction=interaction,
                event=self.event,
                time_slot=self.time_slot,
                is_substitute=self.is_substitute,
                chief_name_input=original_typed_name,
                entered_fc_level=self.entered_fc_level,
                registration_target=self.registration_target,
                confirmed_player_fid=None
            )
        else:
            try:
                player_fid = int(selected_value)
                # Use the bot's lookup data cache instead of calling lookup module directly here
                name_from_lookup = lookup.get_name_by_fid(interaction.client, player_fid)


                if name_from_lookup:
                    confirmed_chief_name = name_from_lookup
                    bot_log.info(f"User confirmed FID {player_fid}, canonical name '{confirmed_chief_name}'.")
                else:
                    bot_log.error(f"Could not map selected FID {player_fid} back to original name in bot cache! Using typed name '{original_typed_name}' as fallback.")
                    confirmed_chief_name = original_typed_name # Fallback

                # Use the _process_registration from the main registration module
                await registration._process_registration(
                    bot=interaction.client, # Pass bot instance
                    interaction=interaction,
                    event=self.event,
                    time_slot=self.time_slot,
                    is_substitute=self.is_substitute,
                    chief_name_input=confirmed_chief_name,
                    entered_fc_level=self.entered_fc_level,
                    registration_target=self.registration_target,
                    confirmed_player_fid=player_fid
                )
            except ValueError:
                bot_log.error(f"Invalid FID value '{selected_value}' in fuzzy select callback.")
                await interaction.followup.send(f"{config.EMOJI_ERROR} An internal error occurred processing the selected name. Please try again.", ephemeral=True)


class PossibleNameView(View):
    def __init__(self, possible_matches, original_chief_name_input, event, time_slot, is_substitute, registration_target, entered_fc_level):
        super().__init__(timeout=180)
        # Pass required data for the select, not the full interaction object
        select = PossibleNameSelect(possible_matches, original_chief_name_input, event, time_slot, is_substitute, registration_target, entered_fc_level)
        self.add_item(select)
        # Store data needed for timeout message update if necessary
        self._original_chief_name_input = original_chief_name_input
        self._interaction_message = None # Will store the message this view is on

    # Setter for message to allow tracking the message this view is attached to
    @property
    def message(self):
        return self._interaction_message

    @message.setter
    def message(self, message):
        self._interaction_message = message


    async def on_timeout(self):
        bot_log.info("PossibleNameView timed out.")
        for item in self.children:
            item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message:
                await self.message.edit(content=f"{config.EMOJI_WARNING} Name selection timed out for '{self._original_chief_name_input}'. Please try registering again.", view=self)
        except discord.HTTPException as e:
            bot_log.warning(f"Failed to edit message on PossibleNameView timeout (HTTP {e.status} - message might be deleted or interaction expired).")
        except AttributeError:
             bot_log.error(f"AttributeError on PossibleNameView timeout - message attribute not set.")
        except Exception as e:
            bot_log.error(f"Unexpected error during PossibleNameView timeout handling: {e}", exc_info=True)
        self.stop()


class ChiefNameModal(Modal, title="Input Registration Details"):
    chief_name_input = TextInput(
        label=f"{config.EMOJI_PERSON} Chief Name",
        placeholder="Enter your chief name (case insensitive)...",
        required=True, min_length=1, max_length=50
    )
    furnace_level_input = TextInput(
        label=f"{config.EMOJI_LEVEL} Furnace Level (1-10 Only)",
        placeholder="Enter FC level 1-10 (e.g., 7)",
        required=True, min_length=1, max_length=2,
        style=discord.TextStyle.short
    )

    def __init__(self, event, time_slot, is_substitute, registration_target):
        super().__init__(timeout=None)
        self.event = event
        self.time_slot = time_slot
        self.is_substitute = is_substitute
        self.registration_target = registration_target

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        chief_name_input = self.chief_name_input.value.strip()
        furnace_level_str = self.furnace_level_input.value.strip()
        furnace_level = None

        if not chief_name_input:
             await interaction.followup.send(f"{config.EMOJI_ERROR} Chief Name cannot be empty.", ephemeral=True)
             return

        try:
            furnace_level = int(furnace_level_str)
            if not (1 <= furnace_level <= 10):
                raise ValueError("FC level must be between 1 and 10.")
        except ValueError:
            bot_log.warning(f"Validation Error: Invalid FC '{furnace_level_str}' from {interaction.user.name}.")
            await interaction.followup.send(f"{config.EMOJI_ERROR} Invalid FC level: '{furnace_level_str}'. Please enter a number between 1 and 10.", ephemeral=True)
            return

        bot_log.info(f"\n--- [Modal Submit Received] ---")
        bot_log.info(f"   Submitter: {interaction.user.name}({interaction.user.id}) | Intent: '{self.registration_target}'")
        bot_log.info(f"   Input: Chief='{chief_name_input}', FC={furnace_level}, Event='{self.event}', Slot='{self.time_slot}', IsSub={self.is_substitute}")

        # Use the lookup function that leverages the bot's loaded data
        lookup_results = lookup.find_player_by_name(interaction.client, chief_name_input)
        exact_match_fid = None
        possible_matches = []

        if lookup_results and isinstance(lookup_results, tuple): # Exact match returned
            name_from_lookup, exact_match_fid, _ = lookup_results # Unpack name, fid, score (score is 100)
            chief_name_to_use = name_from_lookup # Use the canonical name from lookup
            bot_log.info(f"   Exact match found for '{chief_name_input.lower()}': FID {exact_match_fid}, Canonical Name '{chief_name_to_use}'")

            # Call the _process_registration function from the registration module
            await registration._process_registration(
                bot=interaction.client, # Pass bot instance
                interaction=interaction,
                event=self.event,
                time_slot=self.time_slot,
                is_substitute=self.is_substitute,
                chief_name_input=chief_name_to_use,
                entered_fc_level=furnace_level,
                registration_target=self.registration_target,
                confirmed_player_fid=exact_match_fid
            )
        else: # Fuzzy matches or no matches
            if lookup_results and isinstance(lookup_results, list): # Fuzzy matches returned
                 possible_matches = lookup_results
            bot_log.info(f"   Exact match not found for '{chief_name_input}'. Trying fuzzy match...")


            if possible_matches:
                bot_log.info(f"   Found {len(possible_matches)} potential fuzzy matches for '{chief_name_input}'. Showing select menu.")
                # Pass only necessary data, not the interaction object itself, to the view
                view = PossibleNameView(
                    possible_matches=possible_matches,
                    original_chief_name_input=chief_name_input, # Pass the typed name
                    event=self.event, time_slot=self.time_slot, is_substitute=self.is_substitute,
                    registration_target=self.registration_target, entered_fc_level=furnace_level
                )
                # Edit the interaction's original response to show the fuzzy select view
                # Since we deferred ephemerally, we can use edit_original_response
                # Ensure interaction has a response before editing
                if interaction.response.is_done():
                    msg = await interaction.edit_original_response(content="Did you mean one of these names? Please select the correct one or 'None of these'.", view=view)
                    view.message = msg # Store the message object in the view
                else:
                    # If not deferred, send as initial response
                    await interaction.response.send_message(content="Did you mean one of these names? Please select the correct one or 'None of these'.", view=view, ephemeral=True)
                    # Cannot store message object easily if sent this way without fetching later


            else:
                bot_log.info(f"   No exact or suitable fuzzy match found for '{chief_name_input}'. Proceeding without verification.")
                # Call the _process_registration function from the registration module
                await registration._process_registration(
                    bot=interaction.client, # Pass bot instance
                    interaction=interaction,
                    event=self.event,
                    time_slot=self.time_slot,
                    is_substitute=self.is_substitute,
                    chief_name_input=chief_name_input, # Use typed name if no lookup match
                    entered_fc_level=furnace_level,
                    registration_target=self.registration_target,
                    confirmed_player_fid=None
                )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        bot_log.error(f"Error in ChiefNameModal: {error}", exc_info=True)
        try:
             # Use followup.send since the interaction was deferred in on_submit
             await interaction.followup.send(f'{config.EMOJI_ERROR} An unexpected error occurred in the input form. Please try again.', ephemeral=True)
        except discord.HTTPException:
             bot_log.warning("Failed to send modal error followup (HTTPException).")


class RegistrationView(View):
    def __init__(self, event, registration_target):
        super().__init__(timeout=180) # Standard timeout for ephemeral views
        self.event = event
        self.registration_target = registration_target
        self.message = None # To store the message this view is on
        self.add_buttons()

    def add_buttons(self):
        time_slots = ["14UTC", "19UTC"]
        row_num = 0
        for slot in time_slots:
            # Main button
            main_button = Button(
                label=f"{self.event} {slot}",
                emoji=config.EMOJI_SLOT,
                style=discord.ButtonStyle.primary,
                # Custom ID encodes event, slot, is_sub (0 for main), registration target
                custom_id=f"register_{self.event}_{slot}_0_{self.registration_target}",
                row=row_num
            )
            main_button.callback = self.time_slot_button_callback # Assign the callback
            self.add_item(main_button)

            # Substitute button
            sub_button = Button(
                label=f"{self.event} {slot} (Sub)",
                emoji=config.EMOJI_SUB,
                style=discord.ButtonStyle.secondary,
                 # Custom ID encodes event, slot, is_sub (1 for sub), registration target
                custom_id=f"register_{self.event}_{slot}_1_{self.registration_target}",
                row=row_num
            )
            sub_button.callback = self.time_slot_button_callback # Assign the callback
            self.add_item(sub_button)

            row_num += 1

    # Callback function for the time slot buttons
    async def time_slot_button_callback(self, interaction: discord.Interaction):
         # Do NOT defer here. Send the modal directly.
         # Defer will happen in ChiefNameModal.on_submit.

         custom_id = interaction.data['custom_id']
         bot_log.info(f"Time Slot Button Click: Custom ID: {custom_id}")

         try:
             # Parse the custom_id: register_{event}_{slot}_{is_sub_int}_{self/other}
             parts = custom_id.split("_")
             if len(parts) == 5 and parts[0] == "register":
                 event_name = parts[1]
                 time_slot = parts[2]
                 is_substitute = parts[3] == '1' # Check the integer value
                 registration_target = parts[4]

                 bot_log.info(f"Parsed: Event={event_name}, Slot={time_slot}, IsSub={is_substitute}, Target={registration_target}")

                 # Disable the current view's buttons immediately after click
                 for item in self.children:
                     item.disabled = True
                 try:
                      if self.message:
                           await self.message.edit(view=self)
                 except discord.HTTPException: pass
                 except AttributeError: pass


                 # Now show the Chief Name modal
                 modal = ChiefNameModal(event=event_name, time_slot=time_slot, is_substitute=is_substitute, registration_target=registration_target)
                 # Send the modal as the response to the button interaction
                 await interaction.response.send_modal(modal)


             else:
                 bot_log.warning(f"Malformed time slot button custom_id: {custom_id}")
                 # If interaction was deferred, use followup (but we aren't deferring here now)
                 await interaction.response.send_message(f"{config.EMOJI_ERROR} An internal error occurred (Malformed button ID).", ephemeral=True)


         except Exception as e:
             bot_log.error(f"Error processing time slot button callback for {custom_id}: {e}", exc_info=True)
             # Ensure interaction is acknowledged if an error occurs before sending modal
             if not interaction.response.is_done():
                 await interaction.response.send_message(f"{config.EMOJI_ERROR} An unexpected error occurred.", ephemeral=True)
             else: # If already acknowledged (shouldn't happen if send_modal failed immediately)
                 await interaction.followup.send(f"{config.EMOJI_ERROR} An unexpected error occurred.", ephemeral=True)


    async def on_timeout(self):
        bot_log.info(f"RegistrationView timed out for event {self.event}, target {self.registration_target}.")
        for item in self.children:
            item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message:
                await self.message.edit(content=f"{config.EMOJI_WARNING} Time slot selection for {self.event} timed out. Please start again.", view=self)
        except discord.HTTPException as e:
            bot_log.warning(f"Failed to edit message on RegistrationView timeout (HTTP {e.status}).")
        except AttributeError:
            bot_log.warning("Failed to edit message on RegistrationView timeout (AttributeError).")
        self.stop()


class CancelRegistrationButton(View):
    def __init__(self, submitter_user_id, event, chief_name):
        super().__init__(timeout=300) # Standard timeout for ephemeral views
        self.submitter_user_id = submitter_user_id
        self.event = event
        self.chief_name = chief_name
        self.message = None # To store the message this view is on

    @button(label="Cancel This Registration", emoji=config.EMOJI_CANCEL, style=discord.ButtonStyle.danger, custom_id="inline_cancel_reg")
    async def cancel_button_callback(self, interaction: discord.Interaction, button_obj: Button):
        if interaction.user.id != self.submitter_user_id:
            await interaction.response.send_message(f"{config.EMOJI_WARNING} You can only cancel registrations you submitted yourself using this button.", ephemeral=True)
            return

        bot_log.info(f"--- [Inline Cancel Click] User: {interaction.user.name}, Cancelling: Chief='{self.chief_name}', Event='{self.event}' ---")
        await interaction.response.defer(ephemeral=True) # Defer the interaction

        # Call the cancellation logic from the registration module
        # Assuming cancel_registration_logic handles DB update and sends confirmation
        await registration.cancel_registration_logic(interaction, button_obj, self.chief_name, self.event)

        # Stop the view after successful cancellation attempt
        self.stop()

    async def on_timeout(self):
        bot_log.debug(f"CancelRegistrationButton timed out for {self.chief_name} ({self.event}).")
        for item in self.children:
             item.disabled = True
        try:
             # Use the stored message object to edit
             if self.message:
                await self.message.edit(view=self)
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


# This TimeSlotSelectView appears to be a duplicate or used in a different flow.
# Keeping it but noting its likely not part of the main user registration flow described.
class TimeSlotSelectView(View):
    def __init__(self, event, player_fid, nickname, verified_fc_level, verified_fc_display, kingdom_id, avatar_image):
        super().__init__(timeout=180)
        self.event = event
        self.player_fid = player_fid
        self.nickname = nickname
        self.verified_fc_level = verified_fc_level
        self.verified_fc_display = verified_fc_display
        self.kingdom_id = kingdom_id
        self.avatar_image = avatar_image
        self.message = None # To store the message this view is on
        self.add_buttons()

    def add_buttons(self):
        time_slots = ["14UTC", "19UTC"]
        row_num = 0
        for slot in time_slots:
            # Assuming these buttons have callbacks defined elsewhere
            self.add_item(Button(
                label=f"{self.event} {slot}", emoji=config.EMOJI_SLOT, style=discord.ButtonStyle.primary,
                custom_id=f"finalreg_{self.event}_{slot}_0_{self.player_fid}", row=row_num
            ))
            self.add_item(Button(
                label=f"{self.event} {slot} (Sub)", emoji=config.EMOJI_SUB, style=discord.ButtonStyle.secondary,
                custom_id=f"finalreg_{self.event}_{slot}_1_{self.player_fid}", row=row_num
            ))
            row_num += 1

    async def on_timeout(self):
        bot_log.info(f"TimeSlotSelectView timed out for FID {self.player_fid}, event {self.event}.")
        for item in self.children:
            item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message:
                await self.message.edit(content=f"{config.EMOJI_WARNING} Time slot selection timed out. Please start the registration again.", view=self)
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


class EventSelectionView(View):
    def __init__(self, active_events):
        super().__init__(timeout=None) # Persistent view, no timeout
        self.active_events = active_events
        # Store the bot instance if needed in timeout recovery, or rely on get_all_channels
        # self.bot = None # Could add this and set it after instantiation if needed

        self.setup_view()

    def setup_view(self):
        self.clear_items() # Clear existing items if view is reused/re-added

        select_options = []
        if "Foundry" in self.active_events:
            select_options.append(discord.SelectOption(label="Foundry Registration", value="Foundry", emoji=config.EMOJI_EVENT))
        if "Canyon" in self.active_events:
            select_options.append(discord.SelectOption(label="Canyon Clash Registration", value="Canyon", emoji=config.EMOJI_EVENT))

        placeholder = "Select an event to register for..." if select_options else "No events currently active"
        select_menu = discord.ui.Select(
            placeholder=placeholder,
            options=select_options,
            custom_id="persistent_event_selection", # Ensure custom_id is set for persistence
            disabled=not select_options,
            row=0
        )
        select_menu.callback = self.select_callback # Assign the callback
        self.add_item(select_menu)

        manage_button = discord.ui.Button(
            label="Manage My Registrations",
            emoji=config.EMOJI_MANAGE,
            style=discord.ButtonStyle.secondary,
            custom_id="manage_my_registrations", # Ensure custom_id is set for persistence
            row=1
        )
        manage_button.callback = self.manage_registrations_button_callback # Assign the callback
        self.add_item(manage_button)

    async def select_callback(self, interaction: discord.Interaction):
        # Do NOT defer here. The next step (sending RegistrationTypeView) is a direct response.
        # Deferring here would prevent the next send_message.

        if not interaction.data or 'values' not in interaction.data or not interaction.data['values']:
            await interaction.response.send_message(f"{config.EMOJI_ERROR} Invalid selection data received.", ephemeral=True)
            return

        try:
            chosen_event = interaction.data['values'][0]
        except (KeyError, IndexError):
            await interaction.response.send_message(f"{config.EMOJI_ERROR} Error processing event selection.", ephemeral=True)
            return

        user_id = interaction.user.id
        bot_log.info(f"Event '{chosen_event}' selected by {interaction.user.name} ({user_id}) via persistent view.")

        # Access active_events from the bot instance, not self.active_events, for consistency
        # state.active_events was used in the original code, let's stick to that if it's a global state
        # If active_events is stored on the bot instance (as I implemented), use interaction.client.active_events
        # Let's assume active_events is a bot attribute based on recent changes
        if chosen_event not in interaction.client.active_events:
            await interaction.response.send_message(f"{config.EMOJI_WARNING} The event '{chosen_event}' is no longer active or available for registration.", ephemeral=True)
            return

        bot_log.info(f"Offering Self/Other choice for {chosen_event} to user {user_id}.")
        # RegistrationTypeView allows self registration (True) for persistent message flow
        view = RegistrationTypeView(event=chosen_event, allow_self=True)
        # Send the next view as a new ephemeral message (initial response)
        msg = await interaction.response.send_message(f"Registering for **{chosen_event}**. Are you registering yourself or someone else?", view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response() # Store the message for timeout editing
        except discord.HTTPException:
            bot_log.warning("Failed to get original_response for RegistrationTypeView message.")


    async def manage_registrations_button_callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        user_name = interaction.user.name
        bot_log.info(f"'Manage My Registrations' clicked by {user_name} ({user_id})")
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Use the database function to get user registrations
        # Assuming database.get_user_registrations exists and returns a list of registrations
        user_regs = database.get_user_registrations(user_id)

        if not user_regs:
            await interaction.followup.send(f"{config.EMOJI_INFO} You haven't submitted any registrations using this bot.", ephemeral=True)
            return

        embed = discord.Embed(title=f"{config.EMOJI_MANAGE} Your Submitted Registrations", color=config.COLOR_MANAGE)
        desc = f"{config.EMOJI_INFO} Below are the registrations you submitted. Click a button to cancel one.\n\n"
        reg_details_for_view = []

        if len(user_regs) > 15:
            desc += f"{config.EMOJI_WARNING} Displaying first 15 registrations.\n\n"

        # Assuming user_regs is a list of dictionaries or tuples that can be unpacked
        # Adjust indexing/key access based on the actual output of database.get_user_registrations
        for i, reg in enumerate(user_regs[:15]):
             # Attempt to handle both dict and tuple formats from database.get_user_registrations
             if isinstance(reg, dict):
                 event, slot, is_sub, fc_lvl, name, fid, fc_disp = reg.get('event'), reg.get('time_slot'), reg.get('substitute'), reg.get('furnace_level'), reg.get('chief_name'), reg.get('player_fid'), reg.get('verified_fc_display')
             elif isinstance(reg, (list, tuple)) and len(reg) >= 7: # Assuming order from original DB query
                  event, slot, is_sub, fc_lvl, name, fid, fc_disp = reg[0], reg[1], reg[2], reg[3], reg[4], reg[5], reg[6]
             else:
                  bot_log.warning(f"Unexpected registration data format in ManageRegistrationsView: {reg}")
                  continue # Skip malformed entry


             sub_text = f"{config.EMOJI_SUB} Substitute" if is_sub else "Main Roster"
             # Use the get_display_level function (from utils or placeholder)
             fc_text_val = fc_disp or get_display_level(fc_lvl + 30 if fc_lvl else None) or '?'
             fc_text = f"({config.EMOJI_LEVEL} {fc_text_val})"
             desc += f"- {config.EMOJI_PERSON} **{name}** for {config.EMOJI_EVENT} **{event}** at {config.EMOJI_SLOT} **{slot}** {fc_text} ({sub_text})\n"
             # Pass data needed for cancellation button callback
             reg_details_for_view.append({"event": event, "slot": slot, "is_sub": is_sub, "chief_name": name})


        embed.description = desc.strip()
        # Pass registrations data needed by the ManageRegistrationsView to create buttons
        view = ManageRegistrationsView(submitter_user_id=user_id, registrations=reg_details_for_view)

        # Send the message using followup after deferring
        msg = await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        view.message = msg # Store the message object in the view


    async def on_timeout(self):
        # This should ideally not be hit for persistent views (timeout=None).
        # If it is, it means the bot stopped/reloaded and the view wasn't correctly re-added.
        # The on_ready logic in bot.py is responsible for re-adding the view.
        # The original code's attempt to rebuild and edit here might be problematic if self.bot is None or state is inconsistent.
        # Let's keep the log but remove the complex recovery logic as it's better handled in on_ready.
        bot_log.info(f"EventSelectionView timed out (this should not happen for persistent views).")
        self.stop() # Just stop the view instance


# --- Captain Selection Views (Keeping from previous code as they seem unrelated to user reg flow) ---

class SelectEventSlotForCaptainView(discord.ui.View):
    def __init__(self, interaction_user_id, active_events):
        super().__init__(timeout=180)
        self.interaction_user_id = interaction_user_id
        self.message = None

        options = []
        time_slots = ["14UTC", "19UTC"]
        for event in active_events:
            for slot in time_slots:
                options.append(discord.SelectOption(label=f"{event} {slot}", value=f"{event}_{slot}"))

        if options:
            select = discord.ui.Select(placeholder="1. Select Event & Time Slot...", options=options, custom_id="sc_select_event_slot")
            select.callback = self.select_event_slot_callback
            self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction_user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    async def select_event_slot_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_value = interaction.data['values'][0]
        self.selected_event, self.selected_slot = selected_value.split("_")
        bot_log.info(f"Captain Step 1: User {interaction.user.name} selected {self.selected_event} {self.selected_slot}")

        for item in self.children: item.disabled = True
        try:
            # Use the stored message object or edit original response if no message stored
            if self.message: await self.message.edit(content="Step 1 Complete. Proceeding...", view=self)
            else: await interaction.edit_original_response(content="Step 1 Complete. Proceeding...", view=self)
        except discord.HTTPException: pass


        next_view = None
        if self.selected_event == "Foundry":
            next_view = SelectFoundryTeamView(self.interaction_user_id, self.selected_event, self.selected_slot)
            followup_msg = "2. Select Foundry Team:"
        elif self.selected_event == "Canyon":
            next_view = SelectCanyonTeamView(self.interaction_user_id, self.selected_event, self.selected_slot)
            followup_msg = "2. Select Canyon Team:"
        else:
            await interaction.followup.send(f"{config.EMOJI_ERROR} Team selection is not applicable for event type '{self.selected_event}'.", ephemeral=True)
            self.stop()
            return

        msg = await interaction.followup.send(followup_msg, view=next_view, ephemeral=True)
        next_view.message = msg # Store the message for timeout editing

        self.stop()

    async def on_timeout(self):
        bot_log.info("SelectEventSlotForCaptainView timed out.")
        for item in self.children: item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message: await self.message.edit(content=f"{config.EMOJI_WARNING} Captain selection timed out (Step 1).", view=self)
            else: bot_log.warning("No message to edit on SelectEventSlotForCaptainView timeout.")
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


class SelectFoundryTeamView(discord.ui.View):
    def __init__(self, interaction_user_id, event_name, time_slot):
        super().__init__(timeout=180)
        self.interaction_user_id = interaction_user_id
        self.event_name = event_name
        self.time_slot = time_slot
        self.message = None

        options = [discord.SelectOption(label=f"Team {team}", value=team) for team in config.FOUNDRY_TEAMS]
        select = discord.ui.Select(placeholder="Select Foundry Team (A1/A2/D1/D2)...", options=options, custom_id="sc_select_foundry_team")
        select.callback = self.select_team_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction_user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    async def select_team_callback(self, interaction: discord.Interaction):
       await interaction.response.defer(ephemeral=True)
       self.selected_team = interaction.data['values'][0]
       bot_log.info(f"Captain Step 2 (Foundry): User {interaction.user.name} selected Team {self.selected_team} for {self.event_name} {self.time_slot}")

       for item in self.children: item.disabled = True
       try:
           # Use the stored message object or edit original response
           if self.message: await self.message.edit(content="Step 2 Complete. Loading members...", view=self)
           else: await interaction.edit_original_response(content="Step 2 Complete. Loading members...", view=self)
       except discord.HTTPException: pass

       next_view = SelectMemberForCaptainView(self.interaction_user_id, self.event_name, self.time_slot, self.selected_team)
       # Await the populate_members call before checking options
       await next_view.populate_members()

       if not next_view.has_options():
           await interaction.followup.send(f"{config.EMOJI_INFO} No players found assigned to Team {self.selected_team} in {self.event_name} {self.time_slot}. Cannot assign captain.", ephemeral=True)
       else:
           msg = await interaction.followup.send(f"3. Select Member for Team {self.selected_team}:", view=next_view, ephemeral=True)
           next_view.message = msg # Store the message for timeout editing

       self.stop()

    async def on_timeout(self):
        bot_log.info("SelectFoundryTeamView timed out.")
        for item in self.children: item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message: await self.message.edit(content=f"{config.EMOJI_WARNING} Captain selection timed out (Step 2).", view=self)
            else: bot_log.warning("No message to edit on SelectFoundryTeamView timeout.")
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


class SelectCanyonTeamView(discord.ui.View):
    def __init__(self, interaction_user_id, event_name, time_slot):
        super().__init__(timeout=180)
        self.interaction_user_id = interaction_user_id
        self.event_name = event_name
        self.time_slot = time_slot
        self.message = None

        options = [discord.SelectOption(label=f"Team {team}", value=team) for team in config.CANYON_TEAMS]
        select = discord.ui.Select(placeholder="Select Canyon Team (G/B/R)...", options=options, custom_id="sc_select_canyon_team")
        select.callback = self.select_team_callback
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction_user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    async def select_team_callback(self, interaction: discord.Interaction):
       await interaction.response.defer(ephemeral=True)
       self.selected_team = interaction.data['values'][0]
       bot_log.info(f"Captain Step 2 (Canyon): User {interaction.user.name} selected Team {self.selected_team} for {self.event_name} {self.time_slot}")

       for item in self.children: item.disabled = True
       try:
           # Use the stored message object or edit original response
           if self.message: await self.message.edit(content="Step 2 Complete. Loading members...", view=self)
           else: await interaction.edit_original_response(content="Step 2 Complete. Loading members...", view=self)
       except discord.HTTPException: pass


       next_view = SelectMemberForCaptainView(self.interaction_user_id, self.event_name, self.time_slot, self.selected_team)
       # Await the populate_members call before checking options
       await next_view.populate_members()

       if not next_view.has_options():
           await interaction.followup.send(f"{config.EMOJI_INFO} No players found assigned to Team {self.selected_team} in {self.event_name} {self.time_slot}. Cannot assign captain.", ephemeral=True)
       else:
           msg = await interaction.followup.send(f"3. Select Member for Team {self.selected_team}:", view=next_view, ephemeral=True)
           next_view.message = msg # Store the message for timeout editing

       self.stop()

    async def on_timeout(self):
        bot_log.info("SelectCanyonTeamView timed out.")
        for item in self.children: item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message: await self.message.edit(content=f"{config.EMOJI_WARNING} Captain selection timed out (Step 2).", view=self)
            else: bot_log.warning("No message to edit on SelectCanyonTeamView timeout.")
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


class SelectMemberForCaptainView(discord.ui.View):
    def __init__(self, interaction_user_id, event_name, time_slot, team):
        super().__init__(timeout=180)
        self.interaction_user_id = interaction_user_id
        self.event_name = event_name
        self.time_slot = time_slot
        self.team = team
        self.message = None
        self.registrants = []
        self._select_menu = None

    async def populate_members(self):
        # Use the database function to get players in the specific team
        self.registrants = database.get_players_in_team(self.event_name, self.time_slot, self.team)
        bot_log.debug(f"Fetched {len(self.registrants)} members for team {self.team} ({self.event_name} {self.time_slot})")

        options = []
        if not self.registrants:
            options.append(discord.SelectOption(label="No players found in this team", value="-1", emoji=config.EMOJI_WARNING))
        else:
            # Sort by captain status (captains first) then name
            self.registrants.sort(key=lambda x: (-x[1] if x[1] is not None else 0, x[0].lower())) # Handle potential None for is_captain

            for chief_name, is_captain in self.registrants[:25]: # Limit options to 25
                label_prefix = f"{config.EMOJI_CAPTAIN} " if is_captain == 1 else ""
                label_text = f"{label_prefix}{chief_name}"
                if len(label_text) > 100: label_text = label_text[:97] + "..." # Truncate label

                options.append(discord.SelectOption(
                    label=label_text,
                    value=chief_name, # Use chief_name as value
                    description="Current Team Captain" if is_captain == 1 else "Toggle Captain Status"
                ))

        # Remove the old select menu if it exists before adding the new one
        if self._select_menu and self._select_menu in self.children:
             self.remove_item(self._select_menu)

        self._select_menu = discord.ui.Select(
            placeholder="Select Member to make/remove Captain...",
            options=options,
            custom_id="sc_select_member", # Consistent custom_id
            disabled= not self.registrants or (len(self.registrants) == 1 and self.registrants[0][0] is None) # Disable if no players
        )
        self._select_menu.callback = self.select_member_callback # Assign the callback
        self.add_item(self._select_menu)

    def has_options(self) -> bool:
        # Check if the select menu exists and has valid options (not just the error option)
        if not self._select_menu or not self._select_menu.options:
            return False
        return any(opt.value != "-1" for opt in self._select_menu.options)


    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction_user_id:
            await interaction.response.send_message("This interaction is not for you.", ephemeral=True)
            return False
        return True

    async def select_member_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_chief_name = interaction.data['values'][0]

        if selected_chief_name == "-1":
             await interaction.followup.send("No action taken.", ephemeral=True)
             # Disable buttons after selection
             for item in self.children: item.disabled = True
             try:
                 # Use the stored message object to edit
                 if self.message: await self.message.edit(view=self)
                 else: await interaction.edit_original_response(view=self)
             except discord.HTTPException: pass
             self.stop()
             return

        bot_log.info(f"Captain Step 3: User {interaction.user.name} selected '{selected_chief_name}' for {self.event_name} {self.time_slot} Team {self.team}")

        # Call the teams module to handle the status toggle logic
        # Assuming teams.toggle_captain_status handles the DB update and sends user feedback
        await teams.toggle_captain_status(interaction, selected_chief_name, self.event_name, self.time_slot, self.team)

        # After the toggle attempt, update the view to reflect potential changes
        await self.populate_members() # Re-fetch and re-populate options
        try:
             # Edit the message to show the updated list with potential new captain
             if self.message: await self.message.edit(view=self)
             else: await interaction.edit_original_response(view=self)
        except discord.HTTPException: pass

        # The view remains active for further selections


    async def on_timeout(self):
        bot_log.info("SelectMemberForCaptainView timed out.")
        for item in self.children: item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message: await self.message.edit(content=f"{config.EMOJI_WARNING} Captain selection timed out (Step 3).", view=self)
            else: bot_log.warning("No message to edit on SelectMemberForCaptainView timeout.")
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()


class ConfirmPurgeView(View):
    def __init__(self, author_id):
        super().__init__(timeout=30.0)
        self.author_id = author_id
        self.confirmed = None
        self.interaction_response_message = None # To store the message this view is on

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
            return False
        return True

    async def disable_buttons(self):
        for item in self.children:
            if isinstance(item, Button):
                item.disabled = True
        # Use the stored message object to edit
        if self.interaction_response_message:
            try:
                await self.interaction_response_message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()

    @button(label="Confirm Purge", style=discord.ButtonStyle.danger, custom_id="confirm_purge_yes")
    async def confirm_button(self, interaction: discord.Interaction, button_obj: Button):
        self.confirmed = True
        await interaction.response.defer()
        await self.disable_buttons() # Disable buttons after confirmation

    @button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="confirm_purge_no")
    async def cancel_button(self, interaction: discord.Interaction, button_obj: Button):
        self.confirmed = False
        await interaction.response.defer()
        await self.disable_buttons() # Disable buttons after cancellation

    async def on_timeout(self):
        bot_log.info(f"Purge confirmation timed out for user {self.author_id}")
        # Use the stored message object to edit
        if self.interaction_response_message:
            try:
                await self.interaction_response_message.edit(content=f"{config.EMOJI_WARNING} Purge confirmation timed out. Purge cancelled.", view=None)
            except discord.HTTPException: pass
        self.stop()


class SelectEventSlotForAssignView(discord.ui.View):
    def __init__(self, interaction_user_id, active_events):
        super().__init__(timeout=180)
        self.interaction_user_id = interaction_user_id
        self.message = None

        time_slots = ["14UTC", "19UTC"]
        options = []
        for event in active_events:
            for slot in time_slots:
                options.append(discord.SelectOption(
                    label=f"{event} {slot}",
                    value=f"{event}_{slot}",
                    emoji=config.EMOJI_EVENT if event == "Foundry" else config.EMOJI_TEAM
                ))

        if options:
            select_menu = discord.ui.Select(
                placeholder="Select Event and Time Slot to Assign Teams...",
                options=options,
                custom_id="assign_select_slot"
            )
            select_menu.callback = self.assign_slot_callback
            self.add_item(select_menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.interaction_user_id:
            await interaction.response.send_message("This isn't for you!", ephemeral=True)
            return False
        return True

    async def assign_slot_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        selected_value = interaction.data['values'][0]
        event, slot = selected_value.split("_")

        bot_log.info(f"User {interaction.user.name} initiated auto-assignment for {event} {slot}")

        # Disable the select menu after selection
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True
        try:
            # Use the stored message object or edit original response
            if self.message: await self.message.edit(content=f"{config.EMOJI_WAIT} Assigning teams for {event} {slot}...", view=self)
            else: await interaction.edit_original_response(content=f"{config.EMOJI_WAIT} Assigning teams for {event} {slot}...", view=self)
        except discord.HTTPException as e:
            bot_log.warning(f"Failed to disable SelectEventSlotForAssignView select menu: {e}")


        # Call the team assignment logic from the teams module
        # Assuming teams._perform_assignment handles the assignment and sends user feedback
        await teams._perform_assignment(interaction, event, slot)
        self.stop()

    async def on_timeout(self):
        bot_log.info(f"SelectEventSlotForAssignView timed out for user {self.interaction_user_id}.")
        for item in self.children: item.disabled = True
        try:
            # Use the stored message object to edit
            if self.message:
                 await self.message.edit(content=f"{config.EMOJI_WARNING} Team assignment selection timed out.", view=self)
            else:
                 bot_log.warning("No message to edit on SelectEventSlotForAssignView timeout.")
        except discord.HTTPException: pass
        except AttributeError: pass
        self.stop()
