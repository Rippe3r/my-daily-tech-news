import os
import xml.etree.ElementTree as ET
import urllib.request
from google import genai
from google.genai import types

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/category/business/latest/rss"
]

def fetch_rss():
    articles = []
    for feed in RSS_FEEDS:
        try:
            req = urllib.request.Request(feed, headers={'User-Agent': 'Mozilla/5.0'})
            xml_data = urllib.request.urlopen(req).read()
            root = ET.fromstring(xml_data)
            for item in root.findall('.//item')[:5]:
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                articles.append(f"Title: {title}\nURL: {link}")
        except Exception as e:
            print(f"Error: {e}")
    return "\n---\n".join(articles)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

system_prompt = """
You are an expert AI & Tech News Fact-Checker. 
Select 5 to 8 verified, major tech stories from the list below. Exclude clickbait, duplicate topics, and rumors.
Output ONLY raw HTML cards using this template for each story (do NOT use markdown code blocks):


  CATEGORY
  TITLE
  2-3 sentence summary explaining what happened and why it matters.

"""

raw_news = fetch_rss()

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=f"Raw news data:\n{raw_news}",
    config=types.GenerateContentConfig(system_instruction=system_prompt)
)

# Clean up any markdown code blocks returned by Gemini
clean_content = response.text.replace("```html", "").replace("```", "").strip()

html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daily AI & Tech Digest</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; background: #0f172a; color: #f8fafc; line-height: 1.6; }}
        h1 {{ color: #38bdf8; text-align: center; margin-bottom: 4px; }}
        .subtitle {{ text-align: center; color: #94a3b8; font-size: 0.9rem; margin-bottom: 40px; }}
        .news-card {{ background: #1e293b; padding: 20px; border-radius: 12px; margin-bottom: 20px; border: 1px solid #334155; }}
        .category {{ background: #0284c7; color: white; font-size: 0.7rem; padding: 2px 8px; border-radius: 4px; font-weight: bold; text-transform: uppercase; }}
        .news-card h2 {{ margin: 10px 0 8px 0; font-size: 1.2rem; }}
        .news-card a {{ color: #f8fafc; text-decoration: none; }}
        .news-card a:hover {{ color: #38bdf8; }}
        .news-card p {{ color: #94a3b8; margin: 0; font-size: 0.95rem; }}
    </style>
</head>
<body>
    <h1>⚡ Daily AI & Tech Pulse</h1>
    <p class="subtitle">Updated automatically every 24 hours via Gemini API</p>
    <div>
        {clean_content}
    </div>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)
