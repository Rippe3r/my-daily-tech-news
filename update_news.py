import os
import ssl
import re
import json
import time
import html
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from google import genai
from google.genai import types


# ============================================================
# TECH MATRIX PULSE
# Four-channel major-news aggregator:
#   AI          -> major AI news
#   TECHNOLOGY  -> major technology news
#   INDIA       -> major news about India, any subject
#   WORLD       -> major global news, any subject
#
# IMPORTANT:
# No automated system can honestly guarantee that every news story
# is "100% true". This pipeline is deliberately conservative:
# - reputable/primary sources only
# - Google News is discovery material, not a publishable source
# - duplicates are grouped
# - minor/clickbait/rumor stories are rejected
# - the model is forbidden from inventing facts
# - stories that cannot be supported are not published
# ============================================================


# ------------------------------------------------------------
# 1. CATEGORY FEEDS
# ------------------------------------------------------------

CATEGORY_FEEDS = {
    "AI": [
        ("direct", "TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("direct", "WIRED AI", "https://www.wired.com/feed/tag/ai/latest/rss"),
        ("discovery", "Google News AI", "https://news.google.com/rss/search?q=artificial+intelligence+AI+models+agents&hl=en-US&gl=US&ceid=US:en"),
    ],

    "TECH": [
        ("direct", "TechCrunch", "https://techcrunch.com/feed/"),
        ("direct", "The Verge", "https://www.theverge.com/rss/index.xml"),
        ("direct", "WIRED", "https://www.wired.com/feed/rss"),
        ("discovery", "Google News Technology", "https://news.google.com/rss/search?q=technology+gadgets+smartphones+chips+software&hl=en-US&gl=US&ceid=US:en"),
    ],

    "WORLD": [
        ("direct", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("direct", "Reuters World", "https://feeds.reuters.com/reuters/worldNews"),
        ("discovery", "Google News World", "https://news.google.com/rss/search?q=world+breaking+news+major+international&hl=en-US&gl=US&ceid=US:en"),
    ],

    "INDIA": [
        ("direct", "The Hindu", "https://www.thehindu.com/news/national/feeder/default.rss"),
        ("direct", "The Hindu Business", "https://www.thehindu.com/business/feeder/default.rss"),
        ("direct", "Indian Express", "https://indianexpress.com/section/india/feed/"),
        ("direct", "Indian Express Business", "https://indianexpress.com/section/business/feed/"),
        ("discovery", "Google News India", "https://news.google.com/rss/search?q=India+major+breaking+news&hl=en-IN&gl=IN&ceid=IN:en"),
    ],
}


# Direct-publish source domains.
# Discovery feeds may help identify stories, but their URLs are never
# considered sufficient by themselves for publication.
TRUSTED_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "thehindu.com",
    "indianexpress.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "arstechnica.com",

    # Primary / official sources
    "openai.com",
    "blog.google",
    "deepmind.google",
    "google.com",
    "microsoft.com",
    "apple.com",
    "nvidia.com",
    "samsung.com",
    "meta.com",
    "amazon.com",
    "aboutamazon.com",
    "intel.com",
    "qualcomm.com",
    "github.blog",
    "isro.gov.in",
    "pib.gov.in",
    "mea.gov.in",
    "rbi.org.in",
    "sebi.gov.in",
}


# ------------------------------------------------------------
# 2. HELPERS
# ------------------------------------------------------------

NAMESPACES = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}


def clean_text(value):
    if not value:
        return ""
    value = html.unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def domain_from_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower().split(":")[0].removeprefix("www.")
    except Exception:
        return ""


def is_trusted_domain(url):
    domain = domain_from_url(url)
    return any(domain == d or domain.endswith("." + d) for d in TRUSTED_DOMAINS)


def extract_image_url(item):
    for tag in [".//media:content", ".//media:thumbnail"]:
        elem = item.find(tag, NAMESPACES)
        if elem is not None:
            url = elem.attrib.get("url", "")
            if url.startswith("http") and not url.lower().endswith(".svg"):
                return url

    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.attrib.get("url", "")
        if url.startswith("http"):
            return url

    for desc_tag in ["description", "{http://purl.org/rss/1.0/modules/content/}encoded"]:
        elem = item.find(desc_tag)
        if elem is not None and elem.text:
            match = re.search(
                r'<img[^>]+src=["\'](https?://[^"\']+)["\']',
                elem.text,
                flags=re.I,
            )
            if match and not match.group(1).lower().endswith(".svg"):
                return match.group(1)

    return ""


def parse_pub_date(item):
    for tag in ["pubDate", "published", "updated", "{http://purl.org/dc/elements/1.1/}date"]:
        elem = item.find(tag, NAMESPACES)
        if elem is not None and elem.text:
            return elem.text.strip()
    return ""


def parse_feed(feed_url):
    """
    Return normalized RSS candidates.
    Google News candidates are marked discovery-only.
    """
    candidates = []

    ctx = ssl.create_default_context()
    request = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36 "
                "TechMatrixPulse/3.0"
            )
        },
    )

    with urllib.request.urlopen(request, context=ctx, timeout=15) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", NAMESPACES)

    for item in items[:12]:
        title_elem = item.find("title")
        link_elem = item.find("link")

        title = clean_text(title_elem.text if title_elem is not None else "")

        link = ""
        if link_elem is not None:
            link = (link_elem.text or "").strip()
            if not link:
                link = link_elem.attrib.get("href", "").strip()

        if not title or not link:
            continue

        description = ""
        for tag in [
            "description",
            "{http://purl.org/rss/1.0/modules/content/}encoded",
            "summary",
        ]:
            elem = item.find(tag)
            if elem is not None and elem.text:
                description = clean_text(elem.text)
                if description:
                    break

        source_name = ""
        source_elem = item.find("source")
        if source_elem is not None and source_elem.text:
            source_name = clean_text(source_elem.text)

        candidates.append(
            {
                "title": title,
                "url": link,
                "description": description[:1200],
                "rss_image": extract_image_url(item),
                "source_name": source_name,
                "source_domain": domain_from_url(link),
                "published": parse_pub_date(item),
            }
        )

    return candidates


