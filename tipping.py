import hashlib
import logging
import os
import uuid

import aiohttp
from aiohttp import BasicAuth

_logger = logging.getLogger(__name__)


class WalletNotFoundError(Exception):
    pass


class WalletPasswordIncorrectError(Exception):
    pass


class WalletCreationError(Exception):
    pass


class WalletTransactionError(Exception):
    pass


class WalletInsufficientBalanceError(Exception):
    pass


def username_to_uuid(username):
    return str(uuid.uuid5(uuid.UUID(os.environ["WALLET_NAMESPACE"]), username))


def get_wallet_pw(username):
    return hashlib.sha256(username_to_uuid(username).encode() + os.environ["WALLET_ENTROPY"].encode()).hexdigest()


# Kaspa REST wallet functions
async def get_wallet(uuid, password=None):
    async with aiohttp.ClientSession() as session:
        async with session.get(f'https://kaspagames.org/api/wallets/{uuid}',
                               auth=BasicAuth("0", password) if password else None) as resp:
            if resp.status == 404:
                raise WalletNotFoundError()

            if resp.status == 403:
                raise WalletPasswordIncorrectError()

            if resp.status == 200:
                return await resp.json()


async def create_new_wallet(password, uuid=None):
    data = {
        "password": password
    }

    if uuid:
        data["uuid"] = uuid

    async with aiohttp.ClientSession() as session:
        async with session.post(f'https://kaspagames.org/api/wallets', json=data) as resp:
            if resp.status == 400:
                raise WalletCreationError(resp.content)

            if resp.status == 200:
                return await resp.json()


async def create_tx(uuid, password, to_address, amount, inclusiveFee=False):
    assert password

    data = {
        "toAddr": to_address,
        "amount": amount,
        "inclusiveFee": inclusiveFee
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(f'https://kaspagames.org/api/wallets/{uuid}/transactions',
                                json=data,
                                auth=BasicAuth("0", password)) as r:
            resp = r
            content = (await r.content.read()).decode()

    if resp.status == 400:
        _logger.info(f'TX creation error: {resp.content}')

        if (resp.content.decode().startswith("Error: Insufficient")):
            raise WalletInsufficientBalanceError(content)

        raise WalletInsufficientBalanceError()

    if resp.status == 200:
        return content

    if b"Password incorrect" in resp.content:
        raise WalletPasswordIncorrectError()

# print(username_to_uuid("lAmeR11010"))

# print(create_new_wallet("12ab12ab12ab!AY"))
# print(create_new_wallet("12ab12ab12ab!AY", "58854c2c-8a70-57b8-8a5d-15eeda66943e"))


# print(get_wallet("8f847d18-688d-4b6d-85d5-5ebf7192fd50"))
# print(get_wallet("58854c2c-8a70-57b8-8a5d-15eeda66943e"))
# print(get_wallet("58854c2c-8a70-57b8-8a5d-15eeda66943e", "12ab12ab12ab!AY"))

# print(create_tx("58854c2c-8a70-57b8-8a5d-15eeda66943e", "12ab12ab12ab!AY",
#                 "kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73", 100000000))
