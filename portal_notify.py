#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v4
- Basic認証ダイアログ自動突破
- お知らせのURLリンク抽出＆Discord Embed化（クリックで直飛び）
- 複数サーバー通知対応（Discord複数 / Slack / LINE / 汎用Webhook）
"""

import os
import sys
import json
import re
import urllib.request
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

# 設定
PORTAL_TOP = "https://portal-xs.mi.sanno.ac.jp/campusweb/top.do"
SEEN_FILE = "seen.json"

SANNO_ID = os.environ.get("SANNO_ID", "").strip()
SANNO_PASS = os.environ.get("SANNO_PASS", "").strip()
DISCORD_WEBHOOKS = [w.strip() for w in os.environ.get("DISCORD_WEBHOOK", "").split(",") if w.strip()]
SLACK_WEBHOOKS = [w.strip() for w in os.environ.get("SLACK_WEBHOOK", "").split(",") if w.strip()]
GENERIC_WEBHOOKS = [w.strip() for w in os.environ.get("GENERIC_WEBHOOK", "").split(",") if w.strip()]

FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "").strip()
MAX_ITEMS = int(os.environ.get("MAX_ITEMS_PER_RUN", "5"))
DEBUG = os.environ.get("DEBUG", "0") == "1"


def post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "sanno-portal-notify"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def broadcast_message(text):
    """プレーンテキストを全サーバーへ送信（開始通知など）"""
    for wh in DISCORD_WEBHOOKS:
        try:
            post_json(wh, {"content": text})
        except Exception as e:
            print(f"[warn] discord send failed: {e}")
    for wh in SLACK_WEBHOOKS:
        try:
            post_json(wh, {"text": text})
        except Exception as e:
            print(f"[warn] slack send failed: {e}")


def broadcast_items(new_items):
    """URLリンク付きのお知らせを全サーバーへ送信"""
    # 1. Discord 向け（EmbedでタイトルがURLリンクになる！）
    if DISCORD_WEBHOOKS:
        embeds = []
        for it in new_items[:10]:
            e = {
                "title": (it["title"][:240] or "(無題)"),
                "url": it.get("url") or PORTAL_TOP,   # ← タイトルを押すと直で飛べる！
                "description": it.get("meta", "")[:400],
                "color": 0x1E88E5  # 綺麗なブルー
            }
            embeds.append(e)

        payload = {
            "content": f"📢 **産業能率大学ポータル 新着お知らせ ({len(new_items)}件)**",
            "embeds": embeds
        }
        for wh in DISCORD_WEBHOOKS:
            try:
                st = post_json(wh, payload)
                print(f"[portal-notify] discord sent to ...{wh[-10:]} (status {st})")
            except Exception as e:
                print(f"[warn] discord send failed: {e}")

    # 2. Slack 向け（<URL|タイトル> 形式でリンク化）
    if SLACK_WEBHOOKS:
        lines = [f"📢 *産業能率大学ポータル 新着お知らせ ({len(new_items)}件)*\n"]
        for it in new_items[:10]:
            url = it.get("url") or PORTAL_TOP
            meta = f" ({it['meta']})" if it.get("meta") else ""
            lines.append(f"・<{url}|{it['title']}>{meta}")
        slack_payload = {"text": "\n".join(lines)}
        for wh in SLACK_WEBHOOKS:
            try:
                st = post_json(wh, slack_payload)
                print(f"[portal-notify] slack sent (status {st})")
            except Exception as e:
                print(f"[warn] slack send failed: {e}")

    # 3. 汎用Webhook（JSONまるごと送信）
    if GENERIC_WEBHOOKS:
        payload = {"source": "sanno-portal-notify", "count": len(new_items), "items": new_items}
        for wh in GENERIC_WEBHOOKS:
            try:
                post_json(wh, payload)
            except Exception as e:
                print(f"[warn] generic webhook failed: {e}")


def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data if isinstance(data, list) else [])
    except Exception:
        return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen))[-1000:], f, ensure_ascii=False, indent=2)


def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS が設定されてへん")
        sys.exit(1)

    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Basic認証をダイアログが出る前に自動突破
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},
            viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()

        print("[portal-notify] open portal top (with Basic Auth)...")
        page.goto(PORTAL_TOP, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")

        if "signweb" in page.url:
            print("[error] Basic認証が突破できてへん。ID/PASSを確認してな。")
            sys.exit(2)

        page.wait_for_timeout(3000)

        # お知らせ一覧の要素とURLリンクを取得
        # top.do のテーブル行またはリンクを探索
        rows = page.query_selector_all("table tr, .information-item, dl dt, li")
        for r in rows:
            text = (r.inner_text() or "").strip()
            if not text or len(text) < 5:
                continue

            # aタグがあればリンク先URLを取得
            link = r.query_selector("a")
            href = ""
            if link:
                raw_href = link.get_attribute("href") or ""
                if raw_href and not raw_href.startswith("javascript"):
                    href = urljoin(page.url, raw_href)

            # タイトルとメタ情報を分離
            parts = [re.sub(r"\s+", " ", p).strip() for p in text.split("\n") if p.strip()]
            title = parts[0]
            # 日付っぽい行やNEWを整理
            if re.match(r"^(NEW|\d{4}/\d{2}/\d{2}|\d{2}/\d{2})", title) and len(parts) > 1:
                title = parts[1]
                meta = " / ".join(parts[0:1] + parts[2:])
            else:
                meta = " / ".join(parts[1:])

            if len(title) > 3 and not any(kw in title for kw in ["ログアウト", "メニュー", "パスワード変更", "サイトマップ"]):
                items.append({
                    "title": title[:100],
                    "meta": meta[:150],
                    "url": href or PORTAL_TOP  # リンクが拾えなければポータルトップへ
                })

        browser.close()

    # 重複除去
    unique_items = []
    seen_titles = set()
    for it in items:
        if it["title"] not in seen_titles:
            seen_titles.add(it["title"])
            unique_items.append(it)

    return unique_items


def main():
    if not DISCORD_WEBHOOKS and not SLACK_WEBHOOKS and not GENERIC_WEBHOOKS:
        print("[error] 通知先が1つも設定されてへん（DISCORD_WEBHOOK 等を登録してな）")
        sys.exit(1)

    seen = load_seen()
    scraped = scrape_portal()
    print(f"[portal-notify] scraped: {len(scraped)} items")

    # フィルタリング
    if FILTER_KEYWORD:
        scraped = [it for it in scraped if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

    # 初回実行時：基準登録のみ（一斉通知を防ぐ）
    if not seen:
        print(f"[portal-notify] 初回実行: {len(scraped)} 件を基準登録したで。")
        for it in scraped:
            seen.add(it["title"])
        save_seen(seen)
        broadcast_message(f"✅ **産業能率大学ポータル監視** を開始したで！（基準登録: {len(scraped)}件・リンク付対応版）")
        return

    # 2回目以降：差分（新着）を検出
    new_items = [it for it in scraped if it["title"] not in seen]
    print(f"[portal-notify] new items: {len(new_items)}")

    if not new_items:
        print("[portal-notify] no new notices")
        return

    # 通知送信
    to_notify = new_items[:MAX_ITEMS]
    broadcast_items(to_notify)

    # 記録更新
    for it in new_items:
        seen.add(it["title"])
    save_seen(seen)
    print(f"[portal-notify] {len(new_items)} items saved to seen.json")


if __name__ == "__main__":
    main()
