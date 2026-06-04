"""
人事ニュース 週次ダイジェスト 自動生成スクリプト

【アーキテクチャ】
講座Session3で学んだ「Mgr型サブエージェント」パターンを実装。

  DigestManager（このスクリプト）
      ├─ DomesticNewsAgent      : 国内人事ニュースを収集・要約
      ├─ InternationalNewsAgent : 海外人事ニュースを日本語で収集・要約
      ├─ XPostAgent             : X（旧Twitter）の人事パーソン投稿を収集
      └─ DigestWriterAgent      : 全結果を統合してNotionに投稿

各"Agent"はClaude APIへの独立した呼び出しとして実装されており、
それぞれが専門の役割を持つ「担当者」として機能する。
"""

import os
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import URLError

import anthropic
import requests
from notion_client import Client as NotionClient

# ── 定数 ──────────────────────────────────────────────────

JST = timezone(timedelta(hours=9))
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "../config/settings.json")


# ── 設定読み込み ──────────────────────────────────────────

def load_settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


# ── ニュース収集（Google News RSS） ──────────────────────

def fetch_news_from_rss(keyword: str, max_articles: int, lang: str = "ja") -> list[dict]:
    """
    Google News RSSからニュース記事を取得する。
    APIキー不要・無料で利用可能。
    """
    if lang == "ja":
        url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=ja&gl=JP&ceid=JP:ja"
    else:
        url = f"https://news.google.com/rss/search?q={quote(keyword)}&hl=en-US&gl=US&ceid=US:en"

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as response:
            tree = ET.parse(response)
    except URLError as e:
        print(f"  RSS取得失敗 ({keyword}): {e}")
        return []

    articles = []
    for item in tree.findall(".//item")[:max_articles]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        description = item.findtext("description", "").strip()
        # HTMLタグを除去
        description = re.sub(r"<[^>]+>", "", description)[:200]

        if title:
            articles.append({
                "title": title,
                "url": link,
                "published": pub_date,
                "description": description,
            })

    return articles


def collect_articles(keywords: list[str], max_per_feed: int, lang: str) -> list[dict]:
    """複数キーワードで記事を収集し、重複タイトルを除去する"""
    seen_titles = set()
    all_articles = []

    for keyword in keywords:
        articles = fetch_news_from_rss(keyword, max_per_feed, lang)
        for article in articles:
            if article["title"] not in seen_titles:
                seen_titles.add(article["title"])
                all_articles.append(article)

    return all_articles[:max_per_feed * 2]  # 上限を設ける


# ── サブエージェント基底クラス ────────────────────────────

class BaseAgent:
    """
    各サブエージェントの基底クラス。
    Claude APIクライアントを共有し、役割に応じたプロンプトで実行する。
    """
    def __init__(self, client: anthropic.Anthropic, model: str = "claude-haiku-4-5-20251001"):
        self.client = client
        self.model = model

    def run(self, prompt: str, system: str = "", max_tokens: int = 2000) -> str:
        messages = [{"role": "user", "content": prompt}]
        kwargs = {"model": self.model, "max_tokens": max_tokens, "messages": messages}
        if system:
            kwargs["system"] = system
        response = self.client.messages.create(**kwargs)
        return response.content[0].text


# ── サブエージェント①：国内ニュース担当 ─────────────────

class DomesticNewsAgent(BaseAgent):
    """
    国内人事ニュースを収集・要約するサブエージェント。
    担当：日本語RSSから記事を集め、人事担当者向けに要約する。
    """
    SYSTEM = """あなたは日本の人事・HR分野の専門アナリストです。
収集したニュース記事を、企業の人事担当者が5分で読める形に要約してください。
各記事について「ニュース概要」「人事への示唆」を簡潔に書いてください。"""

    def summarize(self, articles: list[dict]) -> str:
        if not articles:
            return "今週は該当する国内ニュースが見つかりませんでした。"

        articles_text = "\n\n".join([
            f"【{i+1}】{a['title']}\nURL: {a['url']}\n概要: {a['description']}"
            for i, a in enumerate(articles)
        ])

        prompt = f"""以下の国内人事ニュース記事（{len(articles)}件）を要約してください。

{articles_text}

各記事について以下の形式でまとめてください：
- タイトル
- ニュース概要（2〜3文）
- 人事担当者への示唆（1〜2文）
- 参考URL"""

        print(f"  DomesticNewsAgent: {len(articles)}件の記事を要約中...")
        return self.run(prompt, self.SYSTEM)


