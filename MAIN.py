# encoding: utf-8
import logging
import math
import os
import re
import threading
import time
from datetime import datetime

import qrcode
import requests
from PIL import Image
from cachetools.func import ttl_cache
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import HorizontalGradiantColorMask
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMedia

import kaspa_api
import tipping
from constants import TOTAL_COIN_SUPPLY, DEV_MINING_ADDR, DEV_DONATION_ADDR, DEBOUNCE_SECS_PRICE
from helper import hashrate_to_int, percent_of_network, get_mining_rewards, MINING_CALC
from plot import get_image_stream
from tipping import create_new_wallet, WalletCreationError, get_wallet, WalletNotFoundError, username_to_uuid, \
    get_wallet_pw, create_tx, WalletInsufficientBalanceError

logging.basicConfig(format="%(asctime)s::%(name)s::%(module)s::%(levelname)s::%(message)s",
                    level=logging.DEBUG)

logging.getLogger("urllib3").setLevel(logging.WARNING)

logging.info('Starting TGBOT')

DEBOUNCE_CACHE = {}

TX_CHECKER = {}
DELETE_MESSAGES_CACHE = []

bot = TeleBot(os.environ["TELEBOT_TOKEN"], threaded=True)

assert os.environ.get('DONATION_ADDRESS') is not None


