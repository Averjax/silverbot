import asyncio
import requests
import datetime
import json
import os

# ================= CONFIG =================
BOT_TOKEN = "8412812041:AAHZmfUMBstBsJdkVs6GAc01QfqFJb51KMg"
ADMIN_IDS = [52699743, 65336142] 
SETTINGS_FILE = "settings.json"

# The Direct API URL you discovered
API_URL = "https://webapi.charisma.ir/api/Plan/plan-calculator-info-by-id?planId=04689a46-3eff-45d4-a070-f83f7d4d20d8"

# Global Variables
buy_price = None
sell_price = None
last_notified_price = None
last_update_time = None
user_states = {}

# ================= UTILS =================
def save_settings():
    data = {"buy_price": buy_price, "sell_price": sell_price}
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f)

def load_settings():
    global buy_price, sell_price
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                buy_price = data.get("buy_price")
                sell_price = data.get("sell_price")
        except: pass

def persian_to_int(text):
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    translation = str.maketrans(persian_digits, english_digits)
    cleaned = str(text).translate(translation).replace("٬", "").replace(",", "").strip()
    return int(float(cleaned))

# ================= NETWORK =================
async def telegram_request(method, url, **kwargs):
    # Set this to None when deploying to your VPS
    PROXIES = {"http": "http://127.0.0.1:10809", "https": "http://127.0.0.1:10809"}
    
    def _do():
        resp = requests.request(method, url, timeout=15, proxies=PROXIES, **kwargs)
        resp.raise_for_status()
        return resp
    return await asyncio.to_thread(_do)

# ================= SCRAPER =================
class SilverPriceScraper:
    def __init__(self, api_url):
        self.api_url = api_url
        self.headers = {"User-Agent": "Mozilla/5.0"}

    async def start(self):
        print("🚀 Scraper active: Direct API Mode (Playwright Removed)")

    async def get_price(self):
        def _fetch():
            # Add timestamp to bypass any server-side caching
            ts_url = f"{self.api_url}&_t={datetime.datetime.now().timestamp()}"
            resp = requests.get(ts_url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return data['lastPrice']

        raw_price = await asyncio.to_thread(_fetch)
        return int(float(raw_price))

    async def close(self): pass

# ================= ACTIONS =================
async def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try: await telegram_request("POST", url, data={"chat_id": chat_id, "text": text})
    except: pass

async def broadcast_message(text):
    await asyncio.gather(*[send_message(aid, text) for aid in ADMIN_IDS])

async def monitor_price(scraper):
    global last_notified_price, last_update_time
    while True:
        try:
            current_price = await scraper.get_price()
            last_update_time = datetime.datetime.now()
            print(f"[{last_update_time.strftime('%H:%M:%S')}] Price: {current_price:,}")
            
            if current_price != last_notified_price:
                if buy_price and current_price <= buy_price:
                    await broadcast_message(f"🟢 سیگنال خرید\nقیمت: {current_price:,}")
                if sell_price and current_price >= sell_price:
                    await broadcast_message(f"🔴 سیگنال فروش\nقیمت: {current_price:,}")
                last_notified_price = current_price
            await asyncio.sleep(60)
        except Exception as e:
            print(f"Monitor Error: {e}")
            await asyncio.sleep(10)

# ================= MESSAGE PROCESSOR =================
async def process_updates(updates):
    global buy_price, sell_price, user_states
    for update in updates:
        msg = update.get("message")
        if not msg or "text" not in msg: continue
        chat_id = msg["from"]["id"]
        if chat_id not in ADMIN_IDS: continue
        
        text = msg["text"].strip()
        state = user_states.get(chat_id)

        if text == "/start":
            await send_message(chat_id, "🤖 ربات نقره فعال شد.\n/price | /buy | /sell | /status")
        elif text == "/price":
            p = f"{last_notified_price:,}" if last_notified_price else "در حال دریافت..."
            await send_message(chat_id, f"💰 قیمت فعلی: {p}")
        elif text == "/status":
            res = f"📊 وضعیت:\nخرید: {buy_price or 0:,}\nفروش: {sell_price or 0:,}\nقیمت: {last_notified_price or 0:,}"
            await send_message(chat_id, res)
        elif text == "/buy":
            user_states[chat_id] = "SET_BUY"
            await send_message(chat_id, "📉 قیمت خرید:")
        elif text == "/sell":
            user_states[chat_id] = "SET_SELL"
            await send_message(chat_id, "📈 قیمت فروش:")
        elif state == "SET_BUY":
            try:
                buy_price = persian_to_int(text); save_settings()
                await send_message(chat_id, f"✅ حد خرید: {buy_price:,}")
                user_states[chat_id] = None
            except: await send_message(chat_id, "❌ خطا در عدد.")
        elif state == "SET_SELL":
            try:
                sell_price = persian_to_int(text); save_settings()
                await send_message(chat_id, f"✅ حد فروش: {sell_price:,}")
                user_states[chat_id] = None
            except: await send_message(chat_id, "❌ خطا در عدد.")

# ================= MAIN =================
async def main():
    load_settings()
    scraper = SilverPriceScraper(API_URL)
    await scraper.start()
    asyncio.create_task(monitor_price(scraper))
    
    print("✅ Bot is running...")
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            resp = await telegram_request("GET", url, params={"timeout": 10, "offset": offset})
            updates = resp.json()["result"]
            if updates:
                await process_updates(updates)
                offset = updates[-1]["update_id"] + 1
        except: await asyncio.sleep(5)
        await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
