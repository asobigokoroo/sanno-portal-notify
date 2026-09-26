#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v17
- signweb.mi.sanno.ac.jp/portal/ にアクセス（Basic認証 or フォーム）
- ログイン後、Ca-Inリンク（id=cain）をクリック
- Ca-Inのお知らせを取得してDiscord通知
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
PORTAL_URL = "https://signweb.mi.sanno.ac.jp/portal/"
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


# ===== お知らせ抽出（Ca-Inページ用・汎用） =====
def extract_notices(page):
    items = []
    
    # 方法1: テーブル行から抽出（既存のセレクタ）
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

        href = link.get_attribute("href")
        if href:
            item_url = urljoin(page.url, href)
        else:
            item_url = page.url

        tds = row.query_selector_all("td")
        date_str = tds[3].inner_text().strip() if len(tds) > 3 else ""
        sender = tds[4].inner_text().strip() if len(tds) > 4 else ""
        category = tds[5].inner_text().strip() if len(tds) > 5 else ""

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

    # 方法2: テーブルが無かったら、ページ内のリンクから「お知らせ」っぽいものを探す（fallback）
    if not items:
        print("[INFO] No table found. Trying generic link extraction...", flush=True)
        all_links = page.query_selector_all("a")
        for link in all_links:
            href = link.get_attribute("href") or ""
            text = link.inner_text().strip()
            # お知らせっぽいリンクを探す（日付っぽい文字列や「お知らせ」を含むもの）
            if text and len(text) > 5 and ("お知らせ" in text or "通知" in text or re.search(r"\d{4}[/\-]\d{1,2}[/\-]\d{1,2}", text)):
                item_url = urljoin(page.url, href) if href else page.url
                items.append({
                    "title": text[:200],
                    "meta": "",
                    "url": item_url,
                    "is_new": False
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
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},  # Basic認証用
            viewport={"width": 1400, "height": 1000}
        )
        page = context.new_page()

        # --- 1. ポータルページにアクセス ---
        print(f"[INFO] Opening {PORTAL_URL}", flush=True)
        page.goto(PORTAL_URL, wait_until="networkidle", timeout=45000)
        print(f"[INFO] URL after portal: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_01_portal.png", full_page=True)
            with open("debug_01_portal.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_01_portal.html/png", flush=True)

        # --- 2. ログインフォームがあれば入力（Basic認証失敗時のfallback）---
        # signwebのログインフォームを探す（idがuserIdやったら）
        login_form = page.locator("#userId")
        if login_form.count() > 0 and login_form.first.is_visible():
            print("[INFO] Login form found on portal. Logging in...", flush=True)

            if DEBUG:
                page.screenshot(path="debug_02_login.png", full_page=True)

            page.fill("#userId", SANNO_ID)
            page.fill("#password", SANNO_PASS)

            # buttonNameをセットして送信
            print("[INFO] Submitting login form...", flush=True)
            try:
                with page.expect_navigation(timeout=15000):
                    page.evaluate("""() => {
                        var dummy = document.getElementById('loginButtonDummy');
                        if (dummy) dummy.value = 'login';
                        var form = document.forms['loginForm'];
                        if (form) form.submit();
                    }""")
                print(f"[INFO] URL after login: {page.url}", flush=True)
            except Exception as e:
                print(f"[WARN] Login navigation issue: {e}", flush=True)
                page.wait_for_load_state("networkidle", timeout=10000)
                print(f"[INFO] URL after wait: {page.url}", flush=True)

            if DEBUG:
                page.screenshot(path="debug_03_after_login.png", full_page=True)
                with open("debug_03_after_login.html", "w", encoding="utf-8") as f:
                    f.write(page.content())

        # --- 3. ポータルページに入れたか確認 ---
        # 学籍番号が表示されてるか、Ca-Inリンクがあるかで判定
        page_text = page.locator("body").inner_text()
        has_cain_link = page.locator("#cain").count() > 0 or page.locator('a[href*="camj_pc"]').count() > 0
        has_student_id = SANNO_ID in page_text

        print(f"[INFO] Has Ca-In link: {has_cain_link}, Has student ID: {has_student_id}", flush=True)

        if not has_cain_link and not has_student_id:
            print("[ERROR] Failed to enter portal page. Check ID/PASS or page structure.", flush=True)
            if DEBUG:
                page.screenshot(path="debug_error_not_portal.png", full_page=True)
            browser.close()
            sys.exit(2)

        print("[INFO] Portal page loaded successfully!", flush=True)

        # --- 4. Ca-Inリンクをクリック ---
        print("[INFO] Looking for Ca-In link...", flush=True)
        cain_link = None

        # 方法1: id="cain" で探す
        if page.locator("#cain").count() > 0:
            cain_link = page.locator("#cain").first
            print("[INFO] Found Ca-In link by #cain", flush=True)
        # 方法2: hrefに camj_pc を含むリンクを探す
        elif page.locator('a[href*="camj_pc"]').count() > 0:
            cain_link = page.locator('a[href*="camj_pc"]').first
            print("[INFO] Found Ca-In link by href", flush=True)

        if not cain_link:
            print("[ERROR] Ca-In link not found!", flush=True)
            if DEBUG:
                page.screenshot(path="debug_error_no_cain.png", full_page=True)
            browser.close()
            sys.exit(3)

        print("[INFO] Clicking Ca-In link...", flush=True)
        try:
            with page.expect_navigation(timeout=15000):
                cain_link.click()
            page.wait_for_timeout(3000)
            print(f"[INFO] URL after Ca-In click: {page.url}", flush=True)
        except Exception as e:
            print(f"[WARN] Ca-In click navigation issue: {e}", flush=True)
            page.wait_for_timeout(3000)
            print(f"[INFO] URL after wait: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_04_cain.png", full_page=True)
            with open("debug_04_cain.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_04_cain.html/png", flush=True)

        # --- 5. Ca-Inページ内で「お知らせ」リンクを探す ---
        # Ca-Inのトップページに「お知らせ」へのリンクがあるかも
        print("[INFO] Looking for notice link in Ca-In...", flush=True)
        notice_link = None

        notice_selectors = [
            'a:has-text("お知らせ")',
            'a:has-text("大学からのお知らせ")',
            'a[href*="wbasmgjr"]',
            'a[href*="notice"]',
            'a[href*="information"]',
        ]

        for sel in notice_selectors:
            if page.locator(sel).count() > 0:
                notice_link = page.locator(sel).first
                print(f"[INFO] Found notice link: {sel}", flush=True)
                break

        if notice_link:
            print("[INFO] Clicking notice link...", flush=True)
            try:
                with page.expect_navigation(timeout=15000):
                    notice_link.click()
                page.wait_for_timeout(3000)
                print(f"[INFO] URL after notice click: {page.url}", flush=True)
            except Exception as e:
                print(f"[WARN] Notice click issue: {e}", flush=True)

            if DEBUG:
                page.screenshot(path="debug_05_notice.png", full_page=True)
                with open("debug_05_notice.html", "w", encoding="utf-8") as f:
                    f.write(page.content())

        # --- 6. お知らせ抽出 ---
        items = extract_notices(page)
        print(f"[INFO] Extracted {len(items)} notices", flush=True)

        # ページネーション
        page_num = 1
        while len(items) < 90 and page_num < 10:
            next_btn = None
            for link in page.query_selector_all("a"):
                txt = link.inner_text().strip()
                if txt in (">", "›", "→", "次へ", "Next"):
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
        send_text(f"✅ 産業能率大学ポータル監視を開始したで！（基準登録: {len(items)}件）\nこれから新着があったら通知するわ。")
        print("=== MAIN END (first run) ===", flush=True)
        return

    # 新着判定
    new_items = [it for it in items if it["url"] not in seen]
    print(f"[INFO] New items: {len(new_items)}", flush=True)

    if not new_items:
        print("[INFO] No new notices. Exiting quietly.", flush=True)
        print("=== MAIN END (no changes) ===", flush=True)
        return

    # 通知
    print(f"[INFO] Sending {len(new_items[:MAX_NOTIFY])} notifications...", flush=True)
    for it in new_items[:MAX_NOTIFY]:
        send_discord(it["title"], it["meta"], it["url"], it["is_new"])
        seen.add(it["url"])

    save_seen(seen)
    print(f"=== MAIN END (notified {len(new_items[:MAX_NOTIFY])} items) ===", flush=True)


if __name__ == "__main__":
    print("=== ENTRY POINT ===", flush=True)
    main()
    print("=== SCRIPT END ===", flush=True)
