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

# 1. RSS Feed Parser
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
    for tag in ['.//media:content', './/media:thumbnail']:
        elem = item.find(tag, NAMESPACES)
        if elem is not None:
            url = elem.attrib.get('url')
            if url and url.startswith('http') and not url.endswith('.svg'):
                return url

    enclosure = item.find('enclosure')
    if enclosure is not None:
        url = enclosure.attrib.get('url')
        if url and url.startswith('http'):
            return url

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
RETENTION_PERIOD = 48 * 3600

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
        model="gemini-3.5-flash",
        contents=f"Raw news data:\n{raw_news}",
        config=types.GenerateContentConfig(system_instruction=system_prompt)
    )
    raw_text = response.text

# 4. Reliable Tech Image Pool
CATEGORY_IMAGES = {
    "AI": [
        "https://images.unsplash.com/photo-1677442136019-21780efad99a?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?auto=format&fit=crop&w=800&q=80"
    ],
    "HARDWARE": [
        "https://images.unsplash.com/photo-1518770660439-4636190af475?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?auto=format&fit=crop&w=800&q=80"
    ],
    "SECURITY": [
        "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1563986768609-322da13575f3?auto=format&fit=crop&w=800&q=80"
    ],
    "SOFTWARE": [
        "https://images.unsplash.com/photo-1555066931-4365d14bab8c?auto=format&fit=crop&w=800&q=80",
        "https://images.unsplash.com/photo-1542831371-29b0f74f9713?auto=format&fit=crop&w=800&q=80"
    ],
    "BUSINESS": [
        "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80"
    ]
}
DEFAULT_IMAGE = "https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=800&q=80"

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

with open(DB_FILE, "w", encoding="utf-8") as f:
    json.dump(active_news, f, indent=2)

# 5. Cyberpunk / Glassmorphism HTML + Three.js 3D Interactive Node Canvas
cards_html = ""
for item in active_news:
    cards_html += f"""
<article class="news-card">
  <div class="card-image">
    <img src="{item.get('image_url')}" alt="Tech News" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='{DEFAULT_IMAGE}';">
  </div>
  <div class="card-content">
    <div class="meta-bar">
      <span class="category">{item.get('category', 'TECH')}</span>
      <span class="timestamp">⏰ {item.get('timestamp_display')}</span>
    </div>
    <h2 class="title"><a href="{item.get('url')}" target="_blank" rel="noopener">{item.get('title')}</a></h2>
    <p class="summary">{item.get('summary')}</p>
    <ul class="key-points">
      <li><strong>Takeaway:</strong> {item.get('point1', '')}</li>
      <li><strong>Impact:</strong> {item.get('point2', '')}</li>
      <li><strong>Outlook:</strong> {item.get('point3', '')}</li>
    </ul>
    <a href="{item.get('url')}" target="_blank" rel="noopener" class="read-more">EXPLORE ARTICLE &rarr;</a>
  </div>
</article>
"""

