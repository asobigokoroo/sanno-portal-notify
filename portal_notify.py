#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v6
- 新着お知らせだけを通知（過去分の一覧は送らない）
- 1件＝1メッセージで個別にポスト（タイトルはクリックでポータルへ）
- 複数サーバー対応（Discord複数 / Slack / 汎用Webhook）
- Basic認証自動突破 / iframe全走査 / DEBUG=1で調査ログ
"""

import os
import re
import json
import sys
import urllib.request
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

PORTAL_TOP = "https://portal-xs.mi.sanno.ac.jp/campusweb/top.do"
SEEN_FILE = "seen.json"
DEBUG = os.environ.get("DEBUG", "0") == "1"

SANNO_ID = os.environ.get("SANNO_ID", "").strip()
SANNO_PASS = os.environ.get("SANNO_PASS", "").strip()
DISCORD_WEBHOOKS = [w.strip() for w in os.environ.get("DISCORD_WEBHOOK", "").split(",") if w.strip()]
SLACK_WEBHOOKS   = [w.strip() for w in os.environ.get("SLACK_WEBHOOK", "").split(",") if w.strip()]
GENERIC_WEBHOOKS = [w.strip() for w in os.environ.get("GENERIC_WEBHOOK", "").split(",") if w.strip()]
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "").strip()
MAX_NOTIFY = int(os.environ.get("MAX_NOTIFY_PER_RUN", "20"))

NOISE = ["ログアウト", "メニュー", "パスワード変更", "サイトマップ", "ホーム", "戻る",
         "在学生", "保護者", "教職員", "ログイン", "ヘルプ", "English"]


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "sanno-portal-notify"},
        method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def send_discord_embed(item):
    embed = {
        "title": (item["title"][:240] or "(無題)"),
        "url": item.get("url") or PORTAL_TOP,
        "color": 0x1E88E5,
    }
    if item.get("meta"):
        embed["description"] = item["meta"][:400]
    embed["footer"] = {"text": "産業能率大学ポータル 新着"}
    for wh in DISCORD_WEBHOOKS:
        try:
            st = post_json(wh, {"embeds": [embed]})
            print(f"[portal-notify] discord 1件送信 (status {st})")
        except Exception as e:
            print(f"[warn] discord failed: {e}")


def send_slack_item(item):
    url = item.get("url") or PORTAL_TOP
    text = f"📢 *新着* <{url}|{item['title']}>"
    if item.get("meta"):
        text += f"\n>{item['meta']}"
    for wh in SLACK_WEBHOOKS:
        try:
            st = post_json(wh, {"text": text})
            print(f"[portal-notify] slack 1件送信 (status {st})")
        except Exception as e:
            print(f"[warn] slack failed: {e}")


def send_generic_item(item):
    payload = {"source": "sanno-portal-notify", "count": 1, "items": [item]}
    for wh in GENERIC_WEBHOOKS:
        try:
            post_json(wh, payload)
        except Exception as e:
            print(f"[warn] generic failed: {e}")


def broadcast_item(item):
    if DISCORD_WEBHOOKS: send_discord_embed(item)
    if SLACK_WEBHOOKS:   send_slack_item(item)
    if GENERIC_WEBHOOKS: send_generic_item(item)


def broadcast_message(text):
    for wh in DISCORD_WEBHOOKS:
        try: post_json(wh, {"content": text})
        except Exception as e: print(f"[warn] discord failed: {e}")
    for wh in SLACK_WEBHOOKS:
        try: post_json(wh, {"text": text})
        except Exception as e: print(f"[warn] slack failed: {e}")


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return set(d if isinstance(d, list) else [])
    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen))[-2000:], f, ensure_ascii=False, indent=2)


def dump_debug(page):
    os.makedirs("debug", exist_ok=True)
    all_links = []
    for i, frame in enumerate(page.frames):
        try:
            html = frame.content()
        except Exception as e:
            html = f"(取得失敗: {e})"
        with open(f"debug/frame_{i}.html", "w", encoding="utf-8") as f:
            f.write(html)
        try:
            for a in frame.query_selector_all("a"):
                href = a.get_attribute("href") or ""
                txt = (a.inner_text() or "").strip().replace("\n", " ")
                if href and not href.startswith("javascript"):
                    all_links.append({"frame": i, "text": txt[:80], "href": href})
        except Exception:
            pass
    with open("debug/links.json", "w", encoding="utf-8") as f:
        json.dump(all_links, f, ensure_ascii=False, indent=2)
    try:
        page.screenshot(path="debug/screenshot.png", full_page=True)
    except Exception:
        pass
    print(f"[portal-notify][DEBUG] frames={len(page.frames)} links={len(all_links)} → debug/ 保存")


def looks_like_date(s):
    return re.match(r"^\s*(\d{4}[/年]\d{1,2}[/月]\d{1,2}日?|\d{1,2}/\d{1,2}|NEW|新着)", s) is not None


def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS 未設定"); sys.exit(1)

    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()
        print("[portal-notify] open portal top (Basic Auth)...")
        page.goto(PORTAL_TOP, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")
        if "signweb" in page.url:
            print("[error] Basic認証突破できず。ID/PASSを確認してな。"); sys.exit(2)
        page.wait_for_timeout(4000)

        if DEBUG:
            dump_debug(page)

        for frame in page.frames:
            try:
                anchors = frame.query_selector_all("a")
            except Exception:
                continue
            for a in anchors:
                try:
                    txt = re.sub(r"\s+", " ", (a.inner_text() or "")).strip()
                    href = a.get_attribute("href") or ""
                except Exception:
                    continue
                if not txt or len(txt) < 4:
                    continue
                if any(n in txt for n in NOISE):
                    continue
                url = PORTAL_TOP if (href.startswith("javascript") or href == "") else urljoin(frame.url, href)
                items.append({"title": txt[:120], "meta": "", "url": url})

    seen_t, unique = set(), []
    for it in items:
        t = it["title"]
        if looks_like_date(t) and len(t) < 12:
            continue
        if t in seen_t:
            continue
        seen_t.add(t)
        unique.append(it)
    return unique


def main():
    if not (DISCORD_WEBHOOKS or SLACK_WEBHOOKS or GENERIC_WEBHOOKS):
        print("[error] 通知先が未設定"); sys.exit(1)

    seen = load_seen()
    scraped = scrape_portal()
    print(f"[portal-notify] scraped: {len(scraped)}")
    if DEBUG:
        for it in scraped[:60]:
            print(f"   - {it['title']}  |  {it['url']}")

    if FILTER_KEYWORD:
        scraped = [it for it in scraped if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

    if not seen:
        print(f"[portal-notify] first run baseline: {len(scraped)}")
        for it in scraped:
            seen.add(it["title"])
        save_seen(seen)
        broadcast_message(f"✅ 産業能率大学ポータル監視を開始したで。以降、新着お知らせだけを1件ずつ通知するわ。（基準登録: {len(scraped)}件）")
        return

    new_items = [it for it in scraped if it["title"] not in seen]
    print(f"[portal-notify] new items: {len(new_items)}")
    if not new_items:
        print("[portal-notify] no new notices")
        return

    for it in new_items[:MAX_NOTIFY]:
        broadcast_item(it)

    for it in new_items:
        seen.add(it["title"])
    save_seen(seen)
    print(f"[portal-notify] notified {min(len(new_items), MAX_NOTIFY)} / saved {len(new_items)}")


if __name__ == "__main__":
    main()
