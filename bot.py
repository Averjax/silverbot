import asyncio
import requests
import datetime
import json
import os
from playwright.async_api import async_playwright

# ================= CONFIG =================
BOT_TOKEN = "8412812041:AAHZmfUMBstBsJdkVs6GAc01QfqFJb51KMg"
ADMIN_IDS = [52699743, 65336142] 
HTTP_PROXY = "http://127.0.0.1:10809"
SETTINGS_FILE = "settings.json"

PROXIES = {"http": HTTP_PROXY, "https": HTTP_PROXY}
CHECK_URL = "https://charisma.ir/plans/silver"

# Global Variables
buy_price = None
sell_price = None
last_update_id = None
last_notified_price = None
last_update_time = None
user_states = {}
# ==========================================

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
                print(f"✅ Settings loaded: Buy={buy_price}, Sell={sell_price}")
        except: pass

def persian_to_int(text):
    persian_digits = "۰۱۲۳۴۵۶۷۸۹"
    english_digits = "0123456789"
    translation = str.maketrans(persian_digits, english_digits)
    cleaned = text.translate(translation).replace("٬", "").replace(",", "").strip()
    return int(cleaned)

# --- Smart Connection Logic (Race) ---
async def telegram_request_race(method, url, **kwargs):
    def _do_request(use_proxy):
        p = PROXIES if use_proxy else None
        resp = requests.request(method, url, timeout=(5, 12), proxies=p, **kwargs)
        resp.raise_for_status()
        return resp

    tasks = [
        asyncio.create_task(asyncio.to_thread(_do_request, False)),
        asyncio.create_task(asyncio.to_thread(_do_request, True))
    ]

    while tasks:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                result = task.result()
                for p in pending: p.cancel()
                return result
            except:
                tasks.remove(task)
        tasks = list(pending)
    raise ConnectionError("Both attempts failed")

# --- Telegram functions ---
async def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        await telegram_request_race("POST", url, data={"chat_id": chat_id, "text": text})
    except: pass

async def broadcast_message(text):
    await asyncio.gather(*[send_message(admin_id, text) for admin_id in ADMIN_IDS])

async def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 10}
    if offset: params["offset"] = offset
    resp = await telegram_request_race("GET", url, params=params)
    return resp.json()["result"]

# --- Scraper ---
class SilverPriceScraper:
    def __init__(self, url):
        self.url = url
        self.page, self.browser, self.playwright = None, None, None

    async def start(self):
        await self.close()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        await self.page.route("**/*", lambda r: r.abort() if r.request.resource_type in ["image", "media", "font"] else r.continue_())

    async def get_price(self):
        await self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        selector = "span.text-x-h2"
        await self.page.wait_for_function("(sel) => { const el = document.querySelector(sel); return el && /[۰-۹]/.test(el.innerText); }", arg=selector, timeout=20000)
        text = await self.page.evaluate(f"document.querySelector('{selector}').innerText")
        for t in text.split():
            if any(ch in t for ch in "۰۱۲۳۴۵۶۷۸۹"): return persian_to_int(t)
        raise ValueError("No price")

    async def close(self):
        try:
            if self.browser: await self.browser.close()
            if self.playwright: await self.playwright.stop()
        except: pass

# --- Monitor ---
async def monitor_price(scraper):
    global last_notified_price, last_update_time
    errs = 0
    while True:
        try:
            current_price = await scraper.get_price()
            errs = 0
            last_update_time = datetime.datetime.now()
            print(f"[{last_update_time.strftime('%H:%M:%S')}] Scraped: {current_price}")
            
            if current_price != last_notified_price:
                last_notified_price = current_price
                if buy_price and current_price <= buy_price:
                    await broadcast_message(f"🟢 سیگنال خرید\nقیمت فعلی ({current_price:,}) به کمتر از حد خرید ({buy_price:,}) رسید.")
                if sell_price and current_price >= sell_price:
                    await broadcast_message(f"🔴 سیگنال فروش\nقیمت فعلی ({current_price:,}) به بیشتر از حد فروش ({sell_price:,}) رسید.")
            await asyncio.sleep(30)
        except Exception as e:
            errs += 1
            if errs >= 2: await scraper.start(); errs = 0
            await asyncio.sleep(5)

# --- Processor ---
async def process_messages(messages):
    global buy_price, sell_price, last_update_id, user_states, last_notified_price, last_update_time
    for msg in messages:
        last_update_id = msg["update_id"] + 1
        message = msg.get("message") or msg.get("edited_message")
        if not message or "text" not in message: continue
        chat_id = message["from"]["id"]
        if chat_id not in ADMIN_IDS: continue
        text = message["text"].strip()
        state = user_states.get(chat_id)

        if text == "/start":
            await send_message(chat_id, "🤖 ربات پایش قیمت نقره فعال شد.\n\n/price | /buy | /sell | /status")
        
        elif text == "/price":
            if last_notified_price:
                time_str = last_update_time.strftime("%H:%M:%S")
                await send_message(chat_id, f"💰 آخرین قیمت: {last_notified_price:,}\n🕒 زمان بروزرسانی: {time_str}")
            else:
                await send_message(chat_id, "⏳ در حال دریافت قیمت از سایت...")

        elif text == "/status":
            bp = f"{buy_price:,}" if buy_price else "تنظیم نشده"
            sp = f"{sell_price:,}" if sell_price else "تنظیم نشده"
            lp = f"{last_notified_price:,}" if last_notified_price else "در حال دریافت..."
            lt = last_update_time.strftime("%H:%M:%S") if last_update_time else "نامشخص"
            await send_message(chat_id, f"📊 وضعیت فعلی:\n\n📥 حد خرید: {bp}\n📤 حد فروش: {sp}\n🏷 آخرین قیمت: {lp}\n🕒 زمان بروزرسانی: {lt}")

        elif text == "/buy":
            user_states[chat_id] = "W_BUY"
            await send_message(chat_id, "📉 لطفا قیمت خرید مورد نظر را وارد کنید:")
        
        elif text == "/sell":
            user_states[chat_id] = "W_SELL"
            await send_message(chat_id, "📈 لطفا قیمت فروش مورد نظر را وارد کنید:")

        elif state == "W_BUY":
            try:
                buy_price = persian_to_int(text); save_settings()
                await send_message(chat_id, f"✅ قیمت خرید روی {buy_price:,} تنظیم و ذخیره شد.")
                user_states[chat_id] = None
            except: await send_message(chat_id, "❌ خطا: عدد معتبر وارد کنید.")

        elif state == "W_SELL":
            try:
                sell_price = persian_to_int(text); save_settings()
                await send_message(chat_id, f"✅ قیمت فروش روی {sell_price:,} تنظیم و ذخیره شد.")
                user_states[chat_id] = None
            except: await send_message(chat_id, "❌ خطا: عدد معتبر وارد کنید.")

async def main():
    global last_update_id
    load_settings()
    scraper = SilverPriceScraper(CHECK_URL)
    await scraper.start()
    asyncio.create_task(monitor_price(scraper))
    while True:
        try:
            updates = await get_updates(last_update_id)
            if updates: await process_messages(updates)
        except: await asyncio.sleep(2)
        await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(main())
