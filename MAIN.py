# encoding: utf-8

import os
import re
import time
from contextlib import suppress
from datetime import datetime

import requests
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import KaspaInterface
import kaspa_api
from constants import TOTAL_COIN_SUPPLY, DEV_MINING_ADDR, DEV_DONATION_ADDR, DEBOUNCE_SECS_PRICE
from helper import hashrate_to_int, percent_of_network, get_mining_rewards, MINING_CALC, normalize_hashrate

DEBOUNCE_CACHE = {}

bot = TeleBot(os.environ["TELEBOT_TOKEN"], threaded=True)
assert os.environ.get('DONATION_ADDRESS') is not None


def check_debounce(seconds=60 * 60):
    def wrapper(*args, **kwargs):
        cmd_id = f'{args[0].chat.id}{args[0].text.split("@")[0]}'

        if time_passed := (time.time() - DEBOUNCE_CACHE.get(cmd_id, 0)) > seconds or args[0].chat.id == -1001208691907:
            DEBOUNCE_CACHE[cmd_id] = time.time()
        else:
            try:
                bot.delete_message(args[0].chat.id, args[0].id)
            except ApiTelegramException as e:
                if "message can't be deleted for everyone" not in str(e):
                    raise

        return time_passed  # True, if timedelta > seconds

    return wrapper


def check_param(*args):
    if not args:
        return False

    is_param = len(args[0].text.split(" ")) > 1
    if not is_param:
        try:
            bot.delete_message(args[0].chat.id, args[0].id)
        except ApiTelegramException as e:
            if "message can't be deleted for everyone" not in str(e):
                raise

    return is_param


def check_only_private(*args):
    if args[0].chat.type in ["group", "supergroup"]:
        try:
            bot.delete_message(args[0].chat.id, args[0].id)
        except ApiTelegramException as e:
            if "message can't be deleted for everyone" not in str(e):
                print(e)
    else:
        return True


@bot.callback_query_handler(func=lambda call: call.data == 'cb_update')
def callback_query_price_update(call):
    try:
        try:
            message = get_price_message()

        except Exception:
            print(f'Raised exception: {e}')

        try:
            bot.edit_message_text(message, call.message.chat.id, call.message.id,
                                  parse_mode="markdown",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                           callback_data="cb_update")]]))
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise

        bot.answer_callback_query(call.id)
    except Exception as e:
        print(str(e))


@bot.callback_query_handler(func=lambda call: call.data == 'cb_update_hashrate')
def callback_query_hashrate_update(call):
    try:
        stats = KaspaInterface.get_stats()
        norm_hashrate = normalize_hashrate(int(stats['hashrate']))

        try:
            bot.edit_message_text(f"Current Hashrate: *{norm_hashrate}*", call.message.chat.id, call.message.id,
                                  parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                           callback_data="cb_update_hashrate")]]))
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise

        bot.answer_callback_query(call.id)
    except TimeoutError as e:
        print(f'Exception raised: {e}')
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["donate"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def donate(e):
    bot.send_message(e.chat.id, f"Please consider a donation for KASPA-Bot: `{os.environ['DONATION_ADDRESS']}`",
                     parse_mode="Markdown")


@bot.message_handler(commands=["balance"], func=check_only_private)
def balance(e):
    try:
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
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["devfund"], func=check_only_private)
def devfund(e):
    try:
        try:
            balance_mining = KaspaInterface.get_balance(DEV_MINING_ADDR)
            balance_donation = KaspaInterface.get_balance(DEV_DONATION_ADDR)
        except TimeoutError as e:
            print(f'Exception raised: {e}')
            return

        bot.send_message(e.chat.id, f"*Balance for devfund*\n\n"
                                    f"```\nMINING\n"
                                    f"    {balance_mining:,} KAS\n"
                                    f"DONATION\n"
                                    f"    {balance_donation:,} KAS\n"
                                    f"{30 * '-'}\n"
                                    f"{balance_mining + balance_donation:,} KAS\n```", parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["coin_supply"], func=check_debounce(60 * 60))
def coin_supply(e):
    try:
        coin_supply = kaspa_api.get_coin_supply()

        if coin_supply is None:
            return

        circulating_supply = float(coin_supply["circulatingSupply"]) / 100000000
        total_supply = float(coin_supply["maxSupply"]) / 100000000

        bot.send_message(e.chat.id,
                         f"```"
                         f"\n"
                         f"Circulating supply  : {circulating_supply:,.0f} KAS\n"
                         f"Uncirculated supply : {total_supply - circulating_supply:,.0f} KAS\n\n"
                         f"{'=' * 40}\n"
                         f"Total supply        : {total_supply:,.0f} KAS\n"
                         f"Percent mined       : {round(circulating_supply / total_supply * 100, 2)}%\n"
                         f"```", parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["price"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def price(e):
    try:
        if e.chat.id == -1001589070884:
            bot.send_message(e.chat.id,
                             f'💰 For price talks please use the price channel 💰\n\nhttps://t.me/KaspaTrading')
        else:
            try:
                msg = get_price_message()

                bot.send_message(e.chat.id, msg,
                                 parse_mode="Markdown",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                          callback_data="cb_update")]]))
            except Exception:
                print(f'Raised exception: {e}')
    except Exception as e:
        print(str(e))


