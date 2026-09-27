#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v25
- 「お知らせ受信一覧」リンクも検索対象に追加
- リンク見つからんかったら直接URLを推測して開く
"""

import os
import json
import sys
import base64
import subprocess
import re
from urllib.parse import urljoin

print("=" * 50, flush=True)
print("=== SCRIPT STARTED v25 ===", flush=True)
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
for i, wh in enumerate(DISCORD_WEBHOOKS):
    print(f"  WEBHOOK[{i}]={wh[:50]}...", flush=True)


# ===== Discord送信（curl版・Cloudflare対策） =====
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

    payload_json = json.dumps({"embeds": [embed]}, ensure_ascii=False)

    for wh in DISCORD_WEBHOOKS:
        cmd = [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-d", payload_json,
            wh
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            status = result.stdout.strip()
            print(f"[OK] Discord embed sent: status={status}", flush=True)
            if status not in ("200", "201", "204"):
                print(f"[WARN] Unexpected status: {status}, stderr={result.stderr[:200]}", flush=True)
        except Exception as e:
            print(f"[NG] Discord failed: {e}", flush=True)


def send_text(text):
    payload_json = json.dumps({"content": text}, ensure_ascii=False)

    for wh in DISCORD_WEBHOOKS:
        cmd = [
            "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "-d", payload_json,
            wh
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            status = result.stdout.strip()
            print(f"[OK] Discord text sent: status={status}", flush=True)
            if status not in ("200", "201", "204"):
                print(f"[WARN] Unexpected status: {status}, stderr={result.stderr[:200]}", flush=True)
        except Exception as e:
            print(f"[NG] Discord failed: {e}", flush=True)


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

        # --- 1. ポータルページにアクセス ---
        print(f"[INFO] Opening {PORTAL_URL}", flush=True)
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45000)
        print(f"[INFO] URL after goto: {page.url}", flush=True)

        print("[INFO] Waiting 5 seconds for page to settle...", flush=True)
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after wait: {page.url}", flush=True)

        html = page.content()
        print(f"[DEBUG] Page HTML length: {len(html)} chars", flush=True)

        body_text = page.locator("body").inner_text() or ""
        print(f"[DEBUG] Body text length: {len(body_text)} chars", flush=True)

        if DEBUG:
            page.screenshot(path="debug_01_portal.png", full_page=True)
            with open("debug_01_portal.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("[DEBUG] Saved debug_01_portal.html/png", flush=True)

        # --- 2. ページ診断 ---
        print("[INFO] Diagnosing page state...", flush=True)
        has_cain = page.locator("#cain").count() > 0
        has_student_id = SANNO_ID in body_text
        link_count = len(page.query_selector_all("a"))

        print(f"[INFO] has_cain={has_cain}, has_student_id={has_student_id}, links={link_count}", flush=True)

        if not has_cain and not has_student_id:
            print("[ERROR] Failed to enter portal page after Basic auth.", flush=True)
            browser.close()
            sys.exit(2)

        print("[INFO] Portal page loaded successfully!", flush=True)

        # --- 3. Ca-Inリンクのhrefを取得して直接遷移 ---
        print("[INFO] Getting Ca-In link URL...", flush=True)
        cain_href = None

        cain_elem = page.locator("#cain").first
        if cain_elem.count() > 0:
            cain_href = cain_elem.get_attribute("href")
            print(f"[INFO] Found #cain href: {cain_href}", flush=True)

        if not cain_href:
            for link in page.query_selector_all("a"):
                href = link.get_attribute("href") or ""
                if "camj_pc" in href:
                    cain_href = href
                    print(f"[INFO] Found camj_pc href: {cain_href}", flush=True)
                    break

        if not cain_href:
            print("[ERROR] Ca-In link not found!", flush=True)
            browser.close()
            sys.exit(3)

        cain_url = urljoin(page.url, cain_href)
        print(f"[INFO] Navigating directly to Ca-In: {cain_url}", flush=True)

        page.goto(cain_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after Ca-In navigation: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_04_cain.png", full_page=True)
            with open("debug_04_cain.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_04_cain.html/png", flush=True)

        # --- 4. 「お知らせ」リンクを探す（v25: より広く検索）---
        print("[INFO] Looking for notice link...", flush=True)
        notice_link = None
        notice_href = None

        # 方法1: セレクタで探す
        notice_selectors = [
            'a:has-text("お知らせ受信一覧")',
            'a:has-text("お知らせ")',
            'a:has-text("大学からのお知らせ")',
            'a[href*="wbasmgjr"]',
            'a[href*="WBASMGJR"]',
            'a[href*="notice"]',
        ]

        for sel in notice_selectors:
            if page.locator(sel).count() > 0:
                notice_link = page.locator(sel).first
                notice_href = notice_link.get_attribute("href")
                print(f"[INFO] Found notice link by selector: {sel} -> {notice_href}", flush=True)
                break

        # 方法2: 全リンクをスキャン（テキストで「お知らせ」を含むリンク）
        if not notice_link:
            print("[INFO] Scanning all links for notice...", flush=True)
            for link in page.query_selector_all("a"):
                txt = link.inner_text().strip()
                href = link.get_attribute("href") or ""
                if "お知らせ" in txt or "受信一覧" in txt:
                    notice_link = link
                    notice_href = href
                    print(f"[INFO] Found notice link by text scan: '{txt}' -> {href}", flush=True)
                    break

        # 方法3: 直接URLを推測して開く
        if not notice_href:
            # top.do のURLから base URL を取得
            base_url = page.url.replace("top.do", "")
            guess_url = base_url + "campussquare.do?_flowId=WBASMGJRFlow"
            print(f"[INFO] No notice link found. Guessing URL: {guess_url}", flush=True)
            notice_href = guess_url

        # お知らせページに遷移
        if notice_href:
            notice_url = urljoin(page.url, notice_href)
            print(f"[INFO] Navigating to notice page: {notice_url}", flush=True)
            page.goto(notice_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            print(f"[INFO] URL after notice navigation: {page.url}", flush=True)

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

    is_first_run = not seen
    print(f"[INFO] is_first_run={is_first_run}, items={len(items)}, seen_before={len(seen)}", flush=True)

    # 初回：基準登録のみ
    if is_first_run:
        print(f"[INFO] First run! Registering {len(items)} items as baseline.", flush=True)
        for it in items:
            seen.add(it["url"])
        save_seen(seen)
        send_text(f"✅ 産業能率大学ポータル監視を開始したで！（基準登録: {len(items)}件）\nこれから新着があったら通知するわ。")
        print("=== MAIN END (first run) ===", flush=True)
    else:
        # 新着判定
        new_items = [it for it in items if it["url"] not in seen]
        print(f"[INFO] New items: {len(new_items)}", flush=True)

        if new_items:
            print(f"[INFO] Sending {len(new_items[:MAX_NOTIFY])} notifications...", flush=True)
            for it in new_items[:MAX_NOTIFY]:
                send_discord(it["title"], it["meta"], it["url"], it["is_new"])
                seen.add(it["url"])
            save_seen(seen)
            print(f"=== MAIN END (notified {len(new_items[:MAX_NOTIFY])} items) ===", flush=True)
        else:
            print("[INFO] No new notices. Exiting quietly.", flush=True)
            print("=== MAIN END (no changes) ===", flush=True)


if __name__ == "__main__":
    print("=== ENTRY POINT ===", flush=True)
    main()
    print("=== SCRIPT END ===", flush=True)