def normalize_title(title):
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    title = re.sub(
        r"\b(breaking|update|latest|exclusive|live|watch|report)\b",
        " ",
        title,
    )
    title = re.sub(r"\s+", " ", title).strip()
    return title


def title_similarity(a, b):
    """
    Lightweight word-overlap similarity. Used only to reduce duplicates,
    not as a factual verification mechanism.
    """
    a_words = set(normalize_title(a).split())
    b_words = set(normalize_title(b).split())

    if not a_words or not b_words:
        return 0.0

    return len(a_words & b_words) / max(1, min(len(a_words), len(b_words)))


def deduplicate_candidates(candidates):
    unique = []

    for candidate in candidates:
        duplicate = False

        for existing in unique:
            if candidate["url"] == existing["url"]:
                duplicate = True
                break

            if title_similarity(candidate["title"], existing["title"]) >= 0.78:
                # Keep the direct/trusted source when the same story appears
                # in a discovery feed.
                if (
                    is_trusted_domain(candidate["url"])
                    and not is_trusted_domain(existing["url"])
                ):
                    existing.update(candidate)
                duplicate = True
                break

        if not duplicate:
            unique.append(candidate)

    return unique


def fetch_all_news():
    all_candidates = []

    for category, feeds in CATEGORY_FEEDS.items():
        for feed_type, feed_name, feed_url in feeds:
            try:
                candidates = parse_feed(feed_url)

                for candidate in candidates:
                    candidate["category_hint"] = category
                    candidate["feed_type"] = feed_type
                    candidate["feed_name"] = feed_name

                    # Discovery feeds are NEVER publishable by themselves.
                    candidate["publishable_source"] = (
                        feed_type == "direct"
                        and is_trusted_domain(candidate["url"])
                    )

                    all_candidates.append(candidate)

            except Exception as exc:
                print(f"[RSS WARNING] {feed_name}: {exc}")

    return deduplicate_candidates(all_candidates)


# ------------------------------------------------------------
# 3. DATABASE
# ------------------------------------------------------------

DB_FILE = "news_data.json"
NOW_EPOCH = int(time.time())
RETENTION_PERIOD = 48 * 3600

existing_news = []

if os.path.exists(DB_FILE):
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            existing_news = json.load(f)
    except Exception as exc:
        print(f"[DB WARNING] Could not load database: {exc}")

active_news = [
    item
    for item in existing_news
    if (NOW_EPOCH - item.get("timestamp_epoch", 0)) < RETENTION_PERIOD
]

existing_urls = {
    item.get("url")
    for item in active_news
    if item.get("url")
}


# ------------------------------------------------------------
# 4. GEMINI EDITORIAL / SIGNIFICANCE FILTER
# ------------------------------------------------------------

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY secret is missing or empty!")

client = genai.Client(api_key=api_key)
raw_candidates = fetch_all_news()

# Keep the model input compact enough for repeated GitHub Actions runs.
model_candidates = raw_candidates[:120]

system_prompt = r"""
You are the senior editor of a conservative, factual news publication.

Your job is to select ONLY genuinely major stories from the supplied RSS candidates.

THE FOUR TOP-LEVEL CATEGORIES

1. AI
   Major artificial-intelligence developments worldwide:
   model releases, major AI companies, major research, AI regulation,
   major AI safety developments, major AI products, major acquisitions,
   or other consequential AI events.

2. TECH
   Major technology developments:
   smartphones, gadgets, computers, chips, software, cybersecurity,
   internet platforms, space technology, major product launches,
   major technology companies, important technical failures,
   and other consequential technology events.

3. INDIA
   Major events happening in or substantially affecting India, regardless
   of subject. This can include:
   politics, government, courts, economy, business, defense, security,
   disasters, major accidents, science, ISRO, infrastructure, elections,
   major sports events, major companies, technology, international relations,
   and other nationally significant events.

4. WORLD
   Major events anywhere in the world, regardless of subject.
   This can include:
   wars/conflicts, elections, geopolitics, governments, global economy,
   disasters, major accidents, security, science, space, major business events,
   major court decisions, major international agreements, and major technology.

WHAT COUNTS AS "BIG NEWS"

A story should normally be published only if it has substantial national,
international, economic, political, technological, security, scientific,
humanitarian, or public impact.

DO NOT publish merely because:
- it is trending
- it has a dramatic headline
- it is a celebrity story
- it is a minor product update
- it is a local incident with no broader significance
- it is speculation or a rumor
- it is a social-media post
- it is clickbait

A useful test:
"If a person checked this site's India/World homepage once or twice a day,
would this be one of the important events they should know about?"

If NO, exclude it.

FACTUAL SAFETY RULES

- Never invent facts.
- Never invent quotations.
- Never invent statistics.
- Never invent dates.
- Never invent a second source.
- Never turn an allegation into a fact.
- Never turn a rumor into a confirmed event.
- Never infer details that are not supported by the supplied reporting.
- Google News discovery candidates are NOT publishable by themselves.
- Prefer direct reporting from reputable publishers and primary sources.
- A single reputable report can be selected if it describes a clearly reported
  major event, but do not call it "corroborated" unless the supplied candidates
  contain genuinely independent reporting on the same event.
- If facts are unclear or contradictory, EXCLUDE the story.

CATEGORY RULES

Every published item must be exactly one of:
AI | TECH | INDIA | WORLD

A major India story belongs in INDIA even if it is not technology.
A major world story belongs in WORLD even if it is not technology.
AI remains a specialized category.
TECH remains a specialized technology category.

OUTPUT

Return ONLY a raw JSON array. No markdown. No explanation.

Return at most 12 stories total.

Schema:
[
  {
    "title": "Accurate factual headline",
    "url": "Original source URL from the supplied candidate",
    "category": "AI | TECH | INDIA | WORLD",
    "importance": "CRITICAL | MAJOR",
    "summary": "2-3 factual sentences based only on supplied reporting.",
    "point1": "One verified key fact.",
    "point2": "Why this matters, only if supported by the reporting.",
    "point3": "Confirmed next step/timeline, or 'No confirmed timeline reported.'",
    "verification": "DIRECT_REPUTABLE_REPORT | PRIMARY_SOURCE | MULTIPLE_REPUTABLE_REPORTS",
    "source_name": "Publisher name",
    "rss_image": "Exact RSS image URL supplied for the selected story, or empty string"
  }
]
"""


