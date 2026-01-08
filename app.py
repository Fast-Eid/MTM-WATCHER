import os
import re
import time
import hashlib
import datetime as dt
from typing import Optional, List

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

# -----------------------------
# SETTINGS
# -----------------------------
DAYS_AHEAD_INCLUSIVE = 8
MIN_MILES = 35.0
SECONDS_PER_DATE_VIEW = 3
SECONDS_BETWEEN_CYCLES = 3

ALLOWED_CITIES = {
    "Milwaukee","Madison","Fitchburg","Middleton","Monona","Sun Prairie",
    "Waunakee","Verona","McFarland","DeForest","Oregon","Stoughton",
    "Cottage Grove","Mount Horeb","Minneapolis","Edina","Bloomington",
    "Burnsville","Rochester","St Paul","Saint Paul","Saint Louis Park",
    "Hudson","Chicago",
}

MARKETPLACE_URL = "https://mtm.mtmlink.net/pe/v1/marketplace?orgId=2291654"

# -----------------------------
# TELEGRAM (ENV VARS)
# -----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=15)
    except:
        pass

# -----------------------------
# HELPERS
# -----------------------------
def clean_address_block(text: str) -> str:
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in text.splitlines()]
    return "\n".join([ln for ln in lines if ln])

def extract_city(addr_block: str) -> Optional[str]:
    for ln in reversed(addr_block.splitlines()):
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
    return 19.0 + max(0.0, miles - 5.0) * 1.8

def trip_key(*args) -> str:
    return hashlib.sha256("|".join(map(str, args)).encode()).hexdigest()

def format_date_mmddyyyy(d: dt.date) -> str:
    return d.strftime("%m/%d/%Y")

# -----------------------------
# PLAYWRIGHT HELPERS
# -----------------------------
def ensure_outside_service_area_on(page) -> None:
    try:
        toggle = page.get_by_text(
            "Include trips outside of your service area", exact=False
        ).locator("..").locator("[role='switch']")
        if toggle.count() and toggle.get_attribute("aria-checked") != "true":
            toggle.click(force=True)
            time.sleep(0.3)
    except:
        pass

def find_date_input(page):
    loc = page.locator("input")
    for i in range(min(loc.count(), 30)):
        el = loc.nth(i)
        try:
            if re.match(r"\d{2}/\d{2}/\d{4}", el.input_value()):
                return el
        except:
            pass
    return None

def set_filter_date_and_apply(page, date_str: str):
    el = find_date_input(page)
    if not el:
        return
    el.click(force=True)
    el.press("Control+A")
    el.type(date_str, delay=30)
    page.keyboard.press("Enter")
    page.get_by_role("button", name=re.compile("Apply", re.I)).click(timeout=5000)
    time.sleep(1)

def sort_miles_desc(page):
    try:
        hdr = page.get_by_text("Trip Miles", exact=False)
        hdr.click(); time.sleep(0.2); hdr.click()
    except:
        pass

def read_trips_from_table(page) -> List[dict]:
    trips = []
    try:
        page.wait_for_selector("table", timeout=8000)
    except PWTimeoutError:
        return trips

    rows = page.locator("table tbody tr")
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        if cells.count() < 5:
            continue
        miles = parse_miles(cells.nth(4).inner_text())
        if miles is None:
            continue
        trips.append({
            "appt_time": cells.nth(0).inner_text().strip(),
            "pickup_time": cells.nth(1).inner_text().strip(),
            "pickup_text": clean_address_block(cells.nth(2).inner_text()),
            "dropoff_text": clean_address_block(cells.nth(3).inner_text()),
            "miles": miles,
        })
    return trips

# -----------------------------
# MAIN
# -----------------------------
def main():
    send_telegram("✅ MTM watcher started")
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto(MARKETPLACE_URL, wait_until="domcontentloaded")
        time.sleep(2)

        while True:
            try:
                ensure_outside_service_area_on(page)
                today = dt.date.today()

                for offset in range(DAYS_AHEAD_INCLUSIVE + 1):
                    d = today + dt.timedelta(days=offset)
                    date_str = format_date_mmddyyyy(d)

                    set_filter_date_and_apply(page, date_str)
                    ensure_outside_service_area_on(page)
                    sort_miles_desc(page)

                    for t in read_trips_from_table(page):
                        if t["miles"] <= MIN_MILES:
                            continue

                        pc = extract_city(t["pickup_text"]) or ""
                        dc = extract_city(t["dropoff_text"]) or ""
                        if pc not in ALLOWED_CITIES and dc not in ALLOWED_CITIES:
                            continue

                        key = trip_key(date_str, t["appt_time"], t["pickup_time"],
                                       t["pickup_text"], t["dropoff_text"], t["miles"])
                        if key in seen:
                            continue
                        seen.add(key)

                        cost = calc_trip_cost(t["miles"])
                        send_telegram(
                            f"🚨 {t['miles']:.2f} miles on {date_str}\n"
                            f"Cost: ${cost:.2f}\n\n"
                            f"Pickup:\n{t['pickup_text']}\n\n"
                            f"Dropoff:\n{t['dropoff_text']}"
                        )

                    time.sleep(SECONDS_PER_DATE_VIEW)

                page.reload(wait_until="domcontentloaded")
                time.sleep(SECONDS_BETWEEN_CYCLES)

            except Exception:
                time.sleep(5)

if __name__ == "__main__":
    main()