def create_qr_code_img(text):
    # taking image which user wants
    # in the QR code center
    logo = Image.open("./res/kaspa-icon.png")

    # taking base width
    basewidth = 100

    # adjust image size
    wpercent = (basewidth / float(logo.size[0]))
    hsize = int((float(logo.size[1]) * float(wpercent)))
    logo = logo.resize((basewidth, hsize), Image.ANTIALIAS)
    QRcode = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        border=7
    )

    # adding URL or text to QRcode
    QRcode.add_data(text)

    # generating QR code
    QRcode.make()
    # adding color to QR code
    QRimg = QRcode.make_image(image_factory=StyledPilImage,
                              module_drawer=RoundedModuleDrawer(),
                              color_mask=HorizontalGradiantColorMask(right_color=(3, 38, 33),
                                                                     left_color=(12, 110, 96))).convert('RGB')
    # set size of QR code
    pos = ((QRimg.size[0] - logo.size[0]) // 2,
           (QRimg.size[1] - logo.size[1]) // 2)
    QRimg.paste(logo, pos)
    # save the QR code generated
    return QRimg


def check_debounce(seconds=60 * 60):
    def wrapper(*args, **kwargs):
        cmd_id = f'{args[0].chat.id}{args[0].text.split("@")[0]}'

        try:
            is_rob = (args[0].from_user.id == 1922783296)
        except AttributeError:
            is_rob = False

        if time_passed := (time.time() - DEBOUNCE_CACHE.get(cmd_id, 0)) > seconds \
                          or args[0].chat.id == -1001208691907 or is_rob:
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


@bot.callback_query_handler(func=lambda call: call.data.startswith('cb_remove_message;'))
def callback_remove_message(call):
    _, request_msg_id, requester_id = call.data.split(";")

    requester_status = bot.get_chat_member(call.message.chat.id, call.from_user.id).status

    if call.from_user.id == requester_id or \
            call.from_user.id == 1922783296 or \
            requester_status in ['administrator', 'creator'] or \
            re.search(fr"@{call.from_user.username} telegram wallet is:",
                      call.message.caption):
        bot.delete_message(call.message.chat.id,
                           call.message.id)

        bot.delete_message(call.message.chat.id,
                           request_msg_id)


@bot.callback_query_handler(func=lambda call: call.data == 'cb_update')
def callback_query_price_update(call):
    try:
        try:
            days = int(re.search("for (\d+)d", call.message.caption)[1])
        except Exception:
            days = 1

        try:
            message = get_price_message(days)
        except Exception:
            logging.exception('Exception at price update')
            return

        if re.findall("\d", message) == re.findall("\d", call.message.caption):
            bot.answer_callback_query(call.id, "Price chart is up to date.", False, cache_time=10)
            return

        try:
            bot.edit_message_media(InputMedia(type='photo',
                                              media=get_image_stream(days),
                                              caption=message,
                                              parse_mode="markdown"),
                                   call.message.chat.id,
                                   call.message.id,
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
    except TimeoutError:
        logging.exception('Exception at hashrate update')
    except Exception:
        logging.exception('Exception at hashrate update')


@bot.message_handler(commands=["donate"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def donate(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"*Please consider a donation for my work on:\n- Kaspa Bot\n- Block explorer\n- REST-API\n\n*"
                     f"`{os.environ['DONATION_ADDRESS']}`\n\n*Thank you!*",
                     parse_mode="Markdown")


@bot.message_handler(commands=["balance"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def balance(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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

        bot.send_message(e.chat.id, f"\nBalance for\n"
                                    f"  {address}\n"
                                    f"{10 * '-'}\n"
                                    f"{balance:,} KAS", parse_mode="Markdown")
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["devfund"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def devfund(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        if e.chat.id == -1001589070884:
            bot.send_message(e.chat.id,
                             f'💰 For price talks please use the price channel 💰\n\nhttps://t.me/KaspaTrading')
        else:
            try:
                try:
                    days = int(re.search(r"(\d+)d", e.text)[1])
                except Exception:
                    days = 1
                msg = get_price_message(days)
                bot.send_photo(e.chat.id,
                               get_image_stream(days),
                               caption=msg,
                               parse_mode="Markdown",
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                        callback_data="cb_update")]]))
            except Exception:
                logging.exception(f'Raised exception')
    except Exception as e:
        logging.exception(str(e))


@ttl_cache(ttl=60)
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
                  f"      {ath_price:0.4f} USD\n" \
                  f" *ATH Date* :\n" \
                  f"      {ath_date:%Y-%m-%d %H:%M}\n" \
                  f" *ATH Change ▼*:\n" \
                  f"      {ath_change_percentage:0.2f} %"

        return message
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["ath"], func=check_debounce(DEBOUNCE_SECS_PRICE))
def ath(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        bot.send_message(e.chat.id, f'<b>For a Kaspa-wallet you can use one of these applications</b>\n\n'
                                    '<b>Web wallet</b>:\n'
                                    '  https://wallet.kaspanet.io/\n'
                                    f'<b>Kaspad (command line wallet)</b>:\n'
                                    f'  <a href="tinyurl.com/ym8sbas7">go to github</a>\n'
                                    '<b>Kaspa for desktop (KDX)</b>:\n'
                                    '  <a href="https://kdx.app/">https://kdx.app/</a>\n'
                                    '<b>Chainge</b>:\n'
                                    '  https://www.chainge.finance/\n'
                                    '<b>Tangem COMING SOON</b>:\n'
                                    '  https://tangem.com/\n'
                                    '<b>Telegram wallet</b>:\n'
                                    '  Talk to @kaspanet_bot with <code>/create_wallet</code> command\n'
                                    '  This wallet is just for fun / demonstration.',
                         parse_mode="html",
                         disable_web_page_preview=True)
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["mining_reward", "mr"], func=ignore_channels(["-1001589070884"]))
def mining_reward(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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
        logging.exception('Exception at /mr')


@bot.message_handler(commands=["id"])
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["chart"])
def chart(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id, f"See *KAS/USDT* chart on *MEXC*:\n"
                                f"    https://www.tradingview.com/chart/?symbol=MEXC%3AKASUSDT\n",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["mcap"], func=check_debounce(60 * 60))
def mcap(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

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
    except Exception:
        logging.exception(f'Raised exception in mcap')


@bot.message_handler(commands=["maxhash"], func=check_debounce(60 * 60))
def max_hashrate(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        max_hashrate = kaspa_api.get_max_hashrate()

        bot.send_message(e.chat.id,
                         f"Max Kaspa Hashrate\n"
                         f"  *{max_hashrate['hashrate']:.2f} THs*\n\n"
                         f"  Date {datetime.fromisoformat(max_hashrate['blockheader']['timestamp']):%Y-%m-%d %H:%M}\n"
                         f"  Block {max_hashrate['blockheader']['hash'][:8]}",
                         parse_mode="Markdown")
    except Exception:
        logging.exception(f'Raised exception in maxhash')


@bot.message_handler(commands=["id"], func=check_only_private)
def id(e):
    bot.send_message(e.chat.id, f"Chat-Id: {e.chat.id}")


@bot.message_handler(commands=["hashrate"], func=check_debounce(60 * 60))
def hashrate(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        hashrate = kaspa_api.get_hashrate()["hashrate"]
        bot.send_message(e.chat.id, f"Current Hashrate: *{hashrate:.02f} TH/s*", parse_mode="Markdown",
                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Update",
                                                                                  callback_data="cb_update_hashrate")]]))
    except Exception as e:
        print(str(e))


@bot.message_handler(commands=["buy", "exchanges"], func=check_debounce(60 * 10))
def buy(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"    💰   *Exchanges*\n"
                     f"----------------------------------\n"
                     f" *CoinEx* [https://www.coinex.com/](https://www.coinex.com/exchange/kas-usdt)\n"
                     f" *Gate io* [https://www.gate.io/de/trade/KAS_USDT]\n"
                     f" *MEXC Global* [https://mexc.com/](https://www.mexc.com/exchange/KAS_USDT)\n"
                     f" *Txbit* [https://txbit.io/]\n"
                     f" *Chainge* [https://www.chainge.finance/](https://www.chainge.finance/info/currencies/KAS)\n"
                     f" *Exibitron* [https://www.exbitron.com/]\n"
                     f" *TradeOgre* [https://www.tradeogre.com](https://tradeogre.com/exchange/USDT-KAS)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["languages"], func=check_debounce(60 * 10))
def buy(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"----------------------------------\n"
                     f" *Kaspa in your language*\n"
                     f" ⚠️Please keep in mind:\n"
                     f"These are *unofficial* groups.\n"
                     f"Use with caution! ⚠️ \n"
                     f"----------------------------------\n"
                     f"🇦🇪 [https://t.me/kaspa_arabic](https://t.me/kaspa_arabic)\n"
                     f"🇨🇳 [https://t.me/kaspa_chinese](https://t.me/kaspa_chinese)\n"
                     f"🇩🇪 [https://t.me/KaspaGerman](https://t.me/KaspaGerman)\n"
                     f"🇪🇸 [https://t.me/kaspaesp](https://t.me/kaspaesp)\n"
                     f"🇫🇮 [https://t.me/kaspa_finland](https://t.me/kaspa_finland)\n"
                     f"🇫🇷 [https://t.me/kasfrench](https://t.me/kasfrench)\n"
                     f"🇮🇳 [https://t.me/kaspaindia](https://t.me/kaspaindia)\n"
                     f"🇮🇩 [https://t.me/Kaspa_Indonesian](https://t.me/Kaspa_Indonesian)\n"
                     f"🇮🇱 [https://t.me/kaspaisrael](https://t.me/kaspaisrael)\n"
                     f"🇮🇹 [https://t.me/kaspaitalia](https://t.me/kaspaitalia)\n"
                     f"🇯🇵 [https://t.me/Kaspa_Japan](https://t.me/Kaspa_Japan)\n"
                     f"🇰🇷 [https://t.me/kaspa_korea](https://t.me/kaspa_korea)\n"
                     f"🇮🇷 [https://t.me/Kaspa_persian](https://t.me/Kaspa_persian)\n"
                     f"🇵🇭 [http://t.me/kaspa_ph](http://t.me/kaspa_ph)\n"
                     f"🇵🇱 [https://t.me/Kaspa_Poland](https://t.me/Kaspa_Poland)\n"
                     f"🇵🇹🇧🇷 [https://t.me/kaspa_portugues](https://t.me/kaspa_portugues)\n"
                     f"🇳🇱 [https://t.me/Kaspa_Dutch](https://t.me/Kaspa_Dutch)\n"
                     f"🇷🇴 [https://t.me/kaspa_ro](https://t.me/kaspa_ro)\n"
                     f"🇷🇺 [https://t.me/kaspa_rus](https://t.me/kaspa_rus)\n"
                     f"🇹🇷 [https://t.me/KaspaTurk](https://t.me/KaspaTurk)\n"
                     f"🇹🇷 NEW [https://t.me/KaspaTr](https://t.me/KaspaTr)\n"
                     f"🇻🇳 [https://t.me/Kaspa_VN](https://t.me/Kaspa_VN)\n"
                     f"🇷🇸 [https://t.me/kaspa_balkan](https://t.me/kaspa_balkan)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["miningpools"], func=check_debounce(60 * 10))
def miningpools(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"----------------------\n"
                     f" *Kaspa mining pools*\n"
                     f"----------------------\n"
                     f"[F2POOL](https://www.f2pool.com/coin/kaspa)\n"
                     f"[ACC-POOL](https://kaspa.acc-pool.pw/)\n"
                     f"[KASPA-POOL](https://kaspa-pool.org/)\n"
                     f"[HEROMINERS](https://kaspa.herominers.com/)\n"
                     f"[KYIV KASPA POOL](https://kaspa.ixbase.info/)\n"
                     f"[WOOLYPOOLY](https://woolypooly.com/en/coin/kas)\n"
                     f"[P1 POOL](https://p1pool.com/)\n"
                     f"[KRYPTEX](https://pool.kryptex.com/en/kas)\n"
                     f"[HASHPOOL](https://hashpool.com/coins/KAS)\n\n"
                     f"[More](https://miningpoolstats.stream/kaspa)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)


@bot.message_handler(commands=["links"], func=check_debounce(60 * 10))
def links(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"----------------------\n"
                     f" *Most important links*\n"
                     f"----------------------\n"
                     f"[Website](https://kaspa.org/)\n"
                     f"[KGI BlockDAG visualizer](https://kgi.kaspad.net/)\n"
                     f"[Kaspa Wiki](https://kaspawiki.net/index.php/Main_Page)\n"
                     f"[Kaspa Faucet](https://faucet.kaspanet.io/)\n"
                     f"[Kaspa Grafana Board](http://kasboard-mainnet.kas.pa/)",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)

    # telegram bot features


@bot.message_handler(commands=["explorers"], func=check_debounce(60 * 10))
def explorers(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     f"----------------------\n"
                     f" *Kaspa explorers*\n"
                     f"----------------------\n"
                     f"[Katnip Explorer](https://katnip.kaspad.net/)\n"
                     f"[Kaspa Block Explorer](https://explorer.kaspa.org/)\n"
                     f"[KAS FYI](https://kas.fyi/)\n",
                     parse_mode="Markdown",
                     disable_web_page_preview=True)

    # telegram bot features


@bot.message_handler(commands=["withdraw"])
def withdraw(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        # sender = e.from_user.username
        sender = f"{e.from_user.id}"
        get_wallet(username_to_uuid(sender))
    except Exception:
        msg = bot.send_message(e.chat.id, f"You do not have a wallet yet. "
                                          f"DM @kaspanet_bot with `/create_wallet` to create a new wallet.",
                               parse_mode="Markdown")
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, e.message_id))
        return

    if not (to_address := re.search(r"kaspa\:[a-zA-Z0-9]{61}", e.text)):
        msg = bot.send_message(e.chat.id, "No valid *kaspa:* address found.\nUse /withdraw <kaspa:addr> <amount>",
                               parse_mode="Markdown")
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, e.message_id))
        return

    if not (amount := re.search(" (\d+([.,]\d+)?)( |$)(KAS)?", e.text, re.IGNORECASE)):
        bot.send_message(e.chat.id, "Valid amount (with unit) missing. Use syntax x.xx KAS")
        return
    else:
        amount = float(amount[1].replace(",", "."))

    inclusive_fee_match = re.search("inclusivefee", e.text, re.IGNORECASE)

    try:
        send_kas_and_log(sender,
                         to_address[0],
                         round(amount * 100000000),
                         e.chat.id,
                         inclusiveFee=inclusive_fee_match is not None)
    except tipping.WalletInsufficientBalanceError as ex:
        bot.send_message(e.chat.id, f"{ex}")


@bot.message_handler(commands=["telegram_wallet"])
def tgwallet(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    bot.send_message(e.chat.id,
                     """<b>To create a wallet DM @kaspanet_bot with the command <code>/create_wallet</code>.</b>

Then you can use:
<b>  /wallet_info</b> - Shows either your or the replied user's wallet information.
<b>  /tip 1.23 KAS</b> - reply to someone's message and send him/her a tip.
<b>  /withdraw kaspa:... 1.23 KAS</b> - Withdraw KAS from your Telegram wallet to another address
"""
                     "\n\n♥ Please consider a donation for my free work to <code>kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73</code>. Thank you - Rob aka lAmeR",
                     parse_mode="html")


@bot.message_handler(commands=["tip"])
def send_kas(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    recipient_username = ""
    sender_name = e.from_user.full_name or ""
    try:
        # sender = e.from_user.username
        sender = f"{e.from_user.id}"
    except Exception:
        msg = bot.send_message(e.chat.id, f"You do not have a wallet yet. "
                                          f"DM @kaspanet_bot with `/create_wallet` to create a new wallet.",
                               parse_mode="Markdown")

        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, e.message_id))

        return

    try:
        # reply to a message?
        # recipient = e.reply_to_message.from_user.username
        recipient = f"{e.reply_to_message.from_user.id}"
        recipient_username = e.reply_to_message.from_user.username
    except Exception:
        # check 2nd argument
        # try:
        #     recipient = re.search("@[^ ]+", e.text)[0]
        #     recipient_username = recipient.lstrip("@")
        # except:
        msg = bot.send_message(e.chat.id, "Could not determine a recipient!\n"
                                          "Reply to someone's message and write:\n `/tip X.XX KAS`.",
                               parse_mode="Markdown")
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, e.message_id))
        return

    try:
        # kaspa bot? use donation address
        if recipient == '5464545065':
            recipient = "kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73"
        else:
            recipient = get_wallet(username_to_uuid(recipient.lstrip("@")))["publicAddress"]
    except Exception:
        msg = bot.send_message(e.chat.id,
                               f"Recipient <b>{recipient_username or recipient}</b> does not have a wallet yet.\n"
                               f"DM @kaspanet_bot with /create_wallet to create a new wallet.",
                               parse_mode="html")
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, e.message_id))
        return

    try:
        amount = float(re.search(" (\d+([.,]\d+)?)( |$)(KAS)?", e.text, re.IGNORECASE)[1].replace(",", "."))
    except Exception:
        msg = bot.send_message(e.chat.id, "Can't parse the amount.\n"
                                          "Reply to someone's message and write:\n `/tip X.XX KAS`.",
                               parse_mode="Markdown")
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, e.message_id))
        return

    if amount < 0.00001:
        msg = bot.send_message(e.chat.id, "Minimum amount is 0.00001 KAS",
                               parse_mode="Markdown")
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 5, e.chat.id, e.message_id))
        return

    try:
        send_kas_and_log(sender,
                         recipient,
                         round(amount * 100000000),
                         e.chat.id,
                         recipient_username=recipient_username,
                         sender_name=sender_name)
    except WalletInsufficientBalanceError:
        bot.send_message(e.chat.id, f"You don't have enough KAS to finish this transaction.")


