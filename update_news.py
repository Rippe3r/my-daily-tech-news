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

html_page = f"""


    
    
    Daily AI & Tech Digest
    


    ⚡ Daily AI & Tech Pulse
    Updated automatically every 24 hours via Gemini API
    
        {response.text}
    


"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)
