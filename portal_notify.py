#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v9
- ログインページでID/PASSを手入力してログイン
- お知らせ受信一覧を巡回
- 新着だけをDiscordに通知
"""

import os
import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

# お知らせ受信一覧ページ（直接アクセス）
NOTICE_URL = "https://portal-xs.mi.sanno.ac.jp/campusweb/wbasmgjr.do?clearAccessData=true&contenam=wbasmgjr&kjnmnNo=2"
LOGIN_URL = "https://signweb.mi.sanno.ac.jp/portal/"
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
        "url": NOTICE_URL,
        "color": 0x1E88E5,
    }
    if item.get("meta"):
        embed["description"] = item["meta"][:400]
    if item.get("is_new"):
        embed["color"] = 0xFF4444
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
    rows = page.query_selector_all("table tbody tr, table tr")
    print(f"[portal-notify] table rows found: {len(rows)}")

    for row in rows:
        title_cell = row.query_selector(".camjnext-list-item-contents")
        if not title_cell:
            continue

        a = title_cell.query_selector("a")
        if not a:
            continue

        title = a.inner_text().strip()
        if not title or len(title) < 4:
            continue

        tds = row.query_selector_all("td")
        date_str = tds[3].inner_text().strip() if len(tds) > 3 else ""
        sender   = tds[4].inner_text().strip() if len(tds) > 4 else ""
        category = tds[5].inner_text().strip() if len(tds) > 5 else ""

        new_tag = title_cell.query_selector(".camjnext-list-tag-new")
        is_new = new_tag is not None

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
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        # --- 1. ログインページへ ---
        print(f"[portal-notify] open login page: {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)

        if DEBUG:
            page.screenshot(path="debug_01_login_page.png", full_page=True)
            with open("debug_01_login_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] saved debug_01_login_page.html / .png")

        # --- 2. ログインフォームの要素を探す ---
        id_selectors = [
            'input[name="j_username"]',
            'input[name="loginId"]',
            'input[name="userID"]',
            'input[name="id"]',
            'input[type="text"]',
        ]
        pass_selectors = [
            'input[name="j_password"]',
            'input[name="password"]',
            'input[type="password"]',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("ログイン")',
            'input[value="ログイン"]',
        ]

        id_sel = next((s for s in id_selectors if page.locator(s).count() > 0), None)
        pass_sel = next((s for s in pass_selectors if page.locator(s).count() > 0), None)
        submit_sel = next((s for s in submit_selectors if page.locator(s).count() > 0), None)

        if not id_sel or not pass_sel or not submit_sel:
            print("[error] ログインフォームの要素が見つからへん。")
            if DEBUG:
                page.screenshot(path="debug_error_no_form.png", full_page=True)
            browser.close()
            sys.exit(2)

        print(f"[portal-notify] form detected: id={id_sel}, pass={pass_sel}, submit={submit_sel}")

        # --- 3. ログイン実行 ---
        try:
            page.fill(id_sel, SANNO_ID)
            page.fill(pass_sel, SANNO_PASS)
            page.click(submit_sel)
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            print(f"[error] ログイン送信でエラー: {e}")
            if DEBUG:
                page.screenshot(path="debug_error_submit.png", full_page=True)
            browser.close()
            sys.exit(3)

        if DEBUG:
            page.screenshot(path="debug_02_after_login.png", full_page=True)
            with open("debug_02_after_login.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"[DEBUG] after login url: {page.url}")

        # --- 4. お知らせページへ ---
        print(f"[portal-notify] open notice list: {NOTICE_URL}")
        page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")

        if "signweb" in page.url or "login" in page.url.lower():
            print("[error] ログイン後も認証ページに戻されたで。ID/PASSを確認してな。")
            if DEBUG:
                page.screenshot(path="debug_error_auth.png", full_page=True)
            browser.close()
            sys.exit(4)

        page.wait_for_timeout(4000)

        if DEBUG:
            page.screenshot(path="debug_03_notice_page.png", full_page=True)
            with open("debug_03_notice_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            preview = page.content()[:1500].replace("\n", " ").replace("  ", " ")
            print(f"[DEBUG] html_preview={preview}...")
            rows = page.query_selector_all("table tr")
            print(f"[DEBUG] total rows: {len(rows)}")
            for i, row in enumerate(rows[:5]):
                txt = row.inner_text().replace("\n", " | ")
                print(f"[DEBUG] row {i}: {txt[:150]}")

        # --- 5. お知らせ抽出 ---
        all_items = extract_notices_from_page(page)

        # ページネーション
        page_num = 1
        while len(all_items) < 89 and page_num < 15:
            next_links = page.query_selector_all("a")
            next_btn = None
            for link in next_links:
                txt = link.inner_text().strip()
                if txt in (">", "›", "→", "次へ"):
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
                existing = {it["title"] for it in all_items}
                for it in items:
                    if it["title"] not in existing:
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
