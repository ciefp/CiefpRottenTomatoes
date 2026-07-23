# -*- coding: utf-8 -*-
import os
import re
import json
import html as _html
import ssl
import time
import threading
import traceback
import urllib.request
import urllib.parse
import math

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.config import config, ConfigSubsection, ConfigYesNo, ConfigSelection
from Screens.Screen import Screen
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from enigma import eTimer, ePicLoad, getDesktop
from Plugins.Plugin import PluginDescriptor


PLUGIN_NAME = "CiefpRottenTomatoes"
PLUGIN_VERSION = "1.4"
BASE = "https://www.rottentomatoes.com"


CACHE_DIR = "/tmp/CiefpRottenTomatoes"
CACHE_POSTERS = os.path.join(CACHE_DIR, "posters")
CACHE_PAGES = os.path.join(CACHE_DIR, "pages")
DEBUG_LOG = os.path.join(CACHE_DIR, "debug.log")

BROWSE_PAGE_SIZE = 28   # RT tipično šalje 28-32 po "load more"
BROWSE_MAX_ITEMS = 150  # tvoj limit
LOAD_MORE_LABEL = ">> Load more..."

PLUGIN_PATH = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER_IMG = os.path.join(PLUGIN_PATH, "placeholder.png")

# Fallback placeholder ako nema slike
if not os.path.exists(PLACEHOLDER_IMG):
    PLACEHOLDER_IMG = "/usr/share/enigma2/skin_default/noprev.png"

config.plugins.ciefprt = ConfigSubsection()
config.plugins.ciefprt.cache_enabled = ConfigYesNo(default=True)
config.plugins.ciefprt.auto_epg = ConfigYesNo(default=True)
config.plugins.ciefprt.max_items = ConfigSelection(
    default="150",
    choices=[("50","50"), ("100","100"), ("150","150"), ("200","200"), ("300","300")]
)
config.plugins.ciefprt.youtube_search = ConfigYesNo(default=True)
config.plugins.ciefprt.player = ConfigSelection(  # NOVO
    default="movieplayer",
    choices=[("movieplayer", "Movie Player"), ("browser", "External Browser"), ("download", "Download & Play")]
)
def ensure_dirs():
    for p in (CACHE_DIR, CACHE_POSTERS, CACHE_PAGES):
        if not os.path.exists(p):
            try:
                os.makedirs(p)
            except:
                pass


def dlog(msg):
    try:
        ensure_dirs()
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(DEBUG_LOG, "a", encoding="utf-8", errors="ignore") as f:
            f.write("[%s] %s\n" % (ts, msg))
    except:
        pass


def clear_debug_log():
    try:
        if os.path.exists(DEBUG_LOG):
            os.remove(DEBUG_LOG)
    except:
        pass


def tail_debug_log(lines=80):
    try:
        if not os.path.exists(DEBUG_LOG):
            return "debug.log not found"
        with open(DEBUG_LOG, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read().splitlines()
        data = data[-lines:]
        return "\n".join(data) if data else "(empty)"
    except Exception as e:
        return "error reading log: %s" % e


def get_cache_size():
    """Calculate total cache size in MB"""
    try:
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(CACHE_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total_size += os.path.getsize(fp)
        return total_size / (1024 * 1024)  # Convert to MB
    except:
        return 0


def ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def http_get(url, timeout=8):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Enigma2; CiefpRottenTomatoes)")
    req.add_header("Accept", "*/*")
    req.add_header("Referer", BASE + "/")
    req.add_header("Origin", BASE)
    try:
        with urllib.request.urlopen(req, context=ssl_ctx(), timeout=timeout) as r:
            return r.read()
    except Exception as e:
        dlog(f"HTTP GET failed for {url}: {e}")
        raise


def search_youtube_trailer(query, year=""):
    """Search YouTube for trailer by title and year using yt-dlp"""
    try:
        import subprocess
        import json

        # Kreiraj search query
        search_query = f"{query} official trailer"
        if year:
            search_query += f" {year}"

        dlog(f"YT-SEARCH: Searching for '{search_query}'...")

        # Koristi yt-dlp za pretragu YouTube-a
        cmd = [
            'yt-dlp',
            '--flat-playlist',
            '--dump-json',
            '--no-warnings',
            '--quiet',
            f'ytsearch5:{search_query}'
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            # Parsiraj JSON linije (svaka linija je jedan video)
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    video_id = data.get('id')
                    if video_id:
                        title = data.get('title', '').lower()
                        # Daj prednost onima koji imaju "trailer" u naslovu
                        if 'trailer' in title or 'official' in title:
                            url = f"https://www.youtube.com/watch?v={video_id}"
                            dlog(f"YT-SEARCH: Found: {url}")
                            return url
                except json.JSONDecodeError:
                    continue

            # Ako nema sa "trailer", uzmi prvi rezultat
            lines = result.stdout.strip().split('\n')
            if lines:
                try:
                    data = json.loads(lines[0])
                    video_id = data.get('id')
                    if video_id:
                        url = f"https://www.youtube.com/watch?v={video_id}"
                        dlog(f"YT-SEARCH: Found (fallback): {url}")
                        return url
                except:
                    pass

        dlog("YT-SEARCH: No results found")
        return None

    except Exception as e:
        dlog(f"YT-SEARCH error: {e}")
        return None

def play_video_with_movieplayer(session, url, title="Trailer"):
    """Play video using Movie Player (enigma2 movie player)"""
    try:
        from enigma import eServiceCenter, eServiceReference

        # Kreiraj service referencu za movie player
        # 4097 = servicemp3 (podržava HLS, HTTP streamove)
        service_ref = eServiceReference(
            4097,  # Service type for stream
            0,
            url
        )

        # Postavi ime za prikaz
        service_ref.setName(title)

        if service_ref:
            session.nav.playService(service_ref)
            return True
        return False
    except Exception as e:
        dlog(f"MoviePlayer error: {e}")
        return False

def fetch_trailer_url(tv_movie_url):
    """Fetch trailer URL from Rotten Tomatoes internal API"""
    try:
        # Prvo dohvatimo HTML da izvučemo ID
        html = http_get(tv_movie_url, timeout=10)
        html_str = html.decode("utf-8", "ignore")

        # Pokušaj pronaći ID u JSON-LD ili meta tagovima
        # 1. Pokušaj iz media-scorecard-json
        scorecard_match = re.search(
            r'<script[^>]+id="media-scorecard-json"[^>]*>\s*({.*?})\s*</script>',
            html_str, re.S | re.I
        )

        if scorecard_match:
            try:
                data = json.loads(scorecard_match.group(1))
                # Pokušaj dobiti video ID
                video_id = data.get("videoId") or data.get("id")
                if video_id:
                    return fetch_trailer_by_id(video_id)
            except:
                pass

        # 2. Pokušaj pronaći u JSON-LD
        ld_match = re.search(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html_str, re.S | re.I
        )
        if ld_match:
            try:
                data = json.loads(ld_match.group(1))
                if isinstance(data, dict):
                    # Pokušaj različite putanje
                    for key in ["video", "trailer", "videoId", "id"]:
                        if key in data:
                            video_data = data[key]
                            if isinstance(video_data, dict):
                                video_id = video_data.get("contentUrl") or video_data.get("url") or video_data.get(
                                    "embedUrl")
                                if video_id and ("m3u8" in video_id or "ts" in video_id):
                                    return video_id
                            elif isinstance(video_data, str):
                                if "m3u8" in video_data or "ts" in video_data:
                                    return video_data
            except:
                pass

        # 3. Pokušaj pronaći u data-attributes
        data_match = re.search(
            r'<[^>]+data-video-id="([^"]+)"',
            html_str, re.I
        )
        if data_match:
            video_id = data_match.group(1)
            return fetch_trailer_by_id(video_id)

        # 4. Pokušaj pronaći bilo koji URL koji sadrži video
        video_match = re.search(
            r'(https?://[^\s"\']+video[^\s"\']*\.(?:m3u8|ts|mp4)[^\s"\']*)',
            html_str, re.I
        )
        if video_match:
            return video_match.group(1)

        dlog("TRAILER: No video ID found in HTML")
        return None

    except Exception as e:
        dlog(f"TRAILER API error: {e}")
        return None


def fetch_trailer_by_id(video_id):
    """Fetch trailer URL using video ID from RT API"""
    try:
        # RT koristi nekoliko mogućih API endpointova
        api_urls = [
            f"https://www.rottentomatoes.com/api/private/v1.0/video/{video_id}",
            f"https://www.rottentomatoes.com/api/private/v2.0/video/{video_id}",
            f"https://www.rottentomatoes.com/api/private/v3.0/video/{video_id}",
            f"https://www.rottentomatoes.com/api/private/v1.0/video/stream/{video_id}",
        ]

        for api_url in api_urls:
            try:
                dlog(f"TRAILER: Trying API: {api_url}")
                raw = http_get(api_url, timeout=8)
                data = json.loads(raw.decode("utf-8", "ignore"))

                # Pokušaj pronaći video URL u odgovoru
                if isinstance(data, dict):
                    # Različite moguće putanje
                    for path in ["video", "stream", "url", "contentUrl", "embedUrl", "sources"]:
                        if path in data:
                            video_data = data[path]
                            if isinstance(video_data, list):
                                for item in video_data:
                                    if isinstance(item, dict):
                                        url = item.get("url") or item.get("src") or item.get("contentUrl")
                                        if url and (".m3u8" in url or ".ts" in url):
                                            dlog(f"TRAILER: Found via API: {url}")
                                            return url
                            elif isinstance(video_data, str):
                                if ".m3u8" in video_data or ".ts" in video_data:
                                    dlog(f"TRAILER: Found via API: {video_data}")
                                    return video_data

                    # Pokušaj pronaći u nested strukturi
                    if "data" in data:
                        return fetch_trailer_by_id_from_data(data["data"])

            except Exception as e:
                dlog(f"TRAILER API {api_url} failed: {e}")
                continue

        return None

    except Exception as e:
        dlog(f"TRAILER fetch error: {e}")
        return None

def play_youtube_with_ytdlp(url):
    """Get YouTube stream URL using yt-dlp"""
    try:
        import subprocess
        import json

        # Prvo probaj dobiti stream URL
        cmd = ['yt-dlp', '-g', '-f', 'best[height<=720]', url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            stream_url = result.stdout.strip().split('\n')[0]
            if stream_url:
                dlog(f"YT-DLP: Got stream URL: {stream_url[:100]}...")
                return stream_url
        return None
    except Exception as e:
        dlog(f"YT-DLP error: {e}")
        return None
    

def fetch_trailer_by_id_from_data(data):
    """Helper function to extract trailer from nested data"""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ["url", "src", "contentUrl", "embedUrl", "videoUrl"]:
                if isinstance(value, str) and (".m3u8" in value or ".ts" in value):
                    return value
            if isinstance(value, (dict, list)):
                result = fetch_trailer_by_id_from_data(value)
                if result:
                    return result
    elif isinstance(data, list):
        for item in data:
            result = fetch_trailer_by_id_from_data(item)
            if result:
                return result
    return None

def cache_key(url):
    return re.sub(r"[^a-zA-Z0-9]+", "_", url).strip("_")


def get_cached_page(url, ttl=300):
    if not config.plugins.ciefprt.cache_enabled.value:
        return None
    ensure_dirs()
    fn = os.path.join(CACHE_PAGES, cache_key(url) + ".html")
    try:
        if os.path.exists(fn) and (time.time() - os.path.getmtime(fn) <= ttl):
            with open(fn, "rb") as f:
                return f.read()
    except:
        pass
    return None


def set_cached_page(url, data):
    if not config.plugins.ciefprt.cache_enabled.value:
        return
    ensure_dirs()
    fn = os.path.join(CACHE_PAGES, cache_key(url) + ".html")
    try:
        with open(fn, "wb") as f:
            f.write(data)
    except:
        pass


def clear_cache():
    try:
        for root, dirs, files in os.walk(CACHE_DIR, topdown=False):
            for fn in files:
                try:
                    if fn != os.path.basename(DEBUG_LOG):
                        os.remove(os.path.join(root, fn))
                except:
                    pass
            for dn in dirs:
                try:
                    os.rmdir(os.path.join(root, dn))
                except:
                    pass
    except:
        pass
    ensure_dirs()
def normalize_rt_url(u):
    if not u:
        return None

    u = u.strip()

    # --- FIX za //www.rottentomatoes.com/...
    if u.startswith("//"):
        return "https:" + u

    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return BASE + u
    return BASE + "/" + u

# ---------- Search functions ----------
def search_rt(query, search_type="movie"):
    """Search Rotten Tomatoes using their API/autocomplete"""
    # Očisti query prije slanja
    clean_query = re.sub(r'[:;!?]', ' ', query)
    clean_query = re.sub(r'\s+', ' ', clean_query).strip()

    search_url = f"{BASE}/api/autocomplete?v=1&query={urllib.parse.quote(clean_query)}"

    try:
        raw = http_get(search_url, timeout=10)
        data = json.loads(raw.decode("utf-8", "ignore"))
        results = []
        
        # Process movies
        if search_type == "movie" and "movies" in data:
            limit = int(config.plugins.ciefprt.max_items.value)
            for movie in data["movies"][:limit]:
                name = movie.get("name", "").strip()
                year = movie.get("year", "")
                url = movie.get("url", "")
                image = movie.get("image", "")
                
                if name and url:
                    display_name = f"{name} ({year})" if year else name
                    results.append({
                        "name": display_name,
                        "url": normalize_rt_url(url),
                        "image": image,
                        "year": year
                    })
        
        # Process TV shows
        elif search_type == "tv" and "tvSeries" in data:
            limit = int(config.plugins.ciefprt.max_items.value)
            for tv in data["tvSeries"][:limit]: 
                name = tv.get("name", "").strip()
                start_year = tv.get("startYear", "")
                url = tv.get("url", "")
                image = tv.get("image", "")
                
                if name and url:
                    display_name = f"{name} ({start_year})" if start_year else name
                    results.append({
                        "name": display_name,
                        "url": normalize_rt_url(url),
                        "image": image,
                        "year": start_year
                    })
                    # Ako API vrati prazan rezultat, probaj fallback (search page)
                    if not results:
                        return search_rt_fallback(query, search_type)

                    return results

        # ... ostatak koda ...
    except Exception as e:
        dlog(f"SEARCH API error: {e}")
        return search_rt_fallback(clean_query, search_type)


def search_rt_fallback(query, search_type="movie"):
    """Fallback search using RT search page - parses Shadow DOM content"""
    try:
        # Očisti query - pretvori & u and
        clean_query = re.sub(r'[&]', 'and', query)
        clean_query = re.sub(r'[:;!?]', ' ', clean_query)
        clean_query = re.sub(r'\s+', ' ', clean_query).strip()

        search_url = f"{BASE}/search?search={urllib.parse.quote(clean_query)}"
        dlog(f"SEARCH: Fallback URL: {search_url}")

        raw = http_get(search_url, timeout=10)
        html = raw.decode("utf-8", "ignore")

        results = []

        # --- METODA 1: Traži search-page-media-row elemente ---
        # Ovi elementi sadrže podatke u atributima
        for m in re.finditer(
                r'<search-page-media-row[^>]*?'
                r'data-qa="data-row"[^>]*?'
                r'release-year="([^"]*)"[^>]*?'
                r'tomatometer-score="([^"]*)"[^>]*?'
                r'tomatometer-sentiment="([^"]*)"[^>]*?'
                r'>(.*?)</search-page-media-row>',
                html, re.I | re.S
        ):
            release_year = m.group(1).strip()
            tomatometer = m.group(2).strip()
            sentiment = m.group(3).strip()
            inner_html = m.group(4)

            # Ekstrakcija URL-a - traži <a href="..."> unutar
            href_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>', inner_html, re.I)
            if not href_match:
                continue
            href = href_match.group(1).strip()

            # Filtriranje po tipu
            if search_type == "movie" and not href.startswith("/m/"):
                continue
            if search_type == "tv" and not href.startswith("/tv/"):
                continue

            # Ekstrakcija naslova
            title = ""
            title_match = re.search(r'<a[^>]+data-qa="info-name"[^>]*>(.*?)</a>', inner_html, re.I | re.S)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                title = re.sub(r'\s+', ' ', title)

            # Ako nema data-qa, probaj drugi način
            if not title:
                title_match = re.search(r'<a[^>]+slot="title"[^>]*>(.*?)</a>', inner_html, re.I | re.S)
                if title_match:
                    title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                    title = re.sub(r'\s+', ' ', title)

            if not title:
                continue

            # Godina iz release-year atributa ili iz naslova
            year = release_year
            if not year:
                year_match = re.search(r'\((\d{4})\)', title)
                if year_match:
                    year = year_match.group(1)
                    title = re.sub(r'\s*\(\d{4}\)\s*$', '', title).strip()

            # Ekstrakcija slike
            image = ""
            img_match = re.search(r'<img[^>]+src="([^"]+)"', inner_html, re.I)
            if img_match:
                image = img_match.group(1).strip()

            display_name = f"{title} ({year})" if year else title
            results.append({
                "name": display_name,
                "url": normalize_rt_url(href),
                "image": image,
                "year": year
            })
            dlog(f"SEARCH: Found: {display_name} -> {href}")

        # --- METODA 2: Ako nema rezultata, probaj sa JSON-LD ---
        if not results:
            dlog("SEARCH: No search-page-media-row found, trying JSON-LD...")
            for script in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S | re.I):
                try:
                    data = json.loads(script.strip())
                    if isinstance(data, dict):
                        # Pokušaj pronaći ItemList
                        if data.get("@type") == "ItemList":
                            items = data.get("itemListElement", [])
                            for item in items:
                                if isinstance(item, dict):
                                    name = item.get("name", "")
                                    url = item.get("url", "")
                                    if name and url:
                                        if search_type == "movie" and not url.startswith("/m/"):
                                            continue
                                        if search_type == "tv" and not url.startswith("/tv/"):
                                            continue
                                        results.append({
                                            "name": name,
                                            "url": normalize_rt_url(url),
                                            "image": "",
                                            "year": ""
                                        })
                                        dlog(f"SEARCH: Found via JSON-LD: {name}")
                except:
                    pass

        # --- METODA 3: Konačni fallback - direktno iz URL-a ---
        if not results:
            dlog("SEARCH: Trying direct URL construction...")
            # Pokušaj s različitim formatima URL-a
            possible_slugs = [
                clean_query.lower().replace(' ', '_'),
                clean_query.lower().replace(' ', '-'),
                clean_query.lower().replace(' ', ''),
            ]
            # Dodaj i specifične varijante za Mr. & Mrs. Smith
            if "mr" in clean_query.lower() and "mrs" in clean_query.lower():
                possible_slugs.extend([
                    "mr_and_mrs_smith",
                    "mr_and_mrs_smith_2005",
                    "mr_and_mrs_smith_2024",
                    "1014325-mr_and_mrs_smith"
                ])

            for slug in possible_slugs:
                test_url = f"{BASE}/m/{slug}"
                try:
                    req = urllib.request.Request(test_url)
                    req.add_header("User-Agent", "Mozilla/5.0")
                    req.get_method = lambda: 'HEAD'
                    with urllib.request.urlopen(req, context=ssl_ctx(), timeout=5) as r:
                        if r.getcode() == 200:
                            results.append({
                                "name": clean_query,
                                "url": test_url,
                                "image": "",
                                "year": ""
                            })
                            dlog(f"SEARCH: Found via direct URL: {test_url}")
                            break
                except:
                    continue

        dlog(f"SEARCH: Fallback found {len(results)} results")
        return results[:20]

    except Exception as e:
        dlog(f"SEARCH fallback error: {e}")
        import traceback
        dlog(traceback.format_exc())
        return []


def parse_browse_api_page(browse_url, page=1, limit=BROWSE_PAGE_SIZE):
    """
    Load more za BASE /browse/... preko HTML ?page=N.
    RT često vraća kumulativnu listu (page=2 ima i page=1 + još),
    zato ovde vraćamo SVE stavke sa te stranice.
    """
    try:
        parts = urllib.parse.urlsplit(browse_url)
        q = urllib.parse.parse_qs(parts.query)

        if page > 1:
            q["page"] = [str(page)]
        else:
            q.pop("page", None)

        new_query = urllib.parse.urlencode(q, doseq=True)
        paged_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))
        dlog("LOAD MORE URL: %s" % paged_url)

        return parse_browse(paged_url) or []
    except Exception as e:
        dlog(f"BROWSE HTML page error: {e}")
        return []

