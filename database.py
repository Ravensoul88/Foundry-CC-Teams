import sqlite3
import config
import logging
import os

bot_log = logging.getLogger('registration_bot')

def initialize_databases():
    bot_log.info(f"Initializing database: {config.DB_MAIN_FILE}...")
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()

            c.execute("""CREATE TABLE IF NOT EXISTS registrations (
                            user_id INTEGER,
                            user_name TEXT,
                            chief_name TEXT NOT NULL COLLATE NOCASE,
                            furnace_level INTEGER,
                            event TEXT NOT NULL,
                            substitute INTEGER DEFAULT 0,
                            time_slot TEXT,
                            date TEXT,
                            is_self_registration INTEGER NOT NULL,
                            player_fid INTEGER,
                            kingdom_id INTEGER,
                            verified_fc_level INTEGER,
                            verified_fc_display TEXT,
                            is_captain INTEGER DEFAULT 0,
                            team_assignment TEXT,
                            PRIMARY KEY (chief_name, event) ON CONFLICT REPLACE
                            )""")
            bot_log.info("Checked/Created 'registrations' table.")

            existing_columns = [info[1] for info in c.execute("PRAGMA table_info(registrations)").fetchall()]
            cols_to_add = {
                'player_fid': 'INTEGER',
                'kingdom_id': 'INTEGER',
                'verified_fc_level': 'INTEGER',
                'verified_fc_display': 'TEXT',
                'is_captain': 'INTEGER DEFAULT 0',
                'team_assignment': 'TEXT'
            }
            for col, col_type in cols_to_add.items():
                if col not in existing_columns:
                    try:
                        c.execute(f"ALTER TABLE registrations ADD COLUMN {col} {col_type}")
                        bot_log.info(f"Added column '{col}' to registrations table.")
                    except sqlite3.OperationalError as e:
                        if "duplicate column name" in str(e).lower():
                            bot_log.warning(f"Column '{col}' likely already existed despite PRAGMA check.")
                        else:
                            raise e

            c.execute("""CREATE TABLE IF NOT EXISTS discord_links (
                            discord_id INTEGER PRIMARY KEY,
                            player_fid INTEGER NOT NULL UNIQUE
                            )""")
            bot_log.info("Checked/Created 'discord_links' table.")

            c.execute("""CREATE TABLE IF NOT EXISTS player_roles (
                            player_fid INTEGER PRIMARY KEY,
                            is_fuel_manager INTEGER DEFAULT 0
                            )""")
            bot_log.info("Checked/Created 'player_roles' table.")


            c.execute("CREATE INDEX IF NOT EXISTS idx_regs_event_slot ON registrations (event, time_slot);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_regs_user ON registrations (user_id);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_regs_fid_event ON registrations (player_fid, event);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_links_fid ON discord_links (player_fid);")
            c.execute("CREATE INDEX IF NOT EXISTS idx_roles_fuel ON player_roles (is_fuel_manager);")
            bot_log.info("Checked/Created DB indices.")

            conn.commit()
            bot_log.info(f"Database initialization complete for {config.DB_MAIN_FILE}")
    except sqlite3.Error as e:
        bot_log.critical(f"FATAL: Failed to initialize database {config.DB_MAIN_FILE}: {e}", exc_info=True)
        raise
    except Exception as e:
         bot_log.critical(f"FATAL: Unexpected error during database initialization: {e}", exc_info=True)
         raise