@bot.message_handler(commands=["create_wallet"])
def create_wallet(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    if e.chat.type != "private":
        msg = bot.send_message(e.chat.id, "Please use a direct message (DM) to @kaspanet_bot to create a new wallet.")

        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 3, e.chat.id, e.message_id))
        return

    try:
        user_id = e.from_user.id
        wallet = create_new_wallet(get_wallet_pw(f"{user_id}"),
                                   username_to_uuid(f"{user_id}"))
        seed = wallet["mnemonic"]
        bot.send_message(e.chat.id, f"<b>Welcome to Kaspa Telegram wallet!</b>\n"
                                    f"Wallet creation was successful. Your kaspa address is:"
                                    f"\n<code>{wallet['publicAddress']}</code>"
                                    f"\n\nYour seed phrase is:\n"
                                    f"<code>{seed}</code>\n\n"
                                    f"To use your wallet or get information, use the following commands:\n"
                                    "  /wallet_info - Shows either your or the replied user's wallet information.\n"
                                    "  /tip 1.23 KAS - reply to someone's message and send him/her a tip.\n"
                                    "  /withdraw kaspa:... 1.23 KAS - Withdraw KAS from your Telegram wallet to another address"
                                    f"\n\nPlease be advised that neither the dev nor any of the kaspa community is"
                                    f" responsible for any issues or losses that may occur with the use of this wallet."
                                    f" Use at your own risk.",
                         parse_mode="html")

        try:
            send_kas_and_log("xemofaucet", wallet["publicAddress"], 100000000, e.chat.id)
            bot.send_message(e.chat.id, "One Kaspa member gifted you 1 KAS for demo issues.")
        except:
            logging.exception("Kaspa start tip didn't work.")


    except WalletCreationError:
        bot.send_message(e.chat.id, "Wallet already created. Use /wallet_info")


