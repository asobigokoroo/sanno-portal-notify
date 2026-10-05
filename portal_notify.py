#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v31
- page.evaluateのnavigationエラーをtry/exceptで捕捉
- ページ遷移後も正しく処理を続行
- Discord送信にリトライ（指数バックオフ）を追加
- 新着が3件以上の時はまとめ通知にする
- 【重要】【休講】【注目】タグで色分け
"""

import os
import json
import sys
import base64
import subprocess
import hashlib
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

PORTAL_URL = "https://signweb.mi.sanno.ac.jp/portal/"
SEEN_FILE = "seen.json"
DEBUG = os.environ.get("DEBUG", "0") == "1"
DISCORD_TIMEOUT = 15
DISCORD_RETRIES = 3
DISCORD_SUCCESS_CODES = {"200", "201", "204"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

SANNO_ID = os.environ.get("SANNO_ID", "").strip()
SANNO_PASS = os.environ.get("SANNO_PASS", "").strip()
DISCORD_WEBHOOKS = [w.strip() for w in os.environ.get("DISCORD_WEBHOOK", "").split(",") if w.strip()]
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "").strip()
MAX_NOTIFY = int(os.environ.get("MAX_NOTIFY_PER_RUN", "20"))

TAG_COLORS = {
    "重要": 0xFF0000,   # 赤
    "休講": 0xFF8800,   # オレンジ
    "注目": 0x9C27B0,   # 紫
}
TAG_EMOJI = {
    "重要": "🚨",
    "休講": "📛",
    "注目": "👀",
}


def send_webhook(payload, message_type):
    """Send one payload to every configured webhook with retries."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    headers = [
        "-H", "Content-Type: application/json",
        "-H", f"User-Agent: {USER_AGENT}",
    ]
    for wh in DISCORD_WEBHOOKS:
        for attempt in range(DISCORD_RETRIES):
            try:
                cmd = [
                    "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                    *headers,
                    "-d", payload_json,
                    wh
                ]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=DISCORD_TIMEOUT
                )
                status = result.stdout.strip()
                if status in DISCORD_SUCCESS_CODES:
                    print(
                        f"[OK] Discord {message_type} sent: status={status}",
                        flush=True,
                    )
                    break
                print(
                    f"[WARN] Unexpected status: {status}, "
                    f"retrying ({attempt + 1}/{DISCORD_RETRIES})...",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[NG] Discord {message_type} failed "
                    f"(attempt {attempt + 1}/{DISCORD_RETRIES}): {e}",
                    flush=True,
                )
            if attempt < DISCORD_RETRIES - 1:
                time.sleep(2 ** attempt)


def send_discord(title, meta, url, is_new=False, tag=""):
    if tag in TAG_COLORS:
        color = TAG_COLORS[tag]
    elif is_new:
        color = 0x00C853
    else:
        color = 0x1E88E5

    embed = {
        "title": title[:250] or "(無題)",
        "url": url,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "産業能率大学ポータル"},
    }
    if meta:
        embed["description"] = meta[:500]

    send_webhook({"embeds": [embed]}, "embed")


def send_text(text):
    send_webhook({"content": text}, "text")


def send_summary_embed(count, items):
    """新着が3件以上ある時、まとめて1つのEmbedにする"""
    lines = []
    for i, it in enumerate(items[:10], 1):
        emoji = ""
        if it.get("tag") and it["tag"] in TAG_EMOJI:
            emoji = TAG_EMOJI[it["tag"]]
        elif it["is_new"]:
            emoji = "🆕"
        lines.append(f"{i}. [{it['title'][:80]}]({it['url']}) {emoji}")

    if len(items) > 10:
        lines.append(f"\n...他 {len(items) - 10} 件")

    description = "\n".join(lines)
    embed = {
        "title": f"📢 新着お知らせ {count}件",
        "url": items[0]["url"] if items else PORTAL_URL,
        "color": 0x00C853,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "産業能率大学ポータル"},
    }

    send_webhook({"embeds": [embed]}, "summary")


