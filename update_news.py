#!/usr/bin/env python3
"""
每日 09:00 / 20:00 自动抓取全网 AI 资讯、中国 AI 动态与 GitHub 热门开源项目，
调用 Gemini API (或兼容 OpenAI 格式的 DeepSeek API) 生成趣味锐评并增量写入 news.json。
仅使用 Python 标准库，零外部依赖，在 GitHub Actions 中秒级运行。
"""
import datetime
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NEWS_JSON_PATH = "news.json"

RSS_FEEDS = [
    ("Global AI", "https://blog.google/innovation-and-ai/technology/ai/rss/"),
    ("MIT AI News", "https://news.mit.edu/rss/topic/artificial-intelligence2"),
    ("IT之家 AI", "https://www.ithome.com/rss/"),
]


def http_get_text(url, headers=None, timeout=15):
    req_headers = {"User-Agent": "AI-Daily-Pulse-Kanban/1.0"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_github_top5():
    """抓取近期高热度 AI 开源项目（附真实 Star 收藏数与简介）"""
    since_date = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    q = urllib.parse.quote(f"topic:ai pushed:>={since_date}")
    url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=10"
    try:
        raw = http_get_text(url, {"Accept": "application/vnd.github+json"})
        items = json.loads(raw).get("items", [])[:8]
        results = []
        for it in items:
            results.append({
                "name": it.get("full_name"),
                "stars": it.get("stargazers_count", 0),
                "forks": it.get("forks_count", 0),
                "description": it.get("description") or "AI open-source project",
                "url": it.get("html_url"),
            })
        return results
    except Exception as e:
        print("GitHub fetch warning:", e)
        return []


def fetch_rss_headlines():
    """抓取 RSS 最新标题与链接"""
    headlines = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            xml_text = http_get_text(feed_url)
            root = ET.fromstring(xml_text)
            for item in root.findall(".//item")[:6]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                desc = (item.findtext("description") or "").strip()[:280]
                if title and link:
                    headlines.append({
                        "source": source_name,
                        "title": title,
                        "url": link,
                        "snippet": desc,
                    })
        except Exception as e:
            print(f"RSS fetch warning ({source_name}):", e)
    return headlines


def call_gemini_api(api_key, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.7},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


def main():
    now_bj = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    date_str = now_bj.strftime("%Y-%m-%d")
    slot = "早报 (09:00)" if now_bj.hour < 15 else "晚报 (20:00)"
    edition_name = f"{date_str} {slot}"

    existing = []
    if os.path.exists(NEWS_JSON_PATH):
        try:
            existing = json.load(open(NEWS_JSON_PATH, "r", encoding="utf-8"))
        except Exception:
            existing = []

    existing_titles = {c.get("title") for c in existing}
    gh_repos = fetch_github_top5()
    rss_items = fetch_rss_headlines()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    new_cards = []

    if api_key and (gh_repos or rss_items):
        prompt = f"""你是「AI 每日趣味资讯看板」主编。请根据以下今日抓取的真实开源项目和科技资讯，加上重点关注中国 AI 动态（如 DeepSeek、豆包、WorkBuddy、Qwen、Kimi），生成 JSON 数组。
当前期数 edition 固定为："{edition_name}"，日期 date 固定为："{date_str}"。
请输出 8~10 条不与已有标题重复的卡片，其中必须包含 5 条 category 为 "github" 的 GitHub 开源项目推荐（必须解释项目是什么、解决什么问题，并在 lab 字段标注真实 Star 收藏数），以及全球 AI 资讯（category 为 "hot"、"models"、"tools"、"fun"）和中国 AI 动态（category 为 "china"）。
每条 JSON 格式严格如下：
{{
  "id": "唯一字符串ID",
  "edition": "{edition_name}",
  "date": "{date_str}",
  "category": "hot|china|github|models|tools|fun",
  "lab": "厂商或⭐ Star数",
  "funScore": 92,
  "title": "吸引人的中文标题",
  "funTake": "一句幽默接地气的趣味锐评",
  "summary": "详细清晰的事实说明（GitHub项目必须解释具体用途与推荐理由）",
  "sources": [{{"label": "原文链接", "url": "真实URL"}}]
}}
今日 GitHub 候选数据：{json.dumps(gh_repos, ensure_ascii=False)}
今日资讯候选数据：{json.dumps(rss_items[:12], ensure_ascii=False)}
已有标题（请勿重复）：{json.dumps(list(existing_titles)[:30], ensure_ascii=False)}
只输出 JSON 数组："""
        try:
            generated = call_gemini_api(api_key, prompt)
            if isinstance(generated, list):
                new_cards = generated
        except Exception as e:
            print("Gemini API call failed, falling back to direct formatting:", e)

    if not new_cards:
        for idx, repo in enumerate(gh_repos[:5]):
            new_cards.append({
                "id": f"{date_str}-gh-{idx+1}",
                "edition": edition_name,
                "date": date_str,
                "category": "github",
                "lab": f"⭐ {repo['stars']:,} Stars",
                "funScore": 92,
                "title": f"{repo['name']}：高热度 AI 开源项目",
                "funTake": f"收获 {repo['stars']:,} Stars 与 {repo['forks']:,} Forks 的热门开源仓库。",
                "summary": f"项目简介：{repo['description']}（GitHub 收藏数：{repo['stars']:,} Stars）。",
                "sources": [{"label": "GitHub 仓库", "url": repo["url"]}],
            })

    added = 0
    for card in reversed(new_cards):
        if not isinstance(card, dict) or not card.get("title"):
            continue
        if card["title"] in existing_titles:
            continue
        existing.insert(0, card)
        existing_titles.add(card["title"])
        added += 1

    with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"Done. Added {added} new cards to {NEWS_JSON_PATH}.")


if __name__ == "__main__":
    main()
