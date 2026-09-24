#!/usr/bin/env python3
"""
sanno-portal-notify  (CA-in / CampusWeb 新着を Discord に通知)

GitHub Actions の cron から呼ばれる前提のスクリプトやで。
- 産能ポータルに Playwright(Chromium) でログイン
- トップ(top.do)の「CA-in」ニュースをスクレイピング
- 未通知のモノだけ Discord Webhook に投げる（seen.json で重複防止）

環境変数（GitHub Secrets に入れる）:
  SANNO_ID            : 学籍番号/ログインID
  SANNO_PASS          : パスワード
  DISCORD_WEBHOOK     : Discord Webhook URL
  LOGIN_URL           : ログインページ (default: https://signweb.mi.sanno.ac.jp/portal/)
  PORTAL_TOP_URL      : default: https://portal-xs.mi.sanno.ac.jp/campusweb/top.do
  SEEN_FILE           : default: seen.json
  DEBUG               : "1" で失敗時にHTML/スクショを debug/ に保存
"""

import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LOGIN_URL = os.environ.get("LOGIN_URL", "https://signweb.mi.sanno.ac.jp/portal/")
PORTAL_TOP_URL = os.environ.get("PORTAL_TOP_URL", "https://portal-xs.mi.sanno.ac.jp/campusweb/top.do")
SEEN_FILE = os.environ.get("SEEN_FILE", "seen.json")
SANNO_ID = os.environ.get("SANNO_ID", "")
SANNO_PASS = os.environ.get("SANNO_PASS", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DEBUG = os.environ.get("DEBUG", "") == "1"

JST = timezone(timedelta(hours=9))


def log(*a):
    print("[portal-notify]", *a, flush=True)


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    # 古いものを捨てて直近400件だけ残す
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen))[-400:], f, ensure_ascii=False, indent=2)


def post_discord(content):
    if not DISCORD_WEBHOOK:
        log("DISCORD_WEBHOOK 未設定。スキップ")
        return
    payload = json.dumps({"username": "SANNO Portal", "content": content[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "sanno-notify/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log("discord status", r.status)
    except Exception as e:
        log("discord post failed:", e)


def try_fill(page, selectors, value):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.fill(value)
                log("filled", sel)
                return True
        except Exception:
            continue
    return False


def try_click(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                el.click()
                log("clicked", sel)
                return True
        except Exception:
            continue
    return False


def scrape_news(page):
    """CA-in のニュースらしき項目を拾う。セレクタは複数候補を試す。"""
    items = []
    seen_keys = set()

    # よくある「ニュース/お知らせ」領域の候補セレクタ
    containers = [
        "#cain-news", ".cain-news", "[class*='cain']",
        "[class*='news']", "[class*='information']", "[class*='info']",
        "[id*='news']", "[id*='information']",
    ]
    scope = None
    for c in containers:
        el = page.query_selector(c)
        if el:
            scope = el
            log("news container:", c)
            break

    root = scope or page
    anchors = root.query_selector_all("a")

    for a in anchors:
        try:
            text = (a.inner_text() or "").strip().replace("\n", " ")
            href = a.get_attribute("href") or ""
            if not text or len(text) < 4:
                continue
            # ナビ系リンクを雑に除外
            if text in ("ログアウト", "トップ", "戻る", "ホーム"):
                continue
            if href.startswith("javascript:") or href == "#":
                continue
            key = text[:120]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append({"text": text[:200], "href": href})
        except Exception:
            continue

    # 領域が見つからん時は、ページ全体から「日付っぽい行」の近くを拾う保険
    if not items and scope is None:
        log("ニュース領域が特定できへん。ページ全体から拾う（要セレクタ調整）")

    return items[:30]


def dump_debug(page, name):
    if not DEBUG:
        return
    os.makedirs("debug", exist_ok=True)
    try:
        with open(os.path.join("debug", name + ".html"), "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path=os.path.join("debug", name + ".png"), full_page=True)
        log("debug dump:", name)
    except Exception as e:
        log("debug dump failed:", e)


def main():
    if not SANNO_ID or not SANNO_PASS:
        log("SANNO_ID / SANNO_PASS が未設定やで。")
        sys.exit(1)

    seen = load_seen()
    new_items = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ja-JP",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        )
        page = ctx.new_page()

        log("open login page")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        # ログインフォーム（name/id の候補を広めに）
        ok_id = try_fill(page, [
            "input[name='username']", "input[id='username']",
            "input[name='userId']", "input[name='j_username']",
            "input[name='loginId']", "input[name='userid']",
            "input[type='text']",
        ], SANNO_ID)
        ok_pw = try_fill(page, [
            "input[name='password']", "input[id='password']",
            "input[name='j_password']", "input[name='passwd']",
            "input[type='password']",
        ], SANNO_PASS)

        if not (ok_id and ok_pw):
            log("ログインフォームのセレクタが見つからんかった。DEBUG=1 で HTML を見て調整してな。")
            dump_debug(page, "login")
            browser.close()
            sys.exit(2)

        try_click(page, [
            "button[type='submit']", "input[type='submit']",
            "#loginButton", "#login_button", ".login-btn",
            "button:has-text('ログイン')", "input[value*='ログイン']",
            "input[value*='ログオン']", "a:has-text('ログイン')",
        ])

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        time.sleep(3)

        log("current url:", page.url)
        dump_debug(page, "after_login")

        # ポータルトップへ
        log("open portal top")
        page.goto(PORTAL_TOP_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        time.sleep(2)
        dump_debug(page, "portal_top")

        items = scrape_news(page)
        log("scraped", len(items), "items")

        for it in items:
            key = "portal|" + str(abs(hash(it["text"])))
            if key in seen:
                continue
            seen.add(key)
            new_items.append(it)

        browser.close()

    if not new_items:
        log("新着なし。")
        return

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    lines = [f"📢 **SANNOポータル 新着 {len(new_items)}件** ({now})", ""]
    for it in new_items[:15]:
        lines.append("・" + it["text"][:120])
    body = "\n".join(lines)
    post_discord(body)

    save_seen(seen)
    log("done. new:", len(new_items))


if __name__ == "__main__":
    main()