def parse_search_page(html, search_type="movie"):
    results = []

    for m in re.finditer(r'(<search-results-item\b[^>]*>.*?</search-results-item>)', html, re.S | re.I):
        block = m.group(1)

        href_m = re.search(r'\bhref="([^"]+)"', block, re.I)
        if not href_m:
            continue
        href = href_m.group(1).strip()

        # filtriranje tipa po URL-u
        if search_type == "movie":
            if not href.startswith("/m/"):
                continue
        else:
            if not href.startswith("/tv/"):
                continue

        # naslov
        title = ""
        t = re.search(r'<rt-text[^>]+slot="title"[^>]*>\s*([^<]+)\s*</rt-text>', block, re.I)
        if t:
            title = t.group(1).strip()
        if not title:
            continue

        year = ""
        ym = re.search(r'\b(19\d{2}|20\d{2})\b', block)
        if ym:
            year = ym.group(1)

        image = ""
        im = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
        if im:
            image = im.group(1).strip()

        display_name = f"{title} ({year})" if year else title

        results.append({
            "name": display_name,
            "url": normalize_rt_url(href),
            "image": image,
            "year": year
        })

    return results

# ---------- Browse parser (JSON-LD ItemList) ----------
def extract_jsonld_itemlist(html_text):
    blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text, flags=re.S | re.I
    )
    for b in blocks:
        b = b.strip()
        try:
            data = json.loads(b)
        except:
            continue

        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if isinstance(obj, dict) and obj.get("@type") == "ItemList":
                ile = obj.get("itemListElement")
                if isinstance(ile, dict) and "itemListElement" in ile:
                    return ile.get("itemListElement", [])
                if isinstance(ile, list):
                    return ile
    return []


def parse_editorial_guide(url):
    raw = get_cached_page(url) or http_get(url)
    set_cached_page(url, raw)
    html = raw.decode("utf-8", "ignore")

    out = []
    seen_urls = set()

    # --- Pronađi sve block-countdown divove ---
    # Ovo je glavni kontejner za svaku stavku na editorial stranicama
    block_re = re.compile(
        r'<div[^>]+id="countdown-index-\d+"[^>]+class="[^"]*block-countdown[^"]*"[^>]*>(.*?)</div>\s*(?:<br>|</div>|$)',
        re.I | re.S
    )

    for block_match in block_re.finditer(html):
        block = block_match.group(1)

        # --- Izdvoji link (href) ---
        href_match = re.search(
            r'<a[^>]+class="[^"]*poster-wrapper[^"]*"[^>]+href="([^"]+)"',
            block, re.I
        )
        if not href_match:
            continue
        href = (href_match.group(1) or "").strip()
        if not href:
            continue

        # --- Izdvoji sliku ---
        img_match = re.search(
            r'<img[^>]+class="[^"]*article_poster[^"]*"[^>]+src="([^"]+)"',
            block, re.I
        )
        img = (img_match.group(1) or "").strip() if img_match else ""

        # --- Izdvoji naslov (meta-title) ---
        title_match = re.search(
            r'<a[^>]+class="[^"]*meta-title[^"]*"[^>]*>(.*?)</a>',
            block, re.I | re.S
        )
        if not title_match:
            continue
        title = (title_match.group(1) or "").strip()
        title = re.sub(r'<[^>]+>', '', title)
        title = re.sub(r'\s+', ' ', title).strip()

        if not title:
            continue

        # --- Izdvoji godine ako postoje u naslovu ---
        year = ""
        year_match = re.search(r'\((\d{4})\)', title)
        if year_match:
            year = year_match.group(1)
            title = re.sub(r'\s*\(\d{4}\)', '', title).strip()

        # --- Normaliziraj URL ---
        item_url = normalize_rt_url(href)

        # --- Spriječi duplikate ---
        if item_url in seen_urls:
            continue
        seen_urls.add(item_url)

        # --- Kreiraj rezultat ---
        if year:
            display_name = f"{title} ({year})"
        else:
            display_name = title

        out.append({
            "name": display_name,
            "url": item_url,
            "image": img
        })

    # --- Ako nismo našli ništa, probaj sa starom metodom (kao fallback) ---
    if not out:
        # Ovdje možeš staviti tvoj stari kod kao fallback
        dlog(f"EDITORIAL: No items found with primary parser, trying fallback...")

        # Pokušaj sa <article> elementima (stari način)
        article_re = re.compile(
            r'<article[^>]*data-rank[^>]*>.*?'
            r'<a[^>]+href="([^"]+)"[^>]*>.*?'
            r'<img[^>]+src="([^"]+)"[^>]*>.*?'
            r'<h[23][^>]*>(.*?)</h[23]>',
            re.I | re.S
        )

        for m in article_re.finditer(html):
            href = (m.group(1) or "").strip()
            img = (m.group(2) or "").strip()
            title = (m.group(3) or "").strip()
            title = re.sub(r'<[^>]+>', '', title)
            title = re.sub(r'\s+', ' ', title).strip()

            if href and title:
                year = ""
                year_match = re.search(r'\((\d{4})\)', title)
                if year_match:
                    year = year_match.group(1)
                    title = re.sub(r'\s*\(\d{4}\)', '', title).strip()

                item_url = normalize_rt_url(href)

                if item_url not in seen_urls:
                    seen_urls.add(item_url)
                    if year:
                        display_name = f"{title} ({year})"
                    else:
                        display_name = title

                    out.append({
                        "name": display_name,
                        "url": item_url,
                        "image": img
                    })

    dlog(f"EDITORIAL: Found {len(out)} items from {url}")
    return out