def register_player(user_id: int, user_name: str, chief_name: str, entered_fc_level: int | None, event: str, substitute: int, time_slot: str, is_self_registration: int, player_fid: int | None, kingdom_id: int | None, verified_fc_level: int | None, verified_fc_display: str | None):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""INSERT INTO registrations
                (user_id, user_name, chief_name, furnace_level, event, substitute, time_slot, date, is_self_registration,
                player_fid, kingdom_id, verified_fc_level, verified_fc_display, is_captain, team_assignment)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'utc'), ?, ?, ?, ?, ?, 0, NULL)
                ON CONFLICT(chief_name, event) DO UPDATE SET
                    user_id=excluded.user_id,
                    user_name=excluded.user_name,
                    furnace_level=excluded.furnace_level,
                    substitute=excluded.substitute,
                    time_slot=excluded.time_slot,
                    date=excluded.date,
                    is_self_registration=excluded.is_self_registration,
                    player_fid=excluded.player_fid,
                    kingdom_id=excluded.kingdom_id,
                    verified_fc_level=excluded.verified_fc_level,
                    verified_fc_display=excluded.verified_fc_display,
                    is_captain=excluded.is_captain,
                    team_assignment=excluded.team_assignment
                """,
                (user_id, user_name, chief_name, entered_fc_level, event, substitute, time_slot, is_self_registration,
                 player_fid, kingdom_id, verified_fc_level, verified_fc_display))
            conn.commit()
        return True
    except sqlite3.Error as e:
        bot_log.error(f"Database error registering player '{chief_name}' for '{event}': {e}", exc_info=True)
        return False

def unregister_player(chief_name: str, event: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM registrations WHERE chief_name = ? AND event = ?", (chief_name, event))
            deleted_rows = c.rowcount
            conn.commit()
        return deleted_rows > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error unregistering player '{chief_name}' from '{event}': {e}", exc_info=True)
        return False

def is_registered(chief_name: str, event: str) -> bool:
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM registrations WHERE chief_name = ? AND event = ? LIMIT 1", (chief_name, event))
            result = c.fetchone()
        return result is not None
    except sqlite3.Error as e:
        bot_log.error(f"Database error checking registration for '{chief_name}' in '{event}': {e}", exc_info=True)
        return False

def get_registration_count(event: str, slot_type: str) -> int:
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            substitute_flag = 1 if slot_type.lower() == 'substitute' else 0
            c.execute("SELECT COUNT(*) FROM registrations WHERE event = ? AND substitute = ?", (event, substitute_flag))
            count = c.fetchone()[0]
        return count
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registration count for '{event}' '{slot_type}': {e}", exc_info=True)
        return 0

def get_all_registrations():
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT event, time_slot, substitute FROM registrations")
            regs = [dict(row) for row in c.fetchall()]
        return regs
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting all registrations: {e}", exc_info=True)
        return []

def link_discord_fid(discord_id: int, player_fid: int):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO discord_links (discord_id, player_fid) VALUES (?, ?)", (discord_id, player_fid))
            conn.commit()
            return c.rowcount > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error linking Discord ID {discord_id} to FID {player_fid}: {e}", exc_info=True)
        return False

def unlink_discord_fid(discord_id: int):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM discord_links WHERE discord_id = ?", (discord_id,))
            deleted_rows = c.rowcount
            conn.commit()
        return deleted_rows > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error unlinking Discord ID {discord_id}: {e}", exc_info=True)
        return False


def get_linked_fid(discord_id: int) -> int | None:
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT player_fid FROM discord_links WHERE discord_id = ?", (discord_id,))
            result = c.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting linked FID for Discord ID {discord_id}: {e}", exc_info=True)
        return None

def get_linked_discord_user(player_fid: int) -> int | None:
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT discord_id FROM discord_links WHERE player_fid = ?", (player_fid,))
            result = c.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting linked Discord user for FID {player_fid}: {e}", exc_info=True)
        return None


def get_user_registrations(user_id: int):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT event, time_slot, substitute, furnace_level, chief_name, player_fid, verified_fc_display
                              FROM registrations
                              WHERE user_id = ?
                              ORDER BY event, time_slot""", (user_id,))
            regs = [dict(row) for row in c.fetchall()]
        return regs
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registrations for user {user_id}: {e}", exc_info=True)
        return []

def get_registration_by_fid_event(player_fid: int, event: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT chief_name FROM registrations
                         WHERE player_fid = ? AND event = ? LIMIT 1""",
                       (player_fid, event))
            row = c.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error checking FID registration for FID {player_fid} event '{event}': {e}", exc_info=True)
        return None

def get_registration_by_chief_name_event(chief_name: str, event: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT * FROM registrations
                         WHERE chief_name = ? AND event = ?""",
                       (chief_name, event))
            row = c.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registration by chief name, event ('{chief_name}', '{event}'): {e}", exc_info=True)
        return None

def get_registration_by_chief_name_event_slot(chief_name: str, event: str, time_slot: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT * FROM registrations
                         WHERE chief_name = ? AND event = ? AND time_slot = ?""",
                       (chief_name, event, time_slot))
            row = c.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registration by chief name, event, slot ('{chief_name}', '{event}', '{time_slot}'): {e}", exc_info=True)
        return None

def get_registration_by_user_event_slot_team(user_id: int, event: str, time_slot: str, team: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT * FROM registrations
                         WHERE user_id = ? AND event = ? AND time_slot = ? AND team_assignment = ?""",
                       (user_id, event, time_slot, team))
            row = c.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registration by user, event, slot, team ({user_id}, '{event}', '{time_slot}', '{team}'): {e}", exc_info=True)
        return None


