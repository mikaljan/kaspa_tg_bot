# encoding: utf-8
import logging
import math
import os
import re
import threading
import time
from datetime import datetime

import requests
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

import kaspa_api
from constants import TOTAL_COIN_SUPPLY, DEV_MINING_ADDR, DEV_DONATION_ADDR, DEBOUNCE_SECS_PRICE
from helper import hashrate_to_int, percent_of_network, get_mining_rewards, MINING_CALC
from plot import get_image_stream
from tipping import create_new_wallet, WalletCreationError, get_wallet, WalletNotFoundError, username_to_uuid, \
    get_wallet_pw, create_tx, WalletInsufficientBalanceError

DEBOUNCE_CACHE = {}

TX_CHECKER = {}

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


def ignore_channels(ignore_ids):
    def wrapper(*args, **kwargs):
        if str(args[0].chat.id) in ignore_ids:
            try:
                bot.delete_message(args[0].chat.id, args[0].id)
                return False
            except ApiTelegramException as e:
                if "message can't be deleted for everyone" not in str(e):
                    print(e)

        return True  # True, if timedelta > seconds

    return wrapper


@bot.callback_query_handler(func=lambda call: call.data == 'cb_update')
def callback_query_price_update(call):
    try:
        try:
            message = get_price_message()

        except Exception as e:
            print(f'Raised exception: {e}')
            return

        try:
            bot.edit_message_caption(message, call.message.chat.id, call.message.id,
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
        try:
            hashrate = kaspa_api.get_hashrate()["hashrate"]
        except Exception as e:
            print(str(e))
            return

        try:
            bot.edit_message_text(f"Current Hashrate: *{hashrate:0.2f} TH/s*", call.message.chat.id, call.message.id,
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
    bot.send_message(e.chat.id,
                     f"*Please consider a donation for my work on:\n- Kaspa Bot\n- Block explorer\n- REST-API\n\n*"
                     f"`{os.environ['DONATION_ADDRESS']}`\n\n*Thank you!*",
                     parse_mode="Markdown")


@bot.message_handler(commands=["balance"], func=check_debounce(DEBOUNCE_SECS_PRICE))
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

        balance = kaspa_api.get_balance(address)["balance"] / 100000000

        bot.send_message(e.chat.id, f"```\nBalance for\n"
                                    f"  {address}\n"
                                    f"{60 * '-'}\n"
                                    f"{balance:,} KAS```", parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["devfund"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def devfund(e):
    try:
        try:
            balance_mining = kaspa_api.get_balance(DEV_MINING_ADDR)["balance"] / 100000000
            balance_donation = kaspa_api.get_balance(DEV_DONATION_ADDR)["balance"] / 100000000
        except TimeoutError as e:
            print(f'Exception raised: {e}')
            return

        bot.send_message(e.chat.id, f"*Balance for devfund*\n\n"
                                    f"```\nMINING\n"
                                    f"    {round(balance_mining, 2):,} KAS\n"
                                    f"DONATION\n"
                                    f"    {round(balance_donation, 2):,} KAS\n"
                                    f"{30 * '-'}\n"
                                    f"{round(balance_mining, 2) + round(balance_donation, 2):,} KAS\n```",
                         parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["coin_supply"], func=check_debounce(60 * 10))
def coin_supply(e):
    try:
        coin_supply = kaspa_api.get_coin_supply()

        if coin_supply is None:
            return

        circulating_supply = float(coin_supply["circulatingSupply"]) / 100000000
        total_supply = float(TOTAL_COIN_SUPPLY)

        bot.send_message(e.chat.id,
                         f"```"
                         f"\n"
                         f"Total supply  : {circulating_supply:,.0f} KAS\n"
                         f"Unmined supply : {total_supply - circulating_supply:,.0f} KAS\n\n"
                         f"{'=' * 15}\n"
                         f"Max supply ~      : {total_supply:,.0f} KAS\n"
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
                bot.send_photo(e.chat.id,
                               get_image_stream(),
                               caption=msg,
                               parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                        callback_data="cb_update")]]))
            except Exception:
                logging.exception(f'Raised exception: {e}')
    except Exception as e:
        logging.exception(str(e))


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
                  f"*Current price:* \n      {round(coin_info['market_data']['current_price']['usd'], 6):0.6f} USD\n\n" \
                  f" *ATH Price:*\n" \
                  f"      {ath_price:0.6f} USD\n" \
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
                         parse_mode="Markdown",
                         disable_web_page_preview=True)
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["mining_reward", "mr"], func=ignore_channels(["-1001589070884"]))
def mining_reward(e):
    try:
        params = " ".join(e.text.split(" ")[1:])
        match = re.match(r"(?P<dec>[\d\.]+) *(?P<suffix>[^\d ]+)", params)

        if match is None:
            return

        suffix = match["suffix"]
        own_hashrate = match["dec"]

        network_hashrate = kaspa_api.get_hashrate()["hashrate"] * 1_000_000_000_000
        own_hashrate = own_hashrate + suffix if suffix else own_hashrate
        own_hashrate = hashrate_to_int(own_hashrate)

        stats = kaspa_api.get_blockdag_info()

        if own_hashrate:
            hash_percent_of_network = percent_of_network(own_hashrate, network_hashrate)
            rewards = get_mining_rewards(int(stats['virtualDaaScore']), hash_percent_of_network)
            bot.send_message(e.chat.id,
                             f"*Mining rewards for {match['dec']} {suffix[:2].upper()}/s*\n" + MINING_CALC(rewards),
                             parse_mode="Markdown")
    except Exception:
        print(f'Raised exception: {e}')


