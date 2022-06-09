# encoding: utf-8

import os
import re

import requests
from telebot import TeleBot

import KaspaInterface
from constants import TOTAL_COIN_SUPPLY, DEV_MINING_ADDR, DEV_DONATION_ADDR
from helper import hashrate_to_int, percent_of_network, get_mining_rewards, MINING_CALC

bot = TeleBot(os.environ["TELEBOT_TOKEN"], threaded=True)
assert os.environ.get('DONATION_ADDRESS') is not None


@bot.message_handler(commands=["donate"])
def donate(e):
    bot.send_message(e.chat.id, f"Please consider a donation: `{os.environ['DONATION_ADDRESS']}`",
                     parse_mode="Markdown")


@bot.message_handler(commands=["balance"])
def balance(e):
    try:
        address = e.text.split(" ")[1]
    except IndexError:
        bot.send_message(e.chat.id, "Command needs kaspa wallet as parameter.")
        return

    if re.match(r"kaspa:[a-zA-Z0-9]{51}", address) is None:
        bot.send_message(e.chat.id, "kaspa wallet not valid.")
        return

    balance = KaspaInterface.get_balance(address)

    bot.send_message(e.chat.id, f"```\nBalance for\n"
                                f"  {address}\n"
                                f"{60 * '-'}\n"
                                f"{balance:,} KAS```", parse_mode="Markdown")


@bot.message_handler(commands=["devfund"])
def devfund(e):
    balance_mining = KaspaInterface.get_balance(DEV_MINING_ADDR)
    balance_donation = KaspaInterface.get_balance(DEV_DONATION_ADDR)

    bot.send_message(e.chat.id, f"*Balance for devfund*\n\n"
                                f"```\nMINING\n"
                                f"    {balance_mining:,} KAS\n"
                                f"DONATION\n"
                                f"    {balance_donation:,} KAS\n"
                                f"{30 * '-'}\n"
                                f"{balance_mining + balance_donation:,} KAS\n```", parse_mode="Markdown")


@bot.message_handler(commands=["coin_supply"])
def coin_supply(e):
    circulacting_supply = KaspaInterface.get_circulating_supply()
    bot.send_message(e.chat.id,
                     f"```"
                     f"\n"
                     f"Circulating supply  : {circulacting_supply:,} KAS\n"
                     f"Uncirculated supply : {TOTAL_COIN_SUPPLY - circulacting_supply:,} KAS\n\n"
                     f"{'=' * 40}\n"
                     f"Total supply        : {TOTAL_COIN_SUPPLY:,} KAS\n"
                     f"Percent mined       : {round(circulacting_supply / TOTAL_COIN_SUPPLY * 100, 2)}%\n"
                     f"```", parse_mode="Markdown")


@bot.message_handler(commands=["price"])
def price(e):
    resp = requests.get("https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": "kaspa",
                                "vs_currencies": "usd"})
    if resp.status_code == 200:
        bot.send_message(e.chat.id, f'Current KAS price: *{resp.json()["kaspa"]["usd"] * 1.0e6:.0f} USD* per 1M KAS',
                         parse_mode="Markdown")

@bot.message_handler(commands=["mining_reward"])
def mining_reward(e):
    params = " ".join(e.text.split(" ")[1:])
    match = re.match(r"(?P<dec>\d+) *(?P<suffix>[^\d ]+)", params)

    suffix = match["suffix"]
    own_hashrate = match["dec"]

    stats = KaspaInterface.get_stats()
    network_hashrate = int(stats['hashrate'])
    own_hashrate = own_hashrate + suffix if suffix else own_hashrate
    own_hashrate = hashrate_to_int(own_hashrate)
    hash_percent_of_network = percent_of_network(own_hashrate, network_hashrate)
    rewards = get_mining_rewards(int(stats['daa_score']), hash_percent_of_network)
    bot.send_message(e.chat.id,
                     MINING_CALC(rewards),
                     parse_mode="Markdown")


bot.polling(none_stop=True)