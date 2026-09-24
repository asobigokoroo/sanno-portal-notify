#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sanno-portal-notify v7
- Basic認証ダイアログ自動突破
- HTML全方位探索：iframe内・テーブル・div・li などから「日付+タイトル」パターンを抽出
- DEBUG=1 でHTML冒頭をログ出力（Artifacts不要で構造確認可能）
- 新着だけを1件ずつ個別通知（過去分は送らない）
- 複数サーバー対応（Discord複数 / Slack / 汎用Webhook）
"""

import os
import re
import json
import sys
import urllib.request
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

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

NOISE = ["ログアウト", "メニュー", "パスワード変更", "サイトマップ", "ホーム", "戻る",
         "在学生", "保護者", "教職員", "ログイン", "ヘルプ", "English", "マイページ",
         "時間割", "成績", "履修", "シラバス", "掲示板", "授業"]


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
        "url": item.get("url") or PORTAL_TOP,
        "color": 0x1E88E5,
    }
    if item.get("meta"):
        embed["description"] = item["meta"][:400]
    embed["footer"] = {"text": "産業能率大学ポータル 新着"}
    for wh in DISCORD_WEBHOOKS:
        try:
            st = post_json(wh, {"embeds": [embed]})
            print(f"[portal-notify] discord 1件送信 (status {st})")
        except Exception as e:
            print(f"[warn] discord failed: {e}")


def send_slack_item(item):
    url = item.get("url") or PORTAL_TOP
    text = f"📢 *新着* <{url}|{item['title']}>"
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


def looks_like_date(s):
    return re.match(r"^(\d{4}[/年]\d{1,2}[/月]\d{1,2}日?|\d{1,2}/\d{1,2}|NEW|新着)", s) is not None


def extract_notices_from_html(html, base_url):
    """HTML文字列から「日付+タイトル+URL」のお知らせを抽出"""
    items = []
n
    # パターン1: table > tr > td 内のテキスト（CampusSquareの定番形式）
    # 例: <tr><td>2026/05/20</td><td><a href="...">タイトル</a></td></tr>
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
        if len(cells) >= 2:
            date_cell = re.sub(r"<[^>]+>", "", cells[0]).strip()
            title_cell_html = cells[1]
            title_text = re.sub(r"<[^>]+>", "", title_cell_html).strip()
            href_match = re.search(r'href=["\\']([^"\\']+)["\\']', title_cell_html)
            url = urljoin(base_url, href_match.group(1)) if href_match else base_url

            if looks_like_date(date_cell) and len(title_text) > 3:
                if not any(n in title_text for n in NOISE):
                    items.append({
                        "title": title_text[:120],
                        "meta": date_cell,
                        "url": url
                    })

    # パターン2: li や div 内の「日付 タイトル」形式
    # 例: <li>2026/05/20 タイトル</li> や <div class="info">...</div>
    text_blocks = re.findall(r"<(?:li|div|p|span)[^>]*>(.*?)</(?:li|div|p|span)>", html, re.DOTALL | re.IGNORECASE)
    for block in text_blocks:
        text = re.sub(r"<[^>]+>", " ", block).strip()
        text = re.sub(r"\s+", " ", text)
        # 「日付 タイトル」または「日付 / タイトル」パターン
        m = re.match(r"(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}|NEW)\s*[/-]?\s*(.+)", text)
        if m and len(m.group(2)) > 3:
            if not any(n in m.group(2) for n in NOISE):
                items.append({
                    "title": m.group(2)[:120],
                    "meta": m.group(1),
                    "url": base_url
                })

    # パターン3: aタグのテキストが日付っぽいものを含む行
    # （前回の方式を残しつつ強化）
    for a_match in re.finditer(r'<a[^>]*href=["\\']([^"\\']+)["\\'][^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE):
        href = a_match.group(1)
        link_text = re.sub(r"<[^>]+>", "", a_match.group(2)).strip()
        link_text = re.sub(r"\s+", " ", link_text)
        if not link_text or len(link_text) < 4:
            continue
        if any(n in link_text for n in NOISE):
            continue
        if looks_like_date(link_text) and len(link_text) < 15:
            continue
        url = urljoin(base_url, href) if not href.startswith("javascript") else base_url
        items.append({"title": link_text[:120], "meta": "", "url": url})

    return items


def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS 未設定"); sys.exit(1)

    all_items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        print("[portal-notify] open portal top (Basic Auth)...")
        page.goto(PORTAL_TOP, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")

        if "signweb" in page.url:
            print("[error] Basic認証突破できず。ID/PASSを確認してな。"); sys.exit(2)

        # iframeの読み込みを待つ（最大10秒）
        page.wait_for_timeout(6000)

        # 全フレーム（親＋iframe）のHTMLを取得して解析
        for i, frame in enumerate(page.frames):
            try:
                html = frame.content()
                url = frame.url
            except Exception as e:
                print(f"[warn] frame {i} content failed: {e}")
                continue

            if DEBUG:
                # HTMLの冒頭2000文字をログに出力（構造確認用）
                preview = html[:2000].replace("\n", " ").replace("  ", " ")
                print(f"[DEBUG] frame {i} url={url} html_preview={preview[:500]}...")

            items = extract_notices_from_html(html, url or PORTAL_TOP)
            if DEBUG and items:
                print(f"[DEBUG] frame {i} extracted {len(items)} items")
            all_items.extend(items)

        # もし0件なら、お知らせ一覧の可能性がある別URLを試す
        if not all_items:
            alt_urls = [
                "https://portal-xs.mi.sanno.ac.jp/campusweb/campussquare.do",
                "https://portal-xs.mi.sanno.ac.jp/campusweb/notice.do",
            ]
            for alt in alt_urls:
                try:
                    print(f"[portal-notify] trying alternative: {alt}")
                    page.goto(alt, wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(3000)
                    html = page.content()
                    if DEBUG:
                        preview = html[:2000].replace("\n", " ").replace("  ", " ")
                        print(f"[DEBUG] alt url html_preview={preview[:500]}...")
                    items = extract_notices_from_html(html, page.url)
                    print(f"[portal-notify] alt url scraped: {len(items)}")
                    all_items.extend(items)
                    if items:
                        break
                except Exception as e:
                    print(f"[warn] alt url failed: {e}")

        browser.close()

    # 重複除去（タイトルで判定）
    seen_t, unique = set(), []
    for it in all_items:
        t = it["title"]
        if t in seen_t:
            continue
        seen_t.add(t)
        unique.append(it)

    return unique


def main():
    if not (DISCORD_WEBHOOKS or SLACK_WEBHOOKS or GENERIC_WEBHOOKS):
        print("[error] 通知先が未設定"); sys.exit(1)

    seen = load_seen()
    scraped = scrape_portal()
    print(f"[portal-notify] scraped: {len(scraped)}")
    for it in scraped[:30]:
        print(f"   - {it['title']}  |  {it['meta']}  |  {it['url']}")

    if FILTER_KEYWORD:
        scraped = [it for it in scraped if FILTER_KEYWORD in it["title"] or FILTER_KEYWORD in it.get("meta", "")]

    if not seen:
        print(f"[portal-notify] first run baseline: {len(scraped)}")
        for it in scraped:
            seen.add(it["title"])
        save_seen(seen)
        broadcast_message(f"✅ 産業能率大学ポータル監視を開始したで。以降、新着お知らせだけを1件ずつ通知するわ。（基準登録: {len(scraped)}件）")
        return

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
