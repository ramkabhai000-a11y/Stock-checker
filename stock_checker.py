#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import logging
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
PRODUCTS_FILE = BASE_DIR / "products.json"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "stock_checker.log"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}

REQUEST_TIMEOUT = 15

DEFAULT_OOS_PHRASES = [
    "out of stock",
    "sold out",
    "currently unavailable",
    "notify me",
    "coming soon",
    "temporarily out of stock",
]

PRICE_REGEX = re.compile(r"(?:\u20b9|Rs\.?|INR|\$)\s?([\d,]+(?:\.\d{1,2})?)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("stock_checker")


def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def send_telegram(message):
    if "PUT_YOUR" in TELEGRAM_BOT_TOKEN or "PUT_YOUR" in TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured - printing alert instead:\n%s", message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            log.error("Telegram send failed: %s", resp.text)
    except requests.RequestException as e:
        log.error("Telegram send error: %s", e)


def fetch_page(url):
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        log.error("Failed to fetch %s: %s", url, e)
        return None


def extract_stock_status(soup, page_text, product):
    selector = product.get("stock_selector")
    if selector:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True).lower()
            phrases = product.get("out_of_stock_phrases", DEFAULT_OOS_PHRASES)
            if any(p in text for p in phrases):
                return "out_of_stock"
            return "in_stock"
    phrases = product.get("out_of_stock_phrases", DEFAULT_OOS_PHRASES)
    lowered = page_text.lower()
    if any(p in lowered for p in phrases):
        return "out_of_stock"
    return "in_stock"


def extract_price(soup, page_text, product):
    selector = product.get("price_selector")
    if selector:
        el = soup.select_one(selector)
        if el:
            match = PRICE_REGEX.search(el.get_text(strip=True))
            if match:
                return float(match.group(1).replace(",", ""))
    match = PRICE_REGEX.search(page_text)
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def check_product(product, state):
    name = product["name"]
    url = product["url"]
    log.info("Checking: %s", name)

    html = fetch_page(url)
    if html is None:
        return state.get(url, {})

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    stock_status = extract_stock_status(soup, page_text, product)
    price = extract_price(soup, page_text, product)

    prev = state.get(url, {})
    prev_status = prev.get("stock_status")
    prev_price = prev.get("price")
    target_price = product.get("target_price")

    messages = []

    if stock_status == "in_stock" and prev_status == "out_of_stock":
        messages.append(
            f"BACK IN STOCK: {name}\nPrice: {price if price is not None else 'N/A'}\n{url}"
        )
    elif prev_status is None and stock_status == "in_stock":
        messages.append(
            f"Tracking started - currently IN STOCK: {name}\nPrice: {price if price is not None else 'N/A'}\n{url}"
        )

    if (
        price is not None
        and target_price is not None
        and price <= target_price
        and (prev_price is None or prev_price > target_price)
    ):
        messages.append(
            f"PRICE DROP: {name}\nNow: {price} (target was {target_price})\n{url}"
        )

    for msg in messages:
        send_telegram(msg)
        log.info("Alert sent: %s", msg.splitlines()[0])

    return {
        "name": name,
        "stock_status": stock_status,
        "price": price,
        "last_checked": datetime.now().isoformat(timespec="seconds"),
    }


def run():
    products = load_json(PRODUCTS_FILE, [])
    if not products:
        log.warning("No products found in products.json - nothing to check.")
        return

    state = load_json(STATE_FILE, {})

    for product in products:
