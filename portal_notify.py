#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v8
- Basic認証突破後、お知らせ受信一覧（wbasmgjr.do）に直接アクセス
- テーブル行を1行ずつ解析：タイトル / 日付 / 送信者 / 種別 / NEWフラグ
- 新着だけを1件＝1メッセージで個別通知
- 複数サーバー対応（Discord複数 / Slack / 汎用Webhook）
"""

import os
import re
import json
import sys
import urllib.request
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

# お知らせ受信一覧ページ（直接アクセス）
NOTICE_URL = "https://portal-xs.mi.sanno.ac.jp/campusweb/wbasmgjr.do?clearAccessData=true&contenam=wbasmgjr&kjnmnNo=2"
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
        "url": NOTICE_URL,  # お知らせ一覧へリンク
        "color": 0x1E88E5,
    }
    if item.get("meta"):
        embed["description"] = item["meta"][:400]
    if item.get("is_new"):
        embed["color"] = 0xFF4444  # NEWなら赤色に目立たせる
    embed["footer"] = {"text": "産業能率大学ポータル 新着"}
    for wh in DISCORD_WEBHOOKS:
        try:
            st = post_json(wh, {"embeds": [embed]})
            print(f"[portal-notify] discord 1件送信 (status {st})")
        except Exception as e:
            print(f"[warn] discord failed: {e}")


def send_slack_item(item):
    text = f"📢 *新着* <{NOTICE_URL}|{item['title']}>"
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


def extract_notices_from_page(page):
    """1ページ分のテーブルからお知らせを抽出"""
    items = []
    
    # テーブル行を取得
    rows = page.query_selector_all("table tbody tr, table tr")
    print(f"[portal-notify] table rows found: {len(rows)}")
    
    for row in rows:
        # タイトルセル（camjnext-list-item-contents クラス）を探す
        title_cell = row.query_selector(".camjnext-list-item-contents")
        if not title_cell:
            continue
        
        # タイトルリンク
        a = title_cell.query_selector("a")
        if not a:
            continue
        
        title = a.inner_text().strip()
        if not title or len(title) < 4:
            continue
        
        # 日付・送信者・種別を取得（td列から）
        tds = row.query_selector_all("td")
        date_str = tds[3].inner_text().strip() if len(tds) > 3 else ""
        sender   = tds[4].inner_text().strip() if len(tds) > 4 else ""
        category = tds[5].inner_text().strip() if len(tds) > 5 else ""
        
        # NEWタグの有無
        new_tag = title_cell.query_selector(".camjnext-list-tag-new")
        is_new = new_tag is not None
        
        # meta情報を組み立て
        meta_parts = [p for p in [date_str, sender, category] if p]
        meta = " / ".join(meta_parts)
        if is_new:
            meta += " / NEW"
        
        items.append({
            "title": title[:200],
            "meta": meta,
            "url": NOTICE_URL,
            "is_new": is_new
        })
    
    return items


def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS 未設定"); sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        print(f"[portal-notify] open notice list: {NOTICE_URL}")
        page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")
        
        if "signweb" in page.url:
            print("[error] Basic認証突破できず。ID/PASSを確認してな。"); sys.exit(2)
        
        page.wait_for_timeout(4000)
        
        if DEBUG:
            # HTML冒頭をログ出力
            html = page.content()
            preview = html[:1500].replace("\n", " ").replace("  ", " ")
            print(f"[DEBUG] html_preview={preview}...")
            # テーブル構造を確認
            rows = page.query_selector_all("table tr")
            print(f"[DEBUG] total rows: {len(rows)}")
            for i, row in enumerate(rows[:5]):
                txt = row.inner_text().replace("\n", " | ")
                print(f"[DEBUG] row {i}: {txt[:150]}")

        # 1ページ目を取得
        all_items = extract_notices_from_page(page)
        
        # ページネーション：「次へ」ボタンがあれば全ページ巡回
        page_num = 1
        while len(all_items) < 89 and page_num < 15:  # 安全弁：最大15ページ
            # 次へリンクを探す（> ボタン）
            next_links = page.query_selector_all("a")
            next_btn = None
            for link in next_links:
                txt = link.inner_text().strip()
                if txt == ">" or txt == "›" or txt == "→":
                    # 無効化されていないか確認
                    onclick = link.get_attribute("onclick") or ""
                    cls = link.get_attribute("class") or ""
                    if "disabled" not in cls and "inactive" not in cls:
                        next_btn = link
                        break
            
            if not next_btn:
                break
            
            try:
                print(f"[portal-notify] clicking next page ({page_num + 1})...")
                next_btn.click()
                page.wait_for_timeout(3000)
                page_num += 1
                items = extract_notices_from_page(page)
                if not items:
                    break
                # 重複を避けるため、新しいものだけ追加
                existing_titles = {it["title"] for it in all_items}
                for it in items:
                    if it["title"] not in existing_titles:
                        all_items.append(it)
            except Exception as e:
                print(f"[warn] pagination failed: {e}")
                break

        browser.close()
        return all_items


def main():
    if not (DISCORD_WEBHOOKS or SLACK_WEBHOOKS or GENERIC_WEBHOOKS):
        print("[error] 通知先が未設定"); sys.exit(1)

    seen = load_seen()
    scraped = scrape_portal()
    print(f"[portal-notify] scraped: {len(scraped)}")
    for it in scraped[:20]:
        print(f"   - {'[NEW] ' if it['is_new'] else ''}{it['title']} | {it['meta']}")

    if FILTER_KEYWORD:
        scraped = [it for it in scraped if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

    # 初回実行：基準登録のみ（過去分は送らない）
    if not seen:
        print(f"[portal-notify] first run baseline: {len(scraped)}")
        for it in scraped:
            seen.add(it["title"])
        save_seen(seen)
        broadcast_message(f"✅ 産業能率大学ポータル監視を開始したで！（基準登録: {len(scraped)}件）")
        return

    # 2回目以降：新着だけを1件ずつ個別通知
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