html_page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI & Tech Cyber Pulse</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <style>
        :root {{
            --bg-color: #050814;
            --card-bg: rgba(15, 23, 42, 0.75);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --cyan: #00f3ff;
            --purple: #a855f7;
            --border: rgba(56, 189, 248, 0.2);
        }}

        * {{ box-sizing: border-box; }}
        
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace, sans-serif; 
            max-width: 960px; 
            margin: 0 auto; 
            padding: 40px 20px; 
            background: var(--bg-color); 
            color: var(--text-main); 
            line-height: 1.6;
            overflow-x: hidden;
        }}

        #bg-canvas {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            z-index: -1;
            pointer-events: none;
        }}

        header {{ 
            text-align: center; 
            margin-bottom: 50px; 
            background: rgba(15, 23, 42, 0.6);
            backdrop-filter: blur(12px);
            padding: 30px;
            border-radius: 20px;
            border: 1px solid var(--border);
            box-shadow: 0 0 30px rgba(0, 243, 255, 0.1);
        }}

        h1 {{ 
            background: linear-gradient(135deg, #00f3ff 0%, #a855f7 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin: 0 0 8px 0; 
            font-size: 2.5rem; 
            letter-spacing: -1px;
            text-transform: uppercase;
        }}

        .subtitle {{ color: var(--text-muted); font-size: 0.95rem; font-family: monospace; }}
        
        .news-card {{ 
            background: var(--card-bg); 
            backdrop-filter: blur(16px);
            border-radius: 16px; 
            margin-bottom: 35px; 
            border: 1px solid var(--border); 
            overflow: hidden; 
            display: flex;
            flex-direction: column;
            position: relative;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            opacity: 0;
            visibility: hidden;
            transform: translateY(28px);
            transition: opacity 0.7s ease, transform 0.35s ease, border-color 0.3s ease, box-shadow 0.3s ease;
        }}

        .news-card.in-view {{
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
        }}

        .news-card::after {{
            content: "";
            position: absolute;
            inset: 0;
            pointer-events: none;
            opacity: 0;
            background: radial-gradient(circle at var(--mx, 50%) var(--my, 50%), rgba(255, 255, 255, 0.16), transparent 45%);
            transition: opacity 0.3s ease;
        }}

        .news-card.tilt-ready:hover::after {{
            opacity: 1;
        }}

        .news-card:hover {{
            border-color: var(--cyan);
            box-shadow: 0 0 25px rgba(0, 243, 255, 0.25);
        }}

        .news-card:hover:not(.tilt-ready) {{
            transform: translateY(-4px) scale(1.01);
        }}
        
        .card-image {{
            background: #0b1120;
            width: 100%;
            height: 220px;
            overflow: hidden;
            flex-shrink: 0;
            position: relative;
        }}
        
        .card-image img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
            transition: transform 0.5s ease;
        }}

        .news-card:hover .card-image img {{
            transform: scale(1.05);
        }}
        
        .card-content {{ padding: 28px; flex-grow: 1; }}
        
        .meta-bar {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
        }}
        
        .category {{ 
            background: linear-gradient(135deg, rgba(0, 243, 255, 0.2), rgba(168, 85, 247, 0.2));
            color: var(--cyan); 
            border: 1px solid var(--cyan);
            font-size: 0.7rem; 
            padding: 4px 12px; 
            border-radius: 20px; 
            font-weight: 700; 
            letter-spacing: 1px;
            font-family: monospace;
            animation: badge-pulse 3.2s ease-in-out infinite;
        }}
        
        .timestamp {{
            color: var(--text-muted);
            font-size: 0.8rem;
            font-family: monospace;
        }}
        
        .title {{ margin: 0 0 14px 0; font-size: 1.4rem; line-height: 1.35; }}
        .title a {{ color: var(--text-main); text-decoration: none; transition: color 0.2s; }}
        .title a:hover {{ color: var(--cyan); }}
        
        .summary {{ color: #cbd5e1; font-size: 0.98rem; margin-bottom: 20px; }}
        
        .key-points {{
            background: rgba(5, 8, 20, 0.7);
            border-left: 3px solid var(--purple);
            padding: 14px 18px 14px 28px;
            margin: 0 0 22px 0;
            border-radius: 0 10px 10px 0;
            font-size: 0.9rem;
            color: #94a3b8;
        }}
        .key-points li {{ margin-bottom: 8px; }}
        .key-points li:last-child {{ margin-bottom: 0; }}
        .key-points strong {{ color: var(--cyan); font-family: monospace; }}
        
        .read-more {{ 
            display: inline-block; 
            color: var(--cyan); 
            font-weight: 700; 
            text-decoration: none; 
            font-size: 0.85rem;
            font-family: monospace;
            letter-spacing: 1px;
            transition: gap 0.2s ease;
        }}
        .read-more:hover {{ color: var(--purple); text-shadow: 0 0 10px var(--purple); }}

        @media (min-width: 680px) {{
            .news-card {{ flex-direction: row; }}
            .card-image {{ width: 38%; height: auto; min-height: 240px; }}
            .card-content {{ width: 62%; }}
        }}

        /* ---- Boot sequence overlay ---- */
        #boot-overlay {{
            position: fixed;
            inset: 0;
            z-index: 1000;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--bg-color);
            opacity: 1;
            transition: opacity 0.6s ease;
            animation: boot-force-hide 0.01s linear 4s forwards;
        }}

        #boot-overlay.boot-hide {{
            opacity: 0;
            pointer-events: none;
        }}

        @keyframes boot-force-hide {{
            to {{ opacity: 0; visibility: hidden; pointer-events: none; }}
        }}

        .boot-terminal {{
            width: 90%;
            max-width: 480px;
            font-family: monospace;
        }}

        .boot-line {{
            overflow: hidden;
            white-space: nowrap;
            width: 0;
            border-right: 2px solid var(--cyan);
            font-size: 0.9rem;
            color: var(--text-muted);
            margin: 0 0 10px 0;
        }}

        .boot-line:nth-child(1) {{ width: 0; animation: typeline-24 0.6s steps(24, end) 0.1s forwards; }}
        .boot-line:nth-child(2) {{ width: 0; animation: typeline-20 0.6s steps(20, end) 0.7s forwards; }}
        .boot-line:nth-child(3) {{ width: 0; animation: typeline-25 0.6s steps(25, end) 1.3s forwards; }}

        @keyframes typeline-24 {{
            to {{ width: 24ch; border-right-color: transparent; }}
        }}
        @keyframes typeline-20 {{
            to {{ width: 20ch; border-right-color: transparent; }}
        }}
        @keyframes typeline-25 {{
            to {{ width: 25ch; border-right-color: transparent; }}
        }}

        .boot-title {{
            margin: 22px 0 0 0;
            font-size: 1.8rem;
            letter-spacing: 2px;
            color: var(--cyan);
            text-shadow: 0 0 18px rgba(0, 243, 255, 0.6);
            opacity: 0;
            animation: boot-fade-in 0.4s ease 1.9s forwards;
        }}

        .boot-skip {{
            margin: 18px 0 0 0;
            font-size: 0.7rem;
            color: var(--text-muted);
            opacity: 0;
            animation: boot-fade-in 0.4s ease 2.7s forwards;
        }}

        @keyframes boot-fade-in {{
            to {{ opacity: 1; }}
        }}

        /* ---- Ambient scanline texture ---- */
        #scan-overlay {{
            position: fixed;
            inset: 0;
            z-index: 5;
            pointer-events: none;
            background: repeating-linear-gradient(
                to bottom,
                rgba(255, 255, 255, 0.025) 0px,
                rgba(255, 255, 255, 0.025) 1px,
                transparent 1px,
                transparent 3px
            );
            mix-blend-mode: overlay;
        }}

        /* ---- Category badge pulse ---- */
        @keyframes badge-pulse {{
            0%, 100% {{ box-shadow: 0 0 4px rgba(0, 243, 255, 0.15); }}
            50% {{ box-shadow: 0 0 12px rgba(0, 243, 255, 0.55); }}
        }}

        /* ---- Scroll progress ---- */
        #scroll-progress {{
            position: fixed;
            top: 0;
            left: 0;
            height: 3px;
            width: 0%;
            background: linear-gradient(90deg, var(--cyan), var(--purple));
            z-index: 1001;
            box-shadow: 0 0 8px rgba(0, 243, 255, 0.6);
            pointer-events: none;
        }}

        /* ---- Cursor trail glow ---- */
        #cursor-glow {{
            position: fixed;
            top: 0;
            left: 0;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(0, 243, 255, 0.55), rgba(168, 85, 247, 0.25) 60%, transparent 75%);
            pointer-events: none;
            z-index: 998;
            transform: translate(-50%, -50%);
            mix-blend-mode: screen;
            opacity: 0;
            transition: opacity 0.3s ease;
            will-change: transform;
        }}
        #cursor-glow.active {{ opacity: 1; }}

        /* ---- Header status line ---- */
        .status-line {{
            margin: 14px 0 0 0;
            font-family: monospace;
            font-size: 0.78rem;
            color: var(--text-muted);
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}

        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--cyan);
            box-shadow: 0 0 6px var(--cyan);
            animation: status-pulse 1.6s ease-in-out infinite;
        }}

        @keyframes status-pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.35; }}
        }}

        @media (prefers-reduced-motion: reduce) {{
            #boot-overlay {{
                display: none;
                animation: none;
            }}
            .boot-line, .boot-title, .boot-skip {{
                animation: none;
            }}
            .news-card {{
                opacity: 1;
                visibility: visible;
                transform: none;
                transition: border-color 0.3s ease, box-shadow 0.3s ease;
            }}
            .category {{
                animation: none;
            }}
            .status-dot {{
                animation: none;
            }}
            #cursor-glow {{
                display: none;
            }}
        }}
    </style>
    <noscript>
        <style>
            .news-card {{ opacity: 1 !important; visibility: visible !important; transform: none !important; }}
            #boot-overlay {{ display: none !important; }}
        </style>
    </noscript>
