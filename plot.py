import io

import plotly.express as px
import requests
from datetime import datetime

import pandas
from cachetools.func import ttl_cache

@ttl_cache(ttl=30)
def request_market_chart(days=1):
    print("requesting chart")
    return requests.get(f"https://api.coingecko.com/api/v3/coins/kaspa/market_chart?vs_currency=usd&days={days}").json()

def get_image_stream(days=1):

    d = request_market_chart(days)

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

if __name__ == '__main__':

    print(get_image_stream())