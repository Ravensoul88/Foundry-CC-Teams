# utils.py

import logging
# Configure logging for this module
bot_log = logging.getLogger('registration_bot')

def get_display_level(fc_level: int | None) -> str:
    """
    Formats the raw FC level into a display string (e.g., "FC7").
    Assumes fc_level is an integer between 1 and 10 (inclusive), or None.
    Returns '?' for None or unexpected values.
    """
    if fc_level is None:
        return '?'
    try:
        # Ensure the level is within the expected range if it's an integer
        if isinstance(fc_level, int) and 1 <= fc_level <= 10:
             return f"FC{fc_level}"
        else:
             bot_log.warning(f"Unexpected raw FC level received in get_display_level (not 1-10 int): {fc_level}")
             # Return a string representation for unexpected types/values, or '?'
             return str(fc_level) if fc_level is not None else '?'
    except TypeError:
         bot_log.warning(f"Non-integer FC level received in get_display_level: {fc_level}")
         return '?' # Return '?' for unexpected types

# Add any other general utility functions here as needed
# def another_utility_function(...):
#    pass

