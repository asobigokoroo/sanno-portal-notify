#!/usr/bin/env python3
"""
sanno-portal-notify v3  (産業能率大学ポータル / CampusWeb お知らせ → Discord)

■ 重要（v3の変更点）
  ログイン画面(signweb)の「ユーザー名/パスワード/ログイン/キャンセル」ダイアログは
  Chrome ネイティブの【HTTP Basic 認証】ダイアログや。
  これはHTMLフォームやないから page.fill() では触れへん。
  → browser.new_context(http_credentials=...) で認証を通す。これが本命。
  （保険としてHTMLフォーム方式のフォールバックも残してある）

  top.do の「お知らせ」テーブル（タイトル / 受信日時 / 送信者 / 種別 / NEW）を
  解析して、新着だけ Discord に投げる。

環境変数（GitHub Secrets）:
  SANNO_ID          : ログインID（ユーザー名）
  SANNO_PASS        : パスワード
  DISCORD_WEBHOOK   : Discord Webhook URL
  LOGIN_URL         : default https://signweb.mi.sanno.ac.jp/portal/
  PORTAL_TOP_URL    : default https://portal-xs.mi.sanno.ac.jp/campusweb/top.do
  FILTER_KEYWORD    : タイトルに含まれる語で絞る（例: 【情マネ】）。空なら全部。
  MAX_ITEMS_PER_RUN : 1回の通知上限（default 5）
  SEEN_FILE         : default seen.json
  DEBUG             : "1" で失敗時のHTML/スクショを debug/ に保存
"""

import os
import sys
import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

LOGIN_URL = os.environ.get("LOGIN_URL", "https://signweb.mi.sanno.ac.jp/portal/")
PORTAL_TOP_URL = os.environ.get("PORTAL_TOP_URL", "https://portal-xs.mi.sanno.ac.jp/campusweb/top.do")
PORTAL_BASE = "https://portal-xs.mi.sanno.ac.jp"
SEEN_FILE = os.environ.get("SEEN_FILE", "seen.json")
SANNO_ID = os.environ.get("SANNO_ID", "")
SANNO_PASS = os.environ.get("SANNO_PASS", "")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
FILTER_KEYWORD = os.environ.get("FILTER_KEYWORD", "")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS_PER_RUN", "5"))
DEBUG = os.environ.get("DEBUG", "") == "1"

JST = timezone(timedelta(hours=9))
DATE_RE = re.compile(r"\d{4}/\d{2}/\d{2}\s*\d{2}:\d{2}")


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
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen))[-400:], f, ensure_ascii=False, indent=2)


def post_discord(content):
    if not DISCORD_WEBHOOK:
        log("DISCORD_WEBHOOK 未設定。スキップ")
        return
    payload = json.dumps({"username": "SANNO Portal", "content": content[:1900]}).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "sanno-notify/3.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log("discord status", r.status)
    except Exception as e:
        log("discord post failed:", e)


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


