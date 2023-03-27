# encoding: utf-8
import asyncio
from urllib.parse import urljoin

import aiohttp
import requests
from cachetools.func import ttl_cache
from aiocache import cached

_session = requests.session()

BASE_URL = "https://api.kaspa.org/"


async def __get(endpoint, params=None):
    async with aiohttp.ClientSession() as session:
        async with session.get(urljoin(BASE_URL, endpoint), params=params) as resp:
            return await resp.json()


async def get_coin_supply():
    return await __get("info/coinsupply")

@cached(ttl=15)
async def get_hashrate():
    return await __get("info/hashrate")


async def get_balance(addr):
    return await __get(f"addresses/{addr}/balance")

@cached(ttl=120)
async def get_max_hashrate():
    return await __get(f"info/hashrate/max")


async def get_coin_supply():
    return await __get(f"info/coinsupply")


async def get_blockdag_info():
    return await __get(f"info/blockdag")
