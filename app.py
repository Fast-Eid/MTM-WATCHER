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
CHECK_EVERY_SECONDS = 0          # we will control sleep ourselves (per-date + per-cycle)
DAYS_AHEAD_INCLUSIVE = 8         # today + next 8 days = 9 days total
MIN_MILES = 35.0

# How long to stay on each date (so you can see it)
SECONDS_PER_DATE_VIEW = 3

# After finishing all 9 dates, refresh once and wait this long before starting again
SECONDS_BETWEEN_CYCLES = 3

ALLOWED_CITIES = {
    "Milwaukee",
    "Madison",
    "Fitchburg",
    "Middleton",
    "Monona",
    "Sun Prairie",
    "Waunakee",
    "Verona",
    "McFarland",
    "DeForest",
    "Oregon",
    "Stoughton",
    "Cottage Grove",
    "Mount Horeb",
    "Minneapolis",
    "Edina",
    "Bloomington",
    "Burnsville",
    "Rochester",
    "St Paul",
    "Saint Paul",
    "Saint Louis Park",
    "Hudson",
    "Chicago",
}

MARKETPLACE_URL = "https://mtm.mtmlink.net/pe/v1/marketplace?orgId=2291654"

# -----------------------------
# TELEGRAM
# -----------------------------
TELEGRAM_BOT_TOKEN = "8224181562:AAE4R-PQ6FMbRURa9IW--QJF77UNpbQ0CVc"
TELEGRAM_CHAT_ID   = "-5186126864"


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or "PASTE_YOUR_BOT_TOKEN_HERE" in TELEGRAM_BOT_TOKEN:
        print("⚠️ Telegram not configured. Paste TELEGRAM_BOT_TOKEN in the code.")
        return
    if not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram chat id missing.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            print(f"Telegram send failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Telegram send exception: {e}")


# -----------------------------
# HELPERS
# -----------------------------
def clean_address_block(text: str) -> str:
    """
    Removes big gaps/blank lines and normalizes spacing so it doesn't look separated.
    """
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", ln.strip()) for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)

def extract_city(addr_block: str) -> Optional[str]:
    # Works with WI / MN / IL lines like: "Milwaukee Wi 53226" or "Chicago Il 60601"
    lines = [ln.strip() for ln in addr_block.splitlines() if ln.strip()]
    for ln in reversed(lines):
        s = re.sub(r"\s+", " ", ln)
        m = re.search(r"^(.*)\s+(W[Ii]|M[Nn]|I[Ll])\b", s)
        if m:
            city_part = m.group(1).strip()
            # If it has digits, it's probably a street line, not the city line
            if any(ch.isdigit() for ch in city_part):
                continue
            return city_part.title()
    return None

def parse_miles(s: str) -> Optional[float]:
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except:
        return None

def calc_trip_cost(miles: float) -> float:
    """
    $19 loading fee + $1.8 per mile after first 5 miles.
    Example: 10 miles => 19 + (10-5)*1.8 = 28
    """
    base = 19.0
    extra = max(0.0, miles - 5.0)
    return base + extra * 1.8

def trip_key(date_str: str, appt_time: str, pickup_time: str, pickup: str, dropoff: str, miles: float) -> str:
    raw = f"{date_str}|{appt_time}|{pickup_time}|{pickup}|{dropoff}|{miles:.2f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def format_date_mmddyyyy(d: dt.date) -> str:
    return d.strftime("%m/%d/%Y")


# -----------------------------
# LOGIN DETECTION (so it doesn't refresh while you type)
# -----------------------------
def is_login_page(page) -> bool:
    try:
        if page.get_by_label("Email Address", exact=False).count() > 0:
            return True
    except:
        pass
    try:
        if page.locator("input[type='password']").count() > 0 and page.locator("text=SIGN IN").count() > 0:
            return True
    except:
        pass
    return False


