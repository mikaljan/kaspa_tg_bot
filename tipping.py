import hashlib
import logging
import os
import uuid

import requests
from requests.auth import HTTPBasicAuth

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
def get_wallet(uuid, password=None):
    resp = requests.get(f'https://kaspagames.org/api/wallets/{uuid}',
                        auth=HTTPBasicAuth("0", password) if password else None)

    if resp.status_code == 404:
        raise WalletNotFoundError()

    if resp.status_code == 403:
        raise WalletPasswordIncorrectError()

    if resp.status_code == 200:
        return resp.json()


def create_new_wallet(password, uuid=None):
    data = {
        "password": password
    }

    if uuid:
        data["uuid"] = uuid

    resp = requests.post(f'https://kaspagames.org/api/wallets', json=data)

    if resp.status_code == 400:
        raise WalletCreationError(resp.content)

    if resp.status_code == 200:
        return resp.json()


def create_tx(uuid, password, to_address, amount):
    assert password

    data = {
        "toAddr": to_address,
        "amount": amount
    }

    resp = requests.post(f'https://kaspagames.org/api/wallets/{uuid}/transactions',
                         json=data,
                         auth=HTTPBasicAuth("0", password))

    if resp.status_code == 400:
        _logger.info(f'TX creation error: {resp.content}')

        if (resp.content.decode().startswith("Error: Insufficient")):
            raise WalletInsufficientBalanceError(resp.content.decode())

        raise WalletInsufficientBalanceError()

    if resp.status_code == 200:
        return resp.content.decode()

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
