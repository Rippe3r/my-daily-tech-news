import os
import ssl
import re
import json
import time
import random
from datetime import datetime, timezone
import urllib.request
import xml.etree.ElementTree as ET
from google import genai
from google.genai import types

# 1. RSS Feed Parser (Handles Media RSS Namespaces)
RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/category/business/latest/rss"
]

NAMESPACES = {
    'media': 'http://search.yahoo.com/mrss/',
    'content': 'http://purl.org/rss/1.0/modules/content/'
}

def extract_image_url(item):
    """Extract valid image URL from RSS XML structure including media namespace."""
    # Check media:content and media:thumbnail
    for tag in ['.//media:content', './/media:thumbnail']:
        elem = item.find(tag, NAMESPACES)
        if elem is not None:
            url = elem.attrib.get('url')
            if url and url.startswith('http') and not url.endswith('.svg'):
                return url

    # Check standard enclosure tag
    enclosure = item.find('enclosure')
    if enclosure is not None:
        url = enclosure.attrib.get('url')
        if url and url.startswith('http'):
            return url

    # Check HTML description tag for <img>
    desc = item.find('description')
    if desc is not None and desc.text:
        img_match = re.search(r'<img [^>]*src="(https?://[^"]+)"', desc.text)
        if img_match:
            img_url = img_match.group(1)
            if not img_url.endswith('.svg'):
                return img_url

    return ""

def fetch_rss():
    articles = []
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for feed in RSS_FEEDS:
        try:
            req = urllib.request.Request(feed, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            xml_data = urllib.request.urlopen(req, context=ctx, timeout=10).read()
            root = ET.fromstring(xml_data)
            for item in root.findall('.//item')[:5]:
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                img = extract_image_url(item)
                if title and link:
                    articles.append(f"Title: {title}\nURL: {link}\nRSS_Image: {img}")
        except Exception as e:
            print(f"Warning feed error {feed}: {e}")
            
    return "\n---\n".join(articles)

# 2. Database & Purge Stories Older Than 48 Hours
DB_FILE = "news_data.json"
NOW_EPOCH = int(time.time())
RETENTION_PERIOD = 48 * 3600  # 48 hours

existing_news = []
if os.path.exists(DB_FILE):
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            existing_news = json.load(f)
    except Exception as e:
        print(f"Error loading database: {e}")

active_news = [item for item in existing_news if (NOW_EPOCH - item.get("timestamp_epoch", 0)) < RETENTION_PERIOD]
existing_urls = {item["url"] for item in active_news}

# 3. Gemini API Call
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY secret is missing or empty!")

client = genai.Client(api_key=api_key)
raw_news = fetch_rss()

system_prompt = """
You are an expert AI & Tech Journalist.
Analyze the raw RSS feeds and return a JSON ARRAY of 3 to 5 verified, distinct major tech stories.

Output ONLY a raw JSON array matching this exact schema for each item (do NOT use ```json code blocks):
[
  {
    "title": "Headline",
    "url": "Original Story Link",
    "category": "AI | HARDWARE | SECURITY | SOFTWARE | MOBILE | BUSINESS",
    "summary": "Detailed 2-3 sentence overview.",
    "point1": "Key fact or primary takeaway.",
    "point2": "Why this matters for users/industry.",
    "point3": "Future outlook or timeline.",
    "rss_image": "Copy exact RSS_Image URL from feed if present, otherwise leave empty string"
  }
]
"""

try:
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"Raw news data:\n{raw_news}",
        config=types.GenerateContentConfig(system_instruction=system_prompt)
    )
    raw_text = response.text
except Exception as e:
    print(f"Primary model failed: {e}. Falling back...")
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=f"Raw news data:\n{raw_news}",
        config=types.GenerateContentConfig(system_instruction=system_prompt)
    )
    raw_text = response.text

# 4. Verified Category Image Pool (Guaranteed Working Unsplash Images)
CATEGORY_IMAGES = {
    "AI": [
        "[https://images.unsplash.com/photo-1677442136019-21780efad99a?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1677442136019-21780efad99a?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1620712943543-bcc4688e7485?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1620712943543-bcc4688e7485?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1485827404703-89b55fcc595e?w=800&auto=format&fit=crop)"
    ],
    "HARDWARE": [
        "[https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1518770660439-4636190af475?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?w=800&auto=format&fit=crop)"
    ],
    "SECURITY": [
        "[https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1563986768609-322da13575f3?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1563986768609-322da13575f3?w=800&auto=format&fit=crop)"
    ],
    "SOFTWARE": [
        "[https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1542831371-29b0f74f9713?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1542831371-29b0f74f9713?w=800&auto=format&fit=crop)"
    ],
    "MOBILE": [
        "[https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=800&auto=format&fit=crop)"
    ],
    "BUSINESS": [
        "[https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&auto=format&fit=crop)",
        "[https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=800&auto=format&fit=crop)"
    ]
}
DEFAULT_IMAGE = "[https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&auto=format&fit=crop](https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=800&auto=format&fit=crop)"

clean_json = raw_text.replace("```json", "").replace("```", "").strip()

try:
    new_items = json.loads(clean_json)
except Exception as e:
    print(f"JSON parsing error: {e}")
    new_items = []

now_utc = datetime.now(timezone.utc)
timestamp_str = now_utc.strftime("%b %d • %I:%M %p UTC")

