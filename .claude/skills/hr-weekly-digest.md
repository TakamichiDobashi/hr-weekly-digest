---
name: hr-weekly-digest
description: 人事ニュースの週次ダイジェストを管理するスキル。「ダイジェスト」「週次レポート」「人事ニュース」「ニュース収集」「キーワード変更」「Notion投稿」「サブエージェント」などのキーワードで起動する。検索キーワードの変更、Notionテンプレートの修正、スケジュール変更、トラブルシューティングを行う。
---

# 人事ニュース 週次ダイジェスト 管理スキル

毎週月曜日 8:00 JST に自動実行される、人事ニュース週次ダイジェストシステムの管理マニュアル。

## システム構成（Mgr型サブエージェントパターン）

```
GitHub Actions（毎週月曜 8:00 JST）
        ↓
hr_weekly_digest.py ← Manager役
        ├─ DomesticNewsAgent   : 国内人事ニュース収集・要約
        ├─ InternationalNewsAgent : 海外人事ニュース収集・要約
        └─ DigestWriterAgent   : 統合・執筆 → Notion投稿
```

これは講座Session3で学んだ「Mgr型サブエージェント」パターンの実装例。
各サブエージェントはClaude APIへの個別の呼び出しとして実装されている。

## ファイル構成

| ファイル | 役割 |
|---|---|
| `.github/workflows/hr-weekly-digest.yml` | スケジューラー |
| `scripts/hr_weekly_digest.py` | Managerとサブエージェントの実装 |
| `config/settings.json` | キーワード・件数・フォーマット設定 |

## GitHub Secrets（設定済みであること）

| Secret名 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic APIキー（console.anthropic.com で発行） |
| `NOTION_API_KEY` | Notion インテグレーショントークン |
| `NOTION_PAGE_ID` | 投稿先NotionページのID |

## よくある依頼と対応手順

### 検索キーワードを変えたい
`config/settings.json` の `domestic_keywords` または `international_keywords` を編集する。
変更後、GitHubにプッシュすれば次回実行から反映される。

### 取得するニュースの件数を変えたい
`config/settings.json` の `max_articles_per_feed` を変更する（デフォルト5件）。

### 実行スケジュールを変えたい
`.github/workflows/hr-weekly-digest.yml` の `cron:` を変更する。
JST = UTC + 9時間。月曜8:00 JST → `cron: '0 23 * * 0'`（日曜23:00 UTC）

### 今すぐ手動で実行したい
GitHubリポジトリの「Actions」タブ →「HR Weekly Digest」→「Run workflow」をクリック。

### Notionの投稿フォーマットを変えたい
`scripts/hr_weekly_digest.py` の `DigestWriterAgent` クラスの `DIGEST_PROMPT` を編集する。

### エラーが出ている
GitHubの「Actions」タブ → 失敗したワークフロー → ログを確認。
よくあるエラー：
- `AuthenticationError`: ANTHROPIC_API_KEYが正しくない
- `APIResponseValidationError`: Notion APIキーまたはページIDが誤り
- `object_not_found`: NotionページIDが存在しない、またはインテグレーションが共有されていない

## カスタマイズのヒント

- `settings.json` を編集することで、コードを触らずにキーワードや件数を変更できる
- Notionのページを変えたい場合はGitHub Secretsの `NOTION_PAGE_ID` を更新する
- 複数のキーワードを追加するほど幅広いニュースが集まるが、処理時間とAPIコストが増える
