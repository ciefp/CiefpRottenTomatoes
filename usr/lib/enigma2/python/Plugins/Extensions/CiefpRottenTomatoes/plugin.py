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
from Components.config import config, ConfigSubsection, ConfigYesNo
from Screens.Screen import Screen
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.VirtualKeyBoard import VirtualKeyBoard
from enigma import eTimer, ePicLoad, getDesktop
from Plugins.Plugin import PluginDescriptor


PLUGIN_NAME = "CiefpRottenTomatoes"
PLUGIN_VERSION = "1.0"
BASE = "https://www.rottentomatoes.com"

CACHE_DIR = "/tmp/CiefpRottenTomatoes"
CACHE_POSTERS = os.path.join(CACHE_DIR, "posters")
CACHE_PAGES = os.path.join(CACHE_DIR, "pages")
DEBUG_LOG = os.path.join(CACHE_DIR, "debug.log")

PLUGIN_PATH = os.path.dirname(os.path.abspath(__file__))
PLACEHOLDER_IMG = os.path.join(PLUGIN_PATH, "placeholder.png")

# Fallback placeholder ako nema slike
if not os.path.exists(PLACEHOLDER_IMG):
    PLACEHOLDER_IMG = "/usr/share/enigma2/skin_default/noprev.png"

config.plugins.ciefprt = ConfigSubsection()
config.plugins.ciefprt.cache_enabled = ConfigYesNo(default=True)
config.plugins.ciefprt.auto_epg = ConfigYesNo(default=True)


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
    if u.startswith("http"):
        return u
    if u.startswith("/"):
        return BASE + u
    return BASE + "/" + u


# ---------- Search functions ----------
def search_rt(query, search_type="movie"):
    """Search Rotten Tomatoes using their API/autocomplete"""
    # RT koristi autocomplete endpoint za pretragu
    search_url = f"{BASE}/api/autocomplete?v=1&query={urllib.parse.quote(query)}"
    
    try:
        raw = http_get(search_url, timeout=10)
        data = json.loads(raw.decode("utf-8", "ignore"))
        
        results = []
        
        # Process movies
        if search_type == "movie" and "movies" in data:
            for movie in data["movies"][:20]:  # Limit to 20 movies
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
            for tv in data["tvSeries"][:20]:  # Limit to 20 TV shows
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

    except Exception as e:
        dlog(f"SEARCH API error: {e}")
        # Fallback to old method
        return search_rt_fallback(query, search_type)

def search_rt_fallback(query, search_type="movie"):
    """Fallback search using RT search page (/search?search=...)"""
    try:
        url = f"{BASE}/search?search={urllib.parse.quote(query)}"
        raw = http_get(url, timeout=10)
        html = raw.decode("utf-8", "ignore")
        return parse_search_page(html, search_type)[:20]
    except Exception as e:
        dlog(f"SEARCH fallback error: {e}")
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


def parse_browse(url):
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

def parse_detail(html):
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
    }

    # poster fallback (og:image)
    m = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html, re.I)
    if m:
        info["poster_url"] = (m.group(1) or "").strip()

    # Backdrop / Theme (rt-img slot="iconic") - src can contain multiple URLs separated by commas
    m = re.search(r'<rt-img[^>]+slot="iconic"[^>]+src="([^"]+)"', html, re.I)
    if m:
        src = (m.group(1) or "").strip()
        parts = [p.strip() for p in src.split(",") if p.strip()]
        if parts:
            info["backdrop_url"] = parts[-1]


    # scores + description (najstabilnije na novom RT)
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
            # RT često ima "100+ Verified Ratings" u bandedRatingCount
            info["audience_count"] = str(audience.get("bandedRatingCount", "") or audience.get("ratingCount", "") or "")

            if data.get("description"):
                info["synopsis"] = (data.get("description") or "").strip()
        except:
            pass

    # metadata-prop (PG, Now Playing, 1h 44m ...)
    props = re.findall(
        r'<rt-text[^>]+slot="metadata-prop"[^>]*>\s*([^<]+)\s*</rt-text>',
        html, flags=re.I
    )
    props = [p.strip() for p in props if p and p.strip()]

    # genres (Documentary, Biography...)
    genres = re.findall(
        r'<rt-text[^>]+slot="metadata-genre"[^>]*>\s*([^<]+)\s*</rt-text>',
        html, flags=re.I
    )
    genres = [g.strip() for g in genres if g and g.strip()]

    # map props -> fields
    for p in props:
        # MPAA rating: PG, R, PG-13, TV-MA...
        if re.match(r'^[A-Z0-9][A-Z0-9\-]{0,6}$', p) and not info["mpaa"]:
            info["mpaa"] = p
        # runtime: "1h 44m" / "44m"
        elif ("h" in p and "m" in p) or re.match(r"^\d+\s*m$", p, re.I):
            info["runtime"] = p
        # status: "Now Playing", "Streaming Now", etc
        elif "playing" in p.lower() or "stream" in p.lower() or "premiere" in p.lower():
            info["status"] = p
    if genres:
        info["genres"] = "/".join(genres)

    # --- Cast & Crew (JSON-LD) ---
    j = extract_jsonld_movie_tv(html)
    if j:
        # directors
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

        # cast / actors
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

    return info