@bot.message_handler(commands=["id"])
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["chart"])
def chart(e):
    bot.send_message(e.chat.id, f"See *KAS/USDT* chart on *MEXC*:\n"
                                f"    https://www.tradingview.com/chart/?symbol=MEXC%3AKASUSDT\n",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["mcap"], func=check_debounce(60 * 60))
def mcap(e):
    try:
        kaspa_info = get_coin_info()
        price_usd = kaspa_info["market_data"]["current_price"]["usd"]
        rank = kaspa_info["market_data"]["market_cap_rank"]

        circ_supply = float(kaspa_api.get_coin_supply()["circulatingSupply"]) / 100000000

        bot.send_message(e.chat.id,
                         f"*$KAS MARKET CAP*\n"
                         f"{'-' * 25}\n"
                         f"```\n"
                         f"Coingecko Market cap rank : {rank}\n"
                         f"Current Market Capitalization : {circ_supply * price_usd:>11,.0f} USD\n"
                         f"Fully Diluted Valuation (FDV) : {TOTAL_COIN_SUPPLY * price_usd:>11,.0f} USD"
                         f"\n```",
                         parse_mode="Markdown")
    except Exception as e:
        print(f'Raised exception: {e}')


@bot.message_handler(commands=["maxhash"], func=check_debounce(60 * 60))
def max_hashrate(e):
    try:
        max_hashrate = kaspa_api.get_max_hashrate()

        bot.send_message(e.chat.id,
                         f"Max Kaspa Hashrate\n"
                         f"  *{max_hashrate['hashrate']:.2f} THs*\n\n"
                         f"  Date {datetime.fromisoformat(max_hashrate['blockheader']['timestamp']):%Y-%m-%d %H:%M}\n"
                         f"  Block {max_hashrate['blockheader']['hash'][:8]}",
                         parse_mode="Markdown")
    except Exception as e:
        print(f'Raised exception: {e}')


