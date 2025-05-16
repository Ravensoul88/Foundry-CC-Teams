import discord
import database
import state
import lookup
import logging
import config
import re
import asyncio
import aiohttp
import sqlite3
import hashlib
import time
from thefuzz import fuzz, process
import datetime
import ui_components

bot_log = logging.getLogger('registration_bot')

def get_display_level(numerical_level: int | None) -> str:
    if numerical_level is None:
        return "Level ?"
    if numerical_level > 30:
        display_name = config.level_mapping.get(numerical_level)
        return display_name if display_name else f"API Lvl {numerical_level}"
    else:
        return f"Level {numerical_level}"

async def call_player_api(session: aiohttp.ClientSession, fid: int) -> dict | None:
    if not config.API_SECRET:
         bot_log.error("API_SECRET is not configured. Cannot call player API.")
         return {"error": "config_error", "msg": "API Secret not set."}
    if not fid or fid <= 0:
         bot_log.warning(f"Invalid FID provided to API call: {fid}")
         return {"error": "invalid_input", "msg": "Invalid FID."}

    try:
        current_time_ms = int(time.time() * 1000)
        form_part = f"fid={fid}&time={current_time_ms}"
        string_to_hash = form_part + config.API_SECRET
        sign = hashlib.md5(string_to_hash.encode('utf-8')).hexdigest()
        form_payload = f"sign={sign}&{form_part}"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}

        async with session.post(config.API_PLAYER_URL, headers=headers, data=form_payload, timeout=15) as response:
            response_text = await response.text()
            bot_log.debug(f"API Call: FID={fid}, Status={response.status}, Response='{response_text[:200]}...'")

            if response.status == 200:
                try:
                    api_response = await response.json(content_type=None)
                except aiohttp.ContentTypeError:
                     bot_log.error(f"API call for FID {fid} returned non-JSON response (Status 200): {response_text[:500]}")
                     return {"error": "invalid_response", "msg": "Non-JSON response from API"}
                except ValueError:
                     bot_log.error(f"API call for FID {fid} returned invalid JSON (Status 200): {response_text[:500]}")
                     return {"error": "invalid_json", "msg": "Invalid JSON response"}

                if api_response is None:
                     bot_log.error(f"API call for FID {fid} decoded to None (Status 200): {response_text[:500]}")
                     return {"error": "empty_json", "msg": "Empty JSON response"}

                if api_response.get("code") == 0 and "data" in api_response:
                    bot_log.debug(f"API Success for FID {fid}")
                    return api_response["data"]
                else:
                    api_msg = api_response.get('msg', 'Unknown API Logic Error')
                    api_code = api_response.get('code', 'N/A')
                    bot_log.warning(f"API Call for FID {fid} failed logically: Code={api_code}, Msg='{api_msg}'")
                    return {"error": "api_logic_fail", "msg": api_msg, "code": api_code}
            elif response.status == 429:
                bot_log.warning(f"API Call for FID {fid} hit rate limit (429).")
                return {"error": "rate_limit", "status": 429}
            else:
                bot_log.warning(f"API Call for FID {fid} failed with HTTP Status {response.status}. Response: {response_text[:500]}")
                return {"error": "http_error", "status": response.status, "msg": f"HTTP {response.status}"}

    except asyncio.TimeoutError:
        bot_log.error(f"API call for FID {fid} timed out.")
        return {"error": "timeout", "msg": "Request timed out"}
    except aiohttp.ClientError as e:
        bot_log.error(f"AIOHTTP ClientError for FID {fid}: {e}", exc_info=True)
        return {"error": "client_error", "msg": str(e)}
    except Exception as e:
        bot_log.error(f"Generic error calling API for FID {fid}: {e}", exc_info=True)
        return {"error": "unknown", "msg": str(e)}