@bot.message_handler(commands=["kaspacity"])
def kaspacity(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    with open("./res/kaspacity.jpg", "rb") as f:
        bot.send_photo(e.chat.id,
                       f,
                       f'''Do you want to play around with live KASPA transactions or just show it to a friend?

Try out this demonstrator! Head to the office, enter the building and get a FREE $KAS! After that, you can spend it in the restaurant or the shopping mall..

So is Kaspa usable in the real world? I would say so, yes!

https://kaspagames.org/kaspacity/

Thanks to everyone who helped with the game.

Have fun! 
Rob 🚀 lAmeR
''')


@bot.message_handler(commands=["wallet_info", "wi"])
def check_wallet(e):
    if e.chat.type != "private":
        add_donation_channel(e.chat.id)

    try:
        user_id = f"{e.reply_to_message.from_user.id}" if "reply_to_message" in e.json else f"{e.from_user.id}"
        username = f"{e.reply_to_message.from_user.username}" if "reply_to_message" in e.json else f"{e.from_user.username}"

        wallet = get_wallet(username_to_uuid(f"{user_id}")
                            # ,get_wallet_pw(username)
                            )

        show_button = InlineKeyboardMarkup([[InlineKeyboardButton("Show in explorer",
                                                                  url=f"https://explorer.kaspa.org/addresses/{wallet['publicAddress']}")],
                                            [InlineKeyboardButton("Remove message",
                                                                  callback_data=f"cb_remove_message;{e.message_id};{e.from_user.id}")]
                                            ])

        wallet_balance = kaspa_api.get_balance(wallet["publicAddress"])["balance"] / 100000000

        wallet_balance = f"{wallet_balance:.8f}"

        if "." in wallet_balance:
            wallet_balance = wallet_balance.rstrip("0")
            wallet_balance = wallet_balance.rstrip(".")

        img_bytes = create_qr_code_img(wallet["publicAddress"])

        msg = bot.send_photo(e.chat.id, photo=img_bytes,
                             caption=f'@{username} telegram wallet is:\n<code>{wallet["publicAddress"]}</code>\nBalance:\n  <b>{wallet_balance} KAS</b>',
                             parse_mode="html",
                             reply_markup=show_button)

    except WalletNotFoundError:
        msg = bot.send_message(e.chat.id,
                               f'No KAS wallet found. Use <code>/create_wallet</code> via DM to to @kaspanet_bot to create a wallet.',
                               parse_mode="html")
        DELETE_MESSAGES_CACHE.append((time.time() + 180, e.chat.id, msg.id))
        DELETE_MESSAGES_CACHE.append((time.time() + 180, e.chat.id, e.message_id))


def progress_bar(perc):
    green_boxes = math.floor(perc / 100 * 8)
    return green_boxes * "🟩" + "⬜" * (8 - green_boxes)


def send_kas_and_log(sender_username, to_address, amount, chat_id,
                     recipient_username=None,
                     inclusiveFee=False,
                     sender_name=""):
    tx_id = create_tx(username_to_uuid(sender_username),
                      get_wallet_pw(sender_username),
                      to_address,
                      amount,
                      inclusiveFee=inclusiveFee)

    msg_amount = f"{amount / 100000000:.8f}"

    if "." in msg_amount:
        msg_amount = msg_amount.rstrip("0")
        msg_amount = msg_amount.rstrip(".")

    message = bot.send_message(chat_id,
                               f"{sender_name} sending <b>{msg_amount} KAS</b> to \n"
                               f"{f'@{recipient_username}' if recipient_username else ''}"
                               f"\n   <a href='https://explorer.kaspa.org/addresses/{to_address}'>{to_address[:16]}...{to_address[-10:]}</a>\n\n"
                               f"Value\n"
                               f"  <b>{amount / 100000000 * _get_kas_price():.02f} USD</b>\n"
                               f"TX-ID\n"
                               f"   <a href='https://explorer.kaspa.org/txs/{tx_id}'>{tx_id[:6]}...{tx_id[-6:]}</a> ✅\n"
                               f"Block-ID\n"
                               f"   ⏳ in progress",
                               parse_mode="html",
                               disable_web_page_preview=True)

    TX_CHECKER[tx_id] = (time.time(), message)


def get_price_message(days):
    coin = "kaspa"
    coin_info = get_coin_info()

    if not coin_info:
        return

    try:
        symbol = coin_info['tickers'][0]['base']
    except (ValueError, IndexError, KeyError):
        symbol = coin.upper()

    price_change_1h = coin_info['market_data']['price_change_percentage_1h_in_currency'].get('usd', 0)
    price_change_24h = coin_info['market_data']['price_change_percentage_24h_in_currency'].get('usd', 0)
    price_change_7d = coin_info['market_data']['price_change_percentage_7d_in_currency'].get('usd', 0)

    rank = coin_info["market_data"]["market_cap_rank"]
    volume = coin_info["market_data"]["total_volume"]["usd"]

    message = f"📈 Price Update for {days}d 📈\n" \
              f"  *{symbol} - {coin_info['name']} [Rank {rank}]*\n" \
              f"{'-' * 40}\n" \
              f"Current price : \n      *{round(coin_info['market_data']['current_price']['usd'], 6):0.4f} USD*\n\n" \
              f"```\n 1h {'▲' if price_change_1h > 0 else '▼'}  : {price_change_1h:.02f} %\n" \
              f"24h {'▲' if price_change_24h > 0 else '▼'}  : {price_change_24h:.02f} %\n" \
              f" 7d {'▲' if price_change_7d > 0 else '▼'}  : {price_change_7d:.02f} %\n" \
              f"Volume : {volume:,.0f} USD\n```" \
        # f"MCAP  : {coin_info['market_data']['market_cap'].get('usd', 0):,} USD\n" \
    # f"FDV   : {coin_info['market_data']['fully_diluted_valuation'].get('usd', 0):,} USD\n" \
    # f"Circ. Supply:\n  {coin_info['market_data']['circulating_supply'] or 0:,}\n" \
    # f"Total Supply:\n  {coin_info['market_data']['total_supply'] or 0:,}\n```"

    return message


@ttl_cache(ttl=60)
def _get_kas_price():
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/simple/price",
                            params={"ids": "kaspa",
                                    "vs_currencies": "usd"})
        if resp.status_code == 200:
            return resp.json()["kaspa"]["usd"]
    except Exception as e:
        logging.exception(str(e))


