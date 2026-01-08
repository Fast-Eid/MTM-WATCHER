import os
import re
import time
import hashlib
import datetime as dt
from typing import Optional, List

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# =============================
# SETTINGS
# =============================
DAYS_AHEAD_INCLUSIVE = 8
MIN_MILES = 35.0
SECONDS_PER_DATE_VIEW = 3

ALLOWED_CITIES = {
    "Milwaukee","Madison","Fitchburg","Middleton","Monona","Sun Prairie",
    "Waunakee","Verona","McFarland","DeForest","Oregon","Stoughton",
    "Cottage Grove","Mount Horeb",
    "Minneapolis","Edina","Bloomington","Burnsville","Rochester",
    "St Paul","Saint Paul","Saint Louis Park","Hudson","Chicago",
}

MARKETPLACE_URL = "https://mtm.mtmlink.net/pe/v1/marketplace?orgId=2291654"

# =============================
# TELEGRAM
# =============================
TELEGRAM_BOT_TOKEN = os.getenv("8224181562:AAE4R-PQ6FMbRURa9IW--QJF77UNpbQ0CVc")
TELEGRAM_CHAT_ID = os.getenv("-5186126864")


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except:
        pass


# =============================
# HELPERS
# =============================
def clean_address_block(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)

def extract_city(addr_block: str) -> Optional[str]:
    for ln in addr_block.splitlines():
        m = re.search(r"^(.*)\s+(WI|MN|IL)\b", ln, re.I)
        if m and not any(ch.isdigit() for ch in m.group(1)):
            return m.group(1).title()
    return None

def parse_miles(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").strip())
    except:
        return None

def calc_trip_cost(miles: float) -> float:
    return 19 + max(0, miles - 5) * 1.8

def trip_key(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()

def format_date_mmddyyyy(d: dt.date) -> str:
    return d.strftime("%m/%d/%Y")


# =============================
# PLAYWRIGHT FUNCTIONS
# =============================
def ensure_outside_service_area_on(page):
    try:
        toggle = page.locator("[role='switch']").first
        if toggle.get_attribute("aria-checked") != "true":
            toggle.click(force=True)
            time.sleep(0.5)
    except:
        pass


def find_date_input(page):
    for i in range(page.locator("input").count()):
        el = page.locator("input").nth(i)
        try:
            if re.match(r"\d{2}/\d{2}/\d{4}", el.input_value()):
                return el
        except:
            pass
    return None


def set_filter_date(page, date_str):
    inp = find_date_input(page)
    if not inp:
        return
    inp.click(force=True)
    inp.fill(date_str)
    page.keyboard.press("Enter")
    time.sleep(1)


def read_trips(page) -> List[dict]:
    trips = []
    rows = page.locator("table tbody tr")
    for i in range(min(rows.count(), 200)):
        tds = rows.nth(i).locator("td")
        if tds.count() < 5:
            continue
        miles = parse_miles(tds.nth(4).inner_text())
        if miles is None:
            continue
        trips.append({
            "appt": tds.nth(0).inner_text().strip(),
            "pickup_time": tds.nth(1).inner_text().strip(),
            "pickup": clean_address_block(tds.nth(2).inner_text()),
            "dropoff": clean_address_block(tds.nth(3).inner_text()),
            "miles": miles,
        })
    return trips


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
        page = context.new_page()

        page.goto(MARKETPLACE_URL, timeout=60000)
        time.sleep(5)

        while True:
            try:
                ensure_outside_service_area_on(page)
                today = dt.date.today()

                for offset in range(DAYS_AHEAD_INCLUSIVE + 1):
                    d = today + dt.timedelta(days=offset)
                    date_str = format_date_mmddyyyy(d)

                    set_filter_date(page, date_str)
                    ensure_outside_service_area_on(page)

                    trips = read_trips(page)

                    for t in trips:
                        if t["miles"] <= MIN_MILES:
                            continue

                        pc = extract_city(t["pickup"]) or ""
                        dc = extract_city(t["dropoff"]) or ""

                        if pc not in ALLOWED_CITIES and dc not in ALLOWED_CITIES:
                            continue

                        key = trip_key(date_str, t["appt"], t["pickup_time"], t["pickup"], t["dropoff"], t["miles"])
                        if key in seen:
                            continue
                        seen.add(key)

                        cost = calc_trip_cost(t["miles"])
                        msg = (
                            f"🚨 {t['miles']:.1f} miles | {date_str}\n"
                            f"Appt: {t['appt']}\nPickup: {t['pickup_time']}\n"
                            f"Cost ≈ ${cost:.2f}\n\n"
                            f"Pickup:\n{t['pickup']}\n\nDropoff:\n{t['dropoff']}"
                        )
                        send_telegram(msg)

                    time.sleep(SECONDS_PER_DATE_VIEW)

                page.reload()
                time.sleep(5)

            except Exception:
                time.sleep(10)


if __name__ == "__main__":
    main()