def call_gemini_with_retry(model, contents, config, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            return response.text or "[]"

        except Exception as exc:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(
                    f"[AI WARNING] Attempt {attempt + 1} failed: {exc}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                raise


candidate_text = json.dumps(model_candidates, ensure_ascii=False)

try:
    raw_text = call_gemini_with_retry(
        "gemini-3.6-flash",
        f"RSS candidates:\n{candidate_text}",
        types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
        ),
    )
except Exception as exc:
    print(f"[AI WARNING] Primary model failed: {exc}. Falling back.")

    try:
        raw_text = call_gemini_with_retry(
            "gemini-3.5-flash",
            f"RSS candidates:\n{candidate_text}",
            types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.1,
            ),
        )
    except Exception as fallback_exc:
        print(f"[AI ERROR] All models failed: {fallback_exc}")
        raw_text = "[]"


def parse_json_array(text):
    text = (text or "").strip()
    text = re.sub(r"^```json\s*", "", text, flags=re.I)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[AI WARNING] JSON parsing failed: {exc}")
        return []


new_items = parse_json_array(raw_text)


# ------------------------------------------------------------
# 5. HARD SAFETY FILTER AFTER AI
# ------------------------------------------------------------

candidate_by_url = {
    candidate["url"]: candidate
    for candidate in raw_candidates
    if candidate.get("url")
}

allowed_categories = {"AI", "TECH", "INDIA", "WORLD"}
allowed_importance = {"CRITICAL", "MAJOR"}

safe_items = []

for item in new_items:
    if not isinstance(item, dict):
        continue

    url = str(item.get("url", "")).strip()
    category = str(item.get("category", "")).upper().strip()
    importance = str(item.get("importance", "")).upper().strip()

    if not url or url not in candidate_by_url:
        continue

    if category not in allowed_categories:
        continue

    if importance not in allowed_importance:
        continue

    original_candidate = candidate_by_url[url]

    # Absolute rule: discovery-only Google News URLs cannot be published.
    if not original_candidate.get("publishable_source"):
        continue

    # Final domain check.
    if not is_trusted_domain(url):
        continue

    # Never let the model replace the source URL with a fabricated one.
    item["url"] = url

    # Prefer source metadata actually found in RSS.
    item["source_name"] = (
        original_candidate.get("source_name")
        or original_candidate.get("feed_name")
        or item.get("source_name")
        or domain_from_url(url)
    )

    # Image must come from the original RSS candidate, not from AI invention.
    item["rss_image"] = original_candidate.get("rss_image", "")

    item["category"] = category
    item["importance"] = importance

    # The model may only use one of the allowed labels.
    allowed_verification = {
        "DIRECT_REPUTABLE_REPORT",
        "PRIMARY_SOURCE",
        "MULTIPLE_REPUTABLE_REPORTS",
    }

    verification = str(item.get("verification", "")).upper().strip()
    if verification not in allowed_verification:
        verification = "DIRECT_REPUTABLE_REPORT"

    item["verification"] = verification

    safe_items.append(item)


# ------------------------------------------------------------
# 6. IMAGES
# ------------------------------------------------------------

CATEGORY_IMAGES = {
    "AI": [
        "https://images.unsplash.com/photo-1677442136019-21780efad99a?auto=format&fit=crop&w=1000&q=85",
        "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?auto=format&fit=crop&w=1000&q=85",
    ],
    "TECH": [
        "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=1000&q=85",
        "https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?auto=format&fit=crop&w=1000&q=85",
    ],
    "WORLD": [
        "https://images.unsplash.com/photo-1521295121783-8a321d551ad2?auto=format&fit=crop&w=1000&q=85",
        "https://images.unsplash.com/photo-1529107386315-e1a2ed48a620?auto=format&fit=crop&w=1000&q=85",
    ],
    "INDIA": [
        "https://images.unsplash.com/photo-1524492412937-b28074a5d7da?auto=format&fit=crop&w=1000&q=85",
        "https://images.unsplash.com/photo-1532375810709-75b1da00537c?auto=format&fit=crop&w=1000&q=85",
    ],
}

DEFAULT_IMAGE = (
    "https://images.unsplash.com/photo-1451187580459-43490279c0fa"
    "?auto=format&fit=crop&w=1000&q=85"
)


def choose_image(item):
    rss_image = str(item.get("rss_image", "")).strip()

    if rss_image.startswith("http") and not rss_image.lower().endswith(".svg"):
        return rss_image

    category = item.get("category", "TECH").upper()
    images = CATEGORY_IMAGES.get(category, CATEGORY_IMAGES["TECH"])

    # Deterministic selection prevents the same story from changing image
    # on every GitHub Actions run.
    index = abs(hash(item.get("url", ""))) % len(images)
    return images[index]


# ------------------------------------------------------------
# 7. MERGE INTO 48-HOUR DATABASE
# ------------------------------------------------------------

now_utc = datetime.now(timezone.utc)
timestamp_str = now_utc.strftime("%b %d • %I:%M %p UTC")

added_count = 0

for item in safe_items:
    url = item["url"]

    if not url or url in existing_urls:
        continue

    item["image_url"] = choose_image(item)
    item["timestamp_epoch"] = NOW_EPOCH
    item["timestamp_display"] = timestamp_str

    active_news.insert(0, item)
    existing_urls.add(url)
    added_count += 1