# ---------- EPG functions ----------
def get_current_epg_info(session):
    """Get current EPG information for the playing channel"""
    try:
        # Get current service reference
        from ServiceReference import ServiceReference
        current_service = session.nav.getCurrentlyPlayingServiceReference()
        
        if not current_service:
            return None
            
        # Get service name
        service_ref = ServiceReference(current_service)
        service_name = service_ref.getServiceName()
        
        # Try to get EPG event info
        from enigma import eEPGCache
        epgcache = eEPGCache.getInstance()
        
        if epgcache:
            event_id = None
            # Try to get current event
            events = epgcache.lookupEvent(["IBDCT", (current_service.toString(), 0, -1, -1)])
            if events:
                for event in events:
                    event_name = event[4]  # Event title
                    event_desc = event[5]  # Event description
                    if event_name:
                        # Clean up the title - remove year and other info in parentheses
                        clean_title = re.sub(r'\s*\(\d{4}\)', '', event_name)  # Remove (2023)
                        clean_title = re.sub(r'\s*-\s*.*$', '', clean_title)  # Remove - Part 1 etc
                        clean_title = clean_title.strip()
                        
                        return {
                            "title": clean_title,
                            "original_title": event_name,
                            "description": event_desc or "",
                            "channel": service_name
                        }
        
        # Fallback: just return service/channel name
        return {
            "title": service_name,
            "channel": service_name
        }
        
    except Exception as e:
        dlog(f"EPG error: {e}")
        return None


# ---------- UI ----------
class CiefpRTMain(Screen):
    skin = """
    <screen position="center,center" size="1920,1080" title="CiefpRottenTomatoes">
        <widget name="status" position="60,40" size="1800,40" font="Regular;30" transparent="1" />

        <widget name="title" position="60,100" size="1200,55" font="Regular;44" transparent="1" foregroundColor="#00ff6e" />
        <widget name="meta" position="60,160" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00e1ff" />
        
        <widget name="score_tomo" position="60,210" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00FF4040" />
        <widget name="score_pop"  position="60,250" size="1200,40" font="Regular;30" transparent="1" foregroundColor="#00FFD84A" />
        <ePixmap position="200,350" size="1520,350" zPosition="1" backgroundColor="#80000000" />

        <widget name="help" position="120,300" size="1480,500"
        zPosition="2"
        font="Regular;34" transparent="1"
        foregroundColor="#FFFFFF"
        halign="center" valign="center" />

        <widget name="synopsis" position="60,320" size="1200,580" font="Regular;28" transparent="1" />
        <widget name="cast" position="60,900" size="1780,80" font="Regular;26" transparent="1" foregroundColor="#ff00ff" />

        <widget name="poster" position="1350,120" size="500,750" alphatest="blend" />

        <ePixmap pixmap="buttons/red.png" position="60,1010" size="35,35" alphatest="blend" />
        <eLabel text="Exit" position="105,1002" size="180,45" font="Regular;26" />
        <ePixmap pixmap="buttons/green.png" position="330,1010" size="35,35" alphatest="blend" />
        <eLabel text="Movies" position="375,1002" size="220,45" font="Regular;26" />
        <ePixmap pixmap="buttons/yellow.png" position="620,1010" size="35,35" alphatest="blend" />
        <eLabel text="Series" position="665,1002" size="220,45" font="Regular;26" />
        <ePixmap pixmap="buttons/blue.png" position="910,1010" size="35,35" alphatest="blend" />
        <eLabel text="Settings" position="955,1002" size="340,45" font="Regular;26" />
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
        
        # UI dispatcher
        self._uiq = []
        self._uit = eTimer()
        self._uit.callback.append(self._drain_uiq)
        self._uit.start(200, False)

        self.picload = ePicLoad()
        self.picload.PictureData.get().append(self._on_pic_ready)

        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "cancel": self.exit,
                "red": self.exit,
                "green": self.open_movies_menu,
                "yellow": self.open_series_menu,
                "blue": self.open_settings_menu,
                "ok": self.open_item_menu,
            },
            -1
        )
        
        # Load placeholder image on startup
        # timers (držimo reference da ne budu GC)
        self._epgTimer = eTimer()
        self._epgTimer.callback.append(self._check_epg)

        self._phTimer = eTimer()
        self._phTimer.callback.append(self._show_placeholder)

        # čekaj da layout završi pa tek onda placeholder
        self.onLayoutFinish.append(self._show_placeholder)
        # U __init__ metodi, na samom kraju:
        self.onLayoutFinish.append(self._show_startup_help)

    def _show_startup_help(self):
        dlog("HELP: shown")
        txt = (
            "Welcome to Ciefp Rotten Tomatoes\n\n"
            "GREEN  = Movies\n"
            "YELLOW = Series\n"
            "BLUE   = Settings\n\n"
            "Tip:\n"
            "Use Settings -> Clear Cache\n"
            "to free memory if plugin becomes slow.\n\n"
            "Press any key to continue..."
        )
        self["help"].setText(txt)
        self["help"].show()
        self.showing_help = True

    def _show_placeholder(self):
        """Show placeholder image (safe - waits for widget instance)"""
        try:
            if self._closing or self._exiting:
                return

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
        if self._closing or self._exiting:
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
        self._show_startup_help()

        # Check if auto EPG is enabled
        if config.plugins.ciefprt.auto_epg.value:
            self._epgTimer.start(1000, True)


    def _check_epg(self):
        """Check EPG and auto-search current program"""
        if self._closing or self._exiting:
            return
            
        epg_info = get_current_epg_info(self.session)
        if epg_info and epg_info.get("title"):
            title = epg_info["title"]
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
            self["status"].setText("Ready - No EPG info found")

    def _search_epg_thread(self, query):
        """Search for EPG program"""
        try:
            if self._closing or self._exiting:
                return
                
            dlog(f"EPG SEARCH: {query}")
            
            # First try movie search
            results = search_rt(query, search_type="movie")
            
            if not results:
                # Try TV search
                results = search_rt(query, search_type="tv")
            
            def process_results():
                if self._closing or self._exiting:
                    return
                    
                if results:
                    # Auto-select first result
                    if len(results) > 0:
                        self._load_item_details(results[0])
                        self["status"].setText(f"Found: {results[0]['name']}")
                    else:
                        self["status"].setText(f"No results for: {query}")
                        self._show_placeholder()
                else:
                    self["status"].setText(f"No results for: {query}")
                    self._show_placeholder()
            
            self.ui(process_results)
        except Exception as e:
            dlog(f"EPG SEARCH error: {e}")
            if not self._closing and not self._exiting:
                self.ui(lambda: self["status"].setText("EPG search failed"))
                self.ui(self._show_placeholder)

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
            ("At home", BASE + "/browse/movies_at_home/"),
            ("Coming soon", BASE + "/browse/movies_coming_soon/"),
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
            ("Acorn TV (Popular)", BASE + "/browse/tv_series_browse/affiliates:acorn-tv~sort:popular"),
            ("Fandango at Home (Popular)", BASE + "/browse/tv_series_browse/affiliates:fandango-at-home~sort:popular"),
            ("Search Series", "search_series"),
        ]
        self.session.openWithCallback(self._browse_choice, ChoiceBox, title="TV Series", list=menu)

    def open_settings_menu(self):
        self._hide_help()
        if self._closing or self._exiting:
            return
        
        cache_size = get_cache_size()
        cache_info = f" ({cache_size:.1f}MB)" if cache_size > 0 else ""
        
        menu = [
            (f"Clear Cache{cache_info}", "clear"),
            ("Show debug log (last 80 lines)", "showlog"),
            ("Clear debug log", "clearlog"),
            ("Auto EPG Search (current: %s)" % ("ON" if config.plugins.ciefprt.auto_epg.value else "OFF"), "auto_epg"),
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
        elif key == "about":
            about_text = f"""{PLUGIN_NAME} v{PLUGIN_VERSION}