def parse_browse(url):
    dlog(f"BROWSE: Parsing URL: {url}")

    # --- EDITORIAL fallback ---
    if "editorial.rottentomatoes.com" in (url or ""):
        dlog("BROWSE: Using editorial parser")
        return parse_editorial_guide(url)

    raw = get_cached_page(url) or http_get(url)
    set_cached_page(url, raw)
    html = raw.decode("utf-8", "ignore")

    items = extract_jsonld_itemlist(html)
    out = []

    for it in items:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        item_url = normalize_rt_url(it.get("url"))
        img = it.get("image")

        if name and item_url:
            out.append({
                "name": name,
                "url": item_url,
                "image": img
            })

    return out

# ---------- Detail parser (media-scorecard-json + metadata slots) ----------
def extract_jsonld_movie_tv(html_text):
    """Try to extract Movie/TVSeries JSON-LD (actors, director, creator)."""
    blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text, flags=re.S | re.I
    )

    for b in blocks:
        b = (b or "").strip()
        try:
            data = json.loads(b)
        except:
            continue

        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            t = obj.get("@type")
            if t not in ("Movie", "TVSeries", "TVSeason", "TVEpisode"):
                continue
            return obj
    return None


def extract_title_from_html(html):
    """Extract title and year from RT page HTML"""
    title = ""
    year = ""

    # Pokušaj izvući iz title taga
    title_match = re.search(r'<title>(.*?)</title>', html, re.I)
    if title_match:
        title = title_match.group(1).strip()
        # Očisti " - Rotten Tomatoes" i slično
        title = re.sub(r'\s*[-|]\s*Rotten Tomatoes.*$', '', title, flags=re.I)
        title = re.sub(r'\s*[-|]\s*TV.*$', '', title, flags=re.I)

    # Ako nema title, pokušaj iz og:title
    if not title:
        title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
        if title_match:
            title = title_match.group(1).strip()
            title = re.sub(r'\s*[-|]\s*Rotten Tomatoes.*$', '', title, flags=re.I)

    # Izvuci godinu
    year_match = re.search(r'\((\d{4})\)', html)
    if year_match:
        year = year_match.group(1)
    else:
        # Pokušaj iz title-a
        year_match = re.search(r'\((\d{4})\)', title)
        if year_match:
            year = year_match.group(1)
            title = re.sub(r'\s*\(\d{4}\)\s*$', '', title)

    # Očisti title
    title = title.strip()
    title = re.sub(r'\s+', ' ', title)

    return title, year

def parse_detail(html, detail_url=None):
    info = {
        "mpaa": "",
        "status": "",
        "runtime": "",
        "genres": "",
        "synopsis": "",
        "director": "",
        "cast": "",
        "director_list": [],
        "cast_list": [],
        "poster_url": "",
        "backdrop_url": "",
        "tomatometer": "",
        "critic_count": "",
        "popcorn": "",
        "audience_count": "",
        "trailer_url": "",
        "trailer_type": "",
    }

    # poster fallback (og:image)
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
    if m:
        info["poster_url"] = (m.group(1) or "").strip()

    # Backdrop / Theme (rt-img slot="iconic")
    m = re.search(r'<rt-img[^>]+slot="iconic"[^>]+src="([^"]+)"', html, re.I)
    if m:
        src = (m.group(1) or "").strip()
        parts = [p.strip() for p in src.split(",") if p.strip()]
        if parts:
            info["backdrop_url"] = parts[-1]

    # scores + description
    m = re.search(
        r'<script[^>]+id="media-scorecard-json"[^>]*>\s*({.*?})\s*</script>',
        html, re.S | re.I
    )
    if m:
        try:
            data = json.loads(m.group(1))
            critics = data.get("criticsScore", {}) or {}
            audience = data.get("audienceScore", {}) or {}

            info["tomatometer"] = str(critics.get("scorePercent", "") or "")
            info["critic_count"] = str(critics.get("reviewCount", "") or "")

            info["popcorn"] = str(audience.get("scorePercent", "") or "")
            info["audience_count"] = str(audience.get("bandedRatingCount", "") or audience.get("ratingCount", "") or "")

            if data.get("description"):
                info["synopsis"] = (data.get("description") or "").strip()
        except:
            pass

    # metadata-prop
    props = re.findall(
        r'<rt-text[^>]+slot="metadata-prop"[^>]*>\s*([^<]+)\s*</rt-text>',
        html, flags=re.I
    )
    props = [p.strip() for p in props if p and p.strip()]

    # genres
    genres = re.findall(
        r'<rt-text[^>]+slot="metadata-genre"[^>]*>\s*([^<]+)\s*</rt-text>',
        html, flags=re.I
    )
    genres = [g.strip() for g in genres if g and g.strip()]

    # map props -> fields
    for p in props:
        if re.match(r'^[A-Z0-9][A-Z0-9\-]{0,6}$', p) and not info["mpaa"]:
            info["mpaa"] = p
        elif ("h" in p and "m" in p) or re.match(r"^\d+\s*m$", p, re.I):
            info["runtime"] = p
        elif "playing" in p.lower() or "stream" in p.lower() or "premiere" in p.lower():
            info["status"] = p
    if genres:
        info["genres"] = "/".join(genres)

    # --- Cast & Crew (JSON-LD) ---
    j = extract_jsonld_movie_tv(html)
    if j:
        directors = j.get("director")
        dir_names = []
        if isinstance(directors, dict) and directors.get("name"):
            dir_names = [directors.get("name")]
        elif isinstance(directors, list):
            dir_names = [d.get("name", "") for d in directors if isinstance(d, dict)]
        dir_names = [n for n in dir_names if n]
        info["director_list"] = dir_names
        if dir_names:
            info["director"] = ", ".join(dir_names[:2])

        actors = j.get("actor") or j.get("actors")
        cast_names = []
        if isinstance(actors, dict) and actors.get("name"):
            cast_names = [actors.get("name")]
        elif isinstance(actors, list):
            for a in actors:
                if isinstance(a, dict) and a.get("name"):
                    cast_names.append(a["name"])
        cast_names = [x for x in cast_names if x]
        info["cast_list"] = cast_names
        if cast_names:
            info["cast"] = ", ".join(cast_names[:8])

    # --- Ekstrakcija trejlera ---
    # 1. Video element sa data-sources
    video_match = re.search(
        r'<video[^>]+data-sources="([^"]+)"',
        html, re.I
    )
    if video_match:
        try:
            sources = json.loads(video_match.group(1))
            for src in sources:
                if src.get("type") == "application/x-mpegURL":
                    info["trailer_url"] = src.get("src", "")
                    info["trailer_type"] = "hls"
                    break
        except:
            pass

    # 2. Video tag sa src
    if not info["trailer_url"]:
        video_match = re.search(
            r'<video[^>]+src="([^"]+)"[^>]*>',
            html, re.I
        )
        if video_match:
            url = video_match.group(1)
            if ".m3u8" in url or ".ts" in url:
                info["trailer_url"] = url
                info["trailer_type"] = "hls"

    # 3. Bilo koji .m3u8 ili .ts link
    if not info["trailer_url"]:
        stream_match = re.search(
            r'(https?://[^\s"\']+\.(?:m3u8|ts)[^\s"\']*)',
            html, re.I
        )
        if stream_match:
            info["trailer_url"] = stream_match.group(1)
            info["trailer_type"] = "hls"

    # 4. YouTube trailer
    if not info["trailer_url"] and config.plugins.ciefprt.youtube_search.value:
        dlog("TRAILER: No trailer found, searching YouTube...")
        try:
            title, year = extract_title_from_html(html)
            if title:
                dlog(f"TRAILER: Searching YouTube for '{title}' ({year})...")
                youtube_url = search_youtube_trailer(title, year)
                if youtube_url:
                    info["trailer_url"] = youtube_url
                    info["trailer_type"] = "youtube"
                    dlog(f"TRAILER: Found on YouTube: {youtube_url}")
                else:
                    dlog("TRAILER: No YouTube trailer found")
            else:
                dlog("TRAILER: Could not extract title for YouTube search")
        except Exception as e:
            dlog(f"TRAILER YouTube search error: {e}")

    # 5. API fallback (samo ako imamo detail_url)
    if not info["trailer_url"] and detail_url:
        dlog("TRAILER: No trailer found in HTML, trying API...")
        try:
            trailer_url = fetch_trailer_url(detail_url)
            if trailer_url:
                info["trailer_url"] = trailer_url
                info["trailer_type"] = "hls"
                dlog(f"TRAILER: Found via API: {trailer_url}")
        except Exception as e:
            dlog(f"TRAILER API error: {e}")

    return info

# ---------- EPG functions ----------
def get_current_epg_info(session):
    """Get current EPG information for the playing channel"""
    try:
        from ServiceReference import ServiceReference
        from enigma import eEPGCache

        current_service = session.nav.getCurrentlyPlayingServiceReference()
        if not current_service:
            dlog("EPG: No current service")
            return None

        service_ref = ServiceReference(current_service)
        service_name = service_ref.getServiceName()
        dlog(f"EPG: Service name: {service_name}")

        epgcache = eEPGCache.getInstance()
        if not epgcache:
            dlog("EPG: No EPG cache available")
            return {"title": service_name, "channel": service_name}

        try:
            events = epgcache.lookupEvent(["IBDCT", (current_service.toString(), 0, -1, -1)])
            dlog(f"EPG: Events found: {len(events) if events else 0}")
        except Exception as e:
            dlog(f"EPG: lookupEvent error: {e}")
            events = []

        if events:
            for event in events:
                try:
                    # Podrška za evente sa 5 ili 6 elemenata
                    if len(event) < 5:
                        dlog(f"EPG: Event too short: {len(event)} elements")
                        continue

                    # event[4] je uvijek title (u većini Enigma2 verzija)
                    event_name = event[4] if len(event) > 4 else ""
                    event_desc = event[5] if len(event) > 5 else ""

                    if event_name:
                        clean_title = re.sub(r'\s*\(\d{4}\)', '', event_name)
                        clean_title = re.sub(r'\s*-\s*.*$', '', clean_title)
                        clean_title = clean_title.strip()

                        # Preskoči ako je title prazan ili samo naziv kanala
                        if clean_title and clean_title != service_name:
                            dlog(f"EPG: Found event: {clean_title}")
                            return {
                                "title": clean_title,
                                "original_title": event_name,
                                "description": event_desc or "",
                                "channel": service_name
                            }
                except Exception as e:
                    dlog(f"EPG: Event parsing error: {e}")
                    continue

        # Fallback - koristi samo naziv kanala
        dlog("EPG: Using fallback - service name only")
        return {"title": service_name, "channel": service_name}

    except Exception as e:
        dlog(f"EPG error: {e}")
        return None