def clear_all_registrations():
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("DELETE FROM registrations")
            deleted_rows = c.rowcount
            conn.commit()
        bot_log.info(f"Cleared 'registrations' table. {deleted_rows} rows affected.")
        return deleted_rows
    except sqlite3.Error as e:
        bot_log.error(f"Database error clearing all registrations: {e}", exc_info=True)
        return 0

def get_registrations_for_export(event_name: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT r.chief_name, r.player_fid, r.verified_fc_display, r.furnace_level,
                         r.verified_fc_level, r.team_assignment, r.is_captain, r.substitute,
                         r.date, r.user_name, r.time_slot
                         FROM registrations r
                         WHERE r.event = ?
                         ORDER BY r.time_slot ASC, r.substitute ASC, r.team_assignment ASC NULLS LAST, r.is_captain DESC, r.verified_fc_level DESC, r.chief_name COLLATE NOCASE
                         """, (event_name,))
            regs = [dict(row) for row in c.fetchall()]
        return regs
    except sqlite3.Error as e:
        bot_log.error(f"Database error fetching registrations for export ('{event_name}'): {e}", exc_info=True)
        return []

def get_registrations_for_viewregs(event_name: str):
     try:
         with sqlite3.connect(config.DB_MAIN_FILE) as conn:
             conn.row_factory = sqlite3.Row
             c = conn.cursor()
             c.execute("""
                 SELECT r.chief_name, r.player_fid, r.kingdom_id, r.verified_fc_display,
                        r.substitute, r.time_slot, r.is_captain, r.team_assignment,
                        COALESCE(pr.is_fuel_manager, 0) as fuel_mgr_status, r.verified_fc_level, r.date
                 FROM registrations r
                 LEFT JOIN player_roles pr ON r.player_fid = pr.player_fid
                 WHERE r.event = ?
                 ORDER BY r.time_slot ASC, r.substitute ASC, r.team_assignment ASC NULLS LAST, r.is_captain DESC, r.verified_fc_level DESC NULLS LAST, r.chief_name COLLATE NOCASE
                 """, (event_name,))
             regs = [dict(row) for row in c.fetchall()]
         return regs
     except sqlite3.Error as e:
         bot_log.error(f"Database error fetching registrations for viewregs ('{event_name}'): {e}", exc_info=True)
         return []


def get_players_for_captain_select(event: str, time_slot: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""SELECT chief_name, is_captain
                              FROM registrations
                              WHERE event = ? AND time_slot = ? AND substitute = 0
                              ORDER BY is_captain DESC, chief_name COLLATE NOCASE""",
                           (event, time_slot))
            regs = c.fetchall()
        return regs
    except sqlite3.Error as e:
        bot_log.error(f"Database error fetching players for captain select ('{event}' '{time_slot}'): {e}", exc_info=True)
        return []

def get_team_members_for_captain_select(event: str, time_slot: str, team: str):
     try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""SELECT chief_name, is_captain
                              FROM registrations
                              WHERE event = ? AND time_slot = ? AND team_assignment = ? AND substitute = 0
                              ORDER BY is_captain DESC, chief_name COLLATE NOCASE""",
                           (event, time_slot, team))
            regs = c.fetchall()
        return regs
     except sqlite3.Error as e:
        bot_log.error(f"Database error fetching team members for captain select ('{event}' '{time_slot}' Team '{team}'): {e}", exc_info=True)
        return []

def update_captain_status(chief_name: str, event: str, time_slot: str, new_status: int) -> bool:
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("UPDATE registrations SET is_captain = ? WHERE chief_name = ? AND event = ? AND time_slot = ?",
                       (new_status, chief_name, event, time_slot))
            conn.commit()
            return c.rowcount > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error updating captain status for '{chief_name}' ('{event}' '{time_slot}'): {e}", exc_info=True)
        return False

def get_registration_by_chief_name_event_slot_team(chief_name: str, event: str, time_slot: str, team: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""SELECT * FROM registrations
                         WHERE chief_name = ? AND event = ? AND time_slot = ? AND team_assignment = ?""",
                       (chief_name, event, time_slot, team))
            row = c.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting registration by chief name, event, slot, team ('{chief_name}', '{event}', '{time_slot}', '{team}'): {e}", exc_info=True)
        return None

