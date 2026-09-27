#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discord Webhook動作確認"""

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

# テスト送信（シンプルなテキスト）
try:
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps({"content": "🧪 Webhookテストやで！"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
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