# ── サブエージェント②：海外ニュース担当 ─────────────────

class InternationalNewsAgent(BaseAgent):
    """
    海外人事ニュースを日本語で収集・要約するサブエージェント。
    担当：日本語キーワードでGoogleNewsを検索し、海外事例を日本語で紹介する。
    """
    SYSTEM = """あなたは海外のHR・人材管理分野の専門アナリストです。
収集した記事の中から海外の事例・動向に関するものを選び、
すべて日本語で要約して日本の人事担当者向けに紹介してください。
タイトル・本文・示唆はすべて日本語で記述してください。"""

    def summarize(self, articles: list[dict]) -> str:
        if not articles:
            return "今週は該当する海外人事ニュースが見つかりませんでした。"

        articles_text = "\n\n".join([
            f"【{i+1}】{a['title']}\nURL: {a['url']}\n概要: {a['description']}"
            for i, a in enumerate(articles)
        ])

        prompt = f"""以下の記事（{len(articles)}件）の中から、海外の人事・HR事例を含むものを選び、
すべて日本語で要約してください。海外事例が含まれない記事はスキップしてください。

{articles_text}

各記事について以下の形式でまとめてください（すべて日本語で）：
- タイトル（日本語）
- ニュース概要（日本語・2〜3文）
- 日本の人事担当者への示唆（1〜2文）
- 参考URL"""

        print(f"  InternationalNewsAgent: {len(articles)}件の記事を日本語で要約中...")
        return self.run(prompt, self.SYSTEM)


# ── サブエージェント③：X投稿収集担当 ────────────────────

class XPostAgent(BaseAgent):
    """
    X（旧Twitter）の人事パーソン投稿を収集するサブエージェント。
    Anthropicのweb_searchツールを使い、有益な発信を探して日本語で紹介する。
    """
    SYSTEM = """あなたは人事・HR分野のSNS情報収集の専門家です。
X（旧Twitter）で人事パーソンが発信している有益な投稿を探し、
日本語でわかりやすく紹介してください。
必ずJSON配列のみを返し、他のテキストは含めないでください。"""

    def collect_posts(self) -> list[dict]:
        """web_searchツールを使ってXの人事系投稿を収集する"""
        prompt = """X（旧Twitter / x.com）で最近発信された人事・HR・採用分野の有益な投稿を検索してください。

以下のようなトピックの投稿を探してください：
・採用・選考の実践的な知見や工夫
・組織開発・人材育成の事例や考察
・HRtechやAI活用の実体験
・CHROや人事リーダーの発信

見つかった投稿を3〜5件、以下のJSON配列形式のみで返してください：
[
  {
    "author": "発信者名またはアカウント名",
    "content": "投稿内容の要約（日本語・2〜3文）",
    "insight": "人事担当者への示唆（1文）",
    "url": "投稿URL（あれば。なければ空文字）"
  }
]"""

        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
        messages = [{"role": "user", "content": prompt}]

        print("  XPostAgent: X投稿をweb_searchで収集中...")

        text = ""
        for _ in range(6):  # 最大6ループ（tool_useが続く場合に備える）
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=self.SYSTEM,
                tools=tools,
                messages=messages
            )

            text_parts = [b.text for b in response.content if hasattr(b, "text")]

            if response.stop_reason == "end_turn":
                text = "".join(text_parts)
                break

            # tool_use が含まれる場合はメッセージに追加してループ継続
            messages.append({"role": "assistant", "content": response.content})
            has_tool_use = any(
                getattr(b, "type", "") == "tool_use" for b in response.content
            )
            if not has_tool_use:
                text = "".join(text_parts)
                break

        if not text:
            print("  XPostAgent: レスポンスを取得できませんでした")
            return []

        # JSONパース
        text = re.sub(r'```(?:json)?\s*', '', text).strip()
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if json_match:
            try:
                posts = json.loads(json_match.group())
                print(f"  XPostAgent: {len(posts)}件の投稿を収集しました")
                return posts[:5]
            except json.JSONDecodeError:
                cleaned = re.sub(r',\s*([}\]])', r'\1', json_match.group())
                try:
                    posts = json.loads(cleaned)
                    return posts[:5]
                except json.JSONDecodeError:
                    pass

        print("  XPostAgent: 投稿の解析に失敗しました")
        return []


# ── サブエージェント④：ダイジェスト執筆・投稿担当 ────────

