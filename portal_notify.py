#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v21
- Basic認証を Authorization ヘッダーで確実に通過
- ポータル→Ca-In→お知らせ の流れ
"""

import os
import json
import sys
import base64
import urllib.request
from urllib.parse import urljoin

print("=" * 50, flush=True)
print("=== SCRIPT STARTED v21 ===", flush=True)
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

    return items


# ===== メイン処理 =====
def scrape():
    print("=== SCRAPE START ===", flush=True)
    if not SANNO_ID or not SANNO_PASS:
        print("[ERROR] SANNO_ID or SANNO_PASS is empty!", flush=True)
        sys.exit(1)

    # Basic認証用の Authorization ヘッダーを生成
    auth_str = base64.b64encode(f"{SANNO_ID}:{SANNO_PASS}".encode()).decode()
    auth_header = f"Basic {auth_str}"
    print(f"[INFO] Authorization header generated (length={len(auth_header)})", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            extra_http_headers={"Authorization": auth_header},
            viewport={"width": 1400, "height": 1000}
        )
        page = context.new_page()

        # --- 1. ポータルページにアクセス（Basic認証をAuthorizationヘッダーで通過）---
        print(f"[INFO] Opening {PORTAL_URL}", flush=True)
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45000)
        print(f"[INFO] URL after goto: {page.url}", flush=True)

        # 強制待機（JavaScriptで動的生成されるページ対策）
        print("[INFO] Waiting 5 seconds for page to settle...", flush=True)
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after wait: {page.url}", flush=True)

        # ページのHTMLを必ずログに出力
        html = page.content()
        print(f"[DEBUG] Page HTML length: {len(html)} chars", flush=True)
        preview = html[:2000].replace("\n", " ").replace("  ", " ")
        print(f"[DEBUG] HTML preview: {preview}", flush=True)

        body_text = page.locator("body").inner_text() or ""
        print(f"[DEBUG] Body text length: {len(body_text)} chars", flush=True)
        print(f"[DEBUG] Body preview: {body_text[:500]}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_01_portal.png", full_page=True)
            with open("debug_01_portal.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("[DEBUG] Saved debug_01_portal.html/png", flush=True)

        # --- 2. ページの状態を診断 ---
        print("[INFO] Diagnosing page state...", flush=True)

        has_cain = page.locator("#cain").count() > 0
        has_student_id = SANNO_ID in body_text
        has_login_form = page.locator('form[name="loginForm"]').count() > 0
        has_userid = page.locator("#userId").count() > 0
        has_password = page.locator('input[type="password"]').count() > 0
        link_count = len(page.query_selector_all("a"))

        print(f"[INFO] has_cain={has_cain}, has_student_id={has_student_id}, has_login_form={has_login_form}, has_userid={has_userid}, has_password={has_password}, links={link_count}", flush=True)

        # ポータルページに入れたか確認
        if not has_cain and not has_student_id:
            print("[ERROR] Failed to enter portal page after Basic auth.", flush=True)
            if DEBUG:
                page.screenshot(path="debug_error_not_portal.png", full_page=True)
                links = page.query_selector_all("a")
                print(f"[DEBUG] All links ({len(links)}):", flush=True)
                for i, link in enumerate(links[:20]):
                    txt = link.inner_text().strip()[:50]
                    href = link.get_attribute("href") or ""
                    print(f"  [{i}] {txt} -> {href}", flush=True)
            browser.close()
            sys.exit(2)

        print("[INFO] Portal page loaded successfully!", flush=True)

        # --- 3. Ca-Inリンクをクリック ---
        print("[INFO] Looking for Ca-In link...", flush=True)
        cain_link = None

        if page.locator("#cain").count() > 0:
            cain_link = page.locator("#cain").first
            print("[INFO] Found Ca-In by #cain", flush=True)
        elif page.locator('a[href*="camj_pc"]').count() > 0:
            cain_link = page.locator('a[href*="camj_pc"]').first
            print("[INFO] Found Ca-In by href", flush=True)

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
            page.wait_for_timeout(5000)  # SSOで自動ログインされるのを待つ
            print(f"[INFO] URL after Ca-In: {page.url}", flush=True)
        except Exception as e:
            print(f"[WARN] Ca-In click issue: {e}", flush=True)
            page.wait_for_timeout(5000)
            print(f"[INFO] URL after wait: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_04_cain.png", full_page=True)
            with open("debug_04_cain.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_04_cain.html/png", flush=True)

        # --- 4. Ca-Inページ内で「お知らせ」リンクを探す ---
        print("[INFO] Looking for notice link...", flush=True)
        notice_link = None

        notice_selectors = [
            'a:has-text("お知らせ")',
            'a:has-text("大学からのお知らせ")',
            'a[href*="wbasmgjr"]',
            'a[href*="notice"]',
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
                print(f"[INFO] URL after notice: {page.url}", flush=True)
            except Exception as e:
                print(f"[WARN] Notice click issue: {e}", flush=True)

            if DEBUG:
                page.screenshot(path="debug_05_notice.png", full_page=True)
                with open("debug_05_notice.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                print("[DEBUG] Saved debug_05_notice.html/png", flush=True)

        # --- 5. お知らせ抽出 ---
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
