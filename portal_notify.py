#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v27
- 「お知らせ」タブをJavaScriptでクリック
- 表示件数を200件に変更して1ページで全件取得
- テーブル構造に合わせて抽出ロジック修正
"""

import os
import json
import sys
import base64
import subprocess
from urllib.parse import urljoin

print("=" * 50, flush=True)
print("=== SCRIPT STARTED v27 ===", flush=True)
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


# ===== お知らせ抽出（v27: HTML構造に合わせて修正） =====
def extract_notices(page):
    items = []

    # 方法1: .camjnext-list-item-contents から直接探す
    contents = page.query_selector_all(".camjnext-list-item-contents")
    print(f"[INFO] Found {len(contents)} .camjnext-list-item-contents elements", flush=True)

    for content in contents:
        try:
            # タイトルリンク
            link = content.query_selector("a")
            if not link:
                continue

            title = link.inner_text().strip()
            if not title or len(title) < 3:
                continue

            # onclickからIDを抽出（selectMsgr(0) → 個別URL生成用）
            onclick = link.get_attribute("onclick") or ""
            # URLは直接取れんから、ページURLをベースにする
            item_url = page.url

            # NEWタグ
            new_tag = content.query_selector(".camjnext-list-tag-new")
            is_new = new_tag is not None

            # 親のtr/tdから日付・送信者・カテゴリを探す
            parent = content.evaluate("el => el.closest('tr')?.innerText || el.closest('td')?.innerText || ''") or ""
            meta = ""
            if parent:
                # 親要素のテキストからメタ情報を抽出
                lines = [l.strip() for l in parent.split("\n") if l.strip() and l.strip() != title]
                if len(lines) >= 3:
                    meta = " / ".join(lines[-3:])  # 最後の3要素をメタとして使う

            if is_new:
                meta += " / 【NEW】" if meta else "【NEW】"

            items.append({
                "title": title[:200],
                "meta": meta,
                "url": item_url,
                "is_new": is_new
            })
        except Exception as e:
            print(f"[WARN] Extract error: {e}", flush=True)
            continue

    if items:
        print(f"[INFO] Method 1 succeeded: {len(items)} items", flush=True)
        return items

    # 方法2: 全リンクをスキャン（フォールバック）
    print(f"[INFO] Method 1 failed. Trying Method 2...", flush=True)
    all_links = page.query_selector_all("a")
    seen_titles = set()

    for link in all_links:
        try:
            onclick = link.get_attribute("onclick") or ""
            if "selectMsgr" not in onclick:
                continue

            title = link.inner_text().strip()
            if not title or title in seen_titles or len(title) < 3:
                continue
            seen_titles.add(title)

            parent = link.evaluate("el => el.parentElement?.innerText || ''") or ""
            is_new = "NEW" in parent

            items.append({
                "title": title[:200],
                "meta": "【NEW】" if is_new else "",
                "url": page.url,
                "is_new": is_new
            })
        except:
            continue

    print(f"[INFO] Method 2 result: {len(items)} items", flush=True)
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
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after portal: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_01_portal.png", full_page=True)

        # --- 2. Ca-Inリンクのhrefを取得して直接遷移 ---
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
        print(f"[INFO] Navigating to Ca-In: {cain_url}", flush=True)

        page.goto(cain_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after Ca-In: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_04_cain.png", full_page=True)
            with open("debug_04_cain.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_04_cain.html/png", flush=True)

        # --- 3. 「お知らせ」タブをJavaScriptでクリック（v27最重要）---
        print("[INFO] Clicking 'お知らせ' tab via JavaScript...", flush=True)
        try:
            page.evaluate("""() => {
                if (typeof searchMgsrIcon === 'function') {
                    searchMgsrIcon('03');
                } else {
                    // searchMgsrIconが見つからん場合は、テキストで探してクリック
                    var links = document.querySelectorAll('a');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].innerText.includes('お知らせ')) {
                            links[i].click();
                            break;
                        }
                    }
                }
            }""")
            page.wait_for_timeout(3000)
            print(f"[INFO] URL after clicking お知らせ tab: {page.url}", flush=True)
        except Exception as e:
            print(f"[WARN] Tab click error: {e}", flush=True)

        # --- 4. 表示件数を200件に変更（v27最重要）---
        print("[INFO] Changing display count to 200...", flush=True)
        try:
            select = page.query_selector("select[name='maxDispListCount']")
            if select:
                select.select_option("200")
                page.wait_for_timeout(3000)
                print(f"[INFO] Changed to 200 items display", flush=True)
            else:
                print(f"[WARN] Select not found, trying JavaScript...", flush=True)
                page.evaluate("""() => {
                    var select = document.querySelector("select[name='maxDispListCount']");
                    if (select) {
                        select.value = "200";
                        select.dispatchEvent(new Event('change'));
                    }
                }""")
                page.wait_for_timeout(3000)
        except Exception as e:
            print(f"[WARN] Display count change error: {e}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_05_notice.png", full_page=True)
            with open("debug_05_notice.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_05_notice.html/png", flush=True)

        # --- 5. お知らせ抽出 ---
        items = extract_notices(page)
        print(f"[INFO] Extracted {len(items)} notices", flush=True)

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
