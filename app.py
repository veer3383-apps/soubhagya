from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
import swisseph as swe
import threading
import psycopg2
import psycopg2.extras
import json
import re
import os

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# ============================================================
# CONFIG
# ============================================================
USERNAME   = "sonali91"
PASSWORD   = "suchita91"
SONALI_DOB = "25-08-1991"
SONALI_TIME = "19:10"
MIN_SCORE  = 18
BASE_URL   = "https://soubhagyalaxmi.com"
LOGIN_URL  = "https://soubhagyalaxmi.com/login"

def build_search_url(agemin, agemax):
    return (
        f"https://soubhagyalaxmi.com/search-result?type=full&religion=3&cast=Any"
        f"&mstatus=Single&gender=Male&agemin={agemin}&agemax={agemax}"
        f"&city=Any&state=Goa&page={{}}"
    )

_status = {
    "running": False, "message": "idle", "last_run": None,
    "result_count": 0, "step": "", "progress": 0, "total": 0
}

# ============================================================
# DATABASE
# ============================================================
def get_db():
    return psycopg2.connect(os.environ["DATABASE_URL"], sslmode="require")

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id       SERIAL PRIMARY KEY,
                    ran_at   TIMESTAMP DEFAULT NOW(),
                    agemin   INTEGER,
                    agemax   INTEGER,
                    months   INTEGER,
                    profiles JSONB NOT NULL
                )
            """)
        conn.commit()
    print("✅ Database ready")

def db_save(matched, agemin, agemax, months):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (agemin, agemax, months, profiles) VALUES (%s,%s,%s,%s)",
                (agemin, agemax, months, json.dumps(matched))
            )
        conn.commit()

def db_latest():
    """Returns (profiles_list, ran_at_str) for the most recent run, or ([], None)."""
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT profiles, ran_at FROM runs ORDER BY ran_at DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    return row["profiles"], row["ran_at"].strftime("%d %b %Y %H:%M")
    except Exception as e:
        print(f"DB read error: {e}")
    return [], None

# ============================================================
# ASTRO DATA
# ============================================================
NAKSHATRA_LIST = [
    "Ashwini","Bharani","Krittika","Rohini","Mrigashira","Ardra","Punarvasu",
    "Pushya","Ashlesha","Magha","Purva Phalguni","Uttara Phalguni","Hasta",
    "Chitra","Swati","Vishakha","Anuradha","Jyeshtha","Mula","Purva Ashadha",
    "Uttara Ashadha","Shravana","Dhanishta","Shatabhisha","Purva Bhadrapada",
    "Uttara Bhadrapada","Revati"
]
RASHI_LIST = [
    "Aries","Taurus","Gemini","Cancer","Leo","Virgo",
    "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"
]
GANA_MAP = [
    "Deva","Manushya","Rakshasa","Manushya","Deva","Rakshasa","Deva",
    "Deva","Rakshasa","Rakshasa","Manushya","Manushya","Deva",
    "Rakshasa","Deva","Rakshasa","Deva","Rakshasa","Rakshasa",
    "Manushya","Manushya","Deva","Rakshasa","Rakshasa","Manushya",
    "Manushya","Deva"
]
NADI_MAP = [
    "Aadi","Madhya","Antya","Antya","Madhya","Aadi","Aadi",
    "Madhya","Antya","Antya","Madhya","Aadi","Aadi",
    "Madhya","Antya","Antya","Madhya","Aadi","Aadi",
    "Madhya","Antya","Antya","Madhya","Aadi","Aadi",
    "Madhya","Antya"
]
YONI_MAP = [
    "Horse","Elephant","Goat","Serpent","Serpent","Dog","Cat","Goat","Cat",
    "Rat","Rat","Cow","Buffalo","Tiger","Buffalo","Tiger","Deer","Deer",
    "Dog","Monkey","Mongoose","Monkey","Lion","Horse","Lion","Cow","Elephant",
]
RASHI_LORDS = [
    "Mars","Venus","Mercury","Moon","Sun","Mercury",
    "Venus","Mars","Jupiter","Saturn","Saturn","Jupiter"
]
RELATION = {
    "Sun":    {"friend":["Moon","Mars","Jupiter"],  "enemy":["Venus","Saturn"],     "neutral":["Mercury"]},
    "Moon":   {"friend":["Sun","Mercury"],           "enemy":[],                    "neutral":["Mars","Jupiter","Venus","Saturn"]},
    "Mars":   {"friend":["Sun","Moon","Jupiter"],    "enemy":["Mercury"],           "neutral":["Venus","Saturn"]},
    "Mercury":{"friend":["Sun","Venus"],             "enemy":["Moon"],              "neutral":["Mars","Jupiter","Saturn"]},
    "Jupiter":{"friend":["Sun","Moon","Mars"],       "enemy":["Venus","Mercury"],   "neutral":["Saturn"]},
    "Venus":  {"friend":["Mercury","Saturn"],        "enemy":["Sun","Moon"],        "neutral":["Mars","Jupiter"]},
    "Saturn": {"friend":["Mercury","Venus"],         "enemy":["Sun","Moon","Mars"], "neutral":["Jupiter"]}
}
GRAHA_SCORE = {
    ("friend",  "friend"):  5,   ("friend",  "neutral"): 4,
    ("neutral", "friend"):  4,   ("neutral", "neutral"): 3,
    ("friend",  "enemy"):   1,   ("enemy",   "friend"):  1,
    ("neutral", "enemy"):   0.5, ("enemy",   "neutral"): 0.5,
    ("enemy",   "enemy"):   0,
}
VASYA_TYPE = [
    "Chatushpada","Chatushpada","Manava","Jalachara","Vanachara","Manava",
    "Manava","Keeta","Chatushpada","Chatushpada","Manava","Jalachara",
]
YONI_ENEMY = {
    frozenset(["Horse","Buffalo"]),   frozenset(["Elephant","Lion"]),
    frozenset(["Goat","Monkey"]),     frozenset(["Dog","Deer"]),
    frozenset(["Cat","Rat"]),         frozenset(["Cow","Tiger"]),
    frozenset(["Serpent","Mongoose"]),
}
YONI_FRIENDLY = {
    frozenset(["Horse","Deer"]),      frozenset(["Horse","Serpent"]),
    frozenset(["Horse","Monkey"]),    frozenset(["Elephant","Cow"]),
    frozenset(["Elephant","Rat"]),    frozenset(["Goat","Tiger"]),
    frozenset(["Goat","Deer"]),       frozenset(["Serpent","Cat"]),
    frozenset(["Dog","Cat"]),         frozenset(["Rat","Cow"]),
    frozenset(["Buffalo","Tiger"]),   frozenset(["Buffalo","Cat"]),
    frozenset(["Monkey","Lion"]),     frozenset(["Monkey","Deer"]),
    frozenset(["Lion","Mongoose"]),
}
YONI_PARTIAL_ENEMY = {
    frozenset(["Horse","Tiger"]),     frozenset(["Elephant","Mongoose"]),
    frozenset(["Goat","Dog"]),        frozenset(["Serpent","Cat"]),
    frozenset(["Deer","Rat"]),        frozenset(["Monkey","Buffalo"]),
    frozenset(["Lion","Cow"]),
}

# ============================================================
# PARSERS
# ============================================================
def parse_date(d):
    for f in ["%d-%m-%Y", "%d %b %Y", "%d %B %Y"]:
        try:
            return datetime.strptime(d.strip(), f)
        except:
            continue
    raise ValueError(f"Cannot parse date: {d}")

def parse_time(t):
    if not t:
        return "12:00"
    t = t.strip().lower()
    t = re.sub(r'(\d+)\.(\d+)', r'\1:\2', t)
    t = re.sub(r'^(\d{1,2})\s+(\d{2})\s*(am|pm)$', r'\1:\2 \3', t)
    t = t.replace(" ", "")
    t = re.sub(r'(\d)(am|pm)', r'\1 \2', t)
    if re.match(r'^\d+\s*(am|pm)$', t):
        t = t.replace(" ", ":00 ")
    for f in ["%I:%M %p", "%H:%M", "%I %p"]:
        try:
            return datetime.strptime(t, f).strftime("%H:%M")
        except:
            continue
    return "12:00"

def is_recent(last_seen_str, months=12):
    if not last_seen_str:
        return False
    try:
        days_limit = int(months * 30.44)
        return (datetime.today() - datetime.strptime(last_seen_str, "%d %b %Y")).days <= days_limit
    except:
        return False

# ============================================================
# MOON CALCULATION
# ============================================================
IST_OFFSET = 5.5

def get_moon(dob, time):
    swe.set_sid_mode(swe.SIDM_LAHIRI)
    d = parse_date(dob)
    t = datetime.strptime(time, "%H:%M")
    utc_hour = t.hour + t.minute / 60 - IST_OFFSET
    day_offset = 0
    if utc_hour < 0:
        utc_hour += 24
        day_offset = -1
    d_utc = d + timedelta(days=day_offset)
    jd   = swe.julday(d_utc.year, d_utc.month, d_utc.day, utc_hour)
    moon = swe.calc_ut(jd, swe.MOON, swe.FLG_SIDEREAL)[0][0]
    return int(moon / (360 / 27)), int(moon / 30)

# ============================================================
# KOOT FUNCTIONS
# ============================================================
def varna(r1, r2):
    v = [3, 2, 1, 4, 3, 2, 1, 4, 3, 2, 1, 4]
    return 1 if v[r2] >= v[r1] else 0

def vashya(r1, r2):
    t1, t2 = VASYA_TYPE[r1], VASYA_TYPE[r2]
    if t1 == t2:
        return 2
    if {t1, t2} in [{"Manava","Chatushpada"}, {"Manava","Keeta"}]:
        return 1
    return 0

def _tara_one(a, b):
    return 0 if ((b - a) % 27) % 9 in [0, 2, 4, 6] else 3

def tara(n1, n2):
    return (_tara_one(n1, n2) + _tara_one(n2, n1)) / 2

def yoni(n1, n2):
    y1, y2 = YONI_MAP[n1], YONI_MAP[n2]
    if y1 == y2:
        return 4
    pair = frozenset([y1, y2])
    if pair in YONI_ENEMY:         return 0
    if pair in YONI_PARTIAL_ENEMY: return 1
    if pair in YONI_FRIENDLY:      return 3
    return 2

def _rel(a, b):
    if b in RELATION[a]["friend"]: return "friend"
    if b in RELATION[a]["enemy"]:  return "enemy"
    return "neutral"

def graha(r1, r2):
    l1, l2 = RASHI_LORDS[r1], RASHI_LORDS[r2]
    return GRAHA_SCORE[(_rel(l1, l2), _rel(l2, l1))]

def gana(n1, n2):
    g1, g2 = GANA_MAP[n1], GANA_MAP[n2]
    if g1 == g2:                         return 6
    if {g1, g2} == {"Deva","Manushya"}:  return 5
    return 0

def bhakoot(r1, r2):
    d = abs(r1 - r2)
    return 0 if d in [1, 11, 4, 8, 5, 7] else 7

def nadi(n1, n2):
    return 0 if NADI_MAP[n1] == NADI_MAP[n2] else 8

def score_breakdown(n1, r1, n2, r2):
    return {
        "Varna (1)":        varna(r1, r2),
        "Vashya (2)":       vashya(r1, r2),
        "Tara (3)":         tara(n1, n2),
        "Yoni (4)":         yoni(n1, n2),
        "Graha Maitri (5)": graha(r1, r2),
        "Gana (6)":         gana(n1, n2),
        "Bhakoot (7)":      bhakoot(r1, r2),
        "Nadi (8)":         nadi(n1, n2),
    }

# ============================================================
# SCRAPER
# ============================================================
def login(page):
    page.goto(LOGIN_URL)
    page.wait_for_selector("input[type='text']")
    page.fill("input[type='text']", USERNAME)
    page.fill("input[type='password']", PASSWORD)
    page.click("input[type='submit']")
    page.wait_for_timeout(3000)
    if "login" in page.url.lower():
        raise Exception("Login failed — check credentials")
    print("✅ Logged in")

def get_total_pages(page, search_url):
    page.goto(search_url.format(1))
    page.wait_for_selector(".pager")
    max_page = 1
    for link in page.query_selector_all(".pager a"):
        href = link.get_attribute("href")
        if href and "page=" in href:
            try:
                max_page = max(max_page, int(href.split("page=")[-1]))
            except:
                pass
    return max_page

def get_profile_ids(page, total_pages, search_url):
    profiles = {}
    for i in range(1, total_pages + 1):
        _status["message"]  = f"Scanning search page {i} of {total_pages}..."
        _status["step"]     = "scanning"
        _status["progress"] = i
        _status["total"]    = total_pages
        page.goto(search_url.format(i))
        page.wait_for_timeout(1500)
        if "no data was returned" in page.content().lower():
            break
        for card in page.query_selector_all(".profileBox"):
            try:
                href = card.query_selector("a[href^='/profile/']").get_attribute("href")
                pid  = href.split("/")[-1]
                header = card.query_selector("div[style*='color:#ff6a00']").inner_text()
                last_seen = header.split("Last Seen :")[-1].strip() if "Last Seen :" in header else None
                profiles[pid] = last_seen
            except:
                continue
    return profiles

def extract_profile(page, profile_id):
    url = f"{BASE_URL}/profile/{profile_id}"
    page.goto(url)
    page.wait_for_selector(".profileDetails")
    data = {"id": profile_id, "url": url}
    for table in page.query_selector_all(".profileDetails table"):
        for row in table.query_selector_all("tr"):
            cols = row.query_selector_all("td")
            if len(cols) == 2:
                key = cols[0].inner_text().strip()
                val = cols[1].inner_text().strip()
                if key:
                    data[key] = val
    return data

# ============================================================
# MATCHER
# ============================================================
def match_profiles(raw_profiles):
    sn, sr = get_moon(SONALI_DOB, SONALI_TIME)
    print(f"Sonali → Nak: {NAKSHATRA_LIST[sn]} ({sn}), Rashi: {RASHI_LIST[sr]} ({sr})")

    out   = []
    total = len(raw_profiles)

    for idx, p in enumerate(raw_profiles):
        _status["progress"] = idx + 1
        _status["message"]  = f"Matching {idx+1} of {total}: {p.get('id','?')}"
        try:
            dob = p.get("DOB")
            if not dob:
                continue

            raw  = p.get("Birth Time")
            used = parse_time(raw)
            n, r = get_moon(dob, used)
            bd   = score_breakdown(sn, sr, n, r)
            score = sum(bd.values())

            if nadi(sn, n) == 0:
                print(f"  ❌ {p['id']} — NADI DOSHA ({score})")
                continue
            if bhakoot(sr, r) == 0:
                print(f"  ❌ {p['id']} — BHAKOOT DOSHA ({score})")
                continue
            if score < MIN_SCORE:
                print(f"  ❌ {p['id']} — LOW SCORE ({score})")
                continue

            print(f"  ✅ {p['id']} — {score}/36")

            x = p.copy()
            x["matched_with"]          = "Sonali"
            x["match_score"]           = round(score, 1)
            x["compatibility_percent"] = round(score / 36 * 100, 1)
            x["score_breakdown"]       = bd
            x["nadi_dosha"]            = (nadi(sn, n) == 0)
            x["bhakoot_dosha"]         = (bhakoot(sr, r) == 0)
            x["image_thumb"]           = f"https://soubhagyalaxmi.com/upload/members/{p['id']}-thumb.jpg"
            x["image_full"]            = f"https://soubhagyalaxmi.com/upload/members/{p['id']}-full.jpg"
            out.append(x)

        except Exception as e:
            print(f"  ERROR [{p.get('id','?')}]: {e}")

    out.sort(key=lambda x: x["match_score"], reverse=True)
    for i, v in enumerate(out):
        v["rank"] = i + 1

    return out

# ============================================================
# PIPELINE — runs in background thread
# ============================================================
def _run_pipeline(agemin=33, agemax=39, months=12):
    _status["running"]  = True
    _status["step"]     = "login"
    _status["progress"] = 0
    _status["total"]    = 0
    _status["message"]  = "Starting up..."

    try:
        search_url = build_search_url(agemin, agemax)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            page = browser.new_page()

            _status["step"]    = "login"
            _status["message"] = "Logging into Soubhagya Laxmi..."
            login(page)

            _status["step"]    = "counting"
            _status["message"] = "Counting search result pages..."
            total_pages = get_total_pages(page, search_url)
            print(f"Total search pages: {total_pages}")

            _status["step"]    = "scanning"
            _status["message"] = f"Scanning {total_pages} pages for profiles..."
            profiles_map = get_profile_ids(page, total_pages, search_url)
            print(f"Found {len(profiles_map)} total profiles")

            recent = [(pid, ls) for pid, ls in profiles_map.items() if is_recent(ls, months)]
            print(f"Active in last {months} months: {len(recent)}")
            _status["message"] = f"Found {len(recent)} active profiles. Scraping details..."

            _status["step"]  = "scraping"
            _status["total"] = len(recent)
            raw_profiles = []
            for idx, (pid, last_seen) in enumerate(recent):
                _status["progress"] = idx + 1
                _status["message"]  = f"Fetching profile {idx+1} of {len(recent)}: {pid}"
                try:
                    profile_data = extract_profile(page, pid)
                    profile_data["last_seen"] = last_seen
                    raw_profiles.append(profile_data)
                except Exception as e:
                    print(f"  Error scraping {pid}: {e}")

            browser.close()

        _status["step"]     = "matching"
        _status["progress"] = 0
        _status["total"]    = len(raw_profiles)
        _status["message"]  = f"Running Ashtakoot matching on {len(raw_profiles)} profiles..."
        matched = match_profiles(raw_profiles)

        _status["message"] = "Saving results to database..."
        db_save(matched, agemin, agemax, months)

        _status["result_count"] = len(matched)
        _status["step"]         = "done"
        _status["message"]      = f"Complete — {len(matched)} matches found"
        _status["last_run"]     = datetime.now().strftime("%d %b %Y %H:%M")
        print(f"\n✅ Done. {len(matched)} matches saved to database.")

    except Exception as e:
        _status["step"]    = "error"
        _status["message"] = f"Error: {str(e)}"
        print(f"Pipeline error: {e}")
    finally:
        _status["running"] = False

# ============================================================
# FLASK API
# ============================================================
@app.route("/api/run", methods=["POST"])
def run_pipeline():
    if _status["running"]:
        return jsonify({"error": "Already running"}), 409
    body   = request.get_json(silent=True) or {}
    agemin = int(body.get("agemin", 33))
    agemax = int(body.get("agemax", 39))
    months = int(body.get("months", 12))
    threading.Thread(target=_run_pipeline, args=(agemin, agemax, months), daemon=True).start()
    return jsonify({"message": "Pipeline started"})

@app.route("/api/has_results")
def has_results():
    profiles, last_run = db_latest()
    return jsonify({"exists": len(profiles) > 0, "count": len(profiles), "last_run": last_run})

@app.route("/api/status")
def get_status():
    count = _status["result_count"]
    if count == 0:
        profiles, _ = db_latest()
        count = len(profiles)
    return jsonify({**_status, "result_count": count})

@app.route("/api/results")
def get_results():
    profiles, _ = db_latest()
    return jsonify(profiles)

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