DONATION_CHANNELS = [-1001589070884, -1001205240510, -1001778657727, -1001208691907, -1001695274086, -1001831752155,
                     -1001707714192, -1001629453639, -1001593411704, -1001493667078, -1001602068748, -1001663502725,
                     -1001539492361, -1001670476757, -1001804214136, -1001877039289, -1001688255696]

IGNORE_CHANNELS = [-1001516174742]


def add_donation_channel(chat_id):
    global DONATION_CHANNELS
    if chat_id not in DONATION_CHANNELS:
        DONATION_CHANNELS.append(chat_id)


def check_donations():
    donation_announced = 0
    while True:
        try:
            donation_addr = "kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73"
            try:
                donation_balance = kaspa_api.get_balance(donation_addr)["balance"] / 100000000
            except Exception:
                time.sleep(1)
                continue

            if donation_balance != donation_announced:
                if donation_announced:
                    for c_id in DONATION_CHANNELS:
                        if c_id in IGNORE_CHANNELS:
                            continue

                        try:
                            if (donation_balance - donation_announced) >= 1000:
                                bot.send_message(c_id,
                                                 f"<b>Donation received for</b>\n"
                                                 f"* Telegram bot\n"
                                                 f"* REST-API\n"
                                                 f"* Blockexplorer\n"
                                                 f"* Telegram wallet feature\n\n"
                                                 f"Did you see the super fast speed?\n\nThank you for <b>{donation_balance - donation_announced:,.0f} KAS</b> donated to \n"
                                                 f"<code>kaspa:qqkqkzjvr7zwxxmjxjkmxxdwju9kjs6e9u82uh59z07vgaks6gg62v8707g73</code>\nI appreciate ♥♥♥",
                                                 parse_mode="html")
                        except Exception:
                            pass

                donation_announced = donation_balance
        except Exception:
            logging.exception('Error checking donation address')

        time.sleep(5)