async def _process_registration(
    bot: discord.Client,
    interaction: discord.Interaction,
    event: str,
    time_slot: str,
    is_substitute: bool,
    chief_name_input: str,
    entered_fc_level: int | None,
    registration_target: str,
    confirmed_player_fid: int | None = None,
    existing_api_data: dict | None = None
):
    submitter_user_id = interaction.user.id
    submitter_user_name = interaction.user.name
    is_self_reg = registration_target == 'self'
    sub_db_value = 1 if is_substitute else 0
    is_self_int = 1 if is_self_reg else 0

    bot_log.info(f"--- Processing Registration ---")
    bot_log.info(f"   Submitter: {submitter_user_name}({submitter_user_id}) | Target: {registration_target}")
    bot_log.info(f"   Event: {event} | Slot: {time_slot} | Sub: {is_substitute}")
    bot_log.info(f"   Input Name: '{chief_name_input}' | Input FC: {entered_fc_level}")
    bot_log.info(f"   Confirmed FID: {confirmed_player_fid} | Existing API Data: {'Yes' if existing_api_data else 'No'}")


    chief_name_to_save = chief_name_input
    player_fid = confirmed_player_fid
    api_data = existing_api_data
    api_status_msg = ""
    avatar_image = None
    kingdom_id = None
    verified_fc_level = None
    verified_fc_display = None

    if player_fid and not api_data:
        bot_log.info(f"   Calling API for FID {player_fid}...")
        api_result = await call_player_api(bot.api_session, player_fid)
        if api_result and not api_result.get("error"):
            api_data = api_result
            bot_log.info(f"   API call successful for FID {player_fid}.")
        else:
            error_detail = api_result.get("msg", api_result.get("error", "Unknown Error")) if api_result else "No response"
            api_status_msg = f"{config.EMOJI_ERROR} Could not verify via API ({error_detail}). Using entered FC level if available."
            bot_log.warning(f"   API Call failed for FID {player_fid}: {error_detail}")

    if api_data:
        api_nickname = api_data.get("nickname")
        verified_fc_level = api_data.get("stove_lv")
        verified_fc_display = get_display_level(verified_fc_level)
        kingdom_id = api_data.get("kid")
        avatar_image = api_data.get("avatar_image")
        bot_log.info(f"   API Data Used: Nick='{api_nickname}', FC={verified_fc_level}({verified_fc_display}), KID={kingdom_id}")

        if api_nickname:
            if api_nickname.lower() != chief_name_to_save.lower():
                bot_log.warning(f"   Nickname mismatch: Input='{chief_name_to_save}', API='{api_nickname}'. Using API name.")
            chief_name_to_save = api_nickname
        else:
            bot_log.warning(f"   API did not return nickname for FID {player_fid}, using input '{chief_name_to_save}'")

        if verified_fc_level is not None and entered_fc_level is not None and verified_fc_level != entered_fc_level + 30:
            input_fc_display = get_display_level(entered_fc_level + 30 if entered_fc_level else None)
            status_detail = f"{config.EMOJI_VERIFY} Verified FC `{verified_fc_display}` differs from input `{input_fc_display}`. Using verified level."
            bot_log.warning(f"   FC Level mismatch: Input translated={input_fc_display}, API={verified_fc_display}. Using API.")
        elif verified_fc_level is not None:
            status_detail = f"{config.EMOJI_VERIFY} Verified FC level: `{verified_fc_display}`."
        elif entered_fc_level is not None :
             status_detail = f"{config.EMOJI_WARNING} Verified FC level not available from API. Using entered level: `{get_display_level(entered_fc_level + 30)}`."
        else:
            status_detail = f"{config.EMOJI_WARNING} No FC level provided or verified."

        api_status_msg = (api_status_msg + "\n" + status_detail) if api_status_msg else status_detail

    elif player_fid:
         verified_fc_level = None
         verified_fc_display = get_display_level(entered_fc_level + 30 if entered_fc_level is not None else None) if entered_fc_level is not None else "Level ?"
         bot_log.info(f"   Using entered FC level '{entered_fc_level}' -> display '{verified_fc_display}' due to API failure.")
    else:
        api_status_msg = f"{config.EMOJI_WARNING} Name not confirmed via lookup; verification skipped."
        verified_fc_level = None
        verified_fc_display = get_display_level(entered_fc_level + 30 if entered_fc_level is not None else None) if entered_fc_level is not None else "Level ?"
        bot_log.info(f"   Using entered FC level '{entered_fc_level}' -> display '{verified_fc_display}' as no FID provided.")

    if is_self_reg and player_fid is not None:
        # Check if this FID is already registered for this event (Self-reg specific check)
        existing_reg = database.get_registration_by_fid_event(player_fid, event) # ADD THIS FUNCTION TO database.py
        if existing_reg:
             existing_name = existing_reg.get('chief_name', 'Unknown')
             bot_log.warning(f"   Self-registration attempt blocked for FID {player_fid} ({existing_name}), Event {event} - already registered.")
             await interaction.followup.send(f"{config.EMOJI_WARNING} You (FID: `{player_fid}`, Name: `{existing_name}`) are already registered for **{event}**. Use '{config.EMOJI_MANAGE} Manage My Registrations' to make changes.", ephemeral=True)
             return

    db_fc_level_to_save = verified_fc_level if verified_fc_level is not None else (entered_fc_level + 30 if entered_fc_level is not None else None)
    db_fc_display_to_save = verified_fc_display

    bot_log.info(f"   Calling database.register_player for '{chief_name_to_save}' (Event: {event})...")
    success = database.register_player(
        user_id=submitter_user_id,
        user_name=submitter_user_name,
        chief_name=chief_name_to_save,
        entered_fc_level=entered_fc_level,
        event=event,
        substitute=sub_db_value,
        time_slot=time_slot,
        is_self_registration=is_self_int,
        player_fid=player_fid,
        kingdom_id=kingdom_id,
        verified_fc_level=db_fc_level_to_save,
        verified_fc_display=db_fc_display_to_save
    )

    if not success:
         error_msg = f"{config.EMOJI_ERROR} Database error saving registration. Please try again or contact an admin."
         if interaction.response.is_done(): await interaction.followup.send(error_msg, ephemeral=True)
         else: await interaction.response.send_message(error_msg, ephemeral=True)
         return

    bot_log.info(f"   Database registration successful for '{chief_name_to_save}'.")

    if is_self_reg and player_fid is not None:
        bot_log.info(f"   Calling database.link_discord_fid for {submitter_user_id} -> {player_fid}...")
        link_added = database.link_discord_fid(submitter_user_id, player_fid)
        if link_added:
            bot_log.info(f"   Stored new Discord link: {submitter_user_id} -> FID {player_fid}")
        else:
             existing_link_discord_id = database.get_linked_discord_user(player_fid)
             if existing_link_discord_id and existing_link_discord_id != submitter_user_id:
                 bot_log.warning(f"   FID {player_fid} is already linked to Discord ID {existing_link_discord_id}. Cannot link to {submitter_user_id}.")
                 api_status_msg += f"\n{config.EMOJI_WARNING} Note: This game FID (`{player_fid}`) is already linked to another Discord user."
             else:
                 bot_log.info(f"   Confirmed existing Discord link: {submitter_user_id} -> FID {player_fid}")


    await state.recalculate_all_counters(bot)
    asyncio.create_task(state.update_registration_embed(bot))

    sub_text = f" ({config.EMOJI_SUB} Sub)" if is_substitute else ""
    title = f"{config.EMOJI_SUCCESS} Registration Confirmed"
    desc = f"{config.EMOJI_PERSON} **{chief_name_to_save}**\n" \
           f"{config.EMOJI_EVENT} **{event}**\n" \
           f"{config.EMOJI_SLOT} **{time_slot}{sub_text}**\n" \
           f"{config.EMOJI_LEVEL} FC: `{db_fc_display_to_save}`"

    if api_status_msg:
        desc += f"\n\n{api_status_msg.strip()}"

    confirm_embed = discord.Embed(title=title, description=desc, color=config.COLOR_SUCCESS)
    if avatar_image and isinstance(avatar_image, str) and avatar_image.startswith("http"):
        confirm_embed.set_thumbnail(url=avatar_image)

    view = ui_components.CancelRegistrationButton(submitter_user_id=submitter_user_id, event=event, chief_name=chief_name_to_save)

    if interaction.response.is_done():
        await interaction.edit_original_response(content=None, embed=confirm_embed, view=view)
    else:
         await interaction.response.send_message(embed=confirm_embed, ephemeral=True, view=view)

    bot_log.info(f"   Confirmation sent for '{chief_name_to_save}'.")
    bot_log.info(f"--- Registration Process End ---")