# Keep newest stories first and avoid an unlimited JSON file.
active_news.sort(
    key=lambda x: x.get("timestamp_epoch", 0),
    reverse=True,
)

# Maximum 60 active stories.
active_news = active_news[:60]

with open(DB_FILE, "w", encoding="utf-8") as f:
    json.dump(active_news, f, indent=2, ensure_ascii=False)


# ------------------------------------------------------------
# 8. HTML CARD GENERATION
# ------------------------------------------------------------

def safe_html(value):
    return html.escape(str(value or ""))


cards_html = ""

for item in active_news:
    category = safe_html(item.get("category", "TECH").upper())
    importance = safe_html(item.get("importance", "MAJOR"))
    verification = safe_html(item.get("verification", "DIRECT_REPUTABLE_REPORT"))
    source_name = safe_html(item.get("source_name", "Trusted source"))
    title = safe_html(item.get("title", "Untitled story"))
    summary = safe_html(item.get("summary", ""))
    point1 = safe_html(item.get("point1", ""))
    point2 = safe_html(item.get("point2", ""))
    point3 = safe_html(item.get("point3", ""))
    image_url = safe_html(item.get("image_url", DEFAULT_IMAGE))
    url = safe_html(item.get("url", "#"))
    timestamp = safe_html(item.get("timestamp_display", ""))

    cards_html += f"""
<article class="news-card"
         data-category="{category}"
         data-importance="{importance}">
    <div class="card-image">
        <img
            src="{image_url}"
            alt="{title}"
            loading="lazy"
            decoding="async"
            referrerpolicy="no-referrer"
            onerror="this.onerror=null;this.src='{DEFAULT_IMAGE}';"
        >
        <div class="image-overlay">
            <span>{category}</span>
            <span>{importance}</span>
        </div>
    </div>

    <div class="card-content">
        <div class="meta-bar">
            <span class="category">{category}</span>
            <span class="importance">{importance}</span>
            <span class="timestamp">⏰ {timestamp}</span>
        </div>

        <div class="source-line">
            <span class="source-check">✓</span>
            <span>{verification}</span>
            <span class="source-separator">•</span>
            <span>{source_name}</span>
        </div>

        <h2 class="title">
            <a href="{url}" target="_blank" rel="noopener noreferrer">
                {title}
            </a>
        </h2>

        <p class="summary">{summary}</p>

        <ul class="key-points">
            <li><strong>FACT</strong>{point1}</li>
            <li><strong>IMPACT</strong>{point2}</li>
            <li><strong>NEXT</strong>{point3}</li>
        </ul>

        <a href="{url}"
           target="_blank"
           rel="noopener noreferrer"
           class="read-more">
            READ ORIGINAL REPORT <span>↗</span>
        </a>
    </div>
</article>
"""


# ------------------------------------------------------------
# 9. HTML PAGE
# ------------------------------------------------------------