def get_coin_info():
    try:
        resp = requests.get(f"https://api.coingecko.com/api/v3/coins/kaspa",
                            params={"tickers": False,
                                    "community_data": False,
                                    "developer_data": False})

        return resp.json()
    except Exception as e:
        print(str(e))


def get_ath_message(name):
    try:
        coin = name

        try:
            coin_info = get_coin_info()
        except Exception:
            return

        if not coin_info:
            return

        try:
            symbol = coin_info['tickers'][0]['base']
        except (ValueError, IndexError):
            symbol = coin.upper()

        ath_price = round(coin_info['market_data']['ath']['usd'], 6)
        ath_date = datetime.fromisoformat(coin_info['market_data']['ath_date']['usd'][:-1] + "+00:00")
        ath_change_percentage = coin_info['market_data']['ath_change_percentage']['usd']

        message = f"📈 ATH for *{symbol} - {coin_info['name']}*\n" \
                  f"{'-' * 40}\n" \
                  f"*Current price:* \n      {round(coin_info['market_data']['current_price']['usd'],6):,} USD\n\n" \
                  f" *ATH Price:*\n" \
                  f"      {ath_price} USD\n" \
                  f" *ATH Date* :\n" \
                  f"      {ath_date:%Y-%m-%d %H:%M}\n" \
                  f" *ATH Change ▼*:\n" \
                  f"      {ath_change_percentage:0.2f} %"

        return message
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["ath"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def ath(e):
    try:
        if e.chat.id == -1001589070884:
            bot.send_message(e.chat.id,
                             f'💰 For price talks please use the price channel 💰\n\nhttps://t.me/KaspaTrading')
        else:
            try:
                message = get_ath_message("kas")
            except Exception as e:
                print(f"Error creating ATH message: {e}")
                return
            if message:
                bot.send_message(e.chat.id, message,
                                 parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["wallet"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def wallet(e):
    try:
        bot.send_message(e.chat.id, f'*For a Kaspa-wallet you can use one of these applications*\n\n'
                                    f'*Kaspad (command line wallet)*:\n'
                                    f'  tinyurl.com/ym8sbas7\n'
                                    '*Kaspa for desktop (KDX)*:\n'
                                    '  https://kdx.app/\n'
                                    '*Web wallet*:\n'
                                    '  https://wallet.kaspanet.io/',
                         parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["mining_reward"], func=check_only_private)
def mining_reward(e):
    try:
        params = " ".join(e.text.split(" ")[1:])
        match = re.match(r"(?P<dec>\d+) *(?P<suffix>[^\d ]+)", params)

        if match is None:
            return

        suffix = match["suffix"]
        own_hashrate = match["dec"]

        stats = KaspaInterface.get_stats()
        network_hashrate = int(stats['hashrate'])
        own_hashrate = own_hashrate + suffix if suffix else own_hashrate
        own_hashrate = hashrate_to_int(own_hashrate)

        if own_hashrate:
            hash_percent_of_network = percent_of_network(own_hashrate, network_hashrate)
            rewards = get_mining_rewards(int(stats['daa_score']), hash_percent_of_network)
            bot.send_message(e.chat.id,
                             MINING_CALC(rewards),
                             parse_mode="Markdown")
    except Exception:
        print(f'Raised exception: {e}')


@bot.message_handler(commands=["id"])
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["chart"])
def chart(e):
    bot.send_message(e.chat.id, f"See *KAS/USDT* chart on exbitron:\n"
                                f"    https://www.exbitron.com/trading/kasusdt",
                     parse_mode="Markdown")


@bot.message_handler(commands=["mcap"], func=check_debounce(60 * 60))
def mcap(e):
    try:
        price_usd = _get_kas_price()

        circ_supply = KaspaInterface.get_circulating_supply()

        bot.send_message(e.chat.id,
                         f"*$KAS MARKET CAP*\n"
                         f"{'-' * 25}\n"
                         f"```\n"
                         f"Current Market Capitalization : {circ_supply * price_usd:>11,.0f} USD\n"
                         f"Fully Diluted Valuation (FDV) : {TOTAL_COIN_SUPPLY * price_usd:>11,.0f} USD"
                         f"\n```",
                         parse_mode="Markdown")
    except Exception as e:
        print(f'Raised exception: {e}')


@bot.message_handler(commands=["id"], func=check_only_private)
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["hashrate"], func=check_debounce(60 * 60))
def hashrate(e):
    try:
        try:
            stats = KaspaInterface.get_stats()
        except TimeoutError:
            print(f'Raised exception: {e}')

        norm_hashrate = normalize_hashrate(int(stats['hashrate']))
        bot.send_message(e.chat.id, f"Current Hashrate: *{norm_hashrate}*", parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                  callback_data="cb_update_hashrate")]]))
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["buy"], func=check_debounce(60 * 60))
def buy(e):
    bot.send_message(e.chat.id,
                     f"----------------------------------\n"
                     f"    💰   *Exchanges*\n"
                     f"----------------------------------\n"
                     f" *Txbit*\n"
                     f"  [https://txbit.io/]\n"
                     f"----------------------------------\n"
                     f"  *Exibitron*\n"
                     f"  [https://www.exbitron.com/]\n"
                     f"----------------------------------\n"
                     f"  *TradeOgre*\n"
                     f"  [https://www.tradeogre.com](https://tradeogre.com/exchange/USDT-KAS)\n"
                     f"----------------------------------\n"
                     f"  *cryptex24*\n"
                     f"  [https://www.cryptex24.io](https://www.cryptex24.io/trade/KAS&USDT)\n"
                     f"----------------------------------\n"
                     f"  *vitex (DEX)*\n"
                     f"  [https://x.vite.net/](https://x.vite.net/trade?symbol=KAS-000_USDT-000)",
                     parse_mode="Markdown")


