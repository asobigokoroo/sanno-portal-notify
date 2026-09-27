#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v28
- doPagingを直接JavaScript実行して200件表示に変更
- ページネーションで残りも取得
"""

import os
import json
import sys
import base64
import subprocess
from urllib.parse import urljoin

print("=" * 50, flush=True)
print("=== SCRIPT STARTED v28 ===", flush=True)
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
    contents = page.query_selector_all(".camjnext-list-item-contents")
    print(f"[INFO] Found {len(contents)} .camjnext-list-item-contents elements", flush=True)

    seen_titles = set()

    for content in contents:
        try:
            link = content.query_selector("a")
            if not link:
                continue

            title = link.inner_text().strip()
            if not title or len(title) < 3 or title in seen_titles:
                continue
            seen_titles.add(title)

            onclick = link.get_attribute("onclick") or ""
            item_url = page.url

            new_tag = content.query_selector(".camjnext-list-tag-new")
            is_new = new_tag is not None

            # 親のtrからメタ情報を取得
            parent_text = content.evaluate("el => el.closest('tr')?.innerText || ''") or ""
            meta = ""
            if parent_text:
                lines = [l.strip() for l in parent_text.split("\n") if l.strip() and l.strip() != title]
                # 日付・送信者・カテゴリを探す
                meta_lines = []
                for line in lines:
                    if any(c in line for c in ["2026/", "2025/", "教務課", "サービス", "センター", "お知らせ", "重要", "休講"]):
                        meta_lines.append(line)
                if meta_lines:
                    meta = " / ".join(meta_lines[-3:])

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

    print(f"[INFO] Extracted {len(items)} unique notices", flush=True)
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

        # --- 1. ポータルページ → Ca-In ---
        print(f"[INFO] Opening {PORTAL_URL}", flush=True)
        page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)

        cain_elem = page.locator("#cain").first
        cain_href = cain_elem.get_attribute("href") if cain_elem.count() > 0 else None
        if not cain_href:
            for link in page.query_selector_all("a"):
                href = link.get_attribute("href") or ""
                if "camj_pc" in href:
                    cain_href = href
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

        # --- 2. 「お知らせ」タブをクリック ---
        print("[INFO] Clicking お知らせ tab...", flush=True)
        page.evaluate("""() => {
            if (typeof searchMgsrIcon === 'function') {
                searchMgsrIcon('03');
            } else {
                var links = document.querySelectorAll('a');
                for (var i = 0; i < links.length; i++) {
                    if (links[i].innerText.includes('お知らせ')) {
                        links[i].click();
                        break;
                    }
                }
            }
        }""")
        page.wait_for_timeout(5000)
        print(f"[INFO] URL after お知らせ click: {page.url}", flush=True)

        # --- 3. 表示件数を200件に変更（v28: doPagingを直接呼ぶ）---
        print("[INFO] Changing display to 200 items...", flush=True)
        page.evaluate("""() => {
            if (typeof doPaging === 'function') {
                doPaging('hojrForm', 'changeStateList', 'pageCount', '', 'maxCount', '200');
            } else {
                var select = document.querySelector("select[name='maxDispListCount']");
                if (select) {
                    select.value = '200';
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        }""")
        page.wait_for_timeout(8000)  # ページ再読み込み待ち
        print(f"[INFO] URL after changing to 200: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_05_notice.png", full_page=True)
            with open("debug_05_notice.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_05_notice.html/png", flush=True)

        # --- 4. お知らせ抽出 ---
        all_items = extract_notices(page)
        print(f"[INFO] Page 1: {len(all_items)} items", flush=True)

        # --- 5. ページネーション（200件でも全部入らん場合）---
        page_num = 1
        while len(all_items) < 90 and page_num < 10:
            # 「次へ」ボタンを探す
            next_found = False
            for link in page.query_selector_all("a"):
                try:
                    cls = link.get_attribute("class") or ""
                    onclick = link.get_attribute("onclick") or ""
                    # 次へボタン: classにnextまたはonclickにdoPaging(...pageCount,'2'...)
                    if ("camjnext-pagination-next" in cls or 
                        ("doPaging" in onclick and f"'{page_num + 1}'" in onclick)):
                        # disabledチェック
                        if "disabled" in cls or link.is_disabled():
                            print(f"[INFO] Next button is disabled. End of pages.", flush=True)
                            next_found = False
                            break
                        print(f"[INFO] Clicking next page ({page_num + 1})...", flush=True)
                        link.click()
                        page.wait_for_timeout(5000)
                        page_num += 1
                        next_found = True

                        new_items = extract_notices(page)
                        existing_titles = {it["title"] for it in all_items}
                        added = 0
                        for it in new_items:
                            if it["title"] not in existing_titles:
                                all_items.append(it)
                                existing_titles.add(it["title"])
                                added += 1
                        print(f"[INFO] Page {page_num}: added {added} items (total: {len(all_items)})", flush=True)
                        break
                except:
                    continue

            if not next_found:
                print(f"[INFO] No more next button. Stopping pagination.", flush=True)
                break

        browser.close()
        print(f"=== SCRAPE DONE: {len(all_items)} items ===", flush=True)
        return all_items


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
        send_text(f"✅ 産業能率大学ポータル監視を開始したで！\nこれから新着があったら通知するわ。")
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