added_count = 0
for item in new_items:
    url = item.get("url", "").strip()
    if url and url not in existing_urls:
        rss_img = item.get("rss_image", "").strip()
        cat = item.get("category", "BUSINESS").upper()
        
        # Determine image: Use valid RSS feed image or pick a verified category image
        if rss_img and rss_img.startswith("http") and not rss_img.endswith(".svg"):
            final_img = rss_img
        else:
            img_list = CATEGORY_IMAGES.get(cat, CATEGORY_IMAGES["BUSINESS"])
            final_img = random.choice(img_list)
            
        item["image_url"] = final_img
        item["timestamp_epoch"] = NOW_EPOCH
        item["timestamp_display"] = timestamp_str
        
        active_news.insert(0, item)
        existing_urls.add(url)
        added_count += 1

# Save database
with open(DB_FILE, "w", encoding="utf-8") as f:
    json.dump(active_news, f, indent=2)

# 5. Build HTML Page
cards_html = ""
for item in active_news:
    cards_html += f"""
<article class="news-card">
  <div class="card-image">
    <img src="{item.get('image_url')}" alt="News Image" loading="lazy" decoding="async" onerror="this.onerror=null; this.src='{DEFAULT_IMAGE}';">
  </div>
  <div class="card-content">
    <div class="meta-bar">
      <span class="category">{item.get('category', 'TECH')}</span>
      <span class="timestamp">⏰ {item.get('timestamp_display')}</span>
    </div>
    <h2 class="title"><a href="{item.get('url')}" target="_blank" rel="noopener">{item.get('title')}</a></h2>
    <p class="summary">{item.get('summary')}</p>
    <ul class="key-points">
      <li><strong>Key Fact:</strong> {item.get('point1', '')}</li>
      <li><strong>Impact:</strong> {item.get('point2', '')}</li>
      <li><strong>Next Steps:</strong> {item.get('point3', '')}</li>
    </ul>
    <a href="{item.get('url')}" target="_blank" rel="noopener" class="read-more">Read Full Article &rarr;</a>
  </div>
</article>
"""

html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hourly AI & Tech Pulse</title>
    <style>
        :root {{
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --border: #334155;
        }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
            max-width: 900px; 
            margin: 40px auto; 
            padding: 0 20px; 
            background: var(--bg-color); 
            color: var(--text-main); 
            line-height: 1.6; 
        }}
        header {{ text-align: center; margin-bottom: 40px; }}
        h1 {{ color: var(--accent); margin-bottom: 6px; font-size: 2.2rem; }}
        .subtitle {{ color: var(--text-muted); font-size: 0.95rem; }}
        
        .news-card {{ 
            background: var(--card-bg); 
            border-radius: 14px; 
            margin-bottom: 30px; 
            border: 1px solid var(--border); 
            overflow: hidden; 
            display: flex;
            flex-direction: column;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            transition: transform 0.2s ease, border-color 0.2s ease;
        }}
        .news-card:hover {{
            transform: translateY(-2px);
            border-color: var(--accent);
        }}
        
        .card-image {{
            background: #1e293b;
            width: 100%;
            height: 200px;
            overflow: hidden;
            flex-shrink: 0;
        }}
        
        .card-image img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}
        
        .card-content {{ padding: 24px; flex-grow: 1; }}
        
        .meta-bar {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
            flex-wrap: wrap;
        }}
        
        .category {{ 
            background: #0284c7; 
            color: white; 
            font-size: 0.72rem; 
            padding: 3px 10px; 
            border-radius: 6px; 
            font-weight: 700; 
            text-transform: uppercase; 
            letter-spacing: 0.5px;
        }}
        
        .timestamp {{
            color: var(--text-muted);
            font-size: 0.8rem;
            font-weight: 500;
        }}
        
        .title {{ margin: 0 0 12px 0; font-size: 1.35rem; line-height: 1.3; }}
        .title a {{ color: var(--text-main); text-decoration: none; transition: color 0.2s; }}
        .title a:hover {{ color: var(--accent); }}
        
        .summary {{ color: #cbd5e1; font-size: 0.98rem; margin-bottom: 16px; }}
        
        .key-points {{
            background: rgba(15, 23, 42, 0.5);
            border-left: 3px solid var(--accent);
            padding: 12px 16px 12px 28px;
            margin: 0 0 20px 0;
            border-radius: 0 8px 8px 0;
            font-size: 0.9rem;
            color: #94a3b8;
        }}
        .key-points li {{ margin-bottom: 6px; }}
        .key-points li:last-child {{ margin-bottom: 0; }}
        .key-points strong {{ color: var(--text-main); }}
        
        .read-more {{ 
            display: inline-block; 
            color: var(--accent); 
            font-weight: 600; 
            text-decoration: none; 
            font-size: 0.9rem;
        }}
        .read-more:hover {{ text-decoration: underline; }}

        @media (min-width: 640px) {{
            .news-card {{ flex-direction: row; }}
            .card-image {{ width: 35%; height: auto; min-height: 220px; }}
            .card-content {{ width: 65%; }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>⚡ Hourly AI & Tech Pulse</h1>
        <p class="subtitle">Live Verified News Feed • Stories Retained for 48 Hours</p>
    </header>
    <main>
        {cards_html}
    </main>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Updated news site successfully. Total active stories: {len(active_news)}")
