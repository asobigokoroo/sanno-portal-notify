#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discord Webhook動作確認（User-Agent偽装版）"""

import os
import json
import urllib.request
import urllib.error

WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
print(f"WEBHOOK={'set' if WEBHOOK else 'EMPTY'}")

if not WEBHOOK:
    print("ERROR: DISCORD_WEBHOOK not set!")
    exit(1)

print(f"WEBHOOK prefix={WEBHOOK[:50]}...")

# User-Agentをブラウザに偽装
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
}

# テスト送信
try:
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps({"content": "🧪 User-Agent偽装テストやで！"}).encode("utf-8"),
        headers=HEADERS,
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = r.read().decode("utf-8")
        print(f"SUCCESS! Status={r.status}")
        print(f"Response: {resp[:200]}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8")
    print(f"HTTPError {e.code}: {e.reason}")
    print(f"Response: {body[:500]}")
    exit(1)
except Exception as e:
    print(f"Error: {e}")
    exit(1)

print("テスト完了！Discordを確認してな！")