# ---------- UI ----------
class CiefpRTMain(Screen):
    skin = """
    <screen position="center,center" size="1920,1080" backgroundColor="#011a2e" >
        <widget name="status" position="60,40" size="1800,40" font="Regular;30" transparent="1" />

        <widget name="title" position="60,100" size="1200,55" font="Regular;44" transparent="1" foregroundColor="#00ff6e" />
        <widget name="meta" position="60,160" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00e1ff" />
        
        <widget name="score_tomo" position="60,210" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00FF4040" />
        <widget name="score_pop"  position="60,250" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00FFD84A" />
        <ePixmap position="200,350" size="1520,350" zPosition="1" backgroundColor="#80000000" />

        <widget name="help" position="120,120" size="1480,750"
        zPosition="2"
        font="Regular;34" transparent="1"
        foregroundColor="#FFFFFF"
        halign="center" valign="center" />

        <widget name="synopsis" position="60,320" size="1200,580" font="Regular;28" transparent="1" />
        <widget name="cast" position="60,900" size="1780,80" font="Regular;26" transparent="1" foregroundColor="#ff00ff" />

        <widget name="poster" position="1350,120" size="500,750" alphatest="blend" />

        <ePixmap pixmap="buttons/red.png" position="60,1010" size="35,35" alphatest="blend" />
        <eLabel text="Exit" position="105,1002" size="180,45" font="Regular;26" backgroundColor="#011a2e" />
        <ePixmap pixmap="buttons/green.png" position="330,1010" size="35,35" alphatest="blend" />
        <eLabel text="Movies" position="375,1002" size="220,45" font="Regular;26" backgroundColor="#011a2e" />
        <ePixmap pixmap="buttons/yellow.png" position="620,1010" size="35,35" alphatest="blend" />
        <eLabel text="Series" position="665,1002" size="220,45" font="Regular;26" backgroundColor="#011a2e" />
        <ePixmap pixmap="buttons/blue.png" position="910,1010" size="35,35" alphatest="blend" />
        <eLabel text="Settings" position="955,1002" size="340,45" font="Regular;26" backgroundColor="#011a2e" />
        <ePixmap pixmap="buttons/key_menu.png" position="1210,1010" size="45,45" alphatest="blend" />
        <eLabel text="▶Trailer" position="1260,1002" size="440,45" font="Regular;26" foregroundColor="#ffffff" backgroundColor="#011a2e" />
    </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        ensure_dirs()

        self["status"] = Label("Ready")
        self["title"] = Label("")
        self["meta"] = Label("")
        self["score_tomo"] = Label("")
        self["score_pop"] = Label("")
        self["synopsis"] = Label("")
        self["cast"] = Label("")
        self["poster"] = Pixmap()
        self["help"] = Label("")
        self.showing_help = True
        self["help"].hide()
        self.showing_help = False

        self.current_item = None
        self.current_detail = {}
        self._closing = False
        self._exiting = False
        self._trailer_data = None  # NOVO
        self.onClose.append(self._on_main_close)

        # UI dispatcher
        self._uiq = []
        self._uit = eTimer()
        self._uit.callback.append(self._drain_uiq)
        self._uit.start(200, False)

        self.picload = ePicLoad()
        self.picload.PictureData.get().append(self._on_pic_ready)

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions", "MenuActions"],
            {
                "cancel": self.exit,
                "red": self.exit,
                "green": self.open_movies_menu,
                "yellow": self.open_series_menu,
                "blue": self.open_settings_menu,
                "ok": self.open_item_menu,
                "menu": self._play_trailer_direct,  # NOVO - bijelo dugme za trejler
            },
            -1
        )
        
        # Load placeholder image on startup
        # timers (držimo reference da ne budu GC)
        self._epgTimer = eTimer()
        self._epgTimer.callback.append(self._check_epg)
        self._epgTimer.start(1000, True)  # Provjeri nakon 1 sekunde

        self._phTimer = eTimer()
        self._phTimer.callback.append(self._show_placeholder)

        # čekaj da layout završi pa tek onda placeholder
        self.onLayoutFinish.append(self._show_placeholder)
        # U __init__ metodi, na samom kraju:
        self.onLayoutFinish.append(self._show_startup_help)
        self.onLayoutFinish.append(self._check_epg)

    def _show_startup_help(self):
        """Show startup help screen"""
        # Ako već imamo učitane detalje, ne prikazuj help
        if self.current_item or self.current_detail:
            dlog("HELP: Skipping - details already loaded")
            return

        dlog("HELP: shown")
        txt = (
            "Welcome to Ciefp Rotten Tomatoes 1.3\n\n"
            "GREEN  = Movies\n"
            "YELLOW = Series\n"
            "BLUE   = Settings\n"
            "OK     = Cast & Crew Backdrop\n"
            "MENU   = Play Trailer\n\n"
            "Tip:\n"
            "Use Settings -> Clear Cache\n"
            "to free memory if plugin becomes slow.\n\n"
            "Press any key to continue...\n\n"
            "Website data provided by RottenTomatoes.com\n"
        )
        self["help"].setText(txt)
        self["help"].show()
        self.showing_help = True

    def _show_placeholder(self):
        """Show placeholder image (safe - waits for widget instance)"""
        try:
            if self._closing or self._exiting:
                return

            # Sakrij help ako je prikazan
            if self.showing_help:
                self["help"].hide()
                self.showing_help = False

            # widget još nije spreman -> pokušaj opet za 200ms
            if not self["poster"].instance:
                self._phTimer.start(200, True)
                return

            w = self["poster"].instance.size().width()
            h = self["poster"].instance.size().height()

            # dimenzije još 0 -> pokušaj opet
            if w <= 0 or h <= 0:
                self._phTimer.start(200, True)
                return

            if os.path.exists(PLACEHOLDER_IMG):
                self.picload.setPara((w, h, 1, 1, 0, 1, "#00000000"))
                self.picload.startDecode(PLACEHOLDER_IMG)
                self["poster"].show()
                dlog("Placeholder loaded")
            else:
                dlog(f"Placeholder not found: {PLACEHOLDER_IMG}")
                self["poster"].hide()

        except Exception as e:
            dlog(f"Placeholder error: {e}")
            # nemoj sakrivati zauvek, probaj opet kad se layout podigne
            try:
                self._phTimer.start(200, True)
            except:
                pass

    # --- UI queue helpers ---
    def ui(self, fn):
        """UI thread dispatcher"""
        if not hasattr(self, '_uiq'):
            self._uiq = []
        if hasattr(self, '_closing') and self._closing:
            return
        if hasattr(self, '_exiting') and self._exiting:
            return
        self._uiq.append(fn)

    def _drain_uiq(self):
        if self._closing or self._exiting:
            return
            
        q = self._uiq
        self._uiq = []
        for fn in q:
            try:
                if not self._closing and not self._exiting:
                    fn()
            except:
                pass

    def _on_pic_ready(self, picInfo=""):
        if self._closing or self._exiting:
            return
        try:
            ptr = self.picload.getData()
            if ptr and self["poster"].instance:
                self["poster"].instance.setPixmap(ptr)
                self["poster"].show()
                dlog("Poster displayed")
            else:
                dlog("No poster data")
                self._show_placeholder()
        except Exception as e:
            dlog(f"Picload error: {e}")
            self._show_placeholder()

    # --- Safe exit ---
    def exit(self):
        if self._closing or self._exiting:
            return
        self._hide_help()

        dlog("EXIT: Starting exit sequence")
        self._exiting = True
        
        try:
            if self._uit:
                self._uit.stop()
                dlog("EXIT: Timer stopped")
        except:
            pass
        
        self._uiq = []
        dlog("EXIT: UI queue cleared")
        
        self._closing = True
        
        try:
            if self.picload:
                self.picload.PictureData.get().remove(self._on_pic_ready)
                dlog("EXIT: Picload callback removed")
        except:
            pass
        
        try:
            if "actions" in self:
                self["actions"].destroy()
                del self["actions"]
                dlog("EXIT: ActionMap destroyed")
        except:
            pass
        
        dlog("EXIT: Calling Screen.close()")
        self.close()

    # --- Thread wrapper ---
    def _thread_wrapper(self, target_func, *args, **kwargs):
        if self._closing or self._exiting:
            dlog(f"THREAD: Not starting {target_func.__name__}, screen is closing")
            return
        
        dlog(f"THREAD: Starting {target_func.__name__}")
        try:
            if self._closing or self._exiting:
                dlog(f"THREAD: Aborting {target_func.__name__} before start")
                return
                
            target_func(*args, **kwargs)
            dlog(f"THREAD: Completed {target_func.__name__}")
        except Exception as e:
            dlog(f"THREAD: Error in {target_func.__name__}: {e}\n{traceback.format_exc()}")
        finally:
            dlog(f"THREAD: Finished {target_func.__name__}")

    def _hide_help(self):
        if self.showing_help:
            self["help"].hide()
            self.showing_help = False

    # --- Auto EPG on startup ---
    def onFirstShow(self):
        """Called when screen is first shown"""
        Screen.onFirstShow(self)

        # Show placeholder immediately
        self._show_placeholder()

        # Samo ako nema učitane stavke, prikaži help
        if not self.current_item:
            self._show_startup_help()

        # Check if auto EPG is enabled
        if config.plugins.ciefprt.auto_epg.value:
            self._epgTimer.start(1000, True)

    def _check_epg(self):
        """Check EPG and auto-search current program"""
        dlog("EPG: _check_epg called")  # NOVO - debug

        if self._closing or self._exiting:
            dlog("EPG: Screen is closing, skipping")
            return

        # Provjeri da li je Auto EPG uključen
        if not config.plugins.ciefprt.auto_epg.value:
            dlog("EPG: Auto EPG is disabled in settings")
            self["status"].setText("Auto EPG disabled")
            return

        epg_info = get_current_epg_info(self.session)
        dlog(f"EPG: Got info: {epg_info}")  # NOVO - debug

        if epg_info and epg_info.get("title"):
            title = epg_info["title"]
            dlog(f"EPG: Searching for '{title}'")  # NOVO - debug
            self["status"].setText(f"Searching for: {title}")
            self["title"].setText(title)
            self["meta"].setText("Auto-search from EPG...")

            # Start search in background
            threading.Thread(
                target=self._thread_wrapper,
                args=(self._search_epg_thread, title),
                daemon=True
            ).start()
        else:
            dlog("EPG: No EPG info found")  # NOVO - debug
            self["status"].setText("Ready - No EPG info found")

    def _search_epg_thread(self, query):
        """Search for EPG program"""
        try:
            # Provjeri da li screen još postoji
            if not hasattr(self, '_closing') or self._closing:
                return
            if not hasattr(self, '_exiting') or self._exiting:
                return

            dlog(f"EPG SEARCH: {query}")

            # Očisti naziv
            clean_query = re.sub(r'[:;!?]', ' ', query)
            clean_query = re.sub(r'\s*\(\d{4}\)\s*$', '', clean_query)
            clean_query = re.sub(r'\s+', ' ', clean_query).strip()

            dlog(f"EPG: Cleaned query: {clean_query}")

            # Prvo probaj sa search_rt (koji koristi API pa fallback)
            results = search_rt(clean_query, search_type="movie")

            if not results:
                results = search_rt(clean_query, search_type="tv")

            def process_results():
                if self._closing or self._exiting:
                    return

                # Sakrij help ako je prikazan
                if self.showing_help:
                    self["help"].hide()
                    self.showing_help = False

                if results:
                    if len(results) > 0:
                        self._load_item_details(results[0])
                        self["status"].setText(f"Found: {results[0]['name']}")
                    else:
                        self["status"].setText(f"No results for: {clean_query}")
                        self._show_placeholder()
                else:
                    self["status"].setText(f"No results for: {clean_query}")
                    self._show_placeholder()

            # Provjeri da li ui metoda postoji
            if hasattr(self, 'ui'):
                self.ui(process_results)
            else:
                process_results()

        except Exception as e:
            dlog(f"EPG SEARCH error: {e}")
            try:
                if hasattr(self, 'ui'):
                    self.ui(lambda: self["status"].setText("EPG search failed"))
                    self.ui(self._show_placeholder)
            except:
                pass

    # --- menus ---
    def open_movies_menu(self):
        self._hide_help()
        if self._closing or self._exiting:
            return
        
        cache_size = get_cache_size()
        cache_info = f" ({cache_size:.1f}MB)" if cache_size > 0 else ""

        menu = [
            ("In theaters (Popular)", BASE + "/browse/movies_in_theaters/sort:popular"),
            ("In theaters (Newest)", BASE + "/browse/movies_in_theaters/sort:newest"),
            ("In theaters (A-Z)", BASE + "/browse/movies_in_theaters/sort:a_z"),
            ("In theaters (Top box office)", BASE + "/browse/movies_in_theaters/sort:top_box_office"),
            ("In theaters (Critic highest)", BASE + "/browse/movies_in_theaters/sort:critic_highest"),
            ("In theaters (Critic lowest)", BASE + "/browse/movies_in_theaters/sort:critic_lowest"),
            ("In theaters (Audience highest)", BASE + "/browse/movies_in_theaters/sort:audience_highest"),
            ("In theaters (Audience lowest)", BASE + "/browse/movies_in_theaters/sort:audience_lowest"),
            ("Best Sci-Fi Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:sci_fi~sort:popular"),
            ("Best Mystery & Thriller Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:mystery_and_thriller~sort:popular"),
            ("Best Action Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:action~sort:popular"),
            ("Best Drama Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:drama~sort:popular"),
            ("Best Comedy Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:comedy~sort:popular"),
            ("Best Crime Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:crime~sort:popular"),
            ("Best Adventure Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:adventure~sort:popular"),
            ("Best Romance Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:romance~sort:popular"),
            ("Best Biography Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:biography~sort:popular"),
            ("Best Sports Movies in Theaters (2026)", BASE + "/browse/movies_in_theaters/genres:sports~sort:popular"),
            ("Best Movies to Stream at Home (2026)", BASE + "/browse/movies_at_home/sort:popular"),
            ("Best Sci-Fi Movies to Stream at Home (2026)", BASE + "/browse/movies_at_home/genres:sci_fi~sort:popular"),
            ("Best Mystery & Thriller Movies to Stream at Home(2026)", BASE + "/browse/movies_at_home/genres:mystery_and_thriller~sort:popular"),
            ("At home", BASE + "/browse/movies_at_home/"),
            ("Coming soon", BASE + "/browse/movies_coming_soon/"),
            ("Best New Movies", "https://editorial.rottentomatoes.com/guide/best-new-movies/"),
            ("Best & Popular", "https://editorial.rottentomatoes.com/guide/popular-movies/"),
            ("Best Movies 2025 (Certified Fresh)",
             "https://editorial.rottentomatoes.com/guide/best-2025-movies-every-certified-fresh/"),
            ("Best Movies 2024 (Certified Fresh)",
             "https://editorial.rottentomatoes.com/guide/best-2024-movies-every-certified-fresh/"),
            ("Best Movies 2023", "https://editorial.rottentomatoes.com/guide/best-movies-of-2023/"),
            ("Best Movies 2022", "https://editorial.rottentomatoes.com/guide/best-movies-2022/"),
            ("Best Movies 2021", "https://editorial.rottentomatoes.com/guide/2021-best-movies/"),
            ("Best Movies 2020", "https://editorial.rottentomatoes.com/guide/the-best-movies-of-2020/"),
            ("Best New Comedies of 2025", "https://editorial.rottentomatoes.com/guide/best-new-comedies-of-2025/"),
            ("Netflix’s 100 Best Movies RIGHT NOW", "https://editorial.rottentomatoes.com/guide/best-netflix-movies-to-watch-right-now/"),
            ("35 Steven Spielberg Movies", "https://editorial.rottentomatoes.com/guide/every-steven-spielberg-movie-ranked-by-tomatometer/"),
            ("140 Essential Action Movies", "https://editorial.rottentomatoes.com/guide/140-essential-action-movies-to-watch-now/"),
            ("150 Essential Sci-Fi Movies", "https://editorial.rottentomatoes.com/guide/essential-sci-fi-movies-of-all-time/"),
            ("Best High School Movies", "https://editorial.rottentomatoes.com/guide/best-high-school-movies/"),
            ("Best Heist Movies", "https://editorial.rottentomatoes.com/guide/best-heist-movies-of-all-time/"),
            ("Best Fantasy Movies", "https://editorial.rottentomatoes.com/guide/best-fantasy-movies-of-all-time/"),
            ("37 Marvel MCU Movies", "https://editorial.rottentomatoes.com/guide/all-marvel-cinematic-universe-movies-ranked/"),
            ("Best Computer-Animated Movies", "https://editorial.rottentomatoes.com/guide/best-computer-animated-movies-of-all-time/"),
            ("50 Best Free Movies on Fandango at home", "https://editorial.rottentomatoes.com/guide/best-movies-fandango-at-home/"),
            ("76 Disney Animated Movies", "https://editorial.rottentomatoes.com/guide/all-disney-animated-theatrical-movies-ranked-by-tomatometer/"),
            ("100 Best Movies of 1995", "https://editorial.rottentomatoes.com/guide/best-movies-1995/"),
            ("Search Movies", "search_movies"),
        ]
        self.session.openWithCallback(self._browse_choice, ChoiceBox, title="Movies", list=menu)

    def open_series_menu(self):
        self._hide_help()
        if self._closing or self._exiting:
            return
        
        cache_size = get_cache_size()
        cache_info = f" ({cache_size:.1f}MB)" if cache_size > 0 else ""
        
        menu = [
            ("TV browse (All)", BASE + "/browse/tv_series_browse/"),
            ("TV (Popular)", BASE + "/browse/tv_series_browse/sort:popular"),
            ("TV (Newest)", BASE + "/browse/tv_series_browse/sort:newest"),
            ("Netflix (Popular)", BASE + "/browse/tv_series_browse/affiliates:netflix~sort:popular"),
            ("Apple TV+ (Popular)", BASE + "/browse/tv_series_browse/affiliates:apple-tv-plus~sort:popular"),
            ("Prime Video (Popular)", BASE + "/browse/tv_series_browse/affiliates:prime-video~sort:popular"),
            ("Max (Popular)", BASE + "/browse/tv_series_browse/affiliates:max~sort:popular"),
            ("Paramount+ (Popular)", BASE + "/browse/tv_series_browse/affiliates:paramount-plus~sort:popular"),
            ("Hulu (Popular)", BASE + "/browse/tv_series_browse/affiliates:hulu~sort:popular"),
            ("AMC+ (Popular)", BASE + "/browse/tv_series_browse/affiliates:amc-plus~sort:popular"),
            ("Peacock (Popular)", BASE + "/browse/tv_series_browse/affiliates:peacock~sort:popular"),
            ("Fandango at Home (Popular)", BASE + "/browse/tv_series_browse/affiliates:fandango-at-home~sort:popular"),
            ("Acorn TV (Popular)", BASE + "/browse/tv_series_browse/affiliates:acorn-tv~sort:popular"),
            ("Best Sci-Fi TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:sci_fi~sort:popular"),
            ("Best Mystery & Thriller TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:mystery_and_thriller~sort:popular"),
            ("Best Action TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:action~sort:popular"),
            ("Best Drama TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:drama~sort:popular"),
            ("Best Crime TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:crime~sort:popular"),
            ("Best Comedy TV Shows (2026)", BASE + "/browse/tv_series_browse/genres:comedy~sort:popular"),
            ("Sci-Fi TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:sci_fi~sort:a_z"),
            ("Action TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:action~sort:a_z"),
            ("Comedy TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:comedy~sort:a_z"),
            ("Drama TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:drama~sort:a_z"),
            ("Romance TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:romance~sort:a_z"),
            ("Mystery & Thriller TV Shows (A-Z)", BASE + "/browse/tv_series_browse/genres:mystery_and_thriller~sort:a_z"),
            ("Best TV Shows of 2026", "https://editorial.rottentomatoes.com/guide/best-new-tv-series-shows/"),
            ("Best TV Shows of 2025", "https://editorial.rottentomatoes.com/guide/best-tv-shows-of-2025/"),
            ("Best Series on Disney+ 2025", "https://editorial.rottentomatoes.com/guide/best-disney-plus-shows/"),
            ("The Best Prime Video TV Shows 2026", "https://editorial.rottentomatoes.com/guide/best-tv-shows-and-movies-original-to-amazon-prime-video/"),
            ("100 Best Netflix Series 2026", "https://editorial.rottentomatoes.com/guide/best-netflix-shows-and-movies-to-binge-watch-now/"),
            ("Best Apple TV+ Original Series", "https://editorial.rottentomatoes.com/guide/best-apple-tv-plus-series-ranked/"),
            ("HBO and HBO Max Series", "https://editorial.rottentomatoes.com/guide/best-hbo-series-of-all-time-ranked/"),
            ("Best Hulu Shows", "https://editorial.rottentomatoes.com/guide/best-hulu-shows-and-movies-to-binge-watch-now/"),
            ("34 Marvel TV Shows Ranked", "https://editorial.rottentomatoes.com/guide/marvel-tv-by-tomatometer/"),
            ("Search Series", "search_series"),
        ]
        self.session.openWithCallback(self._browse_choice, ChoiceBox, title="TV Series", list=menu)

    def open_settings_menu(self):
        self._hide_help()
        if self._closing or self._exiting:
            return

        cache_size = get_cache_size()
        cache_info = f" ({cache_size:.1f}MB)" if cache_size > 0 else ""

        # Trenutni player - uzmi iz configa ili postavi default
        if hasattr(config.plugins.ciefprt, 'player'):
            player_value = config.plugins.ciefprt.player.value
            if player_value == "movieplayer":
                current_player = "Movie Player"
            elif player_value == "browser":
                current_player = "External Browser"
            elif player_value == "download":
                current_player = "Download & Play"
            else:
                current_player = "Movie Player"
        else:
            current_player = "Movie Player"

        menu = [
            (f"Clear Cache{cache_info}", "clear"),
            ("Show debug log (last 80 lines)", "showlog"),
            ("Clear debug log", "clearlog"),
            ("Auto EPG Search (current: %s)" % ("ON" if config.plugins.ciefprt.auto_epg.value else "OFF"), "auto_epg"),
            ("Items load limit (current: %s)" % config.plugins.ciefprt.max_items.value, "max_items"),
            ("YouTube Search (current: %s)" % ("ON" if config.plugins.ciefprt.youtube_search.value else "OFF"),
             "youtube_search"),
            ("Select Player (current: %s)" % current_player, "select_player"),
            ("About", "about"),
        ]
        self.session.openWithCallback(self._settings_choice, ChoiceBox, title="Settings", list=menu)

    def _settings_choice(self, choice):
        if not choice or self._closing or self._exiting:
            return
        key = choice[1]

        if key == "clear":
            clear_cache()
            cache_size = get_cache_size()
            self["status"].setText(f"Cache cleared ({cache_size:.1f}MB)")
        elif key == "showlog":
            self.session.open(MessageBox, tail_debug_log(80), MessageBox.TYPE_INFO, timeout=12)
        elif key == "clearlog":
            clear_debug_log()
            self["status"].setText("Debug log cleared")
        elif key == "auto_epg":
            config.plugins.ciefprt.auto_epg.value = not config.plugins.ciefprt.auto_epg.value
            config.plugins.ciefprt.auto_epg.save()
            status = "ON" if config.plugins.ciefprt.auto_epg.value else "OFF"
            self["status"].setText(f"Auto EPG: {status}")
        elif key == "youtube_search":
            config.plugins.ciefprt.youtube_search.value = not config.plugins.ciefprt.youtube_search.value
            config.plugins.ciefprt.youtube_search.save()
            status = "ON" if config.plugins.ciefprt.youtube_search.value else "OFF"
            self["status"].setText(f"YouTube Search: {status}")
        elif key == "max_items":
            opts = [("50", "50"), ("100", "100"), ("150", "150"), ("200", "200"), ("300", "300")]

            def _set_limit(sel):
                if not sel or self._closing or self._exiting:
                    return
                val = sel[1]
                config.plugins.ciefprt.max_items.value = val
                config.plugins.ciefprt.max_items.save()
                self["status"].setText(f"Items limit set to: {val}")

            self.session.openWithCallback(_set_limit, ChoiceBox, title="Select items load limit", list=opts)

        elif key == "select_player":
            players = [
                ("Movie Player (servicemp3)", "movieplayer"),
                ("External Browser", "browser"),
                ("Download & Play", "download"),
            ]

            def _set_player(sel):
                if not sel or self._closing or self._exiting:
                    return
                player = sel[1]
                # Spremi izbor u config
                if not hasattr(config.plugins.ciefprt, 'player'):
                    config.plugins.ciefprt.player = ConfigSelection(default="movieplayer",
                                                                    choices=[("movieplayer", "Movie Player"),
                                                                             ("browser", "External Browser"),
                                                                             ("download", "Download & Play")])
                config.plugins.ciefprt.player.value = player
                config.plugins.ciefprt.player.save()
                self["status"].setText(f"Player set to: {sel[0]}")

            self.session.openWithCallback(_set_player, ChoiceBox, title="Select Player", list=players)
        elif key == "about":
            about_text = f"""{PLUGIN_NAME} v{PLUGIN_VERSION}

    Browse Rotten Tomatoes movies and TV series.

    Features:
    • Browse popular/trending content
    • Search for movies and series
    • Auto-search from EPG
    • Cache system for faster loading
    • Placeholder images for missing posters
    • YouTube trailer search

    Cache: {get_cache_size():.1f}MB"""
            self.session.open(MessageBox, about_text, MessageBox.TYPE_INFO, timeout=15)

    # --- Search functions ---
    def _open_search_dialog(self, search_type="movie"):
        """Open keyboard for search input"""
        title = "Search Movies" if search_type == "movie" else "Search Series"
        
        def search_callback(result):
            if result and not self._closing and not self._exiting:
                self["status"].setText(f"Searching: {result}")
                self["title"].setText(result)
                self["meta"].setText("Searching...")
                self["score_tomo"].setText("")
                self["score_pop"].setText("")
                self["synopsis"].setText("")
                self["cast"].setText("")
                self._show_placeholder()
                
                threading.Thread(
                    target=self._thread_wrapper,
                    args=(self._search_thread, result, search_type),
                    daemon=True
                ).start()
        
        self.session.openWithCallback(search_callback, VirtualKeyBoard, title=title)

    def _search_thread(self, query, search_type="movie"):
        """Perform search in background"""
        try:
            if self._closing or self._exiting:
                return
                
            dlog(f"SEARCH: {query} ({search_type})")
            results = search_rt(query, search_type)
            
            def show_results():
                if self._closing or self._exiting:
                    return
                    
                if not results:
                    self["status"].setText(f"No results for: {query}")
                    self["title"].setText("")
                    self["meta"].setText("")
                    return

                limit = int(config.plugins.ciefprt.max_items.value)
                display_results = results[:limit]

                # Create choice list
                choice_list = [(item["name"], item) for item in display_results]
                
                def item_chosen(choice):
                    if not choice or self._closing or self._exiting:
                        return
                    self._load_item_details(choice[1])
                
                title = f"Search Results: {query} ({len(results)} found)"
                if len(results) > limit:
                    title += f" (showing {limit})"

                    
                self.session.openWithCallback(
                    item_chosen, 
                    ChoiceBox, 
                    title=title, 
                    list=choice_list
                )
                self["status"].setText(f"Found {len(results)} results")
                self["title"].setText("")
                self["meta"].setText("")
            
            self.ui(show_results)
        except Exception as e:
            dlog(f"SEARCH error: {e}")
            if not self._closing and not self._exiting:
                self.ui(lambda: self["status"].setText("Search failed"))
                self.ui(lambda: self["title"].setText(""))
                self.ui(lambda: self["meta"].setText(""))

    # --- browse ---
    def _browse_choice(self, choice):
        if not choice or self._closing or self._exiting:
            return
        
        if choice[1] == "search_movies":
            self._open_search_dialog("movie")
        elif choice[1] == "search_series":
            self._open_search_dialog("tv")
        else:
            url = choice[1]
            self["status"].setText("Loading list...")
            threading.Thread(
                target=self._thread_wrapper,
                args=(self._load_browse_thread, url),
                daemon=True
            ).start()

    def _load_browse_thread(self, url):
        try:
            if self._closing or self._exiting:
                return

            dlog("BROWSE: %s" % url)

            # settings limit (fallback 150)
            try:
                max_limit = int(config.plugins.ciefprt.max_items.value)
            except Exception:
                max_limit = 150

            # --- BASE /browse/... -> load more paging (HTML ?page=N) ---
            if url.startswith(BASE + "/browse/"):
                items = []
                page = 1
                has_more = True  # gasi se kad nema novih stavki

                # Učitaj prvu stranu
                first = parse_browse_api_page(url, page=1, limit=None) or []
                if first:
                    items = first
                else:
                    # fallback ako parse_browse_api_page iz nekog razloga ne vrati ništa
                    items = parse_browse(url) or []

                # hard cap odmah
                if len(items) > max_limit:
                    items = items[:max_limit]

                def show_choice():
                    if self._closing or self._exiting:
                        return

                    if not items:
                        self["status"].setText("No items found")
                        return

                    choice_list = [(it.get("name", "???"), it) for it in items]

                    # Load more na dnu samo ako:
                    # - još ima prostora do max_limit
                    # - i has_more je True
                    if has_more and len(items) < max_limit:
                        choice_list.append((LOAD_MORE_LABEL, {"__load_more__": True}))

                    def item_chosen(choice):
                        nonlocal page, has_more, items
                        if not choice or self._closing or self._exiting:
                            return

                        payload = choice[1]

                        # Klik na "Load more..."
                        if isinstance(payload, dict) and payload.get("__load_more__"):
                            def load_more_thread():
                                nonlocal page, has_more, items
                                try:
                                    page += 1
                                    self.ui(lambda: self["status"].setText("Loading more..."))

                                    new_items = parse_browse_api_page(url, page=page, limit=None) or []
                                    dlog("LOAD MORE: page=%s, got=%s" % (page, len(new_items)))

                                    # Ako nema ništa -> nema više
                                    if not new_items:
                                        has_more = False
                                        self.ui(show_choice)
                                        return

                                    # RT često vraća kumulativnu listu:
                                    # page=2 sadrži i page=1 + nove stavke.
                                    # Zato: ako je lista porasla -> REPLACE, ako nije -> STOP.
                                    if len(new_items) > len(items):
                                        items = new_items
                                    else:
                                        dlog("LOAD MORE: no new items -> stopping")
                                        has_more = False
                                        self.ui(show_choice)
                                        return

                                    # Dedup po URL (sigurnost)
                                    seen = set()
                                    deduped = []
                                    for it in items:
                                        u = (it.get("url") or "").strip()
                                        if not u or u in seen:
                                            continue
                                        seen.add(u)
                                        deduped.append(it)
                                    items = deduped

                                    # hard cap (settings)
                                    if len(items) > max_limit:
                                        items = items[:max_limit]
                                        has_more = False  # ako smo dostigli limit, nema smisla nuditi još

                                    self.ui(show_choice)

                                except Exception as e:
                                    dlog("LOAD MORE error: %s" % e)
                                    self.ui(lambda: self["status"].setText("Load more failed"))
                                    self.ui(show_choice)

                            threading.Thread(
                                target=self._thread_wrapper,
                                args=(load_more_thread,),
                                daemon=True
                            ).start()
                            return

                        # Normalan izbor -> detalji
                        self._load_item_details(payload)

                    title = "Select (%d items)" % len(items)
                    self.session.openWithCallback(item_chosen, ChoiceBox, title=title, list=choice_list)
                    self["status"].setText("Loaded %d items" % len(items))

                self.ui(show_choice)
                return

            # --- sve ostalo (editorial / search / šta god) ---
            items = parse_browse(url) or []

            if len(items) > max_limit:
                items = items[:max_limit]
                dlog("BROWSE: Limited to %d items" % max_limit)

            def show_choice():
                if self._closing or self._exiting:
                    return
                if not items:
                    self["status"].setText("No items found")
                    return

                choice_list = [(it.get("name", "???"), it) for it in items]

                def item_chosen(choice):
                    if not choice or self._closing or self._exiting:
                        return
                    self._load_item_details(choice[1])

                title = "Select (%d items)" % len(items)
                self.session.openWithCallback(item_chosen, ChoiceBox, title=title, list=choice_list)
                self["status"].setText("Loaded %d items" % len(items))

            self.ui(show_choice)

        except Exception as e:
            dlog("BROWSE thread error: %s" % e)
            self.ui(lambda: self["status"].setText("Browse failed"))

    # --- Load selected item ---
    def _load_item_details(self, item):
        if self._closing or self._exiting:
            return

        # Sakrij help ako je prikazan
        if self.showing_help:
            self["help"].hide()
            self.showing_help = False

        self.current_item = item
        self["title"].setText(item.get("name", ""))
        self["meta"].setText("Loading details...")
        self["score_tomo"].setText("")
        self["score_pop"].setText("")
        self["synopsis"].setText("")
        self["cast"].setText("")

        # Show placeholder while loading
        self._show_placeholder()
        
        # Load poster
        img = item.get("image")
        if img:
            threading.Thread(
                target=self._thread_wrapper,
                args=(self._download_and_scale_poster, img),
                daemon=True
            ).start()
        else:
            # No image, keep placeholder
            dlog("No image URL for item")
        
        # Load details
        threading.Thread(
            target=self._thread_wrapper,
            args=(self._load_detail_thread, item.get("url")),
            daemon=True
        ).start()

    # --- poster (scale to widget) ---
    def _download_and_scale_poster(self, img_url):
        try:
            if self._closing or self._exiting:
                return
                
            dlog(f"POSTER: Downloading {img_url}")
            ensure_dirs()
            fn = os.path.join(CACHE_POSTERS, cache_key(img_url) + ".img")
            
            # Check if we have cached version
            if not os.path.exists(fn):
                if self._closing or self._exiting:
                    return
                data = http_get(img_url, timeout=8)
                with open(fn, "wb") as f:
                    f.write(data)
                dlog(f"POSTER: Downloaded and cached {len(data)} bytes")

            def decode():
                if self._closing or self._exiting:
                    return
                try:
                    w = self["poster"].instance.size().width()
                    h = self["poster"].instance.size().height()
                    self.picload.setPara((w, h, 1, 1, 0, 1, "#00000000"))
                    self.picload.startDecode(fn)
                    dlog("POSTER: Decoding started")
                except Exception as e:
                    dlog(f"POSTER: Decode error: {e}")
                    # If decode fails, show placeholder
                    self._show_placeholder()

            self.ui(decode)
        except Exception as e:
            dlog(f"POSTER: EXCEPTION\n%s" % traceback.format_exc())
            # On error, show placeholder
            self.ui(self._show_placeholder)

    # --- details ---
    def _load_detail_thread(self, detail_url):
        try:
            if self._closing or self._exiting:
                return

            if not detail_url:
                dlog("DETAIL: missing URL")
                return

            dlog("DETAIL: %s" % detail_url)
            raw = get_cached_page(detail_url, ttl=900) or http_get(detail_url, timeout=8)
            set_cached_page(detail_url, raw)
            html = raw.decode("utf-8", "ignore")
            d = parse_detail(html, detail_url)

            def apply():
                if self._closing or self._exiting:
                    return

                # keep full detail around for OK menu (backdrop / cast&crew)
                self.current_detail = d

                mpaa = d.get("mpaa") or ""
                status = d.get("status") or ""
                runtime = d.get("runtime") or ""
                genres = d.get("genres") or ""

                meta = ", ".join([x for x in [mpaa, status, runtime, genres] if x])
                self["meta"].setText(meta if meta else " ")

                tomo = d.get("tomatometer") or "?"
                cc = d.get("critic_count") or "?"
                pop = d.get("popcorn") or "?"
                ac = d.get("audience_count") or "?"

                self["score_tomo"].setText("%s%% Tomatometer (%s reviews)" % (tomo, cc))
                self["score_pop"].setText("%s%% Popcornmeter (%s)" % (pop, ac))

                syn = d.get("synopsis") or ""
                self["synopsis"].setText(syn)

                director = d.get("director") or ""
                cast = d.get("cast") or ""
                lines = []
                if director:
                    lines.append("Director: %s" % director)
                if cast:
                    lines.append("Cast: %s" % cast)
                self["cast"].setText("\n".join(lines))

                # NOVO: Indikator za trejler u statusu
                trailer_url = d.get("trailer_url", "")
                if trailer_url:
                    self["status"].setText("▶ Trailer available - Press OK for menu")
                else:
                    self["status"].setText("Press OK for menu")

                # if the list item had no poster, try og:image
                if (self.current_item and not self.current_item.get("image")) and d.get("poster_url"):
                    threading.Thread(
                        target=self._thread_wrapper,
                        args=(self._download_and_scale_poster, d["poster_url"]),
                        daemon=True
                    ).start()

            # IMPORTANT: schedule UI update here (not inside apply)
            self.ui(apply)

        except Exception:
            dlog("DETAIL: EXCEPTION\n%s" % traceback.format_exc())
            if not self._closing and not self._exiting:
                self.ui(lambda: self["meta"].setText("Details load failed"))

    # --- OK menu ---
    def open_item_menu(self):
        self._hide_help()
        if not self.current_item or self._closing or self._exiting:
            return

        d = getattr(self, "current_detail", {}) or {}
        trailer_url = d.get("trailer_url", "")  # NOVO

        menu = [
            ("Show item URL", "url"),
        ]

        # NOVO: Dodaj opciju za trejler
        if trailer_url:
            menu.append(("▶ Watch Trailer", "trailer"))

        if d.get("backdrop_url"):
            menu.append(("Show Backdrop", "backdrop"))

        if (d.get("director_list") or d.get("cast_list")):
            menu.append(("Cast & Crew", "castcrew"))

        menu.append(("Back to list", "back"))

        self.session.openWithCallback(
            self._item_choice,
            ChoiceBox,
            title=self.current_item.get("name", ""),
            list=menu
        )

    def _show_backdrop(self):
        d = getattr(self, "current_detail", {}) or {}
        url = d.get("backdrop_url") or ""
        if not url or self._closing or self._exiting:
            return

        threading.Thread(
            target=self._thread_wrapper,
            args=(self._download_and_open_backdrop, url),
            daemon=True
        ).start()

    def _download_and_open_backdrop(self, url):
        try:
            if self._closing or self._exiting:
                return

            ensure_dirs()
            fn = os.path.join(CACHE_POSTERS, cache_key(url) + ".bd.jpg")

            if not os.path.exists(fn):
                data = http_get(url, timeout=10)
                with open(fn, "wb") as f:
                    f.write(data)

            self.ui(lambda: self.session.open(CiefpRTBackdrop, fn))
        except Exception as e:
            dlog("BACKDROP error: %s" % e)

    def _open_cast_crew(self):
        d = getattr(self, "current_detail", {}) or {}
        dirs = d.get("director_list") or []
        cast = d.get("cast_list") or []

        lst = []
        for n in dirs[:5]:
            lst.append(("Director: %s" % n, n))
        for n in cast[:40]:
            lst.append(("Cast: %s" % n, n))

        if not lst:
            return

        self.session.openWithCallback(
            self._cast_choice_cb,
            ChoiceBox,
            title="Cast & Crew",
            list=lst
        )

    def _cast_choice_cb(self, choice):
        if not choice or self._closing or self._exiting:
            return
        name = choice[1]
        if name:
            self._open_celebrity(name)

    def _to_celebrity_slug(self, name):
        s = (name or "").strip().lower()
        s = re.sub(r"[^a-z0-9\s_]", "", s)
        s = re.sub(r"\s+", "_", s)
        return s

    def _open_celebrity(self, name):
        url = BASE + "/celebrity/" + self._to_celebrity_slug(name)
        self.session.open(CiefpRTCelebrity, url, name),

    def _item_choice(self, choice):
        if not choice or self._closing or self._exiting:
            return

        action = choice[1]

        if action == "url":
            self.session.open(
                MessageBox,
                self.current_item.get("url", ""),
                MessageBox.TYPE_INFO,
                timeout=8
            )
        elif action == "trailer":
            d = getattr(self, "current_detail", {}) or {}
            trailer_url = d.get("trailer_url", "")
            trailer_type = d.get("trailer_type", "hls")
            if trailer_url:
                name = self.current_item.get("name", "Trailer") if self.current_item else "Trailer"
                # Otvori player direktno (bez zatvaranja glavnog ekrana)
                self.session.open(CiefpRTPlayer, trailer_url, trailer_type, name)

        elif action == "backdrop":
            self._show_backdrop()

        elif action == "castcrew":
            self._open_cast_crew()

        elif action == "back":
            self.current_item = None
            self.current_detail = {}
            self["title"].setText("")
            self["meta"].setText("")
            self["score_tomo"].setText("")
            self["score_pop"].setText("")
            self["synopsis"].setText("")
            self["cast"].setText("")
            self._show_placeholder()
            self["status"].setText("Ready")

    def _play_trailer_direct(self):
        """Direct play trailer when white button is pressed"""
        if self._closing or self._exiting:
            return

        d = getattr(self, "current_detail", {}) or {}
        trailer_url = d.get("trailer_url", "")

        if trailer_url:
            trailer_type = d.get("trailer_type", "hls")
            name = self.current_item.get("name", "Trailer") if self.current_item else "Trailer"
            # Otvori player direktno (bez zatvaranja glavnog ekrana)
            self.session.open(CiefpRTPlayer, trailer_url, trailer_type, name)
        else:
            self.session.open(
                MessageBox,
                "No trailer available for this title.",
                MessageBox.TYPE_INFO,
                timeout=3
            )

    def _on_main_close(self):
        """Called when main screen is closed - open player if trailer data exists"""
        if hasattr(self, '_trailer_data') and self._trailer_data:
            trailer_url, trailer_type, name = self._trailer_data
            dlog(f"MAIN: Opening player for {name}")

            # Otvori player direktno (radi u većini slučajeva)
            try:
                self.session.open(CiefpRTPlayer, trailer_url, trailer_type, name)
            except Exception as e:
                dlog(f"MAIN: Direct open failed: {e}")
                # Ako direktno ne radi, probaj sa timerom
                timer = eTimer()

                def open_player():
                    try:
                        self.session.open(CiefpRTPlayer, trailer_url, trailer_type, name)
                    except Exception as e2:
                        dlog(f"MAIN: Timer open failed: {e2}")

                timer.callback.append(open_player)
                timer.start(200, True)

            # Očisti podatke
            self._trailer_data = None

    def _player_closed(self):
        """Called when player is closed"""
        dlog("MAIN: Player closed")
        # Ovdje možete dodati bilo kakvu akciju nakon zatvaranja playera


def _extract_jsonld_person(html_text):
    blocks = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text, flags=re.S | re.I
    )
    for b in blocks:
        b = (b or "").strip()
        try:
            data = json.loads(b)
        except:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if isinstance(obj, dict) and obj.get("@type") == "Person":
                return obj
    return None

def parse_celebrity(html):
    """
    Parse RottenTomatoes celebrity page (best-effort).
    - Name (h1 or og:title)
    - Portrait image from rt-img celebrity-bio hero
    - Highest/Lowest rated from data-qa blocks
    - Birthday/Birthplace from data-qa blocks (fallback JSON-LD Person if postoji)
    - Bio summary from data-qa summary (fallback meta description)
    """

    out = {
        "name": "",
        "image": "",
        "highest": "",
        "lowest": "",
        "birthday": "",
        "birthplace": "",
        "bio": ""
    }

    # ---------------- helpers ----------------
    def _strip_tags(s):
        s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.S | re.I)
        s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.S | re.I)
        s = re.sub(r"<[^>]+>", " ", s)
        try:
            s = _html.unescape(s)
        except:
            pass
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _extract_jsonld_person(html_text):
        blocks = re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
            html_text, flags=re.S | re.I
        )
        for b in blocks:
            b = (b or "").strip()
            try:
                data = json.loads(b)
            except:
                continue
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if isinstance(obj, dict) and obj.get("@type") == "Person":
                    return obj
        return None

    def _extract_qa_block(html_text, qa_value):
        m = re.search(
            r'<[^>]+data-qa="%s"[^>]*>(.*?)</[^>]+>' % re.escape(qa_value),
            html_text, re.I | re.S
        )
        if not m:
            return ""
        return m.group(1) or ""

    def _extract_hi_lo(html_text, qa_value):
        # target is <p class="celebrity-bio__item" data-qa="celebrity-bio-highest-rated">...</p>
        m = re.search(
            r'<p[^>]+data-qa="%s"[^>]*>(.*?)</p>' % re.escape(qa_value),
            html_text, re.I | re.S
        )
        if not m:
            return ""

        block = m.group(1) or ""

        pm = re.search(r'(\d{1,3})\s*%', block)
        pct = (pm.group(1) + "%") if pm else ""

        tm = re.search(r'<rt-link[^>]*>(.*?)</rt-link>', block, re.I | re.S)
        title = _strip_tags(tm.group(1)) if tm else ""
        title = re.sub(r"\s+", " ", title).strip()

        if pct and title:
            return "%s %s" % (pct, title)
        return title or pct

    def _extract_simple_item(html_text, qa_value):
        # npr: <p ... data-qa="celebrity-bio-bday"> ... Oct 8, 1949 </p>
        m = re.search(
            r'<p[^>]+data-qa="%s"[^>]*>(.*?)</p>' % re.escape(qa_value),
            html_text, re.I | re.S
        )
        if not m:
            return ""
        block = m.group(1) or ""
        # izbaci label deo (Birthday: / Birthplace:)
        block = re.sub(r'<rt-text[^>]*>.*?</rt-text>', ' ', block, flags=re.I | re.S)
        val = _strip_tags(block)
        return val

    # ---------------- JSON-LD Person (opciono) ----------------
    p = _extract_jsonld_person(html)
    if p:
        if not out["name"]:
            out["name"] = (p.get("name") or "").strip()

        # image može biti str/list/dict
        img = p.get("image")
        img_url = ""
        if isinstance(img, str):
            img_url = img.strip()
        elif isinstance(img, list):
            for it in img:
                if isinstance(it, str) and it.strip():
                    img_url = it.strip()
                    break
                if isinstance(it, dict):
                    u = it.get("url") or it.get("@id")
                    if isinstance(u, str) and u.strip():
                        img_url = u.strip()
                        break
        elif isinstance(img, dict):
            u = img.get("url") or img.get("@id")
            if isinstance(u, str) and u.strip():
                img_url = u.strip()
        if img_url and not out["image"]:
            out["image"] = img_url

        if not out["birthday"]:
            out["birthday"] = (p.get("birthDate") or "").strip()

        if not out["birthplace"]:
            bp = p.get("birthPlace")
            if isinstance(bp, dict):
                out["birthplace"] = (bp.get("name") or "").strip()
            elif isinstance(bp, str):
                out["birthplace"] = bp.strip()

        if not out["bio"]:
            desc = p.get("description")
            if isinstance(desc, str):
                out["bio"] = desc.strip()

    # ---------------- Name fallbacks ----------------
    if not out["name"]:
        m = re.search(r'<h1[^>]*data-qa="celebrity-bio-header"[^>]*>(.*?)</h1>', html, re.I | re.S)
        if m:
            out["name"] = _strip_tags(m.group(1))

    if not out["name"]:
        m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html, re.I)
        if m:
            t = (m.group(1) or "").strip()
            out["name"] = t.split("|")[0].strip()

    # ---------------- Portrait image (rt-img hero) ----------------
    # Primarno: <rt-img class="celebrity-bio__hero-img" src="...">
    if not out["image"]:
        m = re.search(r'<rt-img[^>]+class="[^"]*celebrity-bio__hero-img[^"]*"[^>]+src="([^"]+)"', html, re.I)
        if not m:
            m = re.search(r'<rt-img[^>]+class="[^"]*celebrity-bio__hero-mobile[^"]*"[^>]+src="([^"]+)"', html, re.I)
        if m:
            out["image"] = (m.group(1) or "").strip()

    # Fallback: unutrašnji <img src="...">
    if not out["image"]:
        m = re.search(r'celebrity-bio__hero-img[^>]*>.*?<img[^>]+src="([^"]+)"', html, re.I | re.S)
        if not m:
            m = re.search(r'celebrity-bio__hero-mobile[^>]*>.*?<img[^>]+src="([^"]+)"', html, re.I | re.S)
        if m:
            out["image"] = (m.group(1) or "").strip()

    # OG image fallback (ako sve gore omane)
    if not out["image"]:
        m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
        if m:
            out["image"] = (m.group(1) or "").strip()

    # ---------------- Highest/Lowest rated (data-qa) ----------------
    out["highest"] = out["highest"] or _extract_hi_lo(html, "celebrity-bio-highest-rated")
    out["lowest"]  = out["lowest"]  or _extract_hi_lo(html, "celebrity-bio-lowest-rated")

    # ---------------- Birthday / Birthplace (data-qa) ----------------
    # Ovi blokovi su pouzdaniji od JSON-LD jer daju format "Oct 8, 1949"
    bday = _extract_simple_item(html, "celebrity-bio-bday")
    if bday:
        out["birthday"] = bday

    bplace = _extract_simple_item(html, "celebrity-bio-birthplace")
    if bplace:
        out["birthplace"] = bplace

    # ---------------- Bio summary (data-qa) ----------------
    # Najpouzdanije: <p ... data-qa="celebrity-bio-summary">...</p>
    if not out["bio"]:
        m = re.search(r'<p[^>]+data-qa="celebrity-bio-summary"[^>]*>(.*?)</p>', html, re.I | re.S)
        if m:
            out["bio"] = _strip_tags(m.group(1))

    # fallback: og:description / meta description
    if not out["bio"]:
        m = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"', html, re.I)
        if m:
            out["bio"] = (m.group(1) or "").strip()
    if not out["bio"]:
        m = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', html, re.I)
        if m:
            out["bio"] = (m.group(1) or "").strip()

    # cleanup
    for k in ("name", "image", "highest", "lowest", "birthday", "birthplace", "bio"):
        if isinstance(out.get(k), str):
            out[k] = out[k].strip()

    return out

class CiefpRTBackdrop(Screen):
    skin = """
    <screen name="CiefpRTBackdrop" position="0,0" size="1920,1080" flags="wfNoBorder">
        <widget name="pic" position="0,0" size="1920,1080" zPosition="2" alphatest="on" />
    </screen>
    """

    def __init__(self, session, img_path):
        Screen.__init__(self, session)
        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self.close,
            "cancel": self.close
        }, -1)

        self["pic"] = Pixmap()
        self.picload = ePicLoad()
        self._img_path = img_path
        self.picload.PictureData.get().append(self._on_pic_ready)
        self.onLayoutFinish.append(self._load)

    def _load(self):
        try:
            w = self["pic"].instance.size().width()
            h = self["pic"].instance.size().height()
            self.picload.setPara((w, h, 1, 1, 0, 1, "#00000000"))
            self.picload.startDecode(self._img_path)
        except:
            pass

    def _on_pic_ready(self, picInfo=None):
        try:
            ptr = self.picload.getData()
            if ptr and self["pic"].instance:
                self["pic"].instance.setPixmap(ptr)
        except:
            pass

class CiefpRTCelebrity(Screen):
    skin = """
    <screen name="CiefpRTCelebrity" position="0,0" size="1920,1080" title="Celebrity">
        <widget name="title" position="60,60" size="1200,55" font="Regular;44" transparent="1" foregroundColor="#00ff6e" />
        <widget name="meta" position="60,130" size="1200,40" font="Regular;28" transparent="1" foregroundColor="#00e1ff" />
        <widget name="text" position="60,190" size="1200,820" font="Regular;28" transparent="1" />
        <widget name="poster" position="1350,120" size="500,750" alphatest="blend" />
    </screen>
    """

    def __init__(self, session, url, fallback_name=""):
        Screen.__init__(self, session)
        self.url = url
        self.fallback_name = fallback_name

        self["actions"] = ActionMap(["OkCancelActions"], {
            "ok": self.close,
            "cancel": self.close
        }, -1)

        self["title"] = Label(fallback_name or "")
        self["meta"] = Label("")
        self["text"] = Label("")
        self["poster"] = Pixmap()

        self.picload = ePicLoad()
        self.picload.PictureData.get().append(self._on_pic_ready)
        self.onLayoutFinish.append(self._start)

    def _start(self):
        threading.Thread(target=self._thread, daemon=True).start()

    def _thread(self):
        try:
            raw = http_get(self.url, timeout=10)
            html = raw.decode("utf-8", "ignore")
            d = parse_celebrity(html)

            def apply():
                name = d.get("name") or self.fallback_name
                self["title"].setText(name)

                meta_parts = []
                if d.get("birthday"):
                    meta_parts.append("Birthday: %s" % d["birthday"])
                if d.get("birthplace"):
                    meta_parts.append("Birthplace: %s" % d["birthplace"])
                self["meta"].setText("  |  ".join(meta_parts) if meta_parts else " ")

                txt = ""
                if d.get("highest"):
                    txt += "Highest Rated: %s\n" % d["highest"]
                if d.get("lowest"):
                    txt += "Lowest Rated: %s\n\n" % d["lowest"]
                if d.get("bio"):
                    txt += d["bio"]
                self["text"].setText(txt if txt else " ")

                img = d.get("image") or ""
                if img:
                    self._download_and_decode(img)

            # tiny ui dispatch
            t = eTimer()
            t.callback.append(apply)
            t.start(1, True)
            self._celebtimer = t  # keep ref

        except Exception as e:
            dlog("CELEB error: %s" % e)

    def _download_and_decode(self, img_url):
        try:
            ensure_dirs()
            fn = os.path.join(CACHE_POSTERS, cache_key(img_url) + ".cel.img")
            if not os.path.exists(fn):
                data = http_get(img_url, timeout=10)
                with open(fn, "wb") as f:
                    f.write(data)

            if not self["poster"].instance:
                return

            w = self["poster"].instance.size().width()
            h = self["poster"].instance.size().height()
            self.picload.setPara((w, h, 1, 1, 0, 1, "#00000000"))
            self.picload.startDecode(fn)
        except:
            pass

    def _on_pic_ready(self, picInfo=None):
        try:
            ptr = self.picload.getData()
            if ptr and self["poster"].instance:
                self["poster"].instance.setPixmap(ptr)
                self["poster"].show()
        except:
            pass


class CiefpRTPlayer(Screen):
    """Screen for playing trailers using Movie Player"""
    skin = """
    <screen name="CiefpRTPlayer" position="center,center" size="1920,1080" title="Trailer Player">
        <widget name="status" position="60,40" size="1800,40" font="Regular;30" transparent="1" />
        <widget name="info" position="60,100" size="1800,60" font="Regular;34" transparent="1" foregroundColor="#00ff6e" />
        <ePixmap position="200,200" size="1520,680" zPosition="1" backgroundColor="#80000000" />
        <widget name="message" position="200,200" size="1520,680" font="Regular;36" transparent="1" halign="center" valign="center" foregroundColor="#ffffff" />

        <!-- Kontrole -->
        <ePixmap pixmap="buttons/red.png" position="60,1010" size="35,35" alphatest="blend" />
        <eLabel text="Exit" position="105,1002" size="180,45" font="Regular;26" />
        <ePixmap pixmap="buttons/green.png" position="330,1010" size="35,35" alphatest="blend" />
        <eLabel text="Open Browser" position="375,1002" size="280,45" font="Regular;26" />
        <ePixmap pixmap="buttons/yellow.png" position="620,1010" size="35,35" alphatest="blend" />
        <eLabel text="Download" position="665,1002" size="220,45" font="Regular;26" />
    </screen>
    """

    def __init__(self, session, trailer_url, trailer_type="hls", title=""):
        Screen.__init__(self, session)
        self.trailer_url = trailer_url
        self.trailer_type = trailer_type
        self.title = title
        self._downloaded_file = None
        self._closing = False  # NOVO
        self._exiting = False  # NOVO

        self["status"] = Label("Loading trailer...")
        self["info"] = Label(title if title else "Video Trailer")
        self["message"] = Label("Preparing stream...")

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "ok": self.close,
                "cancel": self.close,
                "red": self.close,
                "green": self._play_external,
                "yellow": self._download_video,  # Novo: Preuzmi video
            },
            -1
        )

        self._startTimer = eTimer()
        self._startTimer.callback.append(self._auto_play)
        self._startTimer.start(1000, True)
        self.onClose.append(self._on_close)

    def _auto_play(self):
        """Automatically try to play the trailer"""
        self._play_trailer()

    def _play_trailer(self):
        """Play HLS stream or YouTube trailer based on settings"""
        try:
            # Provjeri koji player je odabran
            if hasattr(config.plugins.ciefprt, 'player'):
                player_mode = config.plugins.ciefprt.player.value
            else:
                player_mode = "movieplayer"

            dlog(f"TRAILER: Using player mode: {player_mode}")

            if self.trailer_type == "hls":
                if player_mode == "browser":
                    self._play_external()
                elif player_mode == "download":
                    self._download_video()
                else:
                    self._play_hls_stream()
            else:
                if player_mode == "browser":
                    self._play_external()
                elif player_mode == "download":
                    self._download_video()
                else:
                    self._play_youtube()
        except Exception as e:
            dlog(f"TRAILER playback error: {e}")
            self._show_manual_instructions()

    def _play_hls_stream(self):
        """Play HLS stream using Movie Player"""
        try:
            if play_video_with_movieplayer(self.session, self.trailer_url, self.title):
                self["status"].setText("▶ Playing trailer... (press OK to stop)")
                self["message"].setText("")
                return
        except Exception as e:
            dlog(f"HLS playback error: {e}")

        # Ako direktna reprodukcija ne radi, ponudi eksterni player
        self._show_manual_instructions()

    def _play_youtube(self):
        """Play YouTube video using yt-dlp and Movie Player"""
        try:
            dlog(f"YT: Attempting to play: {self.trailer_url}")

            # Prvo probaj dobiti direktan stream URL preko yt-dlp
            stream_url = play_youtube_with_ytdlp(self.trailer_url)

            if stream_url:
                dlog(f"YT: Got stream URL, trying Movie Player...")
                # Pokušaj reproducirati sa Movie Player
                if play_video_with_movieplayer(self.session, stream_url, self.title):
                    self["status"].setText("▶ Playing trailer... (press OK to stop)")
                    self["message"].setText("")
                    dlog("YT: Playing with Movie Player")
                    return

            # Ako ne radi, pokušaj direktno sa YouTube linkom
            if play_video_with_movieplayer(self.session, self.trailer_url, self.title):
                self["status"].setText("▶ Playing YouTube trailer...")
                self["message"].setText("")
                return

        except Exception as e:
            dlog(f"YouTube playback error: {e}")

        # Ako ništa ne radi, ponudi eksterni player
        self._show_manual_instructions()

    def _show_manual_instructions(self):
        """Show manual instructions when automatic playback fails"""
        self["status"].setText("Press GREEN for browser, YELLOW to download")
        self["message"].setText(
            f"Trailer URL:\n\n"
            f"{self.trailer_url[:100]}...\n\n"
            f"GREEN - Open in browser\n"
            f"YELLOW - Download video\n"
            f"OK/RED - Close"
        )

    def _play_external(self):
        """Open trailer in external browser"""
        try:
            from Plugins.Extensions.DreamBrowser.plugin import DreamBrowser
            self.session.open(DreamBrowser, self.trailer_url)
        except:
            self.session.open(
                MessageBox,
                f"Open this URL in your browser:\n\n{self.trailer_url}",
                MessageBox.TYPE_INFO,
                timeout=15
            )

    def _download_video(self):
        """Download video for local playback"""
        try:
            import subprocess
            import tempfile

            # Kreiraj temp fajl
            self._downloaded_file = tempfile.mktemp(suffix='.mp4')

            self["status"].setText("Downloading video...")
            self["message"].setText("Please wait...")

            # Pokreni download u pozadini
            threading.Thread(
                target=self._download_thread,
                daemon=True
            ).start()

        except Exception as e:
            dlog(f"Download error: {e}")
            self.session.open(
                MessageBox,
                f"Download failed: {e}",
                MessageBox.TYPE_INFO,
                timeout=5
            )

    def _download_thread(self):
        """Download video in background thread"""
        try:
            import subprocess

            cmd = ['yt-dlp', '-f', 'best[height<=720]', '-o', self._downloaded_file, self.trailer_url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode == 0 and os.path.exists(self._downloaded_file):
                file_size = os.path.getsize(self._downloaded_file)
                if file_size > 0:
                    # Reproduciraj preuzeti fajl
                    self.ui(lambda: self._play_downloaded_file())
                    return

            # Ako download ne uspije
            self.ui(lambda: self["message"].setText("Download failed"))

        except Exception as e:
            dlog(f"Download thread error: {e}")
            self.ui(lambda: self["message"].setText(f"Download error: {e}"))

    def _play_downloaded_file(self):
        """Play downloaded video file"""
        try:
            if self._downloaded_file and os.path.exists(self._downloaded_file):
                if play_video_with_movieplayer(self.session, self._downloaded_file, self.title):
                    self["status"].setText("▶ Playing downloaded trailer...")
                    self["message"].setText("")
                    return
        except Exception as e:
            dlog(f"Play downloaded error: {e}")

        self["message"].setText("Could not play downloaded file")

    def ui(self, fn):
        """UI thread dispatcher"""
        if self._closing or self._exiting:
            return
        # Koristi eTimer za UI ažuriranje
        timer = eTimer()
        timer.callback.append(fn)
        timer.start(1, True)
        self._ui_timer = timer

    def _on_close(self):
        """Called when player is closed"""
        dlog("TRAILER: Player closed")
        # Ovdje možete dodati bilo kakvo čišćenje

# ---------- plugin entry ----------
def main(session, **kwargs):
    session.open(CiefpRTMain)


def Plugins(**kwargs):
    return [
        PluginDescriptor(
            name=f"{PLUGIN_NAME} v{PLUGIN_VERSION}",
            description="Browse RottenTomatoes",
            where=PluginDescriptor.WHERE_PLUGINMENU,
            icon="plugin.png",
            fnc=main
        ),
        PluginDescriptor(
            name=f"{PLUGIN_NAME}",
            description="RottenTomatoes browser",
            where=PluginDescriptor.WHERE_EXTENSIONSMENU,
            fnc=main
        ),
        PluginDescriptor(
            name=f"{PLUGIN_NAME} EPG",
            description="RottenTomatoes from EPG",
            where=PluginDescriptor.WHERE_EVENTINFO,
            fnc=main
        )
    ]