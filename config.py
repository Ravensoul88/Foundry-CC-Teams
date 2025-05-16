import os
from dotenv import load_dotenv
import discord

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID')) if os.getenv('ADMIN_ROLE_ID') else None
GUILD_ID = int(os.getenv('GUILD_ID')) if os.getenv('GUILD_ID') else None
API_SECRET = os.getenv('API_SECRET')

API_PLAYER_URL = 'https://wos-giftcode-api.centurygame.com/api/player'
DB_MAIN_FILE = "registrations.db"
FID_LOOKUP_CSV = "alliance_lookup.csv"
PERSISTENCE_FILE = "registration_message.txt"

FUZZY_MATCH_THRESHOLD = 50
FUZZY_MATCH_LIMIT = 5
DEFAULT_ACTIVE_EVENTS = ["Foundry", "Canyon"]

EMOJI_SUCCESS = "✅"; EMOJI_ERROR = "❌"; EMOJI_WARNING = "⚠️"; EMOJI_INFO = "ℹ️"
EMOJI_EVENT = "🗓️"; EMOJI_SLOT = "⏰"; EMOJI_PERSON = "👤"; EMOJI_LEVEL = "🔢"
EMOJI_SUB = "🔄"; EMOJI_CANCEL = "🗑️"; EMOJI_MANAGE = "⚙️"; EMOJI_EXPORT = "📤"
EMOJI_SETUP = "🛠️"; EMOJI_CLEAR = "🧹"; EMOJI_PURGE = "🔥"; EMOJI_WAIT = "⏳"
EMOJI_VERIFY = "🔍"; EMOJI_TEAM = "⚔️"; EMOJI_CAPTAIN = "👑"
EMOJI_REFRESH = "🔃"; EMOJI_ADD = "➕"; EMOJI_DELETE = "➖"
EMOJI_DB = "💾"; EMOJI_HELP = "❓"; EMOJI_FUEL = "⛽"

FOUNDRY_TEAMS = ["A1", "A2", "D1", "D2"]
CANYON_TEAMS = ["G", "B", "R"]

COLOR_DEFAULT = discord.Color.teal(); COLOR_SUCCESS = discord.Color.green()
COLOR_ERROR = discord.Color.red(); COLOR_WARNING = discord.Color.orange()
COLOR_INFO = discord.Color.blue(); COLOR_MANAGE = discord.Color.blurple()

level_mapping = {
    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4", 35: "FC 1", 36: "FC 1 - 1",
    37: "FC 1 - 2", 38: "FC 1 - 3", 39: "FC 1 - 4", 40: "FC 2", 41: "FC 2 - 1",
    42: "FC 2 - 2", 43: "FC 2 - 3", 44: "FC 2 - 4", 45: "FC 3", 46: "FC 3 - 1",
    47: "FC 3 - 2", 48: "FC 3 - 3", 49: "FC 3 - 4", 50: "FC 4", 51: "FC 4 - 1",
    52: "FC 4 - 2", 53: "FC 4 - 3", 54: "FC 4 - 4", 55: "FC 5", 56: "FC 5 - 1",
    57: "FC 5 - 2", 58: "FC 5 - 3", 59: "FC 5 - 4", 60: "FC 6", 61: "FC 6 - 1",
    62: "FC 6 - 2", 63: "FC 6 - 3", 64: "FC 6 - 4", 65: "FC 7", 66: "FC 7 - 1",
    67: "FC 7 - 2", 68: "FC 7 - 3", 69: "FC 7 - 4", 70: "FC 8", 71: "FC 8 - 1",
    72: "FC 8 - 2", 73: "FC 8 - 3", 74: "FC 8 - 4", 75: "FC 9", 76: "FC 9 - 1",
    77: "FC 9 - 2", 78: "FC 9 - 3", 79: "FC 9 - 4", 80: "FC 10", 81: "FC 10 - 1",
    82: "FC 10 - 2", 83: "FC 10 - 3", 84: "FC 10 - 4"
}
