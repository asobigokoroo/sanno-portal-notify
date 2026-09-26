def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS 未設定"); sys.exit(1)

    LOGIN_URL = "https://signweb.mi.sanno.ac.jp/portal/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        # --- 1. ログインページへ ---
        print(f"[portal-notify] open login page: {LOGIN_URL}")
        page.goto(LOGIN_URL, wait_until="networkidle", timeout=45000)

        if DEBUG:
            page.screenshot(path="debug_01_login_page.png", full_page=True)
            with open("debug_01_login_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] saved debug_01_login_page.html / .png")

        # --- 2. ログインフォームの要素を探す ---
        id_selectors = [
            'input[name="j_username"]',
            'input[name="loginId"]',
            'input[name="userID"]',
            'input[name="id"]',
            'input[type="text"]',
        ]
        pass_selectors = [
            'input[name="j_password"]',
            'input[name="password"]',
            'input[type="password"]',
        ]
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("ログイン")',
            'input[value="ログイン"]',
        ]

        id_sel = next((s for s in id_selectors if page.locator(s).count() > 0), None)
        pass_sel = next((s for s in pass_selectors if page.locator(s).count() > 0), None)
        submit_sel = next((s for s in submit_selectors if page.locator(s).count() > 0), None)

        if not id_sel or not pass_sel or not submit_sel:
            print("[error] ログインフォームの要素が見つからへん。debug_01_login_page.html を確認してな。")
            if DEBUG:
                page.screenshot(path="debug_error_no_form.png", full_page=True)
            browser.close()
            sys.exit(2)

        print(f"[portal-notify] form detected: id={id_sel}, pass={pass_sel}, submit={submit_sel}")

        # --- 3. ログイン実行 ---
        try:
            page.fill(id_sel, SANNO_ID)
            page.fill(pass_sel, SANNO_PASS)
            page.click(submit_sel)
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            print(f"[error] ログイン送信でエラー: {e}")
            if DEBUG:
                page.screenshot(path="debug_error_submit.png", full_page=True)
            browser.close()
            sys.exit(3)

        if DEBUG:
            page.screenshot(path="debug_02_after_login.png", full_page=True)
            with open("debug_02_after_login.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"[DEBUG] after login url: {page.url}")

        # --- 4. お知らせページへ ---
        print(f"[portal-notify] open notice list: {NOTICE_URL}")
        page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")

        if "signweb" in page.url or "login" in page.url.lower():
            print("[error] ログイン後も認証ページに戻されたで。ID/PASSが違うか、ログインに失敗してるわ。")
            if DEBUG:
                page.screenshot(path="debug_error_auth.png", full_page=True)
            browser.close()
            sys.exit(4)

        page.wait_for_timeout(4000)

        if DEBUG:
            page.screenshot(path="debug_03_notice_page.png", full_page=True)
            with open("debug_03_notice_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            preview = page.content()[:1500].replace("\n", " ").replace("  ", " ")
            print(f"[DEBUG] html_preview={preview}...")
            rows = page.query_selector_all("table tr")
            print(f"[DEBUG] total rows: {len(rows)}")
            for i, row in enumerate(rows[:5]):
                txt = row.inner_text().replace("\n", " | ")
                print(f"[DEBUG] row {i}: {txt[:150]}")

        # --- 5. お知らせ抽出（既存のまま）---
        all_items = extract_notices_from_page(page)

        # ページネーション（既存のまま）
        page_num = 1
        while len(all_items) < 89 and page_num < 15:
            next_links = page.query_selector_all("a")
            next_btn = None
            for link in next_links:
                txt = link.inner_text().strip()
                if txt in (">", "›", "→", "次へ"):
                    cls = link.get_attribute("class") or ""
                    if "disabled" not in cls and "inactive" not in cls:
                        next_btn = link
                        break
            if not next_btn:
                break
            try:
                print(f"[portal-notify] clicking next page ({page_num + 1})...")
                next_btn.click()
                page.wait_for_timeout(3000)
                page_num += 1
                items = extract_notices_from_page(page)
                if not items:
                    break
                existing = {it["title"] for it in all_items}
                for it in items:
                    if it["title"] not in existing:
                        all_items.append(it)
            except Exception as e:
                print(f"[warn] pagination failed: {e}")
                break

        browser.close()
        return all_items
