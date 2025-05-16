import pandas as pd
from thefuzz import process
import logging
import os

bot_log = logging.getLogger('registration_bot')

LOOKUP_FILE = 'alliance_lookup.csv'
lookup_data = pd.DataFrame(columns=['Chief Name', 'FID'])

def load_lookup_data():
    global lookup_data
    if os.path.exists(LOOKUP_FILE):
        try:
            lookup_data = pd.read_csv(LOOKUP_FILE)
            bot_log.info(f"Successfully loaded {len(lookup_data)} entries from {LOOKUP_FILE}")
            return len(lookup_data)
        except Exception as e:
            bot_log.error(f"Error loading lookup data from {LOOKUP_FILE}: {e}")
            lookup_data = pd.DataFrame(columns=['Chief Name', 'FID']) # Reset on error
            return 0
    else:
        bot_log.warning(f"{LOOKUP_FILE} not found. Starting with empty lookup data.")
        lookup_data = pd.DataFrame(columns=['Chief Name', 'FID'])
        return 0

def save_lookup_data():
    global lookup_data
    try:
        lookup_data.to_csv(LOOKUP_FILE, index=False)
        bot_log.info(f"Successfully saved {len(lookup_data)} entries to {LOOKUP_FILE}")
    except Exception as e:
        bot_log.error(f"Error saving lookup data to {LOOKUP_FILE}: {e}")

def add_lookup_entry(chief_name, fid):
    global lookup_data
    chief_name = str(chief_name).strip()
    fid = str(fid).strip()

    if chief_name in lookup_data['Chief Name'].values or fid in lookup_data['FID'].values:
        bot_log.warning(f"Lookup entry for Chief Name '{chief_name}' or FID '{fid}' already exists.")
        return False

    new_entry = pd.DataFrame([{'Chief Name': chief_name, 'FID': fid}])
    lookup_data = pd.concat([lookup_data, new_entry], ignore_index=True)
    save_lookup_data()
    bot_log.info(f"Added new lookup entry: '{chief_name}' -> '{fid}'")
    return True

def find_lookup_entry(chief_name, limit=5):
    global lookup_data
    chief_name = str(chief_name).strip()

    if lookup_data.empty:
        bot_log.info("Lookup data is empty. No search possible.")
        return []

    # Use thefuzz to find close matches in Chief Name
    matches = process.extract(chief_name, lookup_data['Chief Name'].tolist(), limit=limit)

    results = []
    for match, score in matches:
        # Find the FID for the matched Chief Name
        fid = lookup_data[lookup_data['Chief Name'] == match]['FID'].iloc[0]
        results.append(((match, fid), score))

    bot_log.info(f"Found {len(results)} potential lookup matches for '{chief_name}'")
    return results

def get_formatted_lookup_data():
    global lookup_data
    if lookup_data.empty:
        return "No lookup data available."

    return lookup_data.to_string(index=False)

# Load data on import
load_lookup_data()
