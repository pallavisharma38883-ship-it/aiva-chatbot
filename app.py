from flask import Flask, request, jsonify, render_template_string, send_from_directory
from groq import Groq
from dotenv import load_dotenv
from gtts import gTTS
from moviepy.editor import ImageClip, concatenate_videoclips, AudioFileClip
from werkzeug.utils import secure_filename
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageFilter
from urllib.parse import urljoin, urlparse
import requests
import os
import uuid
import re
import time
import json 

# Selenium is used only for URL-to-video image fetching.
# Install once:
# pip install selenium
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except Exception:
    webdriver = None
    Options = None
    By = None
    WebDriverWait = None
    EC = None


load_dotenv()

app = Flask(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
GROQ_MODEL = "llama-3.1-8b-instant"

CURRENTS_API_KEY = os.getenv("CURRENTS_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

UPLOAD_FOLDER = "uploads"
VIDEO_FOLDER = "generated_videos"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(VIDEO_FOLDER, exist_ok=True)

chat_history = []

if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


# -------------------------
# Basic AIVA chat utilities
# -------------------------

def detect_language(text):
    hindi_chars = any("\u0900" <= ch <= "\u097F" for ch in text)
    if hindi_chars:
        return "Hindi"

    hinglish_words = [
        "kya", "kaise", "hai", "batao", "mujhe", "karna",
        "kyu", "nahi", "haan", "acha", "theek", "mera", "meri"
    ]

    if any(word in text.lower() for word in hinglish_words):
        return "Hinglish"

    return "English"


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def is_news_query(text):
    text = text.lower()
    news_words = [
        "news", "latest news", "current news", "today news", "world news",
        "worldwide news", "breaking news", "headlines", "samachar", "khabar",
        "खबर", "समाचार", "न्यूज़", "न्यूज"
    ]
    return any(word in text for word in news_words)


def extract_news_topic(user_message):
    text = user_message.lower()

    topic_map = {
        "india": "India",
        "world": "world",
        "worldwide": "world",
        "global": "world",
        "ai": "artificial intelligence",
        "artificial intelligence": "artificial intelligence",
        "technology": "technology",
        "tech": "technology",
        "business": "business",
        "sports": "sports",
        "cricket": "cricket",
        "health": "health",
        "entertainment": "entertainment",
        "science": "science",
        "politics": "politics",
        "bitcoin": "bitcoin",
        "tesla": "tesla",
        "apple": "apple",
        "weather": "weather"
    }

    for key, value in topic_map.items():
        if key in text:
            return value

    return "world"


def get_latest_news(user_message):
    if not CURRENTS_API_KEY:
        return "Currents API key missing. Please add CURRENTS_API_KEY in your .env file."

    topic = extract_news_topic(user_message)

    if topic == "world":
        url = "https://api.currentsapi.services/v1/latest-news"
        params = {"apiKey": CURRENTS_API_KEY, "language": "en"}
    else:
        url = "https://api.currentsapi.services/v1/search"
        params = {"apiKey": CURRENTS_API_KEY, "keywords": topic, "language": "en"}

    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        if response.status_code != 200:
            return f"News API error: {data.get('message', 'Unable to fetch news right now.')}"

        articles = data.get("news", [])[:5]

        if not articles:
            return "Sorry, I couldn't find latest news right now. Please try again."

        news_text = "📰 <b>Latest News</b><br><br>"

        for i, article in enumerate(articles, 1):
            title = clean_text(article.get("title", ""))
            summary = clean_text(article.get("description") or article.get("content") or "")

            if not title:
                continue

            if len(summary) > 130:
                summary = summary[:130] + "..."

            news_text += f"<b>{i}. {title}</b><br>"
            if summary:
                news_text += f"{summary}<br><br>"
            else:
                news_text += "<br>"

        return news_text

    except Exception as e:
        return f"Sorry, I couldn't fetch live news right now. Error: {str(e)}"


def is_weather_query(text):
    text = text.lower()
    weather_words = [
        "weather", "temperature", "temp", "mausam", "mosam",
        "मौसम", "तापमान", "baarish", "barish", "rain", "humidity"
    ]
    return any(word in text for word in weather_words)


def extract_weather_city(user_message):
    lower = user_message.lower().strip()

    known_cities = [
        "Jaipur", "Delhi", "Mumbai", "Pune", "Bangalore", "Bengaluru",
        "Hyderabad", "Chennai", "Kolkata", "Ahmedabad", "Udaipur",
        "Jodhpur", "Kota", "Ajmer", "Sriganganagar", "Sri Ganganagar",
        "Gurgaon", "Noida", "Lucknow", "Indore", "Surat", "Chandigarh"
    ]

    for city in known_cities:
        if city.lower() in lower:
            return city

    patterns = [
        r"weather\s+(?:in|of|for)\s+([a-zA-Z\s]+)",
        r"temperature\s+(?:in|of|for)\s+([a-zA-Z\s]+)",
        r"mausam\s+(?:in|of|for)\s+([a-zA-Z\s]+)",
        r"mosam\s+(?:in|of|for)\s+([a-zA-Z\s]+)",
        r"([a-zA-Z\s]+)\s+weather",
        r"([a-zA-Z\s]+)\s+temperature"
    ]

    for pattern in patterns:
        match = re.search(pattern, lower, re.IGNORECASE)
        if match:
            city = clean_text(match.group(1))
            remove_words = ["today", "current", "latest", "now", "tell me", "please", "what is", "ka", "ki"]
            for word in remove_words:
                city = city.replace(word, "")
            city = clean_text(city)
            if city:
                return city.title()

    return "Jaipur"


def get_weather(user_message):
    if not OPENWEATHER_API_KEY:
        return "OpenWeather API key missing. Please add OPENWEATHER_API_KEY in your .env file."

    city = extract_weather_city(user_message)

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric"}

    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        if response.status_code != 200:
            return f"Weather API error: {data.get('message', 'Unable to fetch weather right now.')}"

        city_name = data.get("name", city)
        country = data.get("sys", {}).get("country", "")
        temp = round(data.get("main", {}).get("temp", 0))
        feels_like = round(data.get("main", {}).get("feels_like", 0))
        humidity = data.get("main", {}).get("humidity", "N/A")
        wind_speed = data.get("wind", {}).get("speed", 0)
        condition = data.get("weather", [{}])[0].get("description", "N/A").title()
        wind_kmh = round(float(wind_speed) * 3.6, 1)

        return f"""🌤 <b>Weather in {city_name}{', ' + country if country else ''}</b><br><br>
<b>Temperature:</b> {temp}°C<br>
<b>Condition:</b> {condition}<br>
<b>Feels Like:</b> {feels_like}°C<br>
<b>Humidity:</b> {humidity}%<br>
<b>Wind:</b> {wind_kmh} km/h<br><br>
Have a great day! 😊"""

    except Exception as e:
        return f"Sorry, I couldn't fetch live weather right now. Error: {str(e)}"



def ask_aiva(user_message):
    try:
        response = requests.post(
            "http://127.0.0.1:8001/chat",
            json={"message": user_message},
            timeout=60
        )

        data = response.json()
        return data.get("reply", "Sorry, I could not get reply from AIVA API.")

    except Exception as e:
        return f"FastAPI Error: {str(e)}"


# -------------------------
# Property URL extraction
# -------------------------

def extract_location_from_text(text):
    possible_locations = [
        "Nirman Nagar", "Vaishali Nagar", "Mansarovar", "Jagatpura",
        "Pratap Nagar", "Malviya Nagar", "Ajmer Road", "C-Scheme",
        "Tonk Road", "Raja Park", "Sodala", "Jhotwara", "Sitapura",
        "Durgapura", "Kalwar Road", "Gopalpura", "Mahapura",
        "Jaipur", "Sanganer", "Vidhyadhar Nagar", "Bani Park",
        "Bapu Nagar", "Gandhi Path", "Sirsi Road", "Bindayaka"
    ]

    found = []
    lower_text = text.lower()

    for loc in possible_locations:
        if loc.lower() in lower_text:
            found.append(loc)

    if found:
        if "Jaipur" not in found:
            found.append("Jaipur")
        return ", ".join(dict.fromkeys(found))

    location_patterns = [
        r"location[:\s-]+([A-Za-z\s]+Jaipur)",
        r"in\s+([A-Za-z\s]+Jaipur)",
        r"at\s+([A-Za-z\s]+Jaipur)",
        r"near\s+([A-Za-z\s]+Jaipur)"
    ]

    for pattern in location_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

    return "Jaipur"


def is_bad_image_url(src):
    lower = src.lower()
    bad_words = [
        "logo", "icon", "avatar", "whatsapp", "facebook", "instagram",
        "youtube", "placeholder", "default", "profile", "sprite", "loader",
        "jaipur-rental", "jaipur_rental", "watermark", "favicon", "no-image",
        "blank", "transparent", "captcha", "banner", "advert", "ads"
    ]
    return any(word in lower for word in bad_words)


def normalize_image_url(src, page_url):
    if not src:
        return None

    src = str(src).strip().strip('"').strip("'")
    src = src.replace("\\/", "/")
    src = src.replace("&amp;", "&")

    if not src or src.startswith("data:image"):
        return None

    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = urljoin(page_url, src)
    elif not src.startswith("http"):
        src = urljoin(page_url, src)

    if not src.startswith("http"):
        return None

    return src


def room_priority(text):
    text = text.lower()
    order = [
        (["hall", "living", "drawing", "lounge"], 1),
        (["bedroom", "bed", "master"], 2),
        (["kitchen"], 3),
        (["bathroom", "washroom", "toilet"], 4),
        (["balcony"], 5),
        (["terrace", "roof"], 6),
        (["front", "building", "outside", "exterior", "elevation"], 7),
    ]
    for words, score in order:
        if any(w in text for w in words):
            return score
    return 8


def collect_images_from_html(html_text, page_url):
    soup = BeautifulSoup(html_text, "lxml")
    image_items = []
    seen = set()

    def add_image(src, keyword_text=""):
        src = normalize_image_url(src, page_url)
        if not src:
            return
        if src in seen:
            return
        if is_bad_image_url(src):
            return
        # Keep image-like URLs and also CDN URLs that contain resize query.
        image_like = re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", src, re.I)
        cdn_like = any(x in src.lower() for x in ["image", "photo", "gallery", "property", "upload", "cdn"])
        if not image_like and not cdn_like:
            return

        seen.add(src)
        image_items.append({
            "url": src,
            "keywords": f"{src} {keyword_text}",
            "priority": room_priority(f"{src} {keyword_text}")
        })

    for img in soup.find_all("img"):
        keyword_text = " ".join([
            img.get("alt") or "",
            img.get("title") or "",
            " ".join(img.get("class", [])) if img.get("class") else "",
            img.get("id") or ""
        ])

        attrs = [
            "src", "data-src", "data-original", "data-lazy", "data-lazy-src",
            "data-url", "data-full", "data-image", "data-img", "data-big",
            "data-large", "data-thumb", "data-thumbnail"
        ]

        for attr in attrs:
            add_image(img.get(attr), keyword_text)

        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for part in srcset.split(","):
                add_image(part.strip().split(" ")[0], keyword_text)

    html_unescaped = html_text.replace("\\/", "/")

    # Full URLs inside scripts/json.
    for found in re.findall(r'https?://[^\"\'\s<>]+?\.(?:jpg|jpeg|png|webp)(?:\?[^\"\'\s<>]*)?', html_unescaped, re.I):
        add_image(found, "script gallery property")

    # CSS backgrounds.
    for found in re.findall(r'url\(([^)]+\.(?:jpg|jpeg|png|webp)[^)]*)\)', html_unescaped, re.I):
        add_image(found.strip(" '\""), "background gallery property")

    # JSON values like "image":"..."
    for found in re.findall(r'["\'](?:image|photo|url|src|full|large|original)["\']\s*:\s*["\']([^"\']+)["\']', html_unescaped, re.I):
        add_image(found, "json gallery property")

    image_items = sorted(image_items, key=lambda x: x["priority"])
    return image_items


def fetch_with_requests(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Referer": url
    }
    response = requests.get(url, headers=headers, timeout=25)
    response.raise_for_status()
    return response.text


def fetch_with_selenium(url):
    if webdriver is None:
        return "", []

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=420,900")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--log-level=3")

    driver = None
    html_text = ""
    direct_urls = []

    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(45)
        driver.get(url)

        time.sleep(3)

        # Scroll slowly so lazy-loaded images are forced to load.
        last_height = 0
        for _ in range(8):
            driver.execute_script("window.scrollBy(0, 700);")
            time.sleep(1)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        # Try clicking gallery/photo buttons if present.
        click_keywords = [
            "photo", "photos", "gallery", "view photos", "show photos",
            "image", "images", "view all", "see all", "चित्र", "फोटो"
        ]

        candidates = driver.find_elements(By.XPATH, "//button | //a | //div[@role='button']")
        for el in candidates[:80]:
            try:
                text = (el.text or "").strip().lower()
                aria = (el.get_attribute("aria-label") or "").strip().lower()
                cls = (el.get_attribute("class") or "").strip().lower()
                all_text = f"{text} {aria} {cls}"

                if any(k in all_text for k in click_keywords):
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(2)
                    break
            except Exception:
                continue

        # Scroll inside modal/gallery if any.
        for _ in range(5):
            try:
                driver.execute_script("window.scrollBy(0, 600);")
                time.sleep(0.7)
            except Exception:
                pass

        # Collect displayed img/currentSrc + background images.
        imgs = driver.find_elements(By.TAG_NAME, "img")
        for img in imgs:
            for attr in ["currentSrc", "src", "data-src", "data-lazy-src", "data-original", "srcset"]:
                try:
                    value = img.get_attribute(attr)
                    if not value:
                        continue
                    if attr == "srcset":
                        for part in value.split(","):
                            direct_urls.append(part.strip().split(" ")[0])
                    else:
                        direct_urls.append(value)
                except Exception:
                    pass

        # background-image urls from all elements.
        bg_urls = driver.execute_script("""
            const urls = [];
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const style = window.getComputedStyle(el);
                const bg = style.getPropertyValue('background-image');
                if (bg && bg.includes('url(')) urls.push(bg);
            }
            return urls;
        """)

        for bg in bg_urls or []:
            try:
                start = bg.find("url(")
                if start == -1:
                    continue
                found = bg[start + 4:].rstrip(")").strip().strip("'").strip('"')
                if found:
                    direct_urls.append(found)
            except Exception:
                pass

        html_text = driver.page_source

    except Exception as e:
        print("Selenium fetch failed:", str(e))

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    return html_text, direct_urls


def fetch_property_details(url):
    selenium_html = ""
    selenium_direct_urls = []

    # First try Selenium because property sites mostly lazy-load gallery images.
    selenium_html, selenium_direct_urls = fetch_with_selenium(url)

    # Fallback to normal requests.
    request_html = ""
    try:
        request_html = fetch_with_requests(url)
    except Exception as e:
        print("Requests fetch failed:", str(e))

    final_html = selenium_html or request_html

    if not final_html:
        raise Exception("Could not open this URL. Site may be blocking access.")

    soup = BeautifulSoup(final_html, "lxml")

    title = clean_text(soup.find("title").get_text()) if soup.find("title") else "Property in Jaipur"

    meta_desc = ""
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        meta_desc = clean_text(desc_tag.get("content"))

    page_text = clean_text(soup.get_text(" "))
    combined_text = f"{title} {meta_desc} {page_text}"
    location = extract_location_from_text(combined_text)

    image_items = []
    seen = set()

    def add_item(src, keyword=""):
        src = normalize_image_url(src, url)
        if not src:
            return
        if src in seen:
            return
        if is_bad_image_url(src):
            return
        image_like = re.search(r"\.(jpg|jpeg|png|webp)(\?|$)", src, re.I)
        cdn_like = any(x in src.lower() for x in ["image", "photo", "gallery", "property", "upload", "cdn"])
        if not image_like and not cdn_like:
            return
        seen.add(src)
        image_items.append({
            "url": src,
            "keywords": f"{src} {keyword}",
            "priority": room_priority(f"{src} {keyword}")
        })

    # Add Selenium direct URLs first because these are usually loaded/full gallery images.
    for u in selenium_direct_urls:
        add_item(u, "selenium loaded gallery")

    # Add all images from Selenium/request HTML.
    for item in collect_images_from_html(selenium_html or "", url):
        add_item(item["url"], item.get("keywords", ""))

    for item in collect_images_from_html(request_html or "", url):
        add_item(item["url"], item.get("keywords", ""))

    image_items = sorted(image_items, key=lambda x: x["priority"])

    property_text = f"""
Title: {title}
Location: {location}
Description: {meta_desc}
Page Content: {page_text[:2500]}
URL: {url}
"""

    print("=" * 60)
    print("Property title:", title)
    print("Location:", location)
    print("Image links found:", len(image_items))
    print("=" * 60)

    return {
        "title": title,
        "location": location,
        "description": meta_desc,
        "content": page_text[:2500],
        "images": image_items[:80],
        "property_text": property_text
    }


def are_images_visually_same(path1, path2):
    try:
        img1 = Image.open(path1).convert("L").resize((16, 16), Image.Resampling.LANCZOS)
        img2 = Image.open(path2).convert("L").resize((16, 16), Image.Resampling.LANCZOS)

        p1 = list(img1.getdata())
        p2 = list(img2.getdata())

        diff = sum(abs(a - b) for a, b in zip(p1, p2)) / len(p1)
        return diff < 4
    except Exception:
        return False


def download_property_images(image_items):
    image_paths = []
    seen_urls = set()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    }

    for item in image_items:
        img_url = item["url"] if isinstance(item, dict) else item

        if img_url in seen_urls:
            continue
        seen_urls.add(img_url)

        try:
            response = requests.get(img_url, headers=headers, timeout=20, stream=True, allow_redirects=True)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "").lower()
            content = response.content

            # Skip tiny assets only.
            if len(content) < 4000:
                continue

            temp_name = f"{uuid.uuid4()}.img"
            temp_path = os.path.join(UPLOAD_FOLDER, temp_name)

            with open(temp_path, "wb") as f:
                f.write(content)

            try:
                img = Image.open(temp_path).convert("RGB")
                w, h = img.size

                # Allow mobile property photos. Only remove tiny icons.
                if w < 240 or h < 240:
                    os.remove(temp_path)
                    continue

                # Remove extremely wide website bars.
                if w / max(h, 1) > 4.5:
                    os.remove(temp_path)
                    continue

                # Remove extremely tall sprites.
                if h / max(w, 1) > 4.5:
                    os.remove(temp_path)
                    continue

                final_path = os.path.join(UPLOAD_FOLDER, f"property_{uuid.uuid4()}.jpg")
                img.save(final_path, quality=95)

                os.remove(temp_path)

                # Visual duplicate check.
                duplicate = False
                for old_path in image_paths:
                    if are_images_visually_same(final_path, old_path):
                        duplicate = True
                        break

                if duplicate:
                    os.remove(final_path)
                    continue

                image_paths.append(final_path)

                print("Downloaded image:", len(image_paths), img_url, w, h)

                if len(image_paths) >= 35:
                    break

            except Exception as e:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                continue

        except Exception as e:
            continue

    print("=" * 60)
    print("Images downloaded:", len(image_paths))
    print("=" * 60)

    return image_paths