async def cancel_registration_logic(interaction: discord.Interaction, button: discord.ui.Button | None, chief_name: str, event: str):
    bot_log.info(f"   Executing cancellation logic: Chief='{chief_name}', Event='{event}'")

    # Fetch registration info before attempting delete to get details for state update
    reg_info = database.get_registration_by_chief_name_event_slot(chief_name, event, "AnySlotPlaceholder") # Need to fix database function or logic if slot isn't needed for unique key
    if not reg_info:
         bot_log.warning(f"   Registration not found for Chief='{chief_name}', Event='{event}'. Already cancelled?")
         if button and button.view:
              button.disabled = True
              button.label = f"{config.EMOJI_INFO} Already Canceled?"
              if interaction.message: await interaction.edit_original_response(view=button.view)
         await interaction.followup.send(f"{config.EMOJI_INFO} Registration for {config.EMOJI_PERSON} **{chief_name}** seems to have already been canceled or did not exist.", ephemeral=True)
         return

    was_substitute = reg_info.get('substitute', -1)
    time_slot = reg_info.get('time_slot')

    bot_log.info(f"   Found registration. Sub: {was_substitute}, Slot: {time_slot}")
    bot_log.info(f"   Calling database.unregister_player...")
    success = database.unregister_player(chief_name, event)

    if success:
        bot_log.info(f"   Database unregistration successful for '{chief_name}' for event '{event}'.")
        await state.recalculate_all_counters(interaction.client)
        asyncio.create_task(state.update_registration_embed(interaction.client))

        if button and button.view:
            button.disabled = True
            button.label = f"{config.EMOJI_SUCCESS} Canceled"
            if interaction.message:
                 await interaction.edit_original_response(view=button.view)

        await interaction.followup.send(f"{config.EMOJI_SUCCESS} Unregistered {config.EMOJI_PERSON} **{chief_name}** from {config.EMOJI_EVENT} **{event}**.", ephemeral=True)
        bot_log.info(f"   Cancellation confirmation sent.")

    else:
        bot_log.error(f"   Database unregistration failed for '{chief_name}' from '{event}'.")
        if button and button.view:
             button.disabled = True
             button.label = f"{config.EMOJI_ERROR} Error Canceling"
             if interaction.message: await interaction.edit_original_response(view=button.view)
        await interaction.followup.send(f"{config.EMOJI_ERROR} Failed to cancel registration for {config.EMOJI_PERSON} **{chief_name}**. Please try again or contact an admin.", ephemeral=True)

