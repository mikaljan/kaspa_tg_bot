import io
import logging
import threading
import time
from datetime import datetime

import aiohttp
import pandas
import plotly.express as px
from aiocache import cached
from aiohttp import ContentTypeError

STARTUP_DEBOUNCE = True


def stop_debounce():
    global STARTUP_DEBOUNCE
    time.sleep(5)
    STARTUP_DEBOUNCE = False


def startup_debounce(func):
    global STARTUP_DEBOUNCE

    async def inner(*args, **kwargs):
        if not STARTUP_DEBOUNCE:
            return await func(*args, **kwargs)

    return inner


threading.Thread(target=stop_debounce).start()

CACHE = None


@startup_debounce
@cached(ttl=120)
async def request_market_chart(days=1):
    global CACHE
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.coingecko.com/api/v3/coins/kaspa/market_chart?vs_currency=usd&days={days}",
                               timeout=10) as resp:
            try:
                CACHE = await resp.json()
            except ContentTypeError:
                if not CACHE:
                    logging.exception('Error reading market chart.')
                    raise
    return CACHE


async def get_image_stream(days=1):
    d = await request_market_chart(days)
    data = [(datetime.utcfromtimestamp(x[0] / 1000), x[1]) for x in d["prices"]]
    a = pandas.DataFrame(data, columns=["Time", "USD"])

    basic_plot = px.line(
        a,
        x="Time",
        y="USD",
        template="plotly_dark"
    )
    label_days = "24h" if days == 1 else f"{days}d"
    basic_plot.update_xaxes(title_font_size=15)
    basic_plot.update_yaxes(title_font_size=15)
    basic_plot.update_layout(
        title=f"KAS / USD chart - {label_days}",
        font={
            "size": 15,
            "color": "#F6F5F4"
        }
    )
    basic_plot.update_traces(line={
        "color": "#14F1D9"
    })

    # basic_plot.update_xaxes(linecolor="#ff0000")
    # basic_plot.update_layout(linecolor="#ff0000")

    # [datetime.fromtimestamp(x[0] / 1000) for x in d["prices"]],
    # symbol = "KAS"
    # y = [x[1] for x in d["prices"]]

    f = io.BytesIO()

    basic_plot.write_image(f)

    f.seek(0)

    return f