# ===== seen.json（タイトルベース管理） =====
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
def extract_notices(page, list_url):
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

            new_tag = content.query_selector(".camjnext-list-tag-new")
            is_new = new_tag is not None

            # 親のtrからメタ情報を取得
            parent_text = content.evaluate("el => el.closest('tr')?.innerText || ''") or ""
            meta = ""
            if parent_text:
                lines = [l.strip() for l in parent_text.split("\n") if l.strip() and l.strip() != title]
                meta_lines = []
                for line in lines:
                    if any(c in line for c in ["2026/", "2025/", "教務課", "サービス", "センター", "お知らせ", "重要", "休講"]):
                        meta_lines.append(line)
                if meta_lines:
                    meta = " / ".join(meta_lines[-3:])
            if is_new:
                meta += " / 【NEW】" if meta else "【NEW】"

            # タグ検出
            tag = ""
            if "【重要】" in title or "[重要]" in title:
                tag = "重要"
            elif "休講" in title:
                tag = "休講"
            elif "【注目】" in title or "[注目]" in title:
                tag = "注目"

            # ユニークIDはタイトルのハッシュ
            item_id = hashlib.md5(title.encode()).hexdigest()[:16]
            href = link.get_attribute("href") or ""
            notice_url = urljoin(page.url, href) if href and not href.lower().startswith("javascript:") else list_url

            items.append({
                "title": title[:200],
                "meta": meta,
                "url": notice_url,
                "item_id": item_id,
                "is_new": is_new,
                "tag": tag,
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

        # --- 1. ポータル → Ca-In ---
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
        try:
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
        except Exception as e:
            print(f"[WARN] Evaluate error (expected due to navigation): {e}", flush=True)

        page.wait_for_timeout(5000)
        list_url = page.url
        print(f"[INFO] URL after お知らせ click: {list_url}", flush=True)

        # --- 3. 表示件数を200件に変更 ---
        print("[INFO] Changing display to 200 items...", flush=True)
        try:
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
        except Exception as e:
            print(f"[WARN] Evaluate error (expected due to navigation): {e}", flush=True)

        page.wait_for_timeout(8000)
        print(f"[INFO] URL after 200 change: {page.url}", flush=True)

        if DEBUG:
            page.screenshot(path="debug_05_notice.png", full_page=True)
            with open("debug_05_notice.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] Saved debug_05_notice.html/png", flush=True)

        # --- 4. お知らせ抽出 ---
        all_items = extract_notices(page, list_url)
        print(f"[INFO] Page 1: {len(all_items)} items", flush=True)

        # --- 5. ページネーション ---
        page_num = 1
        while len(all_items) < 90 and page_num < 10:
            next_found = False
            for link in page.query_selector_all("a"):
                try:
                    cls = link.get_attribute("class") or ""
                    onclick = link.get_attribute("onclick") or ""
                    if ("camjnext-pagination-next" in cls or 
                        ("doPaging" in onclick and f"'{page_num + 1}'" in onclick)):
                        if "disabled" in cls or link.is_disabled():
                            print(f"[INFO] Next button disabled. End.", flush=True)
                            next_found = False
                            break
                        print(f"[INFO] Clicking next page ({page_num + 1})...", flush=True)
                        link.click()
                        page.wait_for_timeout(5000)
                        page_num += 1
                        next_found = True

                        new_items = extract_notices(page, list_url)
                        existing_ids = {it["item_id"] for it in all_items}
                        added = 0
                        for it in new_items:
                            if it["item_id"] not in existing_ids:
                                all_items.append(it)
                                existing_ids.add(it["item_id"])
                                added += 1
                        print(f"[INFO] Page {page_num}: added {added} items (total: {len(all_items)})", flush=True)
                        break
                except Exception as e:
                    print(f"[WARN] Pagination error: {e}", flush=True)
                    continue

            if not next_found:
                print(f"[INFO] No more pages.", flush=True)
                break

        browser.close()
        print(f"=== SCRAPE DONE: {len(all_items)} items ===", flush=True)
        return all_items


def main():
    print("=== MAIN START ===", flush=True)

    if not DISCORD_WEBHOOKS:
        print("[ERROR] No Discord webhooks configured!", flush=True)
        sys.exit(1)

    try:
        seen = load_seen()
        items = scrape()

        if FILTER_KEYWORD:
            items = [it for it in items if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

        is_first_run = len(seen) == 0
        print(f"[INFO] is_first_run={is_first_run}, items={len(items)}, seen_before={len(seen)}", flush=True)

        # タイトルのハッシュで新着判定
        item_ids = {it["item_id"] for it in items}
        new_ids = item_ids - seen
        new_items = [it for it in items if it["item_id"] in new_ids]

        print(f"[INFO] Total items: {len(items)}, New items: {len(new_items)}", flush=True)

        # 初回：基準登録のみ
        if is_first_run:
            print(f"[INFO] First run! Registering {len(items)} items as baseline.", flush=True)
            for it in items:
                seen.add(it["item_id"])
            save_seen(seen)
            send_text(f"✅ 産業能率大学ポータル監視を開始したで！\nこれから新着があったら通知するわ。")
            print("=== MAIN END (first run) ===", flush=True)
            return

        # 新着があれば通知
        if new_items:
            notify_count = len(new_items[:MAX_NOTIFY])
            print(f"[INFO] Sending {notify_count} notifications...", flush=True)

            # 3件以上ならまとめ通知、2件以下は個別通知
            if notify_count >= 3:
                send_summary_embed(notify_count, new_items[:MAX_NOTIFY])
            else:
                for it in new_items[:MAX_NOTIFY]:
                    send_discord(it["title"], it["meta"], it["url"], it["is_new"], it.get("tag", ""))

            for it in new_items[:MAX_NOTIFY]:
                seen.add(it["item_id"])
            save_seen(seen)
            print(f"=== MAIN END (notified {notify_count} items) ===", flush=True)
        else:
            print("[INFO] No new notices. Exiting quietly.", flush=True)
            print("=== MAIN END (no changes) ===", flush=True)

    except Exception as e:
        error_msg = f"❌ スクリプトが落ちたで！エラー: {str(e)[:200]}"
        print(f"[FATAL] {error_msg}", flush=True)
        send_text(error_msg)
        sys.exit(1)


if __name__ == "__main__":
    print("=== ENTRY POINT ===", flush=True)
    main()
    print("=== SCRIPT END ===", flush=True)
