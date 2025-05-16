import aiohttp
import asyncio
import logging
import config

bot_log = logging.getLogger('registration_bot')

api_session = None

async def create_client_session():
    global api_session
    if api_session is None or api_session.closed:
        api_session = aiohttp.ClientSession()
        bot_log.info("aiohttp client session created.")
    return api_session

async def close_client_session():
    global api_session
    if api_session and not api_session.closed:
        await api_session.close()
        bot_log.info("aiohttp client session closed.")
        api_session = None

async def make_api_request(method, url, headers=None, json_data=None):
    session = await create_client_session()
    async with session.request(method, url, headers=headers, json=json_data) as response:
        response.raise_for_status()
        return await response.json()
