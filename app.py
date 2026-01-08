import os
import re
import time
import json
import hashlib
import datetime as dt
from typing import Optional, List

import requests
from playwright.sync_api import sync_playwright

# =============================
# SETTINGS
# =============================
DAYS_AHEAD_INCLUSIVE = 8
MIN_MILES = 35.0
SECONDS_PER_DATE_VIEW = 3
SECONDS_BETWEEN_CYCLES = 3

MARKETPLACE_URL = "https://mtm.mtmlink.net/pe/v1/marketplace?orgId=2291654"

ALLOWED_CITIES = {
    "Milwaukee","Madison","Fitchburg","Middleton","Monona","Sun Prairie",
    "Waunakee","Verona","McFarland","DeForest","Oregon","Stoughton",
    "Cottage Grove","Mount Horeb","Minneapolis","Edina","Bloomington",
    "Burnsville","Rochester","St Paul","Saint Paul","Saint Louis Park",
    "Hudson","Chicago",
}

# =============================
# TELEGRAM
# =============================
TELEGRAM_BOT_TOKEN = os.environ.get("8224181562:AAE4R-PQ6FMbRURa9IW--QJF77UNpbQ0CVc")
TELEGRAM_CHAT_ID = os.environ.get("-5186126864")

def send_telegram(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
        timeout=10
    )

# =============================
# HELPERS
# =============================
def clean(text):
    return "\n".join(x.strip() for x in text.splitlines() if x.strip())

def extract_city(text):
    for ln in reversed(text.splitlines()):
        m = re.search(r"(.*)\s+(WI|MN|IL)\b", ln, re.I)
        if m:
            c = m.group(1).strip()
            if not any(x.isdigit() for x in c):
                return c.title()
    return ""

def trip_key(*args):
    return hashlib.sha256("|".join(map(str,args)).encode()).hexdigest()

def fmt_date(d):
    return d.strftime("%m/%d/%Y")

# =============================
# MAIN
# =============================
def main():
    send_telegram("✅ MTM watcher started")

    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )

        context = browser.new_context()

        # LOAD COOKIES
        if os.path.exists("cookies.json"):
            with open("cookies.json") as f:
                context.add_cookies(json.load(f))

        page = context.new_page()
        page.goto(MARKETPLACE_URL, timeout=60000)
        time.sleep(3)

        while True:
            today = dt.date.today()

            for i in range(DAYS_AHEAD_INCLUSIVE + 1):
                date = today + dt.timedelta(days=i)
                date_str = fmt_date(date)

                page.fill("input", date_str)
                page.keyboard.press("Enter")
                time.sleep(2)

                rows = page.locator("table tbody tr")
                for r in range(rows.count()):
                    tds = rows.nth(r).locator("td")
                    if tds.count() < 5:
                        continue

                    miles = float(tds.nth(4).inner_text().replace(",",""))
                    if miles < MIN_MILES:
                        continue

                    pickup = clean(tds.nth(2).inner_text())
                    dropoff = clean(tds.nth(3).inner_text())

                    pc = extract_city(pickup)
                    dc = extract_city(dropoff)

                    if pc not in ALLOWED_CITIES and dc not in ALLOWED_CITIES:
                        continue

                    key = trip_key(date_str, pickup, dropoff, miles)
                    if key in seen:
                        continue
                    seen.add(key)

                    send_telegram(
                        f"🚨 {miles} miles on {date_str}\n\n"
                        f"Pickup:\n{pickup}\n\nDropoff:\n{dropoff}"
                    )

                time.sleep(SECONDS_PER_DATE_VIEW)

            page.reload()
            time.sleep(SECONDS_BETWEEN_CYCLES)

if __name__ == "__main__":
    main()
