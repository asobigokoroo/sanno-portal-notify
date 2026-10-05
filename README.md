# sanno-portal-notify

産業能率大学ポータルのお知らせを定期確認し、新着情報をDiscordへ通知するGitHub Actionsです。

## 動作

- 30分ごとにポータルへログインしてお知らせを確認
- 初回実行では現在のお知らせを基準として登録し、通知は送信しない
- 2件以下の新着は個別通知、3件以上はまとめて通知
- 通知済みのお知らせは`seen.json`で管理
- Discord通知には送信時刻を表示

GitHub ActionsのスケジュールはUTC基準で、`00分`と`30分`を指定しています。GitHub側の混雑状況により、実際の開始時刻が数分遅れる場合があります。

## 必要なSecrets

リポジトリの **Settings > Secrets and variables > Actions** に次のSecretsを登録してください。

| Secret | 内容 |
| --- | --- |
| `SANNO_ID` | ポータルのログインID |
| `SANNO_PASS` | ポータルのパスワード |
| `DISCORD_WEBHOOK` | Discord Webhook URL。複数指定時はカンマ区切り |

## 手動実行

Actionsの **SANNOポータル通知 > Run workflow** から手動実行できます。

## ローカル実行

```bash
pip install -r requirements.txt
playwright install chromium

SANNO_ID="..." \
SANNO_PASS="..." \
DISCORD_WEBHOOK="..." \
python portal_notify.py
```

`seen.json`は通知履歴のため、実行後に変更された場合はGitHub Actionsが自動でコミットします。