html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <meta
        name="description"
        content="Tech Matrix Pulse — major AI, technology, India and world news."
    >

    <title>TECH MATRIX PULSE — Major News</title>

    <script
        src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
        defer>
    </script>

    <style>
        :root {{
            --bg-color: #050814;
            --card-bg: rgba(15, 23, 42, 0.78);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --cyan: #00f3ff;
            --purple: #a855f7;
            --green: #86efac;
            --border: rgba(56, 189, 248, 0.2);
        }}

        * {{
            box-sizing: border-box;
        }}

        html {{
            scroll-behavior: smooth;
        }}

        body {{
            margin: 0 auto;
            max-width: 1080px;
            padding: 30px 20px 70px;
            background: var(--bg-color);
            color: var(--text-main);
            font-family:
                -apple-system, BlinkMacSystemFont, "Segoe UI",
                Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            overflow-x: hidden;
        }}

        a {{
            color: inherit;
        }}

        #bg-canvas {{
            position: fixed;
            inset: 0;
            width: 100vw;
            height: 100vh;
            z-index: -10;
            pointer-events: none;
        }}

        #scan-overlay {{
            position: fixed;
            inset: 0;
            z-index: 50;
            pointer-events: none;
            background:
                repeating-linear-gradient(
                    to bottom,
                    rgba(255,255,255,.018) 0,
                    rgba(255,255,255,.018) 1px,
                    transparent 1px,
                    transparent 4px
                );
            mix-blend-mode: overlay;
        }}

        #scroll-progress {{
            position: fixed;
            top: 0;
            left: 0;
            height: 3px;
            width: 0;
            z-index: 1001;
            background: linear-gradient(90deg, var(--cyan), var(--purple));
            box-shadow: 0 0 10px rgba(0,243,255,.7);
        }}

        #cursor-glow {{
            position: fixed;
            width: 30px;
            height: 30px;
            left: 0;
            top: 0;
            z-index: 998;
            pointer-events: none;
            opacity: 0;
            border-radius: 50%;
            background:
                radial-gradient(
                    circle,
                    rgba(0,243,255,.48),
                    rgba(168,85,247,.18) 55%,
                    transparent 75%
                );
            transform: translate(-50%, -50%);
            mix-blend-mode: screen;
            transition: opacity .25s ease;
        }}

        #cursor-glow.active {{
            opacity: 1;
        }}

        header {{
            position: relative;
            margin: 0 0 28px;
            padding: 34px 24px;
            text-align: center;
            border: 1px solid var(--border);
            border-radius: 24px;
            background: rgba(15, 23, 42, .62);
            backdrop-filter: blur(18px);
            box-shadow: 0 20px 60px rgba(0,0,0,.35);
            overflow: hidden;
        }}

        header::before {{
            content: "";
            position: absolute;
            width: 240px;
            height: 240px;
            left: 50%;
            top: -190px;
            transform: translateX(-50%);
            border-radius: 50%;
            background: rgba(0,243,255,.12);
            filter: blur(40px);
            pointer-events: none;
        }}

        h1 {{
            position: relative;
            margin: 0 0 7px;
            font-size: clamp(2rem, 7vw, 3.4rem);
            line-height: 1;
            letter-spacing: -2px;
            background: linear-gradient(135deg, #00f3ff, #a855f7);
            -webkit-background-clip: text;
            background-clip: text;
            -webkit-text-fill-color: transparent;
        }}

        .subtitle {{
            margin: 0;
            color: var(--text-muted);
            font: .8rem monospace;
            letter-spacing: 1px;
        }}

        .status-line {{
            display: flex;
            justify-content: center;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            margin: 16px 0 0;
            color: var(--text-muted);
            font: .72rem monospace;
            letter-spacing: .5px;
        }}

        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--cyan);
            box-shadow: 0 0 10px var(--cyan);
            animation: status-pulse 1.6s ease-in-out infinite;
        }}

        @keyframes status-pulse {{
            0%,100% {{ opacity: 1; transform: scale(1); }}
            50% {{ opacity: .35; transform: scale(.75); }}
        }}

        /* ====================================================
           ANIMATED CATEGORY MENU
           ==================================================== */

        .category-nav {{
            position: sticky;
            top: 12px;
            z-index: 100;
            display: flex;
            width: fit-content;
            max-width: 100%;
            margin: 0 auto 16px;
            padding: 6px;
            gap: 4px;
            overflow-x: auto;
            scrollbar-width: none;
            border: 1px solid rgba(56,189,248,.25);
            border-radius: 999px;
            background: rgba(5,8,20,.78);
            backdrop-filter: blur(22px);
            box-shadow:
                0 15px 40px rgba(0,0,0,.3),
                inset 0 0 20px rgba(0,243,255,.025);
        }}

        .category-nav::-webkit-scrollbar {{
            display: none;
        }}

        .category-tab {{
            position: relative;
            z-index: 2;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            min-height: 40px;
            border: 0;
            border-radius: 999px;
            padding: 9px 15px;
            background: transparent;
            color: var(--text-muted);
            font: 700 .7rem monospace;
            letter-spacing: .7px;
            white-space: nowrap;
            cursor: pointer;
            transition:
                color .25s ease,
                transform .25s ease;
        }}

        .category-tab:hover {{
            color: white;
            transform: translateY(-1px);
        }}

        .category-tab.active {{
            color: #050814;
        }}

        .tab-icon {{
            display: inline-block;
            transition: transform .4s cubic-bezier(.2,.8,.2,1);
        }}

        .category-tab.active .tab-icon {{
            transform: rotate(180deg) scale(1.15);
        }}

        .tab-count {{
            display: inline-grid;
            place-items: center;
            min-width: 18px;
            height: 18px;
            padding: 0 5px;
            border-radius: 99px;
            background: rgba(255,255,255,.12);
            font-size: .58rem;
        }}

        .category-tab.active .tab-count {{
            background: rgba(5,8,20,.16);
        }}

        .category-indicator {{
            position: absolute;
            left: 6px;
            top: 6px;
            width: 0;
            height: calc(100% - 12px);
            border-radius: 999px;
            background: linear-gradient(135deg, var(--cyan), var(--purple));
            box-shadow: 0 0 22px rgba(0,243,255,.38);
            transition:
                left .45s cubic-bezier(.2,.8,.2,1),
                width .45s cubic-bezier(.2,.8,.2,1);
        }}

        .verification-banner {{
            display: flex;
            align-items: flex-start;
            gap: 10px;
            margin: 0 0 28px;
            padding: 12px 15px;
            border: 1px solid rgba(134,239,172,.2);
            border-radius: 14px;
            background: rgba(15,23,42,.55);
            color: var(--text-muted);
            font-size: .74rem;
        }}

        .verification-icon {{
            display: grid;
            place-items: center;
            flex: 0 0 22px;
            width: 22px;
            height: 22px;
            border: 1px solid var(--green);
            border-radius: 50%;
            color: var(--green);
            font-weight: 900;
        }}

        .verification-banner strong {{
            color: var(--green);
            letter-spacing: .5px;
        }}

        /* ====================================================
           NEWS CARDS
           ==================================================== */

        .news-card {{
            position: relative;
            display: flex;
            flex-direction: column;
            margin-bottom: 28px;
            overflow: hidden;
            border: 1px solid var(--border);
            border-radius: 20px;
            background: var(--card-bg);
            backdrop-filter: blur(17px);
            box-shadow: 0 14px 45px rgba(0,0,0,.38);

            opacity: 0;
            visibility: hidden;
            transform: translateY(28px);

            transition:
                opacity .6s ease,
                transform .4s ease,
                border-color .3s ease,
                box-shadow .3s ease;
        }}

        .news-card.in-view {{
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
        }}

        .news-card.is-hidden {{
            display: none;
        }}

        .news-card:hover {{
            border-color: rgba(0,243,255,.7);
            box-shadow:
                0 18px 55px rgba(0,0,0,.45),
                0 0 30px rgba(0,243,255,.1);
        }}

        .card-image {{
            position: relative;
            width: 100%;
            height: 220px;
            overflow: hidden;
            background: #0b1120;
        }}

        .card-image img {{
            display: block;
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform .65s cubic-bezier(.2,.8,.2,1);
        }}

        .news-card:hover .card-image img {{
            transform: scale(1.045);
        }}

        .image-overlay {{
            position: absolute;
            left: 12px;
            right: 12px;
            bottom: 12px;
            display: flex;
            justify-content: space-between;
            gap: 8px;
        }}

        .image-overlay span {{
            padding: 5px 9px;
            border: 1px solid rgba(255,255,255,.2);
            border-radius: 999px;
            background: rgba(5,8,20,.68);
            backdrop-filter: blur(8px);
            color: white;
            font: 700 .62rem monospace;
            letter-spacing: .5px;
        }}

        .card-content {{
            flex: 1;
            padding: 24px;
        }}

        .meta-bar {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 9px;
        }}

        .category,
        .importance {{
            display: inline-block;
            padding: 4px 9px;
            border-radius: 999px;
            font: 700 .62rem monospace;
            letter-spacing: .7px;
        }}

        .category {{
            border: 1px solid var(--cyan);
            background: rgba(0,243,255,.08);
            color: var(--cyan);
        }}

        .importance {{
            border: 1px solid rgba(168,85,247,.55);
            background: rgba(168,85,247,.08);
            color: #d8b4fe;
        }}

        .timestamp {{
            color: var(--text-muted);
            font: .68rem monospace;
        }}

        .source-line {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 6px;
            margin-bottom: 13px;
            color: var(--text-muted);
            font: .64rem monospace;
        }}

        .source-check {{
            display: inline-grid;
            place-items: center;
            width: 17px;
            height: 17px;
            border: 1px solid rgba(134,239,172,.5);
            border-radius: 50%;
            color: var(--green);
            font-weight: 900;
        }}

        .source-separator {{
            opacity: .35;
        }}

        .title {{
            margin: 0 0 12px;
            font-size: clamp(1.2rem, 3vw, 1.65rem);
            line-height: 1.3;
            letter-spacing: -.4px;
        }}

        .title a {{
            text-decoration: none;
            transition: color .2s ease;
        }}

        .title a:hover {{
            color: var(--cyan);
        }}

        .summary {{
            margin: 0 0 18px;
            color: #cbd5e1;
            font-size: .94rem;
        }}

        .key-points {{
            margin: 0 0 20px;
            padding: 13px 17px 13px 28px;
            border-left: 3px solid var(--purple);
            border-radius: 0 12px 12px 0;
            background: rgba(5,8,20,.65);
            color: #94a3b8;
            font-size: .83rem;
        }}

        .key-points li {{
            margin-bottom: 7px;
        }}

        .key-points li:last-child {{
            margin-bottom: 0;
        }}

        .key-points strong {{
            display: inline-block;
            min-width: 57px;
            margin-right: 5px;
            color: var(--cyan);
            font: 700 .64rem monospace;
        }}

        .read-more {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            color: var(--cyan);
            text-decoration: none;
            font: 700 .72rem monospace;
            letter-spacing: .7px;
            transition: gap .2s ease, color .2s ease;
        }}

        .read-more:hover {{
            gap: 10px;
            color: white;
        }}

        @media (min-width: 720px) {{
            .news-card {{
                flex-direction: row;
            }}

            .card-image {{
                width: 36%;
                min-height: 260px;
                height: auto;
            }}

            .card-content {{
                width: 64%;
            }}
        }}

        @media (max-width: 600px) {{
            body {{
                padding: 18px 12px 50px;
            }}

            header {{
                padding: 28px 15px;
                border-radius: 19px;
            }}

            .category-nav {{
                width: 100%;
                justify-content: flex-start;
            }}

            .category-tab {{
                padding-left: 12px;
                padding-right: 12px;
            }}

            .verification-banner {{
                font-size: .7rem;
            }}

            .card-content {{
                padding: 19px;
            }}
        }}

        @media (prefers-reduced-motion: reduce) {{
            html {{
                scroll-behavior: auto;
            }}

            .news-card {{
                opacity: 1;
                visibility: visible;
                transform: none;
                transition: border-color .2s ease;
            }}

            .status-dot,
            .category-tab .tab-icon {{
                animation: none;
                transition: none;
            }}

            #cursor-glow,
            #scan-overlay {{
                display: none;
            }}
        }}
    </style>