</head>
<body>
    <canvas id="bg-canvas"></canvas>
    <div id="scan-overlay" aria-hidden="true"></div>
    <div id="scroll-progress"></div>
    <div id="cursor-glow" aria-hidden="true"></div>
    <div id="boot-overlay" aria-hidden="true">
        <div class="boot-terminal">
            <p class="boot-line">&gt; ESTABLISHING UPLINK...</p>
            <p class="boot-line">&gt; DECRYPTING FEED...</p>
            <p class="boot-line">&gt; SYNCING NEURAL NODES...</p>
            <p class="boot-title" id="boot-title">TECH MATRIX PULSE</p>
            <p class="boot-skip">press any key to skip</p>
        </div>
    </div>

    <header>
        <h1>⚡ TECH MATRIX PULSE</h1>
        <p class="subtitle">// NEURAL TECH DIGEST • LIVE 48-HOUR STREAM</p>
        <p class="status-line">
            <span class="status-dot" aria-hidden="true"></span>
            LIVE // <span id="signal-count">0</span> ACTIVE SIGNALS // <span id="live-clock">00:00:00</span> UTC
        </p>
    </header>

    <main>
        {cards_html}
    </main>

    <script>
    // Three.js Interactive Neural Network Background
    (function () {{
        const canvas = document.getElementById('bg-canvas');
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        const scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x050814, 0.05);

        const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.1, 1000);
        camera.position.z = 9;

        const renderer = new THREE.WebGLRenderer({{ canvas: canvas, alpha: true, antialias: true }});
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

        // Soft circular sprite so nodes read as glowing points rather than hard squares
        function makeNodeTexture() {{
            const size = 128;
            const c = document.createElement('canvas');
            c.width = size;
            c.height = size;
            const ctx = c.getContext('2d');
            const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
            g.addColorStop(0, 'rgba(255,255,255,1)');
            g.addColorStop(0.35, 'rgba(255,255,255,0.55)');
            g.addColorStop(1, 'rgba(255,255,255,0)');
            ctx.fillStyle = g;
            ctx.fillRect(0, 0, size, size);
            return new THREE.CanvasTexture(c);
        }}

        const NODE_COUNT = window.innerWidth < 680 ? 55 : 95;
        const BOUNDS = 9;
        const LINK_DIST = 3;

        const positions = new Float32Array(NODE_COUNT * 3);
        const velocities = [];

        for (let i = 0; i < NODE_COUNT; i++) {{
            const idx = i * 3;
            positions[idx] = (Math.random() - 0.5) * BOUNDS * 2;
            positions[idx + 1] = (Math.random() - 0.5) * BOUNDS * 2;
            positions[idx + 2] = (Math.random() - 0.5) * BOUNDS * 2;
            velocities.push({{
                x: (Math.random() - 0.5) * 0.006,
                y: (Math.random() - 0.5) * 0.006,
                z: (Math.random() - 0.5) * 0.006
            }});
        }}

        const nodesGeometry = new THREE.BufferGeometry();
        nodesGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

        const nodesMaterial = new THREE.PointsMaterial({{
            size: 0.32,
            map: makeNodeTexture(),
            color: 0x5eeaff,
            transparent: true,
            opacity: 0.95,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
            sizeAttenuation: true
        }});

        const nodes = new THREE.Points(nodesGeometry, nodesMaterial);

        // Pre-allocate the max possible connection buffer; only a slice is drawn each frame
        const maxPairs = (NODE_COUNT * (NODE_COUNT - 1)) / 2;
        const linePositions = new Float32Array(maxPairs * 2 * 3);
        const lineColors = new Float32Array(maxPairs * 2 * 3);

        const linesGeometry = new THREE.BufferGeometry();
        const linePosAttr = new THREE.BufferAttribute(linePositions, 3);
        const lineColorAttr = new THREE.BufferAttribute(lineColors, 3);
        linePosAttr.setUsage(THREE.DynamicDrawUsage);
        lineColorAttr.setUsage(THREE.DynamicDrawUsage);
        linesGeometry.setAttribute('position', linePosAttr);
        linesGeometry.setAttribute('color', lineColorAttr);

        const linesMaterial = new THREE.LineBasicMaterial({{
            vertexColors: true,
            transparent: true,
            opacity: 0.45,
            depthWrite: false,
            blending: THREE.AdditiveBlending
        }});

        const links = new THREE.LineSegments(linesGeometry, linesMaterial);

        const group = new THREE.Group();
        group.add(links);
        group.add(nodes);
        scene.add(group);

        // Track pointer (mouse or touch) as a 3D world position via a plane facing the camera
        const raycaster = new THREE.Raycaster();
        const pointerNDC = new THREE.Vector2(999, 999);
        const pointerPlane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
        const pointerWorld = new THREE.Vector3();
        let pointerActive = false;

        function setPointer(clientX, clientY) {{
            pointerNDC.x = (clientX / window.innerWidth) * 2 - 1;
            pointerNDC.y = -(clientY / window.innerHeight) * 2 + 1;
            pointerActive = true;
        }}

        window.addEventListener('pointermove', function (e) {{ setPointer(e.clientX, e.clientY); }});
        window.addEventListener('pointerdown', function (e) {{ setPointer(e.clientX, e.clientY); }});
        window.addEventListener('pointerleave', function () {{ pointerActive = false; }});

        const CYAN = new THREE.Color(0x00f3ff);
        const PURPLE = new THREE.Color(0xa855f7);
        const tmpColor = new THREE.Color();

        function buildLinks() {{
            let vi = 0;
            let ci = 0;
            let segments = 0;
            for (let i = 0; i < NODE_COUNT; i++) {{
                const ai = i * 3;
                for (let j = i + 1; j < NODE_COUNT; j++) {{
                    const bi = j * 3;
                    const dx = positions[ai] - positions[bi];
                    const dy = positions[ai + 1] - positions[bi + 1];
                    const dz = positions[ai + 2] - positions[bi + 2];
                    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
                    if (dist < LINK_DIST) {{
                        linePositions[vi++] = positions[ai];
                        linePositions[vi++] = positions[ai + 1];
                        linePositions[vi++] = positions[ai + 2];
                        linePositions[vi++] = positions[bi];
                        linePositions[vi++] = positions[bi + 1];
                        linePositions[vi++] = positions[bi + 2];

                        tmpColor.copy(CYAN).lerp(PURPLE, dist / LINK_DIST);
                        lineColors[ci++] = tmpColor.r;
                        lineColors[ci++] = tmpColor.g;
                        lineColors[ci++] = tmpColor.b;
                        lineColors[ci++] = tmpColor.r;
                        lineColors[ci++] = tmpColor.g;
                        lineColors[ci++] = tmpColor.b;

                        segments++;
                    }}
                }}
            }}
            linePosAttr.needsUpdate = true;
            lineColorAttr.needsUpdate = true;
            linesGeometry.setDrawRange(0, segments * 2);
        }}

        function animate() {{
            requestAnimationFrame(animate);

            if (pointerActive) {{
                raycaster.setFromCamera(pointerNDC, camera);
                raycaster.ray.intersectPlane(pointerPlane, pointerWorld);
            }}

            for (let i = 0; i < NODE_COUNT; i++) {{
                const idx = i * 3;
                positions[idx] += velocities[i].x;
                positions[idx + 1] += velocities[i].y;
                positions[idx + 2] += velocities[i].z;

                if (Math.abs(positions[idx]) > BOUNDS) velocities[i].x *= -1;
                if (Math.abs(positions[idx + 1]) > BOUNDS) velocities[i].y *= -1;
                if (Math.abs(positions[idx + 2]) > BOUNDS) velocities[i].z *= -1;

                if (pointerActive) {{
                    const dx = positions[idx] - pointerWorld.x;
                    const dy = positions[idx + 1] - pointerWorld.y;
                    const dz = positions[idx + 2] - pointerWorld.z;
                    const distSq = dx * dx + dy * dy + dz * dz;
                    const repelRadius = 3.2;
                    if (distSq < repelRadius * repelRadius && distSq > 0.0001) {{
                        const dist = Math.sqrt(distSq);
                        const force = (1 - dist / repelRadius) * 0.03;
                        positions[idx] += (dx / dist) * force;
                        positions[idx + 1] += (dy / dist) * force;
                        positions[idx + 2] += (dz / dist) * force;
                    }}
                }}
            }}
            nodesGeometry.attributes.position.needsUpdate = true;

            buildLinks();

            group.rotation.y += 0.0009;

            renderer.render(scene, camera);
        }}

        if (prefersReducedMotion) {{
            buildLinks();
            renderer.render(scene, camera);
        }} else {{
            animate();
        }}

        window.addEventListener('resize', function () {{
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        }});
    }})();
    </script>

    <script>
    // UI Motion: boot sequence, scroll reveal, card tilt + sheen
    (function () {{
        try {{
            var prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            var cards = Array.prototype.slice.call(document.querySelectorAll('.news-card'));

            // ---------- Boot sequence ----------
            var bootOverlay = document.getElementById('boot-overlay');
            var bootTitle = document.getElementById('boot-title');

            function hideBoot() {{
                if (!bootOverlay) return;
                bootOverlay.classList.add('boot-hide');
                document.body.style.overflow = '';
                window.removeEventListener('keydown', hideBoot);
                window.removeEventListener('pointerdown', hideBoot);
            }}

            if (bootOverlay) {{
                if (prefersReducedMotion) {{
                    bootOverlay.style.display = 'none';
                }} else {{
                    document.body.style.overflow = 'hidden';
                    window.addEventListener('keydown', hideBoot, {{ once: true }});
                    window.addEventListener('pointerdown', hideBoot, {{ once: true }});

                    if (bootTitle) {{
                        (function () {{
                            var fullTitle = bootTitle.textContent;
                            var scrambleChars = '!<>-_\\/[]{{}}=+*^?#$%01';
                            var start = null;
                            var duration = 700;

                            function scrambleFrame(now) {{
                                if (start === null) start = now;
                                var progress = Math.min((now - start) / duration, 1);
                                var out = '';
                                for (var i = 0; i < fullTitle.length; i++) {{
                                    var threshold = (i + 1) / fullTitle.length;
                                    if (fullTitle[i] === ' ') {{
                                        out += ' ';
                                    }} else if (progress >= threshold) {{
                                        out += fullTitle[i];
                                    }} else {{
                                        out += scrambleChars[Math.floor(Math.random() * scrambleChars.length)];
                                    }}
                                }}
                                bootTitle.textContent = out;
                                if (progress < 1) {{
                                    requestAnimationFrame(scrambleFrame);
                                }} else {{
                                    bootTitle.textContent = fullTitle;
                                }}
                            }}

                            setTimeout(function () {{
                                requestAnimationFrame(scrambleFrame);
                            }}, 1900);
                        }})();
                    }}

                    setTimeout(hideBoot, 3300);
                }}
            }}

            // ---------- Scroll reveal ----------
            function revealAll() {{
                cards.forEach(function (card) {{
                    card.classList.add('in-view');
                }});
            }}

            if (prefersReducedMotion || !('IntersectionObserver' in window)) {{
                revealAll();
            }} else {{
                var observer = new IntersectionObserver(function (entries) {{
                    entries.forEach(function (entry) {{
                        if (entry.isIntersecting) {{
                            entry.target.classList.add('in-view');
                            observer.unobserve(entry.target);
                        }}
                    }});
                }}, {{ threshold: 0.15 }});

                cards.forEach(function (card) {{
                    observer.observe(card);
                }});
            }}

            // ---------- Card tilt + sheen ----------
            var canHover = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

            if (canHover && !prefersReducedMotion) {{
                cards.forEach(function (card) {{
                    card.classList.add('tilt-ready');

                    card.addEventListener('pointermove', function (e) {{
                        var rect = card.getBoundingClientRect();
                        var x = (e.clientX - rect.left) / rect.width;
                        var y = (e.clientY - rect.top) / rect.height;
                        var rotateY = (x - 0.5) * 10;
                        var rotateX = (0.5 - y) * 8;
                        card.style.transform = 'perspective(800px) rotateX(' + rotateX.toFixed(2) + 'deg) rotateY(' + rotateY.toFixed(2) + 'deg)';
                        card.style.setProperty('--mx', (x * 100).toFixed(1) + '%');
                        card.style.setProperty('--my', (y * 100).toFixed(1) + '%');
                    }});

                    card.addEventListener('pointerleave', function () {{
                        card.style.transform = '';
                    }});
                }});
            }}

            // ---------- Scroll progress ----------
            var progressBar = document.getElementById('scroll-progress');
            if (progressBar) {{
                var updateProgress = function () {{
                    var docEl = document.documentElement;
                    var scrollTop = docEl.scrollTop || document.body.scrollTop;
                    var scrollHeight = (docEl.scrollHeight || document.body.scrollHeight) - docEl.clientHeight;
                    var pct = scrollHeight > 0 ? (scrollTop / scrollHeight) * 100 : 0;
                    progressBar.style.width = pct + '%';
                }};
                window.addEventListener('scroll', updateProgress, {{ passive: true }});
                updateProgress();
            }}

            // ---------- Live status line ----------
            var TOTAL_SIGNALS = {len(active_news)};  // live count injected by the generator
            var signalCountEl = document.getElementById('signal-count');
            if (signalCountEl) {{
                if (prefersReducedMotion) {{
                    signalCountEl.textContent = TOTAL_SIGNALS;
                }} else {{
                    var currentCount = 0;
                    var countStep = function () {{
                        currentCount += Math.max(1, Math.ceil(TOTAL_SIGNALS / 20));
                        if (currentCount >= TOTAL_SIGNALS) {{
                            signalCountEl.textContent = TOTAL_SIGNALS;
                        }} else {{
                            signalCountEl.textContent = currentCount;
                            requestAnimationFrame(countStep);
                        }}
                    }};
                    requestAnimationFrame(countStep);
                }}
            }}

            var clockEl = document.getElementById('live-clock');
            if (clockEl) {{
                var pad2 = function (n) {{ return (n < 10 ? '0' : '') + n; }};
                var tickClock = function () {{
                    var now = new Date();
                    clockEl.textContent = pad2(now.getUTCHours()) + ':' + pad2(now.getUTCMinutes()) + ':' + pad2(now.getUTCSeconds());
                }};
                tickClock();
                setInterval(tickClock, 1000);
            }}

            // ---------- Cursor trail glow (fine-pointer desktops only) ----------
            var cursorGlow = document.getElementById('cursor-glow');
            if (cursorGlow && canHover && !prefersReducedMotion) {{
                var glowTargetX = window.innerWidth / 2;
                var glowTargetY = window.innerHeight / 2;
                var glowCurrentX = glowTargetX;
                var glowCurrentY = glowTargetY;
                var glowActive = false;

                window.addEventListener('pointermove', function (e) {{
                    if (e.pointerType && e.pointerType !== 'mouse') return;
                    glowTargetX = e.clientX;
                    glowTargetY = e.clientY;
                    if (!glowActive) {{
                        glowActive = true;
                        cursorGlow.classList.add('active');
                    }}
                }});
                window.addEventListener('pointerleave', function () {{
                    glowActive = false;
                    cursorGlow.classList.remove('active');
                }});

                var glowFrame = function () {{
                    glowCurrentX += (glowTargetX - glowCurrentX) * 0.18;
                    glowCurrentY += (glowTargetY - glowCurrentY) * 0.18;
                    cursorGlow.style.transform = 'translate(' + glowCurrentX.toFixed(1) + 'px, ' + glowCurrentY.toFixed(1) + 'px) translate(-50%, -50%)';
                    requestAnimationFrame(glowFrame);
                }};
                glowFrame();
            }}
        }} catch (err) {{
            // Fail safe: a bug in these motion effects must never hide the real content
            var fallbackCards = document.querySelectorAll('.news-card');
            for (var i = 0; i < fallbackCards.length; i++) {{
                fallbackCards[i].classList.add('in-view');
            }}
            var overlay = document.getElementById('boot-overlay');
            if (overlay) overlay.style.display = 'none';
            document.body.style.overflow = '';
        }}
    }})();
    </script>
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Interactive 3JS site updated successfully! Active stories: {len(active_news)}")