def check_del_messages():
    while True:
        for ts, chat_id, msg_id in list(DELETE_MESSAGES_CACHE):
            if time.time() >= ts:
                DELETE_MESSAGES_CACHE.remove((ts, chat_id, msg_id))
                try:
                    bot.delete_message(chat_id, msg_id)
                except Exception:
                    logging.exception('Can not remove this message. Sorry')

        time.sleep(2)


def check_tx_ids():
    resp = requests.get(r"https://api.kaspa.org/info/network")
    start_block = resp.json()["tipHashes"][0]

    i = 0

    logging.info("Starting Thread: check_tx_ids")

    while True:
        i += 1

        try:

            if TX_CHECKER:
                for tx_id, tx_object in dict(TX_CHECKER).items():
                    start_time, message = tx_object
                    stop_time = time.time()
                    try:
                        resp = requests.get(fr"https://api.kaspa.org/blocks?lowHash={start_block}&includeBlocks=true")
                        resp = resp.json()
                    except Exception:
                        time.sleep(0.3)
                        continue

                    # go through blocks and check tx_id
                    for block in resp["blocks"]:
                        if tx_id in TX_CHECKER and tx_id in block["verboseData"]["transactionIds"]:
                            block_hash = block["verboseData"]["hash"]

                            old_html = message.html_text
                            new_html = old_html.replace("⏳ in progress",
                                                        f"<a href='https://explorer.kaspa.org/blocks/{block_hash}'>{block_hash[:6]}...{block_hash[-6:]}</a> ✅")

                            new_html = new_html.replace("Sending", "Sent")

                            new_html += f"\nTime needed:\n   ~ {stop_time - start_time:.02f}s"

                            bot.edit_message_text(new_html,
                                                  chat_id=message.chat.id,
                                                  message_id=message.message_id,
                                                  parse_mode="html",
                                                  disable_web_page_preview=True,
                                                  reply_markup=InlineKeyboardMarkup(
                                                      [[InlineKeyboardButton("Show TX",
                                                                             url=f"https://explorer.kaspa.org/txs/{tx_id}")],
                                                       [InlineKeyboardButton("Show block",
                                                                             url=f"https://explorer.kaspa.org/blocks/{block_hash}")]])
                                                  )

                            print(f"removing {tx_id}")
                            TX_CHECKER.pop(tx_id)

            if i >= 80:
                try:
                    resp = requests.get(r"https://api.kaspa.org/info/network")
                    start_block = resp.json()["tipHashes"][0]
                except Exception:
                    continue

                i = 0

            time.sleep(0.5)
        except Exception:
            logging.exception('Error in TX-checker-thread')