def html_form_login(page):
    """（保険）もしHTMLフォームのログインやったら埋める"""
    for sel in ["input[name='username']", "input[id='username']", "input[name='userid']",
                "input[name='userId']", "input[name='j_username']", "input[name='loginId']"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.fill(SANNO_ID)
            pw = page.query_selector("input[type='password']")
            if pw:
                pw.fill(SANNO_PASS)
            for b in ["button:has-text('ログイン')", "input[type='submit']", "button[type='submit']"]:
                try:
                    be = page.query_selector(b)
                    if be and be.is_visible():
                        be.click()
                        log("html form login submitted")
                        return True
                except Exception:
                    continue
    return False


def scrape_news(page):
    """top.do のお知らせ一覧テーブルを解析"""
    items = []
    try:
        page.wait_for_selector("table", timeout=30000)
    except PWTimeout:
        log("table が見つからん")

    for tr in page.query_selector_all("tr"):
        try:
            a = tr.query_selector("td a")
            if not a:
                continue
            title = re.sub(r"\s+", " ", (a.inner_text() or "")).strip()
            if len(title) < 4:
                continue

            tds = tr.query_selector_all("td")
            texts = [re.sub(r"\s+", " ", (td.inner_text() or "")).strip() for td in tds]

            date, idx = "", -1
            for j, t in enumerate(texts):
                m = DATE_RE.search(t)
                if m:
                    date, idx = m.group(0), j
                    break
            sender, kind = "", ""
            if idx >= 0:
                rest = [t for t in texts[idx + 1:] if t]
                if rest:
                    sender = rest[0]
                if len(rest) > 1:
                    kind = rest[-1]

            href = a.get_attribute("href") or ""
            if href.startswith("http"):
                url = href
            elif href.startswith("/"):
                url = urljoin(PORTAL_BASE, href)
            else:
                url = urljoin(PORTAL_TOP_URL, href)

            is_new = "NEW" in (tr.inner_text() or "")
            items.append({"title": title, "date": date, "sender": sender,
                          "kind": kind, "url": url, "new": is_new})
        except Exception:
            continue

    out, seen_titles = [], set()
    for it in items:
        if it["title"] in seen_titles:
            continue
        seen_titles.add(it["title"])
        out.append(it)
    return out[:30]


def main():
    if not SANNO_ID or not SANNO_PASS:
        log("SANNO_ID / SANNO_PASS が未設定やで。")
        sys.exit(1)

    seen = load_seen()
    first_run = len(seen) == 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ja-JP",
            viewport={"width": 1600, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            # ★ここが本命：HTTP Basic 認証のネイティブダイアログを自動で突破する
            http_credentials={"username": SANNO_ID, "password": SANNO_PASS},
        )
        page = ctx.new_page()

        log("open portal top (Basic認証つき):", PORTAL_TOP_URL)
        try:
            page.goto(PORTAL_TOP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            log("goto failed:", e)
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except PWTimeout:
            pass
        time.sleep(3)
        log("current url:", page.url)

        # Basic認証で通ってへん場合の保険：ログインページ経由＋HTMLフォーム
        if "signweb" in page.url or "login" in page.url.lower():
            log("Basic認証で通らんかった。ログインページ経由を試す")
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
                time.sleep(2)
                html_form_login(page)
                try:
                    page.wait_for_url(lambda u: "signweb" not in u, timeout=45000)
                except PWTimeout:
                    pass
                page.goto(PORTAL_TOP_URL, wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
            except Exception as e:
                log("fallback login failed:", e)

        if "signweb" in page.url:
            log("ログイン突破できんかった。DEBUG=1 でHTMLを見せて。")
            dump_debug(page, "login_fail")
            browser.close()
            sys.exit(2)

        dump_debug(page, "portal_top")
        items = scrape_news(page)
        log("scraped:", len(items))
        browser.close()

    if FILTER_KEYWORD:
        before = len(items)
        items = [it for it in items if FILTER_KEYWORD in it["title"]]
        log("filter '%s': %d -> %d" % (FILTER_KEYWORD, before, len(items)))

    new_items = []
    for it in items:
        key = it["title"] + "|" + it["date"]
        if key in seen:
            continue
        seen.add(key)
        new_items.append(it)

    if first_run:
        save_seen(seen)
        post_discord("✅ **産業能率大学ポータル監視** を開始したで。\n"
                     "今後ここにお知らせの新着を流すわ。（基準登録: " + str(len(items)) + "件）")
        log("first run baseline saved:", len(seen))
        return

    if not new_items:
        log("新着なし。")
        return

    for it in new_items[:MAX_ITEMS]:
        lines = ["📢 **産能ポータル新着" + ("（NEW未読）" if it["new"] else "") + "**"]
        lines.append("[**" + it["title"][:150] + "**](" + it["url"] + ")")
        if it["date"]:
            lines.append("🕐 受信: `" + it["date"] + "`")
        if it["sender"]:
            lines.append("👤 送信者: " + it["sender"][:40])
        if it["kind"]:
            lines.append("🏷️ 種別: " + it["kind"][:20])
        post_discord("\n".join(lines))
        time.sleep(1)

    if len(new_items) > MAX_ITEMS:
        post_discord("…ほか " + str(len(new_items) - MAX_ITEMS) + " 件の新着あり")

    save_seen(seen)
    log("done. new:", len(new_items))


if __name__ == "__main__":
    main()