Browse Rotten Tomatoes movies and TV series.

Features:
• Browse popular/trending content
• Search for movies and series
• Auto-search from EPG
• Cache system for faster loading
• Placeholder images for missing posters

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
                
                # Limit to 30 results
                display_results = results[:30]
                
                # Create choice list
                choice_list = [(item["name"], item) for item in display_results]
                
                def item_chosen(choice):
                    if not choice or self._closing or self._exiting:
                        return
                    self._load_item_details(choice[1])
                
                title = f"Search Results: {query} ({len(results)} found)"
                if len(results) > 30:
                    title += f" (showing 30)"
                    
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
            items = parse_browse(url)
            
            # Limit to 30 items max
            if len(items) > 30:
                items = items[:30]
                dlog(f"BROWSE: Limited to 30 items from {len(items)}")

            def show_choice():
                if self._closing or self._exiting:
                    return
                    
                if not items:
                    self["status"].setText("No items found")
                    return
                
                choice_list = [(item["name"], item) for item in items]
                
                def item_chosen(choice):
                    if not choice or self._closing or self._exiting:
                        return
                    self._load_item_details(choice[1])
                
                self.session.openWithCallback(
                    item_chosen, 
                    ChoiceBox, 
                    title=f"Select ({len(items)} items)", 
                    list=choice_list
                )
                self["status"].setText(f"Loaded {len(items)} items")

            self.ui(show_choice)
        except Exception as e:
            dlog("BROWSE: EXCEPTION\n%s" % traceback.format_exc())
            if not self._closing and not self._exiting:
                self.ui(lambda: self["status"].setText("Load failed"))

    # --- Load selected item ---
    def _load_item_details(self, item):
        if self._closing or self._exiting:
            return
            
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
            d = parse_detail(html)

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

        menu = [
            ("Show item URL", "url"),
        ]

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
        self.session.open(CiefpRTCelebrity, url, name)

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