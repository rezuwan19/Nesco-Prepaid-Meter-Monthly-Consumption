import json
import os
import time
import requests
import discord
import asyncio
import nest_asyncio

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from config import (
    CONSUMER_ID,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    LOG_FILE,
)

nest_asyncio.apply()   # IMPORTANT FIX FOR GITHUB ACTIONS


# ----------------------------------------------------
# Load log file
# ----------------------------------------------------
def load_log():
    if not os.path.exists(LOG_FILE):
        return {"rows": []}
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"rows": []}


# ----------------------------------------------------
# Save log file
# ----------------------------------------------------
def save_log(data):
    print(f"Saving log file to: {LOG_FILE}")
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    print("Log saved successfully.")


# ----------------------------------------------------
# Telegram Sender
# ----------------------------------------------------
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print("Telegram Error:", e)


# ----------------------------------------------------
# Discord Sender (Bot)
# ----------------------------------------------------
async def send_discord(message):
    try:
        intents = discord.Intents.default()
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            channel = client.get_channel(int(DISCORD_CHANNEL_ID))
            await channel.send(message)
            await client.close()

        await client.start(DISCORD_BOT_TOKEN)

    except Exception as e:
        print("Discord Error:", e)


def send_discord_safe(message):
    """Safe Discord sender for GitHub Actions."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(send_discord(message))
    except RuntimeError:
        # Restart event loop if closed
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        new_loop.run_until_complete(send_discord(message))


# ----------------------------------------------------
# Scrape NESCO Website
# ----------------------------------------------------
def scrape_nesco():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")

    from selenium.webdriver.chrome.service import Service
    service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.get("https://customer.nesco.gov.bd/pre/panel")
    time.sleep(2)

    driver.find_element(By.ID, "cust_no").send_keys(CONSUMER_ID)
    driver.find_element(By.ID, "consumption_hist_button").click()
    time.sleep(3)

    html = driver.page_source
    driver.quit()

    soup = BeautifulSoup(html, "html.parser")

    # Extract info
    try:
        info_box = soup.find("div", {"class": "panel-body"})
        rows = info_box.find_all("tr")

        customer_name = rows[0].find_all("td")[1].text.strip()
        consumer_number = rows[1].find_all("td")[1].text.strip()
    except:
        customer_name = "Not Found"
        consumer_number = "Not Found"

    table = soup.find("table")
    tbody = table.find("tbody")
    rows_out = []

    for tr in tbody.find_all("tr"):
        cols = [td.text.strip() for td in tr.find_all("td")]
        rows_out.append(cols)

    return customer_name, consumer_number, rows_out


# ----------------------------------------------------
# MAIN PROCESS
# ----------------------------------------------------
def main():
    print("🔍 Checking NESCO Data...")

    customer_name, consumer_number, new_rows = scrape_nesco()
    log = load_log()

    previous_rows = log["rows"]
    new_found = False

    for row in new_rows:
        if row not in previous_rows:
            new_found = True

            msg = (
                f"📉Monthly Usage📉\n\n"
                f"👤 Consumer Name: {customer_name}\n"
                f"🔢 Consumer No.: {consumer_number}\n\n"
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