def clear_other_captains_in_team(event: str, time_slot: str, team: str, chief_name_to_keep: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""UPDATE registrations SET is_captain = 0
                         WHERE event = ? AND time_slot = ? AND team_assignment = ? AND chief_name != ? AND is_captain = 1""",
                       (event, time_slot, team, chief_name_to_keep))
            conn.commit()
        return c.rowcount
    except sqlite3.Error as e:
        bot_log.error(f"Database error clearing other captains in team ('{event}' '{time_slot}' Team '{team}'): {e}", exc_info=True)
        return 0


def clear_team_assignments_and_captains(event: str, time_slot: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("UPDATE registrations SET team_assignment = NULL, is_captain = 0 WHERE event = ? AND time_slot = ?", (event, time_slot))
            conn.commit()
        return True
    except sqlite3.Error as e:
        bot_log.error(f"Database error clearing team assignments and captains for ('{event}' '{time_slot}'): {e}", exc_info=True)
        return False

def update_player_team_assignment(chief_name: str, event: str, time_slot: str, team: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("UPDATE registrations SET team_assignment = ? WHERE chief_name = ? AND event = ? AND time_slot = ?",
                       (team, chief_name, event, time_slot))
            conn.commit()
        return c.rowcount > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error updating team assignment for '{chief_name}' ('{event}' '{time_slot}' Team '{team}'): {e}", exc_info=True)
        return False

def update_player_captain_status(chief_name: str, event: str, time_slot: str, team: str, status: int):
     try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("UPDATE registrations SET is_captain = ? WHERE chief_name = ? AND event = ? AND time_slot = ? AND team_assignment = ?",
                           (status, chief_name, event, time_slot, team))
            conn.commit()
        return c.rowcount > 0
     except sqlite3.Error as e:
         bot_log.error(f"Database error updating captain status for '{chief_name}' ('{event}' '{time_slot}' Team '{team}'): {e}", exc_info=True)
         return False

def get_assignable_players(event: str, time_slot: str):
     try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT r.chief_name, r.verified_fc_level, COALESCE(pr.is_fuel_manager, 0) as fuel_mgr_status, r.player_fid
                FROM registrations r
                LEFT JOIN player_roles pr ON r.player_fid = pr.player_fid
                WHERE r.event = ? AND r.time_slot = ? AND r.substitute = 0 AND r.verified_fc_level IS NOT NULL
                ORDER BY r.verified_fc_level DESC, r.chief_name COLLATE NOCASE
                """, (event, time_slot))
            regs = [dict(row) for row in c.fetchall()]
        return regs
     except sqlite3.Error as e:
         bot_log.error(f"Database error getting assignable players ('{event}' '{time_slot}'): {e}", exc_info=True)
         return []

def get_unassignable_players_names(event: str, time_slot: str):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""SELECT chief_name
                          FROM registrations
                          WHERE event = ? AND time_slot = ? AND substitute = 0 AND verified_fc_level IS NULL""", (event, time_slot))
            names = [row[0] for row in c.fetchall()]
        return names
    except sqlite3.Error as e:
        bot_log.error(f"Database error getting unassignable players names ('{event}' '{time_slot}'): {e}", exc_info=True)
        return []

def add_fuel_manager_role(fid: int):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO player_roles (player_fid, is_fuel_manager) VALUES (?, 1)
                ON CONFLICT(player_fid) DO UPDATE SET is_fuel_manager = 1
            """, (fid,))
            conn.commit()
        return True
    except sqlite3.Error as e:
        bot_log.error(f"Database error adding fuel manager role for FID {fid}: {e}", exc_info=True)
        return False

def remove_fuel_manager_role(fid: int):
    try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            c = conn.cursor()
            c.execute("UPDATE player_roles SET is_fuel_manager = 0 WHERE player_fid = ?", (fid,))
            conn.commit()
        return c.rowcount > 0
    except sqlite3.Error as e:
        bot_log.error(f"Database error removing fuel manager role for FID {fid}: {e}", exc_info=True)
        return False

def get_fuel_managers():
     try:
        with sqlite3.connect(config.DB_MAIN_FILE) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT player_fid FROM player_roles WHERE is_fuel_manager = 1")
            fids = [row['player_fid'] for row in c.fetchall()]
        return fids
     except sqlite3.Error as e:
         bot_log.error(f"Database error getting fuel managers: {e}", exc_info=True)
         return []

