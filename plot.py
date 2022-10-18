import io

import plotly_express as px
import requests
from datetime import datetime

import pandas

def get_image_stream():
    d = requests.get("https://api.coingecko.com/api/v3/coins/kaspa/market_chart?vs_currency=usd&days=1").json()

    data = [(datetime.utcfromtimestamp(x[0] / 1000), x[1]) for x in d["prices"]]
    a = pandas.DataFrame(data, columns=["Time", "USD"])

    basic_plot = px.line(
        a,
        x="Time",
        y="USD",
        template="plotly_dark"
    )

    basic_plot.update_xaxes(title_font_size=15)
    basic_plot.update_yaxes(title_font_size=15)
    basic_plot.update_layout(
        title="KAS / USD chart - 24h",
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