</head>

<body>
    <canvas id="bg-canvas" aria-hidden="true"></canvas>
    <div id="scan-overlay" aria-hidden="true"></div>
    <div id="scroll-progress" aria-hidden="true"></div>
    <div id="cursor-glow" aria-hidden="true"></div>

    <header>
        <h1>⚡ TECH MATRIX PULSE</h1>
        <p class="subtitle">
            // MAJOR NEWS • AI • TECHNOLOGY • INDIA • WORLD
        </p>

        <p class="status-line">
            <span class="status-dot" aria-hidden="true"></span>
            LIVE FEED //
            <span id="signal-count">0</span> ACTIVE STORIES //
            <span id="live-clock">00:00:00</span> UTC
        </p>
    </header>

    <nav class="category-nav" aria-label="News categories">
        <button class="category-tab active" data-filter="ALL" type="button">
            <span class="tab-icon">◈</span>
            <span>ALL</span>
            <span class="tab-count">{len(active_news)}</span>
        </button>

        <button class="category-tab" data-filter="AI" type="button">
            <span class="tab-icon">✦</span>
            <span>AI</span>
        </button>

        <button class="category-tab" data-filter="TECH" type="button">
            <span class="tab-icon">⌘</span>
            <span>TECHNOLOGY</span>
        </button>

        <button class="category-tab" data-filter="WORLD" type="button">
            <span class="tab-icon">◎</span>
            <span>WORLD</span>
        </button>

        <button class="category-tab" data-filter="INDIA" type="button">
            <span class="tab-icon">◇</span>
            <span>INDIA</span>
        </button>

        <span class="category-indicator" aria-hidden="true"></span>
    </nav>

    <div class="verification-banner">
        <span class="verification-icon">✓</span>
        <span>
            <strong>STRICT SOURCE POLICY</strong> —
            Major stories are restricted to reputable or primary sources.
            Discovery feeds are not accepted as publishable proof.
            Unverified or minor stories are filtered out.
        </span>
    </div>

    <main id="news-feed">
        {cards_html}
    </main>

    <script>
    // ========================================================
    // ANIMATED CATEGORY FILTER
    // ========================================================
    (function () {{
        const tabs = Array.from(document.querySelectorAll(".category-tab"));
        const indicator = document.querySelector(".category-indicator");
        const cards = Array.from(document.querySelectorAll(".news-card"));
        const nav = document.querySelector(".category-nav");

        function moveIndicator(tab) {{
            if (!tab || !indicator || !nav) return;

            const navRect = nav.getBoundingClientRect();
            const tabRect = tab.getBoundingClientRect();

            indicator.style.left =
                (tabRect.left - navRect.left) + "px";

            indicator.style.width =
                tabRect.width + "px";
        }}

        function updateCategoryCounts() {{
            tabs.forEach(function (tab) {{
                const filter = tab.dataset.filter;
                const countEl = tab.querySelector(".tab-count");

                if (!countEl || filter === "ALL") return;

                const count = cards.filter(function (card) {{
                    return card.dataset.category === filter;
                }}).length;

                countEl.textContent = count;
            }});
        }}

        function filterCards(filter) {{
            cards.forEach(function (card) {{
                const visible =
                    filter === "ALL" ||
                    card.dataset.category === filter;

                card.classList.toggle("is-hidden", !visible);

                if (visible) {{
                    card.classList.add("in-view");
                }}
            }});
        }}

        tabs.forEach(function (tab) {{
            tab.addEventListener("click", function () {{
                tabs.forEach(function (item) {{
                    item.classList.remove("active");
                }});

                tab.classList.add("active");
                filterCards(tab.dataset.filter);
                moveIndicator(tab);
            }});
        }});

        updateCategoryCounts();

        window.addEventListener("resize", function () {{
            const active = document.querySelector(".category-tab.active");
            moveIndicator(active);
        }});

        requestAnimationFrame(function () {{
            moveIndicator(document.querySelector(".category-tab.active"));
        }});
    }})();


    // ========================================================
    // SCROLL REVEAL
    // ========================================================
    (function () {{
        const cards = Array.from(document.querySelectorAll(".news-card"));
        const reduced =
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        if (reduced || !("IntersectionObserver" in window)) {{
            cards.forEach(function (card) {{
                card.classList.add("in-view");
            }});
            return;
        }}

        const observer = new IntersectionObserver(
            function (entries) {{
                entries.forEach(function (entry) {{
                    if (entry.isIntersecting) {{
                        entry.target.classList.add("in-view");
                        observer.unobserve(entry.target);
                    }}
                }});
            }},
            {{ threshold: 0.12 }}
        );

        cards.forEach(function (card) {{
            observer.observe(card);
        }});
    }})();


    // ========================================================
    // SCROLL PROGRESS
    // ========================================================
    (function () {{
        const progress = document.getElementById("scroll-progress");

        function update() {{
            const doc = document.documentElement;
            const scrollTop = doc.scrollTop || document.body.scrollTop;
            const max =
                (doc.scrollHeight - doc.clientHeight) || 1;

            progress.style.width =
                Math.min(100, Math.max(0, scrollTop / max * 100)) + "%";
        }}

        window.addEventListener("scroll", update, {{ passive: true }});
        update();
    }})();


    // ========================================================
    // LIVE CLOCK / SIGNAL COUNT
    // ========================================================
    (function () {{
        const clock = document.getElementById("live-clock");
        const count = document.getElementById("signal-count");
        const target = {len(active_news)};

        function pad(n) {{
            return String(n).padStart(2, "0");
        }}

        function tick() {{
            const now = new Date();

            clock.textContent =
                pad(now.getUTCHours()) + ":" +
                pad(now.getUTCMinutes()) + ":" +
                pad(now.getUTCSeconds());
        }}

        count.textContent = target;
        tick();
        setInterval(tick, 1000);
    }})();


    // ========================================================
    // CURSOR GLOW
    // ========================================================
    (function () {{
        const glow = document.getElementById("cursor-glow");
        const finePointer =
            window.matchMedia("(hover: hover) and (pointer: fine)").matches;
        const reduced =
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        if (!glow || !finePointer || reduced) return;

        let tx = innerWidth / 2;
        let ty = innerHeight / 2;
        let x = tx;
        let y = ty;

        window.addEventListener("pointermove", function (event) {{
            if (event.pointerType && event.pointerType !== "mouse") return;

            tx = event.clientX;
            ty = event.clientY;
            glow.classList.add("active");
        }});

        window.addEventListener("pointerleave", function () {{
            glow.classList.remove("active");
        }});

        function animate() {{
            x += (tx - x) * .16;
            y += (ty - y) * .16;

            glow.style.transform =
                "translate(" + x.toFixed(1) + "px," +
                y.toFixed(1) + "px) translate(-50%,-50%)";

            requestAnimationFrame(animate);
        }}

        animate();
    }})();


    // ========================================================
    // THREE.JS AMBIENT NEURAL BACKGROUND
    // ========================================================
    (function () {{
        const canvas = document.getElementById("bg-canvas");
        const reduced =
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        if (!canvas || typeof THREE === "undefined") return;

        const scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x050814, .05);

        const camera = new THREE.PerspectiveCamera(
            60,
            window.innerWidth / window.innerHeight,
            .1,
            1000
        );

        camera.position.z = 9;

        const renderer = new THREE.WebGLRenderer({{
            canvas: canvas,
            alpha: true,
            antialias: true
        }});

        renderer.setSize(
            window.innerWidth,
            window.innerHeight
        );

        renderer.setPixelRatio(
            Math.min(window.devicePixelRatio || 1, 1.75)
        );

        function makeNodeTexture() {{
            const size = 128;
            const c = document.createElement("canvas");

            c.width = size;
            c.height = size;

            const ctx = c.getContext("2d");

            const gradient = ctx.createRadialGradient(
                size / 2,
                size / 2,
                0,
                size / 2,
                size / 2,
                size / 2
            );

            gradient.addColorStop(0, "rgba(255,255,255,1)");
            gradient.addColorStop(.35, "rgba(255,255,255,.55)");
            gradient.addColorStop(1, "rgba(255,255,255,0)");

            ctx.fillStyle = gradient;
            ctx.fillRect(0, 0, size, size);

            return new THREE.CanvasTexture(c);
        }}

        const nodeCount =
            window.innerWidth < 680 ? 45 : 80;

        const bounds = 9;
        const linkDistance = 3.1;

        const positions =
            new Float32Array(nodeCount * 3);

        const velocities = [];

        for (let i = 0; i < nodeCount; i++) {{
            const index = i * 3;

            positions[index] =
                (Math.random() - .5) * bounds * 2;

            positions[index + 1] =
                (Math.random() - .5) * bounds * 2;

            positions[index + 2] =
                (Math.random() - .5) * bounds * 2;

            velocities.push({{
                x: (Math.random() - .5) * .0055,
                y: (Math.random() - .5) * .0055,
                z: (Math.random() - .5) * .0055
            }});
        }}

        const nodesGeometry =
            new THREE.BufferGeometry();

        nodesGeometry.setAttribute(
            "position",
            new THREE.BufferAttribute(positions, 3)
        );

        const nodesMaterial =
            new THREE.PointsMaterial({{
                size: .3,
                map: makeNodeTexture(),
                color: 0x5eeaff,
                transparent: true,
                opacity: .8,
                depthWrite: false,
                blending: THREE.AdditiveBlending,
                sizeAttenuation: true
            }});

        const nodes =
            new THREE.Points(
                nodesGeometry,
                nodesMaterial
            );

        const maxPairs =
            (nodeCount * (nodeCount - 1)) / 2;

        const linePositions =
            new Float32Array(maxPairs * 2 * 3);

        const lineColors =
            new Float32Array(maxPairs * 2 * 3);

        const linesGeometry =
            new THREE.BufferGeometry();

        const positionAttribute =
            new THREE.BufferAttribute(
                linePositions,
                3
            );

        const colorAttribute =
            new THREE.BufferAttribute(
                lineColors,
                3
            );

        positionAttribute.setUsage(
            THREE.DynamicDrawUsage
        );

        colorAttribute.setUsage(
            THREE.DynamicDrawUsage
        );

        linesGeometry.setAttribute(
            "position",
            positionAttribute
        );

        linesGeometry.setAttribute(
            "color",
            colorAttribute
        );

        const linesMaterial =
            new THREE.LineBasicMaterial({{
                vertexColors: true,
                transparent: true,
                opacity: .32,
                depthWrite: false,
                blending: THREE.AdditiveBlending
            }});

        const links =
            new THREE.LineSegments(
                linesGeometry,
                linesMaterial
            );

        const group = new THREE.Group();

        group.add(links);
        group.add(nodes);
        scene.add(group);

        const cyan = new THREE.Color(0x00f3ff);
        const purple = new THREE.Color(0xa855f7);
        const tempColor = new THREE.Color();

        function buildLinks() {{
            let vi = 0;
            let ci = 0;
            let segments = 0;

            for (let i = 0; i < nodeCount; i++) {{
                const a = i * 3;

                for (let j = i + 1; j < nodeCount; j++) {{
                    const b = j * 3;

                    const dx =
                        positions[a] - positions[b];

                    const dy =
                        positions[a + 1] -
                        positions[b + 1];

                    const dz =
                        positions[a + 2] -
                        positions[b + 2];

                    const distance =
                        Math.sqrt(
                            dx * dx +
                            dy * dy +
                            dz * dz
                        );

                    if (distance < linkDistance) {{
                        linePositions[vi++] = positions[a];
                        linePositions[vi++] = positions[a + 1];
                        linePositions[vi++] = positions[a + 2];

                        linePositions[vi++] = positions[b];
                        linePositions[vi++] = positions[b + 1];
                        linePositions[vi++] = positions[b + 2];

                        tempColor
                            .copy(cyan)
                            .lerp(
                                purple,
                                distance / linkDistance
                            );

                        for (let n = 0; n < 2; n++) {{
                            lineColors[ci++] = tempColor.r;
                            lineColors[ci++] = tempColor.g;
                            lineColors[ci++] = tempColor.b;
                        }}

                        segments++;
                    }}
                }}
            }}

            positionAttribute.needsUpdate = true;
            colorAttribute.needsUpdate = true;

            linesGeometry.setDrawRange(
                0,
                segments * 2
            );
        }}

        function animate() {{
            requestAnimationFrame(animate);

            for (let i = 0; i < nodeCount; i++) {{
                const index = i * 3;

                positions[index] += velocities[i].x;
                positions[index + 1] += velocities[i].y;
                positions[index + 2] += velocities[i].z;

                if (Math.abs(positions[index]) > bounds) {{
                    velocities[i].x *= -1;
                }}

                if (Math.abs(positions[index + 1]) > bounds) {{
                    velocities[i].y *= -1;
                }}

                if (Math.abs(positions[index + 2]) > bounds) {{
                    velocities[i].z *= -1;
                }}
            }}

            nodesGeometry.attributes.position.needsUpdate = true;

            buildLinks();

            group.rotation.y += .0007;
            group.rotation.x += .00008;

            renderer.render(scene, camera);
        }}

        buildLinks();

        if (!reduced) {{
            animate();
        }} else {{
            renderer.render(scene, camera);
        }}

        window.addEventListener("resize", function () {{
            camera.aspect =
                window.innerWidth / window.innerHeight;

            camera.updateProjectionMatrix();

            renderer.setSize(
                window.innerWidth,
                window.innerHeight
            );
        }});
    }})();
    </script>
</body>
</html>
"""


# ------------------------------------------------------------
# 10. WRITE WEBSITE
# ------------------------------------------------------------

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print("=" * 64)
print("TECH MATRIX PULSE UPDATED SUCCESSFULLY")
print("=" * 64)
print(f"New stories accepted: {added_count}")
print(f"Active stories:       {len(active_news)}")
print("Categories:           AI | TECH | INDIA | WORLD")
print("India/World mode:     MAJOR NEWS ABOUT ANY SUBJECT")
print("Source policy:        TRUSTED / PRIMARY SOURCES")
print("Discovery-only feeds: NEVER PUBLISHED DIRECTLY")
print("=" * 64)