# -------------------------
# Video generation
# -------------------------

def make_vertical_fullscreen_frame(image_path):
    """
    Create a true full-screen 9:16 frame.

    This version does NOT create a blurred background and does NOT paste
    a small image in the center. Every image is resized to cover the full
    1080x1920 vertical screen, then center-cropped.
    """
    img = Image.open(image_path).convert("RGB")
    target_w, target_h = 1080, 1920

    img_w, img_h = img.size
    img_ratio = img_w / max(img_h, 1)
    target_ratio = target_w / target_h

    # Resize image so it fully covers the 9:16 frame.
    # Landscape images will be cropped from left/right, but will not look tiny.
    if img_ratio > target_ratio:
        new_h = target_h
        new_w = int(target_h * img_ratio)
    else:
        new_w = target_w
        new_h = int(target_w / img_ratio)

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = max((new_w - target_w) // 2, 0)
    top = max((new_h - target_h) // 2, 0)
    frame = img.crop((left, top, left + target_w, top + target_h))

    out_path = os.path.join(UPLOAD_FOLDER, f"frame_{uuid.uuid4()}.jpg")
    frame.save(out_path, quality=96)
    return out_path

def generate_property_voiceover(property_text, image_count=12):
    # Minimum 1 minute voice-over for property reel timing.
    duration_hint = max(60, image_count * 5)

    prompt = f"""
Create a realistic Hindi/Hinglish real estate voice-over for a vertical property video.

Property information:
{property_text}

Important:
- Start directly with the location/property name.
- Do NOT say specific room names like hall, bedroom, kitchen, bathroom, balcony unless the exact room data is present in property text.
- Use safe lines that match any property image: interiors, space, layout, natural light, finishing, location, family living.
- No long welcome.
- No "sapno ka ghar" type generic line.
- Do not invent fake BHK, price, area, floor, owner, or amenities.
- Keep it natural and premium, like a real property reel.
- Duration: around {duration_hint} seconds.
- Language: simple Hindi/Hinglish.
- End with: Site visit ke liye aaj hi contact karein.
- Return only voice-over script, no headings.
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": "You write short, direct Hindi/Hinglish real estate walkthrough voice-overs."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.5
    )

    return response.choices[0].message.content


def create_voice_file(voice_text, voice_lang="hi"):
    voice_text = clean_text(voice_text)
    if not voice_text:
        raise Exception("Voice text is empty")

    if voice_lang not in ["hi", "en"]:
        voice_lang = "hi"

    audio_name = f"voice_{uuid.uuid4()}.mp3"
    audio_path = os.path.join(UPLOAD_FOLDER, audio_name)

    tts = gTTS(text=voice_text, lang=voice_lang, slow=False)
    tts.save(audio_path)

    return audio_path


def create_video_from_images(image_paths, voice_path, video_path):
    if not image_paths:
        raise Exception("No images found for video")

    audio = AudioFileClip(voice_path)

    # Final rule decided for AIVA property videos:
    # 12 images = 60 sec, 15 images = 75 sec, 20 images = 100 sec, 25 images = 125 sec.
    # Every image gets 5 seconds. If fewer than 12 images are available, repeat them
    # with different smooth zoom movements so the video is still minimum 1 minute.
    per_image_duration = 5.0
    minimum_slots = 12

    final_image_paths = list(image_paths)
    if len(final_image_paths) < minimum_slots:
        original_images = list(final_image_paths)
        repeat_index = 0
        while len(final_image_paths) < minimum_slots:
            final_image_paths.append(original_images[repeat_index % len(original_images)])
            repeat_index += 1

    total_images = len(final_image_paths)
    clips = []

    for i, img in enumerate(final_image_paths):
        frame_img = make_vertical_fullscreen_frame(img)

        base_clip = ImageClip(frame_img).set_duration(per_image_duration)

        # Gentle cinematic movements. Repeated images will still feel different.
        effect_type = i % 4
        if effect_type == 0:
            moving_clip = base_clip.resize(lambda t: 1.00 + 0.035 * min(t / per_image_duration, 1))
        elif effect_type == 1:
            moving_clip = base_clip.resize(lambda t: 1.035 - 0.020 * min(t / per_image_duration, 1))
        elif effect_type == 2:
            moving_clip = base_clip.resize(lambda t: 1.015 + 0.025 * min(t / per_image_duration, 1))
        else:
            moving_clip = base_clip.resize(lambda t: 1.030 - 0.015 * min(t / per_image_duration, 1))

        moving_clip = (
            moving_clip
            .set_position("center")
            .crop(width=1080, height=1920, x_center=540, y_center=960)
            .fadein(0.12)
            .fadeout(0.12)
        )

        clips.append(moving_clip)

    final_video = concatenate_videoclips(clips, method="compose")
    video_duration = final_video.duration

    # If voice is longer than video, trim it. If voice is shorter, video continues naturally.
    final_audio = audio.subclip(0, video_duration) if audio.duration > video_duration else audio
    final_video = final_video.set_audio(final_audio)

    final_video.write_videofile(
        video_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="9000k",
        preset="medium",
        threads=4
    )

    audio.close()
    final_video.close()

    return video_path


# -------------------------
# UI
# -------------------------

@app.route("/")
def home():
    return render_template_string("""
<!DOCTYPE html>
<html>
<head>
<title>AIVA AI Assistant</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<style>
*{
    margin:0;
    padding:0;
    box-sizing:border-box;
    font-family:Arial,sans-serif;
}

html, body{
    width:100%;
    height:100%;
    overflow:hidden;
    background:#eef6ff;
}

.main-app{
    width:100vw;
    height:100vh;
    overflow:hidden;
}

.sidebar{
    position:fixed;
    top:0;
    left:0;
    width:280px;
    height:100vh;
    background:linear-gradient(180deg,#ffffff,#eef7ff);
    border-right:1px solid #d9eaff;
    padding:20px;
    overflow-y:auto;
    overflow-x:hidden;
}

.chat-section{
    position:fixed;
    top:0;
    left:280px;
    right:0;
    bottom:0;
    background:#eef6ff;
    overflow:hidden;
}

.top-header{
    position:absolute;
    top:0;
    left:0;
    right:0;
    height:92px;
    padding:20px 30px;
    background:white;
    border-bottom:1px solid #e6eef8;
}

.top-header h1{
    color:#1d3557;
}

.top-header p{
    color:#6c7a89;
}

.quick-actions{
    position:absolute;
    top:92px;
    left:0;
    right:0;
    min-height:66px;
    display:flex;
    gap:10px;
    padding:15px 20px;
    flex-wrap:wrap;
    background:white;
    border-bottom:1px solid #e6eef8;
}

.quick-actions button{
    border:none;
    background:#eef5ff;
    padding:10px 14px;
    border-radius:20px;
    cursor:pointer;
}

#chat-box{
    position:absolute;
    top:158px;
    left:0;
    right:0;
    bottom:85px;
    overflow-y:auto;
    overflow-x:hidden;
    padding:25px;
}

.input-area{
    position:absolute;
    left:0;
    right:0;
    bottom:0;
    height:85px;
    display:flex;
    gap:12px;
    padding:18px;
    background:white;
    border-top:1px solid #e6eef8;
}

.profile{
    text-align:center;
    margin-bottom:20px;
}

.profile-icon{
    width:65px;
    height:65px;
    margin:auto;
    background:#4f8cff;
    color:white;
    border-radius:20px;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:32px;
}

.profile h2{
    margin-top:10px;
    color:#1d3557;
}

.profile p{
    font-size:13px;
    color:#6c7a89;
}

.new-chat{
    width:100%;
    padding:13px;
    border:none;
    border-radius:14px;
    background:#4f8cff;
    color:white;
    cursor:pointer;
    margin-bottom:20px;
}

.section{
    margin-bottom:20px;
}

.section h3{
    margin-bottom:10px;
    color:#1d3557;
}

.section select,
.section button{
    width:100%;
    padding:11px;
    margin-bottom:8px;
    border:none;
    border-radius:12px;
    background:white;
    cursor:pointer;
}

.video-box{
    background:white;
    border-radius:14px;
    padding:12px;
    margin-bottom:20px;
}

.video-box input,
.video-box textarea,
.video-box select{
    width:100%;
    margin-bottom:8px;
    padding:10px;
    border:1px solid #d9e8ff;
    border-radius:12px;
    outline:none;
}

.video-box textarea{
    height:80px;
    resize:none;
}

.video-box button{
    width:100%;
    padding:11px;
    border:none;
    border-radius:12px;
    background:#4f8cff;
    color:white;
    cursor:pointer;
}

.message{
    width:100%;
    display:flex;
    align-items:flex-end;
    gap:10px;
    margin-bottom:16px;
}

.avatar{
    width:35px;
    height:35px;
    border-radius:50%;
    background:white;
    display:flex;
    align-items:center;
    justify-content:center;
    flex-shrink:0;
    box-shadow:0 4px 12px rgba(0,0,0,0.05);
}

.bubble{
    display:inline-block;
    width:auto;
    max-width:620px;
    height:auto;
    padding:10px 14px;
    border-radius:16px;
    line-height:1.5;
    font-size:15px;
    word-break:break-word;
    background:white;
    color:#111;
    box-shadow:0 4px 12px rgba(0,0,0,0.05);
}

.bot-message{
    justify-content:flex-start;
}

.bot-message .bubble{
    border-bottom-left-radius:5px;
}

.user-message{
    justify-content:flex-end;
}

.user-message .bubble{
    border-bottom-right-radius:5px;
}

.user-message .avatar{
    order:2;
}

.time{
    font-size:10px;
    opacity:0.55;
    margin-top:4px;
}

.input-area input[type="text"]{
    flex:1;
    padding:15px;
    border:1px solid #d9e8ff;
    border-radius:16px;
    outline:none;
}

.send-btn{
    width:55px;
    min-width:55px;
    border:none;
    border-radius:16px;
    background:#4f8cff;
    color:white;
    font-size:20px;
    cursor:pointer;
}

.mic-listening{
    background:red !important;
    color:white !important;
    animation:pulse 1s infinite;
}

@keyframes pulse{
    0%{transform:scale(1);}
    50%{transform:scale(1.1);}
    100%{transform:scale(1);}
}

@media(max-width:850px){
    .sidebar{
        display:none;
    }

    .chat-section{
        left:0;
    }

    .bubble{
        max-width:85%;
    }
}
</style>
</head>

<body>

<div class="main-app">

<aside class="sidebar">

    <div class="profile">
        <div class="profile-icon">🤖</div>
        <h2>AIVA</h2>
        <p>AI Personal Assistant</p>
    </div>

    <button class="new-chat" onclick="clearChat()">➕ New Chat</button>

    <div class="section">
        <h3>🔗 URL to Video</h3>
        <div class="video-box">
            <input type="text" id="property-url" placeholder="Paste property URL here">
            <button onclick="generateVideoFromUrl()">Generate from URL</button>
        </div>
    </div>

    <div class="section">
        <h3>🎬 Images to Video</h3>
        <div class="video-box">
            <input type="file" id="video-images" accept=".png,.jpg,.jpeg,.webp" multiple>
            <textarea id="voice-text" placeholder="Optional voice-over text. Leave blank for auto direct Hindi walkthrough."></textarea>
            <select id="voice-lang">
                <option value="hi">Hindi Voice</option>
                <option value="en">English Voice</option>
            </select>
            <button onclick="generateVideo()">Generate Video</button>
        </div>
    </div>

    <div class="section">
        <h3>🌐 Language</h3>
        <select id="language">
            <option>English</option>
            <option>Hindi</option>
            <option>Hinglish</option>
        </select>
    </div>

    <div class="section">
        <h3>🎯 Assistant Mode</h3>
        <select id="mode">
            <option>General</option>
            <option>Study Help</option>
            <option>Coding Help</option>
            <option>Career Guide</option>
            <option>Creative Writing</option>
            <option>Interview Prep</option>
        </select>
    </div>

    <div class="section">
        <h3>⚡ Quick Tools</h3>
        <button onclick="quickMessage('Tell me today weather')">🌤 Weather</button>
        <button onclick="quickMessage('Tell me latest news')">📰 News</button>
        <button onclick="quickMessage('Help me with coding')">💻 Coding</button>
        <button onclick="quickMessage('Help me study this topic')">🎓 Study</button>
    </div>

    <div class="section">
        <h3>⚙ Settings</h3>
        <button onclick="toggleTheme()">🌙 Dark / Light</button>
        <button onclick="clearChat()">🧹 Clear Chat</button>
        <button onclick="quickMessage('Tell me about AIVA')">ℹ About AIVA</button>
    </div>

</aside>

<section class="chat-section">

    <header class="top-header">
        <h1>🤖 AIVA</h1>
        <p>Your smart AI assistant for study, coding, career and daily help</p>
    </header>

    <div class="quick-actions">
        <button onclick="quickMessage('Explain AI in simple words')">✨ Explain AI</button>
        <button onclick="quickMessage('Help me write LinkedIn bio')">💼 LinkedIn Bio</button>
        <button onclick="quickMessage('Give me Python project idea')">🐍 Python Project</button>
        <button onclick="quickMessage('Prepare me for interview')">🎤 Interview</button>
    </div>

    <main id="chat-box">
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">
                👋 Hi! How can I help you today?
                <div class="time">AIVA</div>
            </div>
        </div>
    </main>

    <div class="input-area">
        <input type="file" id="file-input" accept=".pdf,.docx,.png,.jpg,.jpeg" multiple hidden>
        <button class="send-btn" onclick="openFileUpload()" title="Upload file">➕</button>
        <input type="text" id="user-input" placeholder="Ask anything..." autocomplete="off">
        <button class="send-btn" id="mic-btn" onclick="startVoiceInput()" title="Voice input">🎤</button>
        <button class="send-btn" onclick="sendMessage()">➤</button>
    </div>

</section>

</div>

<script>
const chatBox = document.getElementById("chat-box");
const userInput = document.getElementById("user-input");
const micBtn = document.getElementById("mic-btn");

function getTime(){
    return new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
}

function addMessage(text, sender){
    const msg = document.createElement("div");
    msg.className = sender === "user" ? "message user-message" : "message bot-message";

    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.innerText = sender === "user" ? "👤" : "🤖";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = text;

    const time = document.createElement("div");
    time.className = "time";
    time.innerText = sender === "user" ? "You • " + getTime() : "AIVA • " + getTime();

    bubble.appendChild(time);
    msg.appendChild(avatar);
    msg.appendChild(bubble);

    chatBox.appendChild(msg);
    chatBox.scrollTop = chatBox.scrollHeight;
}

async function clearChat(){
    stopSpeaking();

    await fetch("/clear", {
        method:"POST",
        headers:{"Content-Type":"application/json"}
    });

    chatBox.innerHTML = `
        <div class="message bot-message">
            <span class="avatar">🤖</span>
            <div class="bubble">
                👋 Hi! How can I help you today?
                <div class="time">AIVA</div>
            </div>
        </div>
    `;
}

function quickMessage(text){
    userInput.value = text;
    sendMessage();
}

async function sendMessage(){
    const text = userInput.value.trim();
    if(!text) return;

    addMessage(text, "user");
    userInput.value = "";

    addMessage("AIVA is thinking...", "bot");

    try{
        const response = await fetch("/chat", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({message:text})
        });

        const data = await response.json();

        const lastBubble = chatBox.lastChild.querySelector(".bubble");
        lastBubble.innerHTML = data.reply;

        const time = document.createElement("div");
        time.className = "time";
        time.innerText = "AIVA • " + getTime();
        lastBubble.appendChild(time);

        speakAIVA(data.reply, text);

    }catch(error){
        const lastBubble = chatBox.lastChild.querySelector(".bubble");
        lastBubble.innerText = "Sorry, something went wrong.";
    }
}

async function generateVideo(){
    const files = document.getElementById("video-images").files;
    const voiceText = document.getElementById("voice-text").value.trim();
    const voiceLang = document.getElementById("voice-lang").value;

    if(files.length === 0){
        alert("Please select images first.");
        return;
    }

    addMessage("🎬 Creating direct full-screen walkthrough video... please wait.", "bot");

    const formData = new FormData();
    for(let i = 0; i < files.length; i++){
        formData.append("images", files[i]);
    }

    formData.append("voice_text", voiceText);
    formData.append("voice_lang", voiceLang);

    try{
        const response = await fetch("/create-video", {
            method:"POST",
            body:formData
        });

        const data = await response.json();

        if(data.success){
            addMessage(`✅ Video created successfully!<br><br><b>Images used:</b> ${data.image_count}<br><br><b>Voice-over:</b><br>${data.voice_text}<br><br><a href="${data.video_url}" target="_blank">⬇ Download Video</a>`, "bot");
        }else{
            addMessage("❌ Video error: " + data.error, "bot");
        }

    }catch(error){
        addMessage("❌ Something went wrong while creating video.", "bot");
    }
}

async function generateVideoFromUrl(){
    const url = document.getElementById("property-url").value.trim();

    if(!url){
        alert("Please paste property URL first.");
        return;
    }

    addMessage("🔗 Opening URL with Chrome, fetching gallery images and creating full-screen video... please wait.", "bot");

    try{
        const response = await fetch("/create-video-from-url", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({url:url})
        });

        const data = await response.json();

        if(data.success){
            addMessage(`✅ Property video created successfully!<br><br><b>Images used:</b> ${data.image_count}<br><b>Image links found:</b> ${data.found_image_links}<br><br><b>Location:</b> ${data.location}<br><br><b>Title:</b> ${data.title}<br><br><b>Voice-over:</b><br>${data.voice_text}<br><br><a href="${data.video_url}" target="_blank">⬇ Download Video</a>`, "bot");
        }else{
            addMessage("❌ URL video error: " + data.error, "bot");
        }

    }catch(error){
        addMessage("❌ Something went wrong while creating video from URL.", "bot");
    }
}

