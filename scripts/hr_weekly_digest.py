"""
人事ニュース 週次ダイジェスト 自動生成スクリプト

【アーキテクチャ】
講座Session3で学んだ「Mgr型サブエージェント」パターンを実装。

  DigestManager（このスクリプト）
      ├─ DomesticNewsAgent   : 国内人事ニュースを収集・要約
      ├─ InternationalNewsAgent : 海外人事ニュースを収集・要約
      └─ DigestWriterAgent   : 両結果を統合してNotionに投稿

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
    海外人事ニュースを収集・要約するサブエージェント。
    担当：英語RSSから記事を集め、日本語で人事担当者向けに要約する。
    """
    SYSTEM = """あなたは海外のHR・人材管理分野の専門アナリストです。
英語で書かれた海外ニュースを日本語に翻訳・要約し、
日本の人事担当者が参考にできる形で提供してください。"""

    def summarize(self, articles: list[dict]) -> str:
        if not articles:
            return "今週は該当する海外ニュースが見つかりませんでした。"

        articles_text = "\n\n".join([
            f"【{i+1}】{a['title']}\nURL: {a['url']}\n概要: {a['description']}"
            for i, a in enumerate(articles)
        ])

        prompt = f"""以下の海外人事ニュース記事（{len(articles)}件）を日本語で要約してください。

{articles_text}

各記事について以下の形式でまとめてください：
- タイトル（日本語訳）
- ニュース概要（日本語・2〜3文）
- 日本の人事担当者への示唆（1〜2文）
- 参考URL

※翻訳・要約の過程で内容が正確でない場合があります。URLから原文をご確認ください。"""

        print(f"  InternationalNewsAgent: {len(articles)}件の記事を要約中...")
        return self.run(prompt, self.SYSTEM)


# ── サブエージェント③：ダイジェスト執筆・投稿担当 ────────

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
        # JSONが長くなるのでmax_tokensを4000に増やす
        response_text = self.run(prompt, self.SYSTEM, max_tokens=4000)

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
                       digest_data: dict, title: str) -> str:
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

        response = notion.pages.create(
            parent={"page_id": page_id},
            properties={"title": {"title": [{"type": "text", "text": {"content": title}}]}},
            children=blocks
        )
        return response["url"]


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

        print("\n[サブエージェント②] 海外ニュース収集・要約")
        international_articles = collect_articles(
            self.settings["international_keywords"], max_articles, lang="en"
        )
        international_agent = InternationalNewsAgent(self.claude)
        international_summary = international_agent.summarize(international_articles)

        # ── Step 2: DigestWriterAgentが統合・執筆・投稿 ──────────

        print("\n[サブエージェント③] ダイジェスト執筆・Notion投稿")
        writer_agent = DigestWriterAgent(self.claude)
        digest_data = writer_agent.write_digest(
            domestic_summary, international_summary, self.settings, target_date,
            domestic_articles=domestic_articles,
            international_articles=international_articles
        )
        notion_url = writer_agent.post_to_notion(
            self.notion, self.notion_page_id, digest_data, title
        )

        print(f"\n=== 完了 ===")
        print(f"Notionに投稿しました: {notion_url}")
        return notion_url


# ── エントリーポイント ─────────────────────────────────────

if __name__ == "__main__":
    required = ["ANTHROPIC_API_KEY", "NOTION_API_KEY", "NOTION_PAGE_ID"]
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"環境変数が未設定です: {', '.join(missing)}")

    manager = DigestManager()
    manager.run()