@bot.message_handler(commands=["languages"], func=check_debounce(60 * 60))
def buy(e):
    bot.send_message(e.chat.id,
                     f"----------------------------------\n"
                     f" *Kaspa in your language*\n"
                     f"----------------------------------\n"
                     f"🇨🇳 [https://t.me/kaspa_chinese/]\n"
                     f"🇩🇪 [https://t.me/KaspaGerman/]\n"
                     f"🇪🇸 [https://t.me/kaspaesp]\n"
                     f"🇮🇱 [https://t.me/kaspahebrewgroup/]\n"
                     f"🇮🇹 [https://t.me/kaspaitalia]\n"
                     f"🇯🇵 [https://t.me/Kaspa_Japan]\n"
                     f"🇰🇷 [https://t.me/kaspa_korea]\n"
                     f"🇵🇱 [https://t.me/Kaspa_Poland]\n"
                     f"🇳🇱 [https://t.me/Kaspa_Dutch/]\n"
                     f"🇷🇺 [https://t.me/kaspa_rus/]\n"
                     f"🇹🇷 [https://t.me/kaspa_turkish/]",
                     parse_mode="Markdown")


def get_price_message():
    coin = "kaspa"
    coin_info = get_coin_info()

    if not coin_info:
        return

    try:
        symbol = coin_info['tickers'][0]['base']
    except (ValueError, IndexError):
        symbol = coin.upper()

    price_change_1h = coin_info['market_data']['price_change_percentage_1h_in_currency']['usd']
    price_change_24h = coin_info['market_data']['price_change_percentage_24h_in_currency']['usd']
    price_change_7d = coin_info['market_data']['price_change_percentage_7d_in_currency']['usd']

    message = f"📈 Price Update for 📈\n" \
              f"  *{symbol} - {coin_info['name']}*\n" \
              f"{'-' * 40}\n" \
              f"Current price : \n      *{round(coin_info['market_data']['current_price']['usd'],6)} USD*\n\n" \
              f"```\n 1h {'▲' if price_change_1h > 0 else '▼'}  : {price_change_1h:.02f} %\n" \
              f"24h {'▲' if price_change_24h > 0 else '▼'}  : {price_change_24h:.02f} %\n" \
              f" 7d {'▲' if price_change_7d > 0 else '▼'}  : {price_change_7d:.02f} %\n```" \
        # f"MCAP  : {coin_info['market_data']['market_cap'].get('usd', 0):,} USD\n" \
    # f"FDV   : {coin_info['market_data']['fully_diluted_valuation'].get('usd', 0):,} USD\n" \
    # f"Circ. Supply:\n  {coin_info['market_data']['circulating_supply'] or 0:,}\n" \
    # f"Total Supply:\n  {coin_info['market_data']['total_supply'] or 0:,}\n```"

    return message


def _get_kas_price():
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/simple/price",
                            params={"ids": "kaspa",
                                    "vs_currencies": "usd"})
        if resp.status_code == 200:
            return resp.json()["kaspa"]["usd"]
    except Exception as e:
        print(str(e))


# notifiy in channel on donation
command = 'notifyUtxosChangedRequest'
payload = {"addresses": [os.environ["DONATION_ADDRESS"]]}


def callback_func(notification: dict):  # create a callback function to process the notifications
    with suppress(Exception):
        donation_amount = int(notification["utxosChangedNotification"]["added"][0]["utxoEntry"]["amount"]) / 100000000
        if chat_id := os.environ.get("DONATION_ANNOUNCEMENT"):
            bot.send_message(chat_id, f"Donation received. Thank you for {donation_amount} KAS. ♥♥♥",
                             parse_mode="Markdown")

        if chat_id := os.environ.get("DONATION_ANNOUNCEMENT_2"):
            bot.send_message(chat_id, f"Donation received. Thank you for {donation_amount} KAS. ♥♥♥",
                             parse_mode="Markdown")


# send the request to the server and retrive the response
with KaspaInterface.kaspa_connection() as client:
    # subscribe utxo change for donation address
    resp = client.subscribe(command=command, payload=payload, callback=callback_func)
    bot.polling(none_stop=True)
