# encoding: utf-8


from urllib.parse import urljoin

import requests

_session = requests.session()

BASE_URL = "http://api.kaspa.org/"


def __get(endpoint, params=None):
    try:
        return _session.get(urljoin(BASE_URL, endpoint), params=params).json()
    except requests.exceptions.JSONDecodeError:
        pass


def get_coin_supply():
    return __get("info/coinsupply")


def get_hashrate():
    return __get("info/hashrate")


def get_balance(addr):
    return __get(f"info/addresses/{addr}/balance")


def get_coin_supply():
    return __get(f"info/coinsupply")