userInput.addEventListener("keydown", function(e){
    if(e.key === "Enter"){
        sendMessage();
    }
});

function toggleTheme(){
    document.body.classList.toggle("dark");
}

function openFileUpload(){
    document.getElementById("file-input").click();
}

function detectVoiceLang(text){
    const hindiRegex = /[\\u0900-\\u097F]/;
    const lower = text.toLowerCase();
    const hinglishWords = ["kya", "kaise", "hai", "batao", "mujhe", "karna", "nahi", "haan"];

    if(hindiRegex.test(text)) return "hi-IN";
    if(hinglishWords.some(w => lower.includes(w))) return "hi-IN";
    return "en-IN";
}

function speakAIVA(reply, userText){
    stopSpeaking();

    const cleanReply = reply.replace(/<[^>]*>/g, "");

    const speech = new SpeechSynthesisUtterance(cleanReply);
    speech.lang = detectVoiceLang(userText);
    speech.rate = 1;
    speech.pitch = 1;
    speech.volume = 1;

    window.speechSynthesis.speak(speech);
}

function stopSpeaking(){
    window.speechSynthesis.cancel();
}

function startVoiceInput(){
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if(!SpeechRecognition){
        alert("Voice input is supported in Google Chrome only.");
        return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = "hi-IN";
    recognition.continuous = false;
    recognition.interimResults = false;

    micBtn.classList.add("mic-listening");
    micBtn.innerText = "🔴";

    recognition.start();

    recognition.onresult = function(event){
        const voiceText = event.results[0][0].transcript;
        userInput.value = voiceText;
        sendMessage();
    };

    recognition.onend = function(){
        micBtn.classList.remove("mic-listening");
        micBtn.innerText = "🎤";
    };

    recognition.onerror = function(){
        micBtn.classList.remove("mic-listening");
        micBtn.innerText = "🎤";
    };
}
</script>

</body>
</html>
""")


# -------------------------
# Routes
# -------------------------

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"reply": "Please type or speak something."})

    try:
        if is_news_query(user_message):
            reply = get_latest_news(user_message)
            return jsonify({"reply": reply})

        if is_weather_query(user_message):
            reply = get_weather(user_message)
            return jsonify({"reply": reply})

        reply = ask_aiva(user_message)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"reply": f"Error: {str(e)}"})


@app.route("/create-video", methods=["POST"])
def create_video():
    try:
        images = request.files.getlist("images")
        voice_text = request.form.get("voice_text", "")
        voice_lang = request.form.get("voice_lang", "hi")

        if not images:
            return jsonify({"success": False, "error": "No images uploaded"})

        image_paths = []

        for image in images:
            filename = secure_filename(image.filename)
            unique_name = f"{uuid.uuid4()}_{filename}"
            image_path = os.path.join(UPLOAD_FOLDER, unique_name)
            image.save(image_path)

            # Convert any uploaded image to clean jpg.
            try:
                img = Image.open(image_path).convert("RGB")
                jpg_path = os.path.join(UPLOAD_FOLDER, f"upload_{uuid.uuid4()}.jpg")
                img.save(jpg_path, quality=95)
                os.remove(image_path)
                image_paths.append(jpg_path)
            except Exception:
                image_paths.append(image_path)

        if not voice_text:
            property_text = """
Location: Jaipur
Images show property walkthrough with living area, kitchen, bedroom and balcony.
"""
            voice_text = generate_property_voiceover(property_text, len(image_paths))

        voice_path = create_voice_file(voice_text, voice_lang)

        video_name = f"{uuid.uuid4()}.mp4"
        output_video_path = os.path.join(VIDEO_FOLDER, video_name)

        create_video_from_images(image_paths, voice_path, output_video_path)

        return jsonify({
            "success": True,
            "video_url": f"/download-video/{video_name}",
            "voice_text": voice_text,
            "image_count": len(image_paths),
            "images_used": len(image_paths)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/create-video-from-url", methods=["POST"])
def create_video_from_url():
    try:
        data = request.get_json()
        url = data.get("url", "")

        if not url:
            return jsonify({"success": False, "error": "URL is missing"})

        details = fetch_property_details(url)

        if not details["images"]:
            return jsonify({
                "success": False,
                "error": "No images found on this URL. This site may block scraping or require login."
            })

        image_paths = download_property_images(details["images"])

        if not image_paths:
            return jsonify({
                "success": False,
                "error": "Image links found but images could not download. Site may block hotlinking."
            })

        # Use up to 25 images. Timing stays 5 sec per image:
        # 12 images = 60 sec, 15 images = 75 sec, 20 images = 100 sec, 25 images = 125 sec.
        if len(image_paths) > 25:
            image_paths = image_paths[:25]

        voice_text = generate_property_voiceover(details["property_text"], len(image_paths))

        voice_path = create_voice_file(voice_text, "hi")

        video_name = f"{uuid.uuid4()}.mp4"
        output_video_path = os.path.join(VIDEO_FOLDER, video_name)

        create_video_from_images(image_paths, voice_path, output_video_path)

        return jsonify({
            "success": True,
            "title": details["title"],
            "location": details["location"],
            "video_url": f"/download-video/{video_name}",
            "voice_text": voice_text,
            "image_count": len(image_paths),
            "images_used": len(image_paths),
            "found_image_links": len(details["images"]),
            "images_found": len(details["images"])
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/download-video/<filename>")
def download_video(filename):
    response = send_from_directory(VIDEO_FOLDER, filename, as_attachment=True)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/clear", methods=["POST"])
def clear_memory():
    try:
        requests.post("http://127.0.0.1:8001/clear", timeout=10)
    except Exception:
        pass

    return jsonify({"message": "Memory cleared"})


if __name__ == "__main__":
    app.run(debug=True)
