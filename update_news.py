import os
import ssl
import re
import urllib.request
import xml.etree.ElementTree as ET
from google import genai
from google.genai import types

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.wired.com/feed/category/business/latest/rss"
]

def extract_image_url(item):
    """Extract thumbnail or image URL from RSS XML structure."""
    for child in item:
        if child.tag.endswith(('thumbnail', 'content')):
            url = child.attrib.get('url')
            if url: return url
        if child.tag == 'enclosure':
            url = child.attrib.get('url')
            if url: return url
            
    desc = item.find('description')
    if desc is not None and desc.text:
        img_match = re.search(r'<img [^>]*src="([^"]+)"', desc.text)
        if img_match:
            return img_match.group(1)
            
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
            for item in root.findall('.//item')[:6]:
                title = item.find('title').text if item.find('title') is not None else ""
                link = item.find('link').text if item.find('link') is not None else ""
                img = extract_image_url(item)
                if title and link:
                    articles.append(f"Title: {title}\nURL: {link}\nImage: {img}")
        except Exception as e:
            print(f"Warning feed error {feed}: {e}")
            
    return "\n---\n".join(articles)

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY secret is missing or empty in GitHub Secrets!")

client = genai.Client(api_key=api_key)

raw_news = fetch_rss()
if not raw_news:
    raise ValueError("Could not retrieve news items from RSS feeds.")

system_prompt = """
You are an expert AI & Tech Journalist and Fact-Checker.
Select 6 to 8 verified, major tech stories from the raw feed list. Exclude clickbait, duplicate topics, and unverified rumors.

For each story, output strictly this HTML structure (do NOT use ```html formatting blocks):

<article class="news-card">
  <div class="card-image">
    <img src="IMAGE_URL_OR_FALLBACK" alt="News Image" onerror="this.src='[https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=600&auto=format&fit=crop)'">
  </div>
  <div class="card-content">
    <span class="category">CATEGORY_NAME</span>
    <h2 class="title"><a href="ORIGINAL_STORY_URL" target="_blank" rel="noopener">STORY_TITLE</a></h2>
    <p class="summary">Detailed 2-3 sentence overview explaining what occurred, key context, and industry impact.</p>
    <ul class="key-points">
      <li><strong>Key Fact:</strong> Important statistic, announcement, or primary takeaway.</li>
      <li><strong>Impact:</strong> Why this matters for the tech industry or users.</li>
      <li><strong>Next Steps:</strong> Expected future updates, product timeline, or legal/business implications.</li>
    </ul>
    <a href="ORIGINAL_STORY_URL" target="_blank" rel="noopener" class="read-more">Read Full Article &rarr;</a>
  </div>
</article>

IMAGE SELECTION RULES:
1. If the input item provides an Image URL, use that link in <img src="...">.
2. If Image URL is blank or missing, select one appropriate Unsplash image based on topic:
   - AI / Machine Learning: [https://images.unsplash.com/photo-1677442136019-21780efad99a?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1677442136019-21780efad99a?w=600&auto=format&fit=crop)
   - Chips / Hardware / Data Centers: [https://images.unsplash.com/photo-1518770660439-4636190af475?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1518770660439-4636190af475?w=600&auto=format&fit=crop)
   - Security / Law / Privacy: [https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1550751827-4bd374c3f58b?w=600&auto=format&fit=crop)
   - Dev Tools / Code / Software: [https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1555066931-4365d14bab8c?w=600&auto=format&fit=crop)
   - General Business / Startups: [https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=600&auto=format&fit=crop](https://images.unsplash.com/photo-1451187580459-43490279c0fa?w=600&auto=format&fit=crop)
"""

try:
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=f"Raw news data:\n{raw_news}",
        config=types.GenerateContentConfig(system_instruction=system_prompt)
    )
    raw_text = response.text
except Exception as e:
    print(f"Gemini 3.6 call failed: {e}. Falling back to gemini-1.5-flash...")
    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=f"Raw news data:\n{raw_news}",
        config=types.GenerateContentConfig(system_instruction=system_prompt)
    )
    raw_text = response.text

clean_content = raw_text.replace("```html", "").replace("```", "").strip()

html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daily AI & Tech Digest</title>
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
        
        .card-image img {{
            width: 100%;
            height: 220px;
            object-fit: cover;
            display: block;
        }}
        
        .card-content {{ padding: 24px; flex-grow: 1; }}
        
        .category {{ 
            background: #0284c7; 
            color: white; 
            font-size: 0.72rem; 
            padding: 3px 10px; 
            border-radius: 6px; 
            font-weight: 700; 
            text-transform: uppercase; 
            letter-spacing: 0.5px;
            display: inline-block;
            margin-bottom: 12px;
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
            .card-image {{ width: 35%; flex-shrink: 0; }}
            .card-image img {{ height: 100%; }}
            .card-content {{ width: 65%; }}
        }}
    </style>
</head>
<body>
    <header>
        <h1>⚡ Daily AI & Tech Pulse</h1>
        <p class="subtitle">Verified Daily News, Images & Deep-Dive Takeaways</p>
    </header>
    <main>
        {clean_content}
    </main>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print("Successfully generated index.html with images, hyperlinks, and detailed takeaways!")