class DigestWriterAgent(BaseAgent):
    """
    国内・海外の要約を統合し、最終ダイジェストを執筆してNotionに投稿するサブエージェント。
    担当：構造化JSONでダイジェストを生成し、Notionに美しいレイアウトで投稿する。
    """
    SYSTEM = """あなたは人事・HR分野のニュースレター編集者です。
提供された国内・海外のニュース要約をもとに、
人事担当者が月曜の朝に読みたくなる週次ダイジェストをJSON形式で作成してください。
必ずJSONのみを返し、他のテキストは一切含めないでください。"""

    def write_digest(self, domestic_summary: str, international_summary: str,
                     settings: dict, target_date: str,
                     domestic_articles: list = None,
                     international_articles: list = None) -> dict:
        """構造化JSONでダイジェストを生成する"""

        # 元記事URLの対照表を作成（タイトルの一部で照合用）
        dom_urls = {a["title"][:30]: a["url"] for a in (domestic_articles or []) if a.get("url")}
        int_urls = {a["title"][:30]: a["url"] for a in (international_articles or []) if a.get("url")}

        prompt = f"""以下の国内・海外ニュース要約をもとに、週次ダイジェストを作成してください。

## 国内ニュース要約
{domestic_summary}

## 海外ニュース要約
{international_summary}

## 国内ニュース 元記事URL参照（タイトルの冒頭30文字をキーにURLを照合してください）
{json.dumps(dom_urls, ensure_ascii=False)}

## 海外ニュース 元記事URL参照
{json.dumps(int_urls, ensure_ascii=False)}

以下のJSON形式のみで返してください（コードブロックや説明文は不要）：
{{
  "highlights": ["今週の重要ポイント1", "今週の重要ポイント2", "今週の重要ポイント3"],
  "domestic_news": [
    {{
      "title": "記事タイトル",
      "summary": "ニュース概要（2〜3文）",
      "hr_insight": "人事担当者への示唆（1〜2文）",
      "url": "元記事のURL（上記URL参照から該当するものを使う。なければ空文字）"
    }}
  ],
  "international_news": [
    {{
      "title": "記事タイトル（日本語）",
      "summary": "ニュース概要（日本語・2〜3文）",
      "hr_insight": "日本の人事担当者への示唆（1〜2文）",
      "url": "元記事のURL（上記URL参照から該当するものを使う。なければ空文字）"
    }}
  ],
  "weekly_comment": "今週全体の総括コメント（3〜4文）"
}}

対象週: {target_date}"""

        print("  DigestWriterAgent: ダイジェストを執筆中...")
        # X投稿セクション追加でJSONが長くなるため8000に増やす
        response_text = self.run(prompt, self.SYSTEM, max_tokens=8000)

        # マークダウンのコードブロックを除去
        response_text = re.sub(r'```(?:json)?\s*', '', response_text).strip()

        # JSONを抽出してパース
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError(f"JSON形式のレスポンスが取得できませんでした: {response_text[:300]}")

        raw_json = json_match.group()
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            # 末尾カンマなど軽微な問題を修正して再試行
            cleaned = re.sub(r',\s*([}\]])', r'\1', raw_json)
            return json.loads(cleaned)

    def post_to_notion(self, notion: NotionClient, page_id: str,
                       digest_data: dict, title: str,
                       domestic_articles: list = None,
                       international_articles: list = None,
                       x_posts: list = None) -> str:
        """構造化データをNotionの美しいレイアウトで投稿する"""

        def paragraph(text: str, url: str = None, bold: bool = False) -> dict:
            rich = {"type": "text", "text": {"content": text}}
            if url:
                rich["text"]["link"] = {"url": url}
            if bold:
                rich["annotations"] = {"bold": True}
            return {"object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [rich]}}

        def heading(text: str, level: int = 2) -> dict:
            t = f"heading_{level}"
            return {"object": "block", "type": t, t: {
                "rich_text": [{"type": "text", "text": {"content": text}}]
            }}

        def callout(text: str, emoji: str, color: str) -> dict:
            return {"object": "block", "type": "callout", "callout": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
                "icon": {"emoji": emoji},
                "color": color
            }}

        def divider() -> dict:
            return {"object": "block", "type": "divider", "divider": {}}

        blocks = []

        # ── ① 今週のハイライト ──
        highlights = "\n".join([f"• {h}" for h in digest_data.get("highlights", [])])
        blocks.append(callout(f"今週のハイライト\n\n{highlights}", "📌", "yellow_background"))
        blocks.append(divider())

        # ── ② 国内ニュース ──
        blocks.append(heading("🇯🇵 国内注目ニュース", 2))
        for article in digest_data.get("domestic_news", []):
            url = article.get("url") or None
            blocks.append(heading(article["title"], 3))
            if url:
                blocks.append(paragraph(f"🔗 元記事を読む", url=url))
            blocks.append(paragraph(article.get("summary", "")))
            blocks.append(callout(article.get("hr_insight", ""), "💡", "blue_background"))

        blocks.append(divider())

        # ── ③ 海外ニュース ──
        blocks.append(heading("🌐 海外注目ニュース", 2))
        for article in digest_data.get("international_news", []):
            url = article.get("url") or None
            blocks.append(heading(article["title"], 3))
            if url:
                blocks.append(paragraph(f"🔗 元記事を読む", url=url))
            blocks.append(paragraph(article.get("summary", "")))
            blocks.append(callout(article.get("hr_insight", ""), "💡", "blue_background"))

        blocks.append(divider())

        # ── ④ 今週の総括 ──
        blocks.append(callout(digest_data.get("weekly_comment", ""), "💬", "gray_background"))

        # ── ⑤ X 人事パーソンのつぶやき ──
        if x_posts:
            blocks.append(divider())
            blocks.append(heading("𝕏 人事パーソンのつぶやき", 2))
            for post in x_posts:
                author = post.get("author", "")
                content = post.get("content", "")
                insight = post.get("insight", "")
                url = post.get("url") or None

                blocks.append(heading(f"@{author}" if author else "投稿", 3))
                blocks.append(paragraph(content))
                if insight:
                    blocks.append(callout(insight, "💡", "purple_background"))
                if url:
                    blocks.append(paragraph("🔗 投稿を見る", url=url))

        # ── ⑥ 参考リンク（元記事リストから確実に追加） ──
        ref_sections = []
        if domestic_articles:
            ref_sections.append(("🇯🇵 国内ニュース", domestic_articles))
        if international_articles:
            ref_sections.append(("🌐 海外ニュース", international_articles))

        if ref_sections:
            blocks.append(divider())
            blocks.append(heading("📎 参考リンク", 2))
            for section_title, articles in ref_sections:
                blocks.append(heading(section_title, 3))
                for article in articles:
                    url = article.get("url", "")
                    art_title = article.get("title", "（タイトルなし）")
                    if url:
                        blocks.append(paragraph(f"・{art_title}", url=url))

        response = notion.pages.create(
            parent={"page_id": page_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}},
            children=blocks
        )
        # ページアイコンを設定
        notion.pages.update(page_id=response["id"], icon={"type": "emoji", "emoji": "🗞️"})
        return {"url": response["url"], "id": response["id"]}


