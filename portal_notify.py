#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v12
- top.do にログイン → お知らせページへ遷移
- JavaScriptのexec()関数を使ったログイン対応
- 個別のお知らせURLを取得してDiscord通知
"""

import os
import json
import sys
import urllib.request
from urllib.parse import urljoin

print("=" * 50, flush=True)
print("=== SCRIPT STARTED ===", flush=True)
print("=" * 50, flush=True)

from playwright.sync_api import sync_playwright

print("=== IMPORT OK ===", flush=True)

# ===== 設定 =====
TOP_URL = "https://portal-xs.mi.sanno.ac.jp/campusweb/top.do"
NOTICE_URL = "https://portal-xs.mi.sanno.ac.jp/campusweb/wbasmgjr.do?clearAccessData=true&contenam=wbasmgjr&kjnmnNo=2"
SEEN_FILE = "seen.json"
DEBUG = os.environ.get("DEBUG", "0") == "1"

SANNO_ID = os.environ.get("SANNO_ID", "").strip()
SANNO_PASS = os.environ.get("SANNO_PASS", "").strip()
DISCORD_WEBHOOKS = [w.strip() for w in os.environ.get("DISCORD_WEBHOOK", "").split(",") if w.strip()]
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "").strip()
MAX_NOTIFY = int(os.environ.get("MAX_NOTIFY_PER_RUN", "20"))

print(f"DEBUG={DEBUG}, ID={'set' if SANNO_ID else 'EMPTY'}, PASS={'set' if SANNO_PASS else 'EMPTY'}, WEBHOOKS={len(DISCORD_WEBHOOKS)}", flush=True)


# ===== Discord送信 =====
def send_discord(title, meta, url, is_new=False):
    color = 0xFF4444 if is_new else 0x1E88E5
    embed = {
        "title": title[:250] or "(無題)",
        "url": url,
        "color": color,
        "footer": {"text": "産業能率大学ポータル"}
    }
    if meta:
        embed["description"] = meta[:500]

    payload = {"embeds": [embed]}
    for wh in DISCORD_WEBHOOKS:
        try:
            req = urllib.request.Request(
                wh,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                print(f"[OK] Discord sent: status={r.status}", flush=True)
        except Exception as e:
            print(f"[NG] Discord failed: {e}", flush=True)


def send_text(text):
    for wh in DISCORD_WEBHOOKS:
        try:
            req = urllib.request.Request(
                wh,
                data=json.dumps({"content": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                print(f"[OK] Discord text sent: status={r.status}", flush=True)
        except Exception as e:
            print(f"[NG] Discord text failed: {e}", flush=True)


# ===== seen.json =====
def load_seen():
    if not os.path.exists(SEEN_FILE):
        print(f"[INFO] {SEEN_FILE} not found. First run.", flush=True)
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            data = set(d if isinstance(d, list) else [])
            print(f"[INFO] loaded seen: {len(data)} items", flush=True)
            return data
    except Exception as e:
        print(f"[WARN] load_seen failed: {e}", flush=True)
        return set()


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_set))[-2000:], f, ensure_ascii=False, indent=2)
        print(f"[INFO] saved seen: {len(seen_set)} items", flush=True)
    except Exception as e:
        print(f"[WARN] save_seen failed: {e}", flush=True)


# ===== お知らせ抽出 =====
def extract_notices(page):
    items = []
    rows = page.query_selector_all("table tbody tr, table tr")
    print(f"[INFO] Found {len(rows)} table rows", flush=True)

    for row in rows:
        title_cell = row.query_selector(".camjnext-list-item-contents")
        if not title_cell:
            continue

        link = title_cell.query_selector("a")
        if not link:
            continue

        title = link.inner_text().strip()
        if not title or len(title) < 3:
            continue

        # 個別URLを取得
        href = link.get_attribute("href")
        if href:
            item_url = urljoin(page.url, href)
        else:
            item_url = NOTICE_URL

        # 日付・送信者・種別
        tds = row.query_selector_all("td")
        date_str = tds[3].inner_text().strip() if len(tds) > 3 else ""
        sender = tds[4].inner_text().strip() if len(tds) > 4 else ""
        category = tds[5].inner_text().strip() if len(tds) > 5 else ""

        # NEWタグ
        new_tag = title_cell.query_selector(".camjnext-list-tag-new")
        is_new = new_tag is not None

        meta_parts = [p for p in [date_str, sender, category] if p]
        meta = " / ".join(meta_parts)
        if is_new:
            meta += " / 【NEW】"

        items.append({
            "title": title[:200],
            "meta": meta,
            "url": item_url,
            "is_new": is_new
        })

    return items


# ===== メイン処理 =====
def scrape():
    print("=== SCRAPE START ===", flush=True)
    if not SANNO_ID or not SANNO_PASS:
        print("[ERROR] SANNO_ID or SANNO_PASS is empty!", flush=True)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1400, "height": 1000}
        )
        page = context.new_page()

        # --- 1. top.do にアクセス ---
        print(f"[INFO] Opening {TOP_URL}", flush=True)
        page.goto(TOP_URL, wait_until="networkidle", timeout=45000)
        print(f"[INFO] URL after top.do: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_01_top.png", full_page=True)
            with open("debug_01_top.html", "w", encoding="utf-8") as f:
                f.write(page.content())

        # --- 2. ログインフォームがあれば入力 ---
        if page.locator("#userId").count() > 0:
            print("[INFO] Login form found. Logging in...", flush=True)

            if DEBUG:
                page.screenshot(path="debug_02_login.png", full_page=True)

            page.fill("#userId", SANNO_ID)
            page.fill("#password", SANNO_PASS)

            # JavaScriptのexec()関数を使ってログイン
            print("[INFO] Calling exec() via JavaScript...", flush=True)
            try:
                result = page.evaluate("""() => {
                    if (typeof exec === 'function') {
                        exec('login', document.getElementById('loginButtonDummy'), null);
                        return 'exec_called';
                    }
                    return 'exec_not_found';
                }""")
                print(f"[INFO] exec() result: {result}", flush=True)
            except Exception as e:
                print(f"[WARN] exec() failed: {e}", flush=True)
                # fallback: フォーム直接submit
                page.evaluate("document.forms['loginForm'].submit()")
                print("[INFO] Submitted form directly", flush=True)

            page.wait_for_load_state("networkidle", timeout=30000)
            print(f"[INFO] URL after login: {page.url}", flush=True)

            if DEBUG:
                page.screenshot(path="debug_03_after_login.png", full_page=True)
                with open("debug_03_after_login.html", "w", encoding="utf-8") as f:
                    f.write(page.content())

        # --- 3. お知らせページへ ---
        print(f"[INFO] Opening notice page: {NOTICE_URL}", flush=True)
        page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
        print(f"[INFO] URL after notice: {page.url}", flush=True)

        # まだログイン画面やったら失敗
        if page.locator("#userId").count() > 0:
            print("[ERROR] Still on login page after login! Check ID/PASS.", flush=True)
            if DEBUG:
                page.screenshot(path="debug_error_still_login.png", full_page=True)
            browser.close()
            sys.exit(2)

        page.wait_for_timeout(3000)

        if DEBUG:
            page.screenshot(path="debug_04_notice.png", full_page=True)
            with open("debug_04_notice.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_04_notice.png/html", flush=True)

        # --- 4. お知らせ抽出 ---
        items = extract_notices(page)
        print(f"[INFO] Extracted {len(items)} notices", flush=True)

        # ページネーション
        page_num = 1
        while len(items) < 90 and page_num < 10:
            next_btn = None
            for link in page.query_selector_all("a"):
                txt = link.inner_text().strip()
                if txt in (">", "›", "→", "次へ"):
                    cls = link.get_attribute("class") or ""
                    if "disabled" not in cls and "inactive" not in cls:
                        next_btn = link
                        break

            if not next_btn:
                break

            try:
                print(f"[INFO] Clicking next page ({page_num + 1})...", flush=True)
                next_btn.click()
                page.wait_for_timeout(3000)
                page_num += 1
                new_items = extract_notices(page)
                existing_urls = {it["url"] for it in items}
                for it in new_items:
                    if it["url"] not in existing_urls:
                        items.append(it)
            except Exception as e:
                print(f"[WARN] Pagination error: {e}", flush=True)
                break

        browser.close()
        print(f"=== SCRAPE DONE: {len(items)} items ===", flush=True)
        return items


def main():
    print("=== MAIN START ===", flush=True)

    if not DISCORD_WEBHOOKS:
        print("[ERROR] No Discord webhooks configured!", flush=True)
        sys.exit(1)

    seen = load_seen()
    items = scrape()

    if FILTER_KEYWORD:
        items = [it for it in items if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

    # 初回：基準登録のみ
    if not seen:
        print(f"[INFO] First run! Registering {len(items)} items as baseline.", flush=True)
        for it in items:
            seen.add(it["url"])
        save_seen(seen)
        send_text(f"✅ 産業能率大学ポータルの監視を開始したで！（基準登録: {len(items)}件）")
        print("=== MAIN END (first run) ===", flush=True)
        return

    # 新着判定
    new_items = [it for it in items if it["url"] not in seen]
    print(f"[INFO] New items: {len(new_items)}", flush=True)

    if not new_items:
        print("[INFO] No new notices.", flush=True)
        print("=== MAIN END (no news) ===", flush=True)
        return

    # 通知
    for it in new_items[:MAX_NOTIFY]:
        send_discord(it["title"], it["meta"], it["url"], it["is_new"])
        seen.add(it["url"])

    save_seen(seen)
    print(f"=== MAIN END (notified {len(new_items[:MAX_NOTIFY])} items) ===", flush=True)


if __name__ == "__main__":
    print("=== ENTRY POINT ===", flush=True)
    main()
    print("=== SCRIPT END ===", flush=True)