# -----------------------------
# PLAYWRIGHT ACTIONS
# -----------------------------
def ensure_outside_service_area_on(page) -> bool:
    """
    DO NOT CHANGE THIS (this is your working version).
    Attempts multiple ways to switch ON the 'Include trips outside of your service area' toggle.
    Returns True if it believes it's ON, else False.
    """
    label_text = "Include trips outside of your service area"

    # Try to find a checkbox near the label
    try:
        checkbox = page.locator(f"input[type='checkbox'][aria-label*='{label_text}']").first
        if checkbox.count() > 0:
            if not checkbox.is_checked():
                checkbox.check(force=True)
                time.sleep(0.3)
            return checkbox.is_checked()
    except:
        pass

    # Try clicking the label text / surrounding card
    try:
        label = page.get_by_text(label_text, exact=False).first
        if label.count() > 0:
            label.click(timeout=2000)
            time.sleep(0.3)
            label.click(timeout=2000)
            time.sleep(0.3)
    except:
        pass

    # Try clicking the toggle element inside the same container as label
    try:
        container = page.get_by_text(label_text, exact=False).locator("..").locator("..")
        switch = container.locator("[role='switch']").first
        if switch.count() > 0:
            aria = switch.get_attribute("aria-checked")
            if aria == "true":
                return True
            switch.click(force=True)
            time.sleep(0.3)
            aria = switch.get_attribute("aria-checked")
            return aria == "true"
    except:
        pass

    # Last resort: click the small toggle "pill"
    try:
        card = page.get_by_text("trips in your service area", exact=False).locator("..").locator("..")
        guess = card.locator(
            "css=div[class*='switch'], div[class*='toggle'], span[class*='switch'], span[class*='toggle']"
        ).first
        if guess.count() > 0:
            guess.click(force=True)
            time.sleep(0.3)
    except:
        pass

    return False


def find_date_input(page):
    loc = page.locator("css=input")
    for i in range(min(loc.count(), 30)):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            v = el.input_value(timeout=500)
            if re.match(r"^\d{2}/\d{2}/\d{4}$", (v or "").strip()):
                return el
        except:
            continue
    return None


def set_filter_date_and_apply(page, date_str: str) -> None:
    date_input = find_date_input(page)
    if not date_input:
        raise RuntimeError("Could not find the date input on the page.")

    date_input.click(force=True)
    date_input.press("Control+A")
    date_input.type(date_str, delay=30)
    page.keyboard.press("Enter")
    time.sleep(0.2)

    page.get_by_role("button", name=re.compile("Apply Filter", re.I)).click(timeout=5000)
    time.sleep(1.0)


def sort_miles_desc(page) -> None:
    try:
        header = page.get_by_text("Trip Miles", exact=False).first
        header.click(timeout=2000)
        time.sleep(0.3)
        header.click(timeout=2000)
        time.sleep(0.3)
    except:
        pass


def read_trips_from_table(page) -> List[dict]:
    try:
        page.wait_for_selector("css=table", timeout=8000)
    except PWTimeoutError:
        pass

    trips = []
    rows = page.locator("css=table tbody tr")
    if rows.count() == 0:
        return trips

    for i in range(min(rows.count(), 200)):
        row = rows.nth(i)
        try:
            cells = row.locator("css=td")
            # Expected columns:
            # 0 = Appt. Time (LEG A)
            # 1 = Pickup Time (LEG B)
            # 2 = Pickup Address
            # 3 = Dropoff Address
            # 4 = Trip Miles
            if cells.count() < 5:
                continue

            appt_time = cells.nth(0).inner_text().strip()
            pickup_time = cells.nth(1).inner_text().strip()

            pickup_text = clean_address_block(cells.nth(2).inner_text().strip())
            dropoff_text = clean_address_block(cells.nth(3).inner_text().strip())

            miles_text = cells.nth(4).inner_text().strip()
            miles = parse_miles(miles_text)
            if miles is None:
                continue

            trips.append({
                "appt_time": appt_time,
                "pickup_time": pickup_time,
                "pickup_text": pickup_text,
                "dropoff_text": dropoff_text,
                "miles": miles,
            })
        except:
            continue

    return trips


