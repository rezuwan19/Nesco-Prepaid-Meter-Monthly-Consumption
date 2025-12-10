import json
import os
import time
import requests
import discord
import asyncio
import nest_asyncio

import config

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Apply nest_asyncio to help with closed event loop issues in CI environments
nest_asyncio.apply()


# ------------------------------
# Constants loaded from config
# ------------------------------
TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
DISCORD_BOT_TOKEN = config.DISCORD_BOT_TOKEN
DISCORD_CHANNEL_ID = int(config.DISCORD_CHANNEL_ID) if str(config.DISCORD_CHANNEL_ID).isdigit() else None
LOG_FILE = config.LOG_FILE
CONSUMER_ID = config.CONSUMER_ID
CONSUMER_NAME = config.CONSUMER_NAME
REQUESTS_TIMEOUT = getattr(config, "REQUESTS_TIMEOUT", 10)


# ----------------------------------------------------
# Load log file
# ----------------------------------------------------
def load_log():
    if not os.path.exists(LOG_FILE):
        return {"rows": []}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Failed to read log file:", e)
        return {"rows": []}


# ----------------------------------------------------
# Save log file
# ----------------------------------------------------
def save_log(data):
    try:
        print(f"Saving log file to: {LOG_FILE}")
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print("Log saved successfully.")
    except Exception as e:
        print("Failed to save log file:", e)


# ----------------------------------------------------
# Telegram Sender
# ----------------------------------------------------
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        resp = requests.post(url, json=payload, timeout=REQUESTS_TIMEOUT)
        resp.raise_for_status()
        print("Telegram sent OK.")
    except Exception as e:
        print("Telegram Error:", e)


# ----------------------------------------------------
# Discord Sender (Bot) - uses bot token and channel ID
# ----------------------------------------------------
def send_discord_safe(message):
    """
    Send a message to a Discord channel using Bot token via REST API.
    This avoids starting a websocket client and event-loop issues in CI.
    """
    if not DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID is None:
        print("Discord credentials missing; skipping Discord send.")
        return

    try:
        # Ensure channel id is a string
        channel_id = str(DISCORD_CHANNEL_ID)

        # Build authorization header: prefix with "Bot " if needed
        auth_token = DISCORD_BOT_TOKEN
        if not auth_token.lower().startswith("bot "):
            auth_header = f"Bot {auth_token}"
        else:
            auth_header = auth_token

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": auth_header,
            "User-Agent": "nesco-notifier/1.0",
            "Content-Type": "application/json",
        }
        payload = {"content": message}

        resp = requests.post(url, headers=headers, json=payload, timeout=REQUESTS_TIMEOUT)
        if resp.status_code == 200 or resp.status_code == 201:
            print("Discord message sent (REST).")
        elif resp.status_code == 401:
            print("Discord REST error: Unauthorized (check token).", resp.status_code, resp.text)
        elif resp.status_code == 403:
            print("Discord REST error: Forbidden (bot missing permissions / not in server).", resp.status_code, resp.text)
        elif resp.status_code == 404:
            print("Discord REST error: Channel not found.", resp.status_code, resp.text)
        else:
            # handle rate-limit (429) or other non-OK statuses
            print(f"Discord REST returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print("Discord REST send failed:", e)

# ----------------------------------------------------
# Scrape NESCO Website using Selenium
# ----------------------------------------------------
def scrape_nesco():
    chrome_options = Options()
    # Use new headless if available; keep compatibility
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--remote-debugging-port=9222")
    # optional: reduce logging
    chrome_options.add_argument("--log-level=3")

    try:
        service = webdriver.chrome.service.Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except Exception as e:
        print("Failed to start ChromeDriver:", e)
        raise

    try:
        driver.set_page_load_timeout(30)
        driver.get("https://customer.nesco.gov.bd/pre/panel")
        wait = WebDriverWait(driver, 15)

        # Wait for the consumer input field
        wait.until(EC.presence_of_element_located((By.ID, "cust_no")))
        cust_input = driver.find_element(By.ID, "cust_no")
        cust_input.clear()
        cust_input.send_keys(CONSUMER_ID)

        # click view/history button - adjust if button id changes
        driver.find_element(By.ID, "consumption_hist_button").click()

        # wait until the table appears (you may need to adjust selector)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))

        # small pause to let JS render fully
        time.sleep(1)

        html = driver.page_source
    finally:
        driver.quit()

    soup = BeautifulSoup(html, "html.parser")

    # Extract info
    try:
        info_box = soup.find("div", {"class": "panel-body"})
        rows = info_box.find_all("tr")

        customer_name = rows[0].find_all("td")[1].text.strip() if rows and len(rows) > 0 else config.CONSUMER_NAME
        consumer_number = rows[1].find_all("td")[1].text.strip() if rows and len(rows) > 1 else CONSUMER_ID
    except Exception:
        customer_name = config.CONSUMER_NAME
        consumer_number = CONSUMER_ID

    table = soup.find("table")
    if not table:
        print("No table found on page.")
        return customer_name, consumer_number, []

    tbody = table.find("tbody")
    if not tbody:
        print("Table has no tbody.")
        return customer_name, consumer_number, []

    rows_out = []
    for tr in tbody.find_all("tr"):
        cols = [td.text.strip() for td in tr.find_all("td")]
        if cols:
            rows_out.append(cols)

    return customer_name, consumer_number, rows_out


# ----------------------------------------------------
# MAIN PROCESS
# ----------------------------------------------------
def main():
    print("🔍 Checking NESCO Data...")

    try:
        customer_name, consumer_number, new_rows = scrape_nesco()
    except Exception as e:
        print("Scrape failed:", e)
        return

    log = load_log()
    previous_rows = log.get("rows", [])
    new_found = False

    for row in new_rows:
        # Use safer comparison if row lengths differ - treat identical if exact list match
        if row not in previous_rows:
            new_found = True

            # Some tables change shape; guard indexes with try/except
            def safe(idx, default="N/A"):
                try:
                    return row[idx]
                except Exception:
                    return default

            msg = (
                f"📉Monthly Usage📉\n\n"
                f"👤 Consumer Name: {CONSUMER_NAME}\n"
                f"🔢 Consumer No.: {CONSUMER_ID}\n\n"
                f"🗓️ Month: {row[1]}\n"
                f"📅 Year: {row[0]}\n\n"
                f"💳 Total Recharge (Tk.): {row[2]} Tk\n"
                f"⚡ Energy Usage (Tk): {row[4]} Tk\n"
                f"🪫 Energy Usage (KWH): {row[12]} kwh\n"
                f"💲 Month End Meter Balance (Tk.): {row[11]} Tk\n\n"
                f"📜 Meter Rent: {row[5]} Tk\n"
                f"📜 Demand Charge: {row[6]} Tk\n"
                f"🧾 VAT: {row[9]} Tk\n"
                f"🎁 Rebate: {row[3]} Tk\n"
                f"💰 Total Usage/Deduction (Tk.): {row[10]} Tk\n"
            )
            
            print("New row found, sending messages...")
            send_telegram(msg)
            send_discord_safe(msg)

            previous_rows.append(row)

    if new_found:
        save_log({"rows": previous_rows})
        print("✔ New data sent. Log updated.")
    else:
        print("ℹ No new updates.")


if __name__ == "__main__":
    main()