# ── Manager：全体を統括 ───────────────────────────────────

class DigestManager:
    """
    Mgr型サブエージェントパターンのManager役。
    各サブエージェントに指示を出し、結果を統合する。
    """

    def __init__(self):
        self.settings = load_settings()
        self.claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.notion = NotionClient(auth=os.environ["NOTION_API_KEY"])
        self.notion_page_id = os.environ["NOTION_PAGE_ID"]

    def run(self):
        today = datetime.now(JST)
        target_date = today.strftime("%Y年%-m月%-d日（週）")
        title = f"{self.settings['notion']['title_prefix']} {today.strftime('%Y/%m/%d')}"

        print(f"=== 週次ダイジェスト生成開始: {target_date} ===")
        max_articles = self.settings["max_articles_per_feed"]

        # ── Step 1: 各サブエージェントが担当ニュースを収集・要約 ──

        print("\n[サブエージェント①] 国内ニュース収集・要約")
        domestic_articles = collect_articles(
            self.settings["domestic_keywords"], max_articles, lang="ja"
        )
        domestic_agent = DomesticNewsAgent(self.claude)
        domestic_summary = domestic_agent.summarize(domestic_articles)

        print("\n[サブエージェント②] 海外ニュース収集・要約（日本語）")
        international_articles = collect_articles(
            self.settings["international_keywords"], max_articles, lang="ja"
        )
        international_agent = InternationalNewsAgent(self.claude)
        international_summary = international_agent.summarize(international_articles)

        print("\n[サブエージェント③] X投稿収集")
        x_agent = XPostAgent(self.claude)
        x_posts = x_agent.collect_posts()

        # ── Step 2: DigestWriterAgentが統合・執筆・投稿 ──────────

        print("\n[サブエージェント④] ダイジェスト執筆・Notion投稿")
        writer_agent = DigestWriterAgent(self.claude)
        digest_data = writer_agent.write_digest(
            domestic_summary, international_summary, self.settings, target_date,
            domestic_articles=domestic_articles,
            international_articles=international_articles
        )
        notion_result = writer_agent.post_to_notion(
            self.notion, self.notion_page_id, digest_data, title,
            domestic_articles=domestic_articles,
            international_articles=international_articles,
            x_posts=x_posts
        )

        # ── Step 3: 親ページのバックナンバーにリンクを追加 ──
        print("\n[Manager] 親ページにリンクを追加中...")
        date_label = today.strftime("%Y年%-m月%-d日（%a）").replace(
            "Mon", "月").replace("Tue", "火").replace("Wed", "水").replace(
            "Thu", "木").replace("Fri", "金").replace("Sat", "土").replace("Sun", "日")
        self.update_parent_index(notion_result["id"], date_label)

        # ── Step 4: Slack通知 ──────────────────────────────
        slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")
        if slack_webhook:
            print("\n[Manager] Slackに通知中...")
            self.notify_slack(slack_webhook, notion_result["url"], title, today)

        print(f"\n=== 完了 ===")
        print(f"Notionに投稿しました: {notion_result['url']}")
        return notion_result["url"]

    def notify_slack(self, webhook_url: str, notion_url: str,
                     title: str, today: datetime) -> None:
        """Slack Incoming Webhook でダイジェスト更新を通知する"""
        date_str = today.strftime("%Y年%-m月%-d日（%a）").replace(
            "Mon", "月").replace("Tue", "火").replace("Wed", "水").replace(
            "Thu", "木").replace("Fri", "金").replace("Sat", "土").replace("Sun", "日")

        payload = {
            "text": f"🗞️ 人事AIニュースダイジェストが更新されました！",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🗞️ 人事AIニュースダイジェスト",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{date_str}* の週次ダイジェストが更新されました！\n"
                            f"国内ニュース・海外ニュース・X人事パーソン投稿をまとめています。"
                        )
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"👉 *<{notion_url}|Notionで読む>*"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "📌 毎週月曜 8:00 JST に自動配信 | KAGダイジェストBot"
                        }
                    ]
                }
            ]
        }

        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=10
        )
        if resp.status_code == 200:
            print("  Slack通知送信完了 ✅")
        else:
            print(f"  Slack通知エラー: {resp.status_code} {resp.text}")

    def update_parent_index(self, digest_page_id: str, date_label: str):
        """
        親ページの「📚 バックナンバー」セクションに新しいダイジェストへのリンクを追加する。
        「バックナンバー」見出しの直後に挿入するため、常に最新が上に表示される。
        """
        # 親ページの全ブロックを取得
        result = self.notion.blocks.children.list(block_id=self.notion_page_id)
        all_blocks = result.get("results", [])

        # 「📚 バックナンバー」見出しブロックを探す
        heading_block_id = None
        for block in all_blocks:
            if block.get("type") == "heading_2":
                texts = block["heading_2"].get("rich_text", [])
                content = "".join(t.get("text", {}).get("content", "") for t in texts)
                if "バックナンバー" in content:
                    heading_block_id = block["id"]
                    break

        # エントリーブロック（callout形式でカード風に）
        entry = {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": f"📅 {date_label}\n"},
                        "annotations": {"bold": True}
                    },
                    {
                        "type": "mention",
                        "mention": {"type": "page", "page": {"id": digest_page_id}}
                    }
                ],
                "icon": {"emoji": "🗞️"},
                "color": "default"
            }
        }

        # 見出しの直後に挿入（新しいものが上に来る）
        kwargs = {"children": [entry]}
        if heading_block_id:
            kwargs["after"] = heading_block_id

        self.notion.blocks.children.append(
            block_id=self.notion_page_id,
            **kwargs
        )
        print(f"  親ページにリンクを追加しました: {date_label}")


# ── エントリーポイント ─────────────────────────────────────

if __name__ == "__main__":
    required = ["ANTHROPIC_API_KEY", "NOTION_API_KEY", "NOTION_PAGE_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"環境変数が未設定です: {', '.join(missing)}")

    manager = DigestManager()
    manager.run()