@bot.message_handler(commands=["id"], func=check_only_private)
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["hashrate"], func=check_debounce(60 * 60))
def hashrate(e):
    try:
        hashrate = kaspa_api.get_hashrate()["hashrate"]
        bot.send_message(e.chat.id, f"Current Hashrate: *{hashrate:.02f} TH/s*", parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                  callback_data="cb_update_hashrate")]]))
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["buy"], func=check_debounce(60 * 10))
def buy(e):
    bot.send_message(e.chat.id,
                     f"    💰   *Exchanges*\n"
                     f"----------------------------------\n"
                     f" *CoinEx* [https://www.coinex.com/](https://www.coinex.com/exchange/kas-usdt)\n"
                     f" *MEXC Global* [https://mexc.com/](https://www.mexc.com/exchange/KAS_USDT)\n"
                     f" *Txbit* [https://txbit.io/]\n"
                     f" *Exibitron* [https://www.exbitron.com/]\n"
                     f" *TradeOgre* [https://www.tradeogre.com](https://tradeogre.com/exchange/USDT-KAS)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["languages"], func=check_debounce(60 * 10))
def buy(e):
    bot.send_message(e.chat.id,
                     f"----------------------------------\n"
                     f" *Kaspa in your language*\n"
                     f" ⚠️Please keep in mind:\n"
                     f"These are *unofficial* groups.\n"
                     f"Use with caution! ⚠️ \n"
                     f"----------------------------------\n"
                     f"🇦🇪 [https://t.me/kaspa_arabic]\n"
                     f"🇨🇳 [https://t.me/kaspa_chinese]\n"
                     f"🇩🇪 [https://t.me/KaspaGerman]\n"
                     f"🇪🇸 [https://t.me/kaspaesp]\n"
                     f"🇫🇷 [https://t.me/kasfrench]\n"
                     f"🇮🇳 [https://t.me/kaspaindia](https://t.me/kaspaindia)\n"
                     f"🇮🇩 [https://t.me/Kaspa_Indonesian]\n"
                     f"🇮🇱 [https://t.me/kaspaisrael]\n"
                     f"🇮🇹 [https://t.me/kaspaitalia]\n"
                     f"🇯🇵 [https://t.me/Kaspa_Japan]\n"
                     f"🇰🇷 [https://t.me/kaspa_korea]\n"
                     f"🇮🇷 [https://t.me/Kaspa_persian]\n"
                     f"🇵🇭 [http://t.me/kaspa_ph]\n"
                     f"🇵🇱 [https://t.me/Kaspa_Poland]\n"
                     f"🇵🇹🇧🇷 [https://t.me/kaspa_portugues]\n"
                     f"🇳🇱 [https://t.me/Kaspa_Dutch]\n"
                     f"🇷🇴 [https://t.me/KaspaRomania]\n"
                     f"🇷🇺 [https://t.me/kaspa_rus]\n"
                     f"🇹🇷 [https://t.me/KaspaTurk]\n"
                     f"🇻🇳 [https://t.me/Kaspa_VN]\n"
                     f"🇷🇸 [https://t.me/kaspa_balkan]",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["miningpools"], func=check_debounce(60 * 10))
def miningpools(e):
    bot.send_message(e.chat.id,
                     f"----------------------\n"
                     f" *Kaspa mining pools*\n"
                     f"----------------------\n"
                     f"[ACC-POOL](https://kaspa.acc-pool.pw/)\n"
                     f"[KASPA-POOL](https://kaspa-pool.org/)\n"
                     f"[HEROMINERS](https://kaspa.herominers.com/)\n"
                     f"[KYIV KASPA POOL](https://kaspa.ixbase.info/)\n"
                     f"[WOOLYPOOLY](https://woolypooly.com/en/coin/kas)\n"
                     f"[HASHPOOL](https://hashpool.com/coins/KAS)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["links"], func=check_debounce(60 * 10))
def links(e):
    bot.send_message(e.chat.id,
                     f"----------------------\n"
                     f" *Most important links*\n"
                     f"----------------------\n"
                     f"[Website](https://kaspa.org/)\n"
                     f"[Katnip Explorer](https://katnip.kaspad.net/)\n"
                     f"[Block Explorer (BETA)](https://explorer.kaspa.org/)\n"
                     f"[KGI BlockDAG visualizer](https://kgi.kaspad.net/)\n"
                     f"[Kaspa Wiki](https://kaspawiki.net/index.php/Main_Page)\n"
                     f"[Kaspa Faucet](https://faucet.kaspanet.io/)\n"
                     f"[Kaspa Grafana Board](http://kasboard-mainnet.kas.pa/)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)

    # telegram bot features


@bot.message_handler(commands=["tip"])
def send_kas(e):
    if e.chat.id != -1001446859854:
        return

    try:
        sender = e.from_user.username
    except Exception:
        print("You don't have a wallet. DM ME with /create_wallet to create one.")
        return

    try:
        # reply to a message?
        recipient = e.reply_to_message.from_user.username
    except Exception:
        # check 2nd argument
        try:
            recipient = re.search("@[^ ]+", e.text)[0]
        except:
            print("No recipient found in your message!")
            return

    try:
        recipient = get_wallet(username_to_uuid(recipient.lstrip("@")))["publicAddress"]
    except Exception:
        bot.send_message(e.chat.id, f"Recipient {recipient} does not have a wallet yet. "
                                    f"DM the bot with /create_wallet to create a new wallet.")
        return

    amount = float(re.search("(\d+([.,]\d+)?) ?KAS", e.text, re.IGNORECASE)[1].replace(",", "."))

    try:
        send_kas_and_log(sender,
                         recipient,
                         amount * 100000000,
                         e.chat.id)
    except WalletInsufficientBalanceError:
        bot.send_message(e.chat.id, f"You don't have enough KAS to finish this transaction.")


@bot.message_handler(commands=["create_wallet"], chat_types=['private'])
def create_wallet(e):
    try:
        wallet = create_new_wallet(get_wallet_pw(e.from_user.username),
                                   username_to_uuid(e.from_user.username))
        seed = wallet["mnemonic"]
        bot.send_message(e.chat.id, f"Wallet creation successful. Your kaspa address is:"
                                    f"\n`{wallet['publicAddress']}`"
                                    f"\n\nYour seed phrase is:\n"
                                    f"`{seed}`"
                                    f"\n\nPlease be advised that neither the dev nor any of the kaspa community is"
                                    f" responsible for any issues or losses that may occur with the use of this wallet."
                                    f" Use at your own risk.",
                         parse_mode="Markdown")
    except WalletCreationError:
        bot.send_message(e.chat.id, "Wallet already created. Use /my_wallet")


@bot.message_handler(commands=["create_wallet"], chat_types=['supergroup'])
def create_wallet(e):
    bot.send_message(e.chat.id, "To create a new wallet, please use a direct message to me.")


@bot.message_handler(commands=["my_wallet"], chat_types=['private'])
def my_wallet(e):
    print()
    try:
        wallet = get_wallet(username_to_uuid(e.from_user.username)
                            # ,get_wallet_pw(e.from_user.username)
                            )

        wallet_balance = kaspa_api.get_balance(wallet["publicAddress"])["balance"] / 100000000
        bot.send_message(e.chat.id, f'Wallet:\n`{wallet["publicAddress"]}`\nBalance:\n  *{wallet_balance} KAS*',
                         parse_mode="Markdown")
    except WalletNotFoundError:
        bot.send_message(e.chat.id, f'No KAS wallet found. Use /create_wallet')


def progress_bar(perc):
    green_boxes = math.floor(perc / 100 * 8)
    return green_boxes * "🟩" + "⬜" * (8 - green_boxes)


def send_kas_and_log(sender_username, to_address, amount, chat_id):
    tx_id = create_tx(username_to_uuid(sender_username),
                      get_wallet_pw(sender_username),
                      to_address,
                      amount)

    message = bot.send_message(chat_id,
                               f"Sending *{amount / 100000000} KAS* to @{sender_username}\n   [{to_address[:16]}...{to_address[-10:]}](https://explorer.kaspa.org/addresses/{to_address})\n\n"
                               f"TX-ID\n"
                               f"   [{tx_id[:6]}...{tx_id[-6:]}](https://explorer.kaspa.org/txs/{tx_id}) ✅\n"
                               f"Block-ID\n"
                               f"   `...`",
                               parse_mode="Markdown",
                               disable_web_page_preview=True)

    TX_CHECKER[tx_id] = message


def get_price_message():
    coin = "kaspa"
    coin_info = get_coin_info()

    if not coin_info:
        return

    try:
        symbol = coin_info['tickers'][0]['base']
    except (ValueError, IndexError):
        symbol = coin.upper()

    price_change_1h = coin_info['market_data']['price_change_percentage_1h_in_currency'].get('usd', 0)
    price_change_24h = coin_info['market_data']['price_change_percentage_24h_in_currency'].get('usd', 0)
    price_change_7d = coin_info['market_data']['price_change_percentage_7d_in_currency'].get('usd', 0)

    rank = coin_info["market_data"]["market_cap_rank"]
    volume = coin_info["market_data"]["total_volume"]["usd"]

    message = f"📈 Price Update for 📈\n" \
              f"  *{symbol} - {coin_info['name']} [Rank {rank}]*\n" \
              f"{'-' * 40}\n" \
              f"Current price : \n      *{round(coin_info['market_data']['current_price']['usd'], 6):0.6f} USD*\n\n" \
              f"```\n 1h {'▲' if price_change_1h > 0 else '▼'}  : {price_change_1h:.02f} %\n" \
              f"24h {'▲' if price_change_24h > 0 else '▼'}  : {price_change_24h:.02f} %\n" \
              f" 7d {'▲' if price_change_7d > 0 else '▼'}  : {price_change_7d:.02f} %\n" \
              f"Volume : {volume:,.0f} USD\n```" \
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
        logging.exception(str(e))


DONATION_CHANNELS = [-1001589070884,
                     -1001205240510]


def check_donations():
    donation_announced = 0
    while True:
        donation_addr = "kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73"
        try:
            donation_balance = kaspa_api.get_balance(donation_addr)["balance"] / 100000000
        except Exception:
            time.sleep(1)
            continue

        if donation_balance != donation_announced:
            if donation_announced:
                for c_id in DONATION_CHANNELS:
                    bot.send_message(c_id,
                                     f"*Donation received for*\n"
                                     f"* Telegram bot\n"
                                     f"* REST-API\n"
                                     f"* Blockexplorer\n\n"
                                     f"Did you see the super fast speed?\n\nThank you for *{donation_balance - donation_announced:,.0f} KAS* donated to \n"
                                     f"```kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73```\nI appreciate ♥♥♥",
                                     parse_mode="Markdown")

            donation_announced = donation_balance
        time.sleep(60)


def check_tx_ids():
    resp = requests.get(r"https://api.kaspa.org/info/network")
    start_block = resp.json()["tipHashes"][0]

    i = 0

    while True:
        i += 1

        if TX_CHECKER:
            done_tx_ids = []
            for tx_id, message in TX_CHECKER.items():
                stop_time = time.time()
                resp = requests.get(fr"https://api.kaspa.org/blocks?lowHash={start_block}&includeBlocks=true")
                resp = resp.json()

                # go through blocks and check tx_id
                for block in resp["blocks"]:
                    if tx_id not in done_tx_ids and tx_id in block["verboseData"]["transactionIds"]:
                        block_hash = block["verboseData"]["hash"]

                        old_html = message.html_text
                        new_html = old_html.replace("<code>...</code>",
                                                    f"<a href='https://explorer.kaspa.org/blocks/{block_hash}'>{block_hash[:6]}...{block_hash[-6:]}</a> ✅")

                        new_html = new_html.replace("Sending", "Sent")

                        new_html += f"\nTime needed:\n   {stop_time - message.date:.02f}s"

                        bot.edit_message_text(new_html,
                                              chat_id=message.chat.id,
                                              message_id=message.message_id,
                                              parse_mode="html",
                                              disable_web_page_preview=True)
                        done_tx_ids.append(tx_id)

            for done_tx_id in done_tx_ids:
                print(f"removing {done_tx_id}")
                TX_CHECKER.pop(done_tx_id)

        if i == 40:
            resp = requests.get(r"https://api.kaspa.org/info/network")
            start_block = resp.json()["tipHashes"][0]
            i = 0

        time.sleep(1)


if __name__ == '__main__':
    # send the request to the server and retrive the response
    # with KaspaInterface.kaspa_connection() as client:
    # subscribe utxo change for donation address
    t1 = threading.Thread(target=check_tx_ids)
    t1.start()

    t2 = threading.Thread(target=check_donations)
    t2.start()

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception:
            time.sleep(60)
