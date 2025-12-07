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
async def _send_discord_async(message):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(DISCORD_CHANNEL_ID)
            if channel is None:
                print("Discord: channel not found or bot doesn't have access.")
            else:
                await channel.send(message)
                print("Discord message sent.")
        except Exception as e:
            print("Discord send error in on_ready:", e)
        finally:
            await client.close()

    try:
        await client.start(DISCORD_BOT_TOKEN)
    except Exception as e:
        print("discord.Client.start() error:", e)
        try:
            await client.close()
        except Exception:
            pass


def send_discord_safe(message):
    """Safe wrapper that handles closed event loops (useful for GitHub Actions)."""
    if not DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID is None:
        print("Discord credentials missing; skipping Discord send.")
        return

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # If loop is closed or running, create a fresh loop to run this short task
    try:
        if loop.is_running():
            # create a temporary new loop in a thread-like manner
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            new_loop.run_until_complete(_send_discord_async(message))
            new_loop.close()
            asyncio.set_event_loop(loop)
        else:
            loop.run_until_complete(_send_discord_async(message))
    except Exception as e:
        # Final fallback: try asyncio.run (Python 3.7+)
        try:
            asyncio.run(_send_discord_async(message))
        except Exception as e2:
            print("Discord send failed:", e, e2)


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
                f"📉 মাসিক ব্যবহার 📉\n\n"
                f"👤 গ্রাহকের নাম: {CONSUMER_NAME}\n"
                f"🔢 কনজ্যুমার নম্বর: {CONSUMER_ID}\n\n"
                f"🗓️ মাস: {safe(1)}\n"
                f"📅 বছর: {safe(0)}\n\n"
                f"💳 সর্বমোট রিচার্জ: {safe(2)} টাকা\n"
                f"⚡ ব্যবহৃত বিদ্যুৎ (টাকা): {safe(4)} টাকা\n"
                f"🪫 ব্যবহৃত বিদ্যুৎ (kWh): {safe(12)} kwh\n"
                f"💲 মাস শেষে মিটার ব্যালেন্স: {safe(11)} টাকা\n\n"
                f"📜 মিটার রেন্ট: {safe(5)} টাকা\n"
                f"📜 ডিমান্ড চার্জ: {safe(6)} টাকা\n"
                f"🧾 ভ্যাট: {safe(9)} টাকা\n"
                f"🎁 রেয়াত: {safe(3)} টাকা\n"
                f"💰 সর্বমোট ব্যবহার/কর্তন: {safe(10)} টাকা\n"
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