# -----------------------------
# MAIN LOOP
# -----------------------------
def main():
    print("✅ MTM watcher starting...")
    send_telegram("✅ MTM watcher started")

    seen = set()

    with sync_playwright() as p:
        user_data_dir = os.path.join(os.path.expanduser("~"), "mtm_playwright_profile")
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=["--start-maximized"],
        )
        page = context.new_page()

        page.goto(MARKETPLACE_URL, wait_until="domcontentloaded")
        time.sleep(1)

        send_telegram("⚠️ If you are not logged in, please login in the opened browser. I will NOT refresh while you are on the login page.")

        while True:
            try:
                # 1) If login page, DO NOT refresh. Just wait until you login.
                if is_login_page(page):
                    print("🔑 Login page detected. Waiting for you to login...")
                    time.sleep(3)
                    continue

                # 2) Enable outside service area (your working code)
                ensure_outside_service_area_on(page)

                # 3) Check dates today -> next 8 days (NO page reload inside this loop)
                today = dt.date.today()

                for offset in range(0, DAYS_AHEAD_INCLUSIVE + 1):
                    ensure_outside_service_area_on(page)

                    d = today + dt.timedelta(days=offset)
                    date_str = format_date_mmddyyyy(d)

                    print(f"\n📅 Switching to date: {date_str}")
                    set_filter_date_and_apply(page, date_str)

                    ensure_outside_service_area_on(page)

                    sort_miles_desc(page)

                    trips = read_trips_from_table(page)
                    print(f"📅 {date_str}: Found {len(trips)} trips (visible page)")

                    for t in trips:
                        miles = t["miles"]
                        if miles <= MIN_MILES:
                            continue

                        pickup_city = extract_city(t["pickup_text"]) or ""
                        dropoff_city = extract_city(t["dropoff_text"]) or ""

                        # notify if pickup OR dropoff is in your allowed cities
                        if (pickup_city not in ALLOWED_CITIES) and (dropoff_city not in ALLOWED_CITIES):
                            continue

                        leg_a = (t["appt_time"] or "").strip()
                        leg_b = (t["pickup_time"] or "").strip()

                        # Normalize dashes / blanks
                        if leg_a in {"", "—", "-"}:
                            leg_a = "N/A"
                        if leg_b in {"", "—", "-"}:
                            leg_b = "N/A"

                        cost = calc_trip_cost(miles)

                        key = trip_key(date_str, leg_a, leg_b, t["pickup_text"], t["dropoff_text"], miles)
                        if key in seen:
                            continue
                        seen.add(key)

                        msg = (
                            f"🚨 New long trip ({miles:.2f} miles) on {date_str}\n"
                            f"Leg A (Appt Time): {leg_a}\n"
                            f"Leg B (Pickup Time): {leg_b}\n"
                            f"Estimated Cost: ${cost:.2f}\n\n"
                            f"Pickup: {pickup_city or 'Unknown'}\n{t['pickup_text']}\n\n"
                            f"Dropoff: {dropoff_city or 'Unknown'}\n{t['dropoff_text']}"
                        )
                        print(msg)
                        send_telegram(msg)

                    time.sleep(SECONDS_PER_DATE_VIEW)

                # 4) After finishing all 9 days, go back to today
                today_str = format_date_mmddyyyy(dt.date.today())
                print(f"\n🔁 Finished 9 days. Returning to today: {today_str}")
                try:
                    set_filter_date_and_apply(page, today_str)
                    ensure_outside_service_area_on(page)
                    sort_miles_desc(page)
                except:
                    pass

                # 5) Refresh ONCE per full cycle
                print("🔄 Refreshing once after full 9-day scan...")
                page.reload(wait_until="domcontentloaded")
                time.sleep(2)

            except KeyboardInterrupt:
                print("Stopping...")
                send_telegram("⏹️ MTM watcher stopped")
                break
            except Exception as e:
                print(f"Error: {e}")
                time.sleep(5)

        context.close()


if __name__ == "__main__":
    main()