def scrape_portal():
    if not SANNO_ID or not SANNO_PASS:
        print("[error] SANNO_ID / SANNO_PASS 未設定"); sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1400, "height": 1000})
        page = context.new_page()

        # --- 1. お知らせページに直接アクセス ---
        print(f"[portal-notify] open notice list: {NOTICE_URL}")
        page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
        print(f"[portal-notify] current url: {page.url}")

        if DEBUG:
            page.screenshot(path="debug_01_first_access.png", full_page=True)
            with open("debug_01_first_access.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            print("[DEBUG] saved debug_01_first_access.html / .png")

        # --- 2. ページ内にログインフォームがあるかチェック ---
        # ユーザID入力欄があれば = ログインが必要
        user_id_input = page.locator("#userId")
        has_login_form = user_id_input.count() > 0

        if has_login_form:
            print("[portal-notify] login form detected on page. filling credentials...")

            if DEBUG:
                page.screenshot(path="debug_02_login_form.png", full_page=True)
                print("[DEBUG] saved debug_02_login_form.png")

            try:
                # 確実に見つかった要素に入力
                page.fill("#userId", SANNO_ID)
                page.fill("#password", SANNO_PASS)
                page.click("#loginButton")
                
                # ログイン後の遷移を待つ
                page.wait_for_load_state("networkidle", timeout=30000)
                print(f"[portal-notify] after login url: {page.url}")

            except Exception as e:
                print(f"[error] ログイン送信でエラー: {e}")
                if DEBUG:
                    page.screenshot(path="debug_error_submit.png", full_page=True)
                browser.close()
                sys.exit(3)

            # ログイン後、再度お知らせページへ
            if "wbasmgjr" not in page.url:  # お知らせページのURLに含まれる文字列で判定
                print(f"[portal-notify] re-open notice list after login")
                page.goto(NOTICE_URL, wait_until="networkidle", timeout=45000)
                print(f"[portal-notify] current url after re-open: {page.url}")

            # まだログインフォームが残ってたら失敗
            if page.locator("#userId").count() > 0:
                print("[error] ログイン後もフォームが残ってる。ID/PASSが違うかもしれん。")
                if DEBUG:
                    page.screenshot(path="debug_error_still_login.png", full_page=True)
                browser.close()
                sys.exit(4)

        # --- 3. お知らせページのスクショ ---
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

        # --- 4. お知らせ抽出 ---
        all_items = extract_notices_from_page(page)

        # ページネーション
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