def check_exchange_pool():
    donation_announced = 0
    while True:
        donation_addr = "kaspa:qpx4nyz06zk7j5mvfk98w69ayzt3g0j46c0qr4hkya509e9e69dn65h9q8n9z"
        try:
            donation_balance = kaspa_api.get_balance(donation_addr)["balance"] / 100000000
        except Exception:
            time.sleep(1)
            continue

        if donation_balance != donation_announced:
            if donation_announced:
                if donation_balance - donation_announced >= 5000:
                    for c_id in DONATION_CHANNELS:
                        bot.send_message(c_id,  # -1001589070884,
                                         f"[Exchange funding pool](https://explorer.kaspa.org/addresses/kaspa:qpx4nyz06zk7j5mvfk98w69ayzt3g0j46c0qr4hkya509e9e69dn65h9q8n9z)\n"
                                         f" We received a new donation of\n\n"
                                         f" *{donation_balance - donation_announced:,.0f} KAS* for the new exchange"
                                         f"\n\n♥♥♥",
                                         parse_mode="Markdown")

            donation_announced = donation_balance
        time.sleep(60)


@bot.message_handler(commands=["version"])
def version(e):
    bot.send_message(e.chat.id,
                     f"*Kaspa Telegram Bot version: {os.getenv('VERSION', 'x.x.x')}*",
                     parse_mode="Markdown")


@bot.message_handler(commands=["channels"])
def channels(e):
    global DONATION_CHANNELS
    if e.chat.id == 1922783296:
        bot.send_message(e.chat.id,
                         f"{DONATION_CHANNELS}",
                         parse_mode="Markdown")


if __name__ == '__main__':
    # send the request to the server and retrive the response
    # with KaspaInterface.kaspa_connection() as client:
    # subscribe utxo change for donation address
    t1 = threading.Thread(target=check_tx_ids, daemon=False)
    t1.start()

    t2 = threading.Thread(target=check_donations, daemon=False)
    t2.start()

    t3 = threading.Thread(target=check_del_messages, daemon=False)
    t3.start()

    t4 = threading.Thread(target=check_exchange_pool, daemon=False)
    t4.start()

    while True:
        try:
            bot.polling(none_stop=True)
        except Exception:
            logging.exception("Something happenend")
            time.sleep(10)
