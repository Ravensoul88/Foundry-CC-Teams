import logging
import sys

def setup_logging():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
                        stream=sys.stdout)
    # Optional: Set discord.py logger level to WARNING or ERROR to reduce verbosity
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    logging.getLogger('discord.gateway').setLevel(logging.WARNING)
