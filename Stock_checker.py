#!/usr/bin/env python3
"""
Universal Stock & Price Checker
================================
Monitors any number of product pages (any site, Flipkart, Amazon, a small
brand's Shopify store, whatever) for two things:
  1. Back-in-stock status
  2. Price drops below a target you set

When something changes, it sends you a Telegram message instantly.
"""

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
