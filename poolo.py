import requests

h = {
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Domain": "app",
    "Host": "api.poolo.io",
    "If-None-Match": 'W/"15a8-/QyElDsLF5Q4wnAxTAVyE5NRj9o"',
    "Origin": "https://app.poolo.io",
    "Referer": "https://app.poolo.io/",
    "sec-ch-ua": '"Chromium";v="104", " Not A;Brand";v="99", "Google Chrome";v="104"',
    "sec-ch-ua-mobile": '?0',
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
    "x-sender-address": "Address undefined"}


def get_data(id):
    return requests.get(f"https://api.poolo.io/app/pool/{id}/sync",
                     headers=h, allow_redirects=True).json()