"""
親ページ（人事AIニュースダイジェスト）のUIを整備するスクリプト。
初回セットアップ時に一度だけ実行する。
"""

import os
from notion_client import Client as NotionClient


def setup_parent_page():
    notion = NotionClient(auth=os.environ["NOTION_API_KEY"])
    page_id = os.environ["NOTION_PAGE_ID"]

    # ── ブロックのヘルパー関数 ──────────────────────────

    def callout(text: str, emoji: str, color: str) -> dict:
        return {
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
                "icon": {"emoji": emoji},
                "color": color
            }
        }

    def heading(text: str, level: int = 2) -> dict:
        t = f"heading_{level}"
        return {"object": "block", "type": t, t: {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        }}

    def paragraph(text: str) -> dict:
        return {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}
        }

    def bullet(text: str) -> dict:
        return {
            "object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            }
        }

    def divider() -> dict:
        return {"object": "block", "type": "divider", "divider": {}}

    # ── ページアイコンを設定 ──────────────────────────────
    notion.pages.update(
        page_id=page_id,
        icon={"type": "emoji", "emoji": "🗞️"}
    )
    print("ページアイコンを設定しました")

    # ── 既存のブロックをすべて削除 ────────────────────────
    existing = notion.blocks.children.list(block_id=page_id)
    for block in existing.get("results", []):
        notion.blocks.delete(block_id=block["id"])
    print(f"既存ブロックを削除しました（{len(existing.get('results', []))}件）")

    # ── 新しいブロックを追加 ──────────────────────────────
    blocks = [
        # ① ヘッダー紹介
        callout(
            "人事・HR分野の国内外ニュースを毎週月曜日 8:00 JST に自動収集・要約して届けるダイジェストです。"
            "\n各週のレポートは下部に自動追加されます。",
            "🗞️", "green_background"
        ),

        divider(),

        # ② 使い方
        heading("📖 使い方", 2),
        paragraph("最新のダイジェストはページ下部の一番上に追加されます。タイトルをクリックすると内容が確認できます。"),
        paragraph("各ダイジェストは以下の構成で作成されています："),
        bullet("📌 今週のハイライト（重要ポイント3点）"),
        bullet("🇯🇵 国内注目ニュース（要約・人事への示唆・元記事リンク）"),
        bullet("🌐 海外注目ニュース（要約・人事への示唆・元記事リンク）"),
        bullet("💬 今週の総括コメント"),
        bullet("📎 参考リンク一覧"),

        divider(),

        # ③ システム情報
        heading("⚙️ システム情報", 2),
        bullet("更新頻度：毎週月曜日 8:00 JST（GitHub Actionsで自動実行）"),
        bullet("ニュースソース：Google News RSS（国内・海外）"),
        bullet("AI：Claude（Anthropic API）"),
        bullet("キーワード変更など設定変更は config/settings.json を編集"),

        divider(),

        # ④ バックナンバーの案内
        heading("📚 バックナンバー", 2),
        paragraph("↓ 以下に各週のダイジェストが自動追加されます"),
    ]

    notion.blocks.children.append(block_id=page_id, children=blocks)
    print(f"親ページのUI設定が完了しました（{len(blocks)}ブロックを追加）")


if __name__ == "__main__":
    required = ["NOTION_API_KEY", "NOTION_PAGE_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"環境変数が未設定です: {', '.join(missing)}")

    setup_parent_page()
