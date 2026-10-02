"""週末去哪：活動爬蟲（只用 Python 標準函式庫）。

來源
  tourism   交通部觀光署「觀光資訊資料庫：活動」開放資料（data.gov.tw 7778）
  moc       文化部「文化資料開放服務網」展演開放資料（data.gov.tw 6012 等）
  accupass  Accupass 活動通搜尋頁與活動頁（robots.txt 允許；讀活動頁的 schema.org JSON-LD）

禮貌規則
  每個網址兩次請求之間至少間隔 DELAY 秒；Accupass 每次最多讀 ACCUPASS_MAX_PAGES 個活動頁，
  已經抓過的活動頁直接沿用上次結果（--prev）。遇到 403、429 或逾時就停用該來源，不重試、不繞過。

用法
  python scripts/crawl.py --out <輸出資料夾> [--prev <上次的 crawled.json>] [--overrides overrides.json]
修正檔 overrides.json
  {"<活動 id>": {"town": "信義區", "category": "market", "hidden": true, ...}}
  由 Claude 維護：補鄉鎮、改分類、隱藏不適合的活動。town、venue、address 套到第一個地點，其他欄位直接覆蓋。
輸出
  <輸出資料夾>/crawled.json   全部活動與各來源狀態
  <輸出資料夾>/docs/<id>.json 每筆活動一個檔，可直接給 ArtifactData batch 的 file_path 用
"""
import argparse, gzip, hashlib, html, io, json, pathlib, re, sys, time, unicodedata, urllib.parse, urllib.request, zipfile
from datetime import datetime, timedelta, timezone

UA = "Mozilla/5.0 (compatible; weekend-tw-bot/1.0; +https://github.com/)"
DELAY = 2.0
HORIZON_DAYS = 90          # 只收 90 天內開始的活動
MAX_SPAN_DAYS = 180        # 持續超過半年的（常設展、整年度方案）不收
ACCUPASS_QUERIES = ["市集", "展覽", "快閃店", "音樂節", "親子活動"]
# 課程、培訓、招商、揪團這類不是「週末去逛」的活動，不收
ACCUPASS_SKIP = re.compile(r"課|工作坊|培訓|實戰班|講座|研討|說明會|年會|讀書會|招攤|招商|招募|徵攤|報名|聚會所|任務|挑戰|交友|聯誼|單身|沙龍|星盤|占卜")
# 場地欄位有時填的是交通說明，這時改用地址
VAGUE_VENUE = re.compile(r"^[（(]|^交通|^近|^捷運|^導航|出口|步行")
ACCUPASS_MAX_PAGES = 40
MOC_CATEGORIES = {"6": "exhibit", "4": "outdoor", "17": "festival", "1": "festival"}

TW = timezone(timedelta(hours=8))
TODAY = datetime.now(TW).strftime("%Y-%m-%d")
HORIZON = (datetime.now(TW) + timedelta(days=HORIZON_DAYS)).strftime("%Y-%m-%d")

COUNTIES = ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "苗栗縣", "臺中市", "彰化縣", "南投縣",
            "雲林縣", "嘉義市", "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣",
            "金門縣", "連江縣"]

_last_hit = {}


class Blocked(Exception):
    pass


def fetch(url, binary=False):
    host = urllib.parse.urlparse(url).netloc
    wait = DELAY - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Blocked(f"HTTP {e.code}")
        raise
    finally:
        _last_hit[host] = time.time()
    return data if binary else data.decode("utf-8-sig", errors="replace")


# ---------- text helpers ----------

LONG_DASHES = re.compile("[%s%s]" % (chr(0x2013), chr(0x2014)))
MATH_ALNUM = re.compile("[%s-%s]+" % (chr(0x1D400), chr(0x1D7FF)))
EMOJI = re.compile("[%s-%s%s-%s%s%s]" % (chr(0x1F000), chr(0x1FAFF), chr(0x2600), chr(0x27BF), chr(0xFE0F), chr(0x200D) + chr(0x200B)))
HAN = "%s-%s" % (chr(0x4E00), chr(0x9FFF))
TOWN_AT_START = re.compile(r"\s*([%s]{1,3}?[區鄉鎮市])" % HAN)
TOWN_AFTER_LABEL = re.compile(r"[｜|\s（(]([%s]{1,3}?[區鄉鎮])" % HAN)


def clean_text(s, limit=None):
    if not s:
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s)))
    s = LONG_DASHES.sub("-", s)
    s = EMOJI.sub("", s)
    s = MATH_ALNUM.sub(lambda m: unicodedata.normalize("NFKC", m.group()), s)  # 𝟮𝟬𝟮𝟲 這類花體字改回一般字
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        cut = s[:limit]
        stop = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
        s = cut[: stop + 1] if stop > limit * 0.4 else cut.rstrip() + "…"
    return s


def norm_county(s):
    s = (s or "").replace("台", "臺")
    return s if s in COUNTIES else ""


def parse_address(addr):
    """從地址字串找出縣市與鄉鎮市區。找不到鄉鎮就回傳空字串，交給 Claude 補。"""
    a = re.sub(r"^(台灣|臺灣)", "", (addr or "").replace("台", "臺"))
    a = re.sub(r"\d{3,6}", " ", a)
    county = next((c for c in COUNTIES if c in a), "")
    town = ""
    if county:
        rest = a.split(county, 1)[1].lstrip(" ,")
        while rest.lstrip().startswith(county):  # 「臺中市40453 臺中市北區…」這種重複縣市名
            rest = rest.lstrip()[len(county):]
        m = TOWN_AT_START.match(rest) or TOWN_AFTER_LABEL.search(rest)
        if m and not m.group(1).endswith("路"):
            town = m.group(1)
            if town == county:
                town = ""
    return county, town


CAT_RULES = [
    ("mall", r"百貨|週年慶|周年慶|購物節|outlet|購物中心"),
    ("market", r"市集|市場|夜市|擺攤|農夫|小農|跳蚤"),
    ("exhibit", r"特展|展覽|展出|博覽會|美術館|博物館|藝廊|畫展|攝影展|[^發]展$"),
    ("outdoor", r"單車|自行車|騎乘|健行|登山|步道|花季|賞花|花海|露營|樂園|親子|生態|導覽|觀星|海灘|衝浪"),
]


def guess_category(title, desc, default="festival"):
    t = f"{title} {desc[:80]}"
    for cat, pat in CAT_RULES:
        if re.search(pat, t, re.I):
            return cat
    return default


def keep(start, end):
    if not end:
        end = start
    if not end or end < TODAY:
        return False
    if start and start > HORIZON:
        return False
    if start and end:
        span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
        if span > MAX_SPAN_DAYS:
            return False
    return True


def make_id(prefix, raw):
    return f"c-{prefix}-" + hashlib.sha1(str(raw).encode()).hexdigest()[:12]


def event(**kw):
    e = {k: v for k, v in kw.items() if v not in (None, "", [], {})}
    e["verified_on"] = TODAY
    e["crawled"] = True
    return e


# ---------- sources ----------

def crawl_tourism():
    url = "https://media.taiwan.net.tw/XMLReleaseAll_public/v2.0/Zh_tw/Event-json.zip"
    z = zipfile.ZipFile(io.BytesIO(fetch(url, binary=True)))
    data = json.loads(z.read("EventList.json").decode("utf-8-sig"))
    out = []
    for ev in data.get("Events", []):
        start = (ev.get("StartDateTime") or "")[:10]
        end = (ev.get("EndDateTime") or "")[:10]
        if ev.get("EventStatus") not in (None, "", "EventScheduled", "EventRescheduled") or not keep(start, end):
            continue
        pa = ev.get("PostalAddress") or {}
        county, town = norm_county(pa.get("City")), (pa.get("Town") or "").replace("台", "臺")
        if not county:
            continue
        title, desc = clean_text(ev.get("EventName")), clean_text(ev.get("Description"))
        site = ev.get("WebsiteURL") or next(iter(ev.get("SameAsURLs") or []), "") or ""
        out.append(event(
            id=make_id("tour", ev.get("EventID")), title=title,
            category=guess_category(title, desc), start_date=start, end_date=end,
            locations=[{"county": county, "town": town, "venue": clean_text(pa.get("StreetAddress")) or title,
                        "address": clean_text(pa.get("StreetAddress"))}],
            ticket="free" if ev.get("IsAccessibleForFree") == 1 else ("paid" if ev.get("FeeInfo") else "unknown"),
            price=clean_text(ev.get("FeeInfo"), 80), summary=clean_text(desc, 120),
            organizer=", ".join(clean_text(o.get("Name")) for o in ev.get("Organizations") or [] if o.get("Name")),
            tips=clean_text(ev.get("TrafficInfo"), 100),
            source_url=site if site.startswith("https://") else "https://www.taiwan.net.tw/m1.aspx?sNo=0001019",
            source_name="交通部觀光署觀光資訊資料庫", source_type="official",
        ))
    return out


def crawl_moc():
    out = []
    for cat, our_cat in MOC_CATEGORIES.items():
        url = f"https://cloud.culture.tw/frontsite/trans/SearchShowAction.do?method=doFindTypeJ&category={cat}"
        for it in json.loads(fetch(url)):
            start = (it.get("startDate") or "").replace("/", "-")[:10]
            end = (it.get("endDate") or "").replace("/", "-")[:10]
            if not keep(start, end):
                continue
            locs, seen, prices = [], set(), set()
            for s in it.get("showInfo") or []:
                county, town = parse_address(s.get("location"))
                name = clean_text(s.get("locationName"))
                if not county or (county, name) in seen:
                    continue
                seen.add((county, name))
                locs.append({"county": county, "town": town, "venue": name or clean_text(s.get("location")),
                             "address": clean_text(s.get("location"))})
                if s.get("price"):
                    prices.add(clean_text(s.get("price"), 60))
            if not locs:
                continue
            title, desc = clean_text(it.get("title")), clean_text(it.get("descriptionFilterHtml"))
            price = " / ".join(sorted(prices))[:120]
            free = bool(re.search(r"免費|免票|自由入場|不收費", price + desc[:200]))
            site = it.get("sourceWebPromote") or it.get("webSales") or ""
            out.append(event(
                id=make_id("moc", it.get("UID")), title=title,
                category=our_cat if our_cat != "festival" else guess_category(title, desc, "festival"),
                start_date=start, end_date=end, locations=locs[:6],
                ticket="free" if free else ("paid" if price else "unknown"), price=price,
                indoor=True if cat == "6" else None, summary=clean_text(desc, 120),
                organizer=clean_text(it.get("showUnit")),
                source_url=site if site.startswith("https://") else "https://cloud.culture.tw/",
                source_name="文化部文化資料開放服務網", source_type="official",
            ))
    return out


def accupass_venue(title, venue, address):
    """回傳可放上地圖的場地名稱；不該收的活動回傳空字串。
    沒有門牌地址（只寫「捷運站附近」這類）或標題是課程、招商、揪團的，都不收。"""
    address = clean_text(address)
    if ACCUPASS_SKIP.search(title or "") or not re.search(r"\d", address):
        return ""
    venue = clean_text(venue)
    if not venue or VAGUE_VENUE.search(venue):
        venue = re.sub(r"^(台灣|臺灣)", "", address)
    return venue


def crawl_accupass(prev):
    known = {e["source_url"]: e for e in prev if e.get("id", "").startswith("c-acc-")}
    urls = []
    for q in ACCUPASS_QUERIES:
        page = fetch("https://www.accupass.com/search?q=" + urllib.parse.quote(q))
        for path in re.findall(r'href="(/event/\d{10,})', page):
            u = "https://www.accupass.com" + path
            if u not in urls:
                urls.append(u)
    out, fetched = [], 0
    for u in urls:
        if u in known and known[u].get("end_date", "") >= TODAY:
            old = known[u]  # 舊資料也要符合現在的收錄規則
            loc = (old.get("locations") or [{}])[0]
            old["title"] = clean_text(old.get("title"))
            old["summary"] = clean_text(old.get("summary"), 120)
            venue = accupass_venue(old["title"], loc.get("venue"), loc.get("address"))
            if venue:
                loc["venue"] = venue
                if not loc.get("town"):
                    loc["town"] = parse_address(loc.get("address"))[1]
                out.append(old)
            continue
        if fetched >= ACCUPASS_MAX_PAGES:
            continue
        fetched += 1
        page = fetch(u)
        for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
            try:
                ld = json.loads(block)
            except ValueError:
                continue
            if ld.get("@type") != "Event":
                continue
            if "Offline" not in (ld.get("eventAttendanceMode") or "Offline"):
                break
            start, end = (ld.get("startDate") or "")[:10], (ld.get("endDate") or "")[:10]
            loc = ld.get("location") or {}
            county, town = parse_address(loc.get("address"))
            title, desc = clean_text(ld.get("name")), clean_text(ld.get("description"))
            venue = accupass_venue(title, loc.get("name"), loc.get("address"))
            if not county or not keep(start, end) or not venue:
                break
            out.append(event(
                id=make_id("acc", u), title=title, category=guess_category(title, desc, "festival"),
                start_date=start, end_date=end,
                locations=[{"county": county, "town": town, "venue": venue,
                            "address": clean_text(loc.get("address"))}],
                ticket="unknown", summary=clean_text(desc, 120),
                organizer=clean_text((ld.get("organizer") or {}).get("name")),
                source_url=u, source_name="Accupass 活動通", source_type="media",
            ))
            break
    return out


# ---------- merge ----------

def title_key(e):
    t = re.sub(r"20\d\d|11\d年|第[一二三四五六七八九十\d]+屆|[\s\W_]", "", e.get("title", "")).lower()
    c = (e.get("locations") or [{}])[0].get("county", "")
    return f"{c}|{t}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--prev")
    ap.add_argument("--overrides")
    args = ap.parse_args()
    out_dir = pathlib.Path(args.out)
    (out_dir / "docs").mkdir(parents=True, exist_ok=True)
    prev = []
    if args.prev and pathlib.Path(args.prev).exists():
        prev = json.loads(pathlib.Path(args.prev).read_text(encoding="utf-8")).get("events", [])

    status, merged, seen = {}, [], set()
    for name, fn in [("tourism", crawl_tourism), ("moc", crawl_moc), ("accupass", lambda: crawl_accupass(prev))]:
        try:
            items = fn()
            status[name] = {"ok": True, "count": len(items)}
        except Exception as e:  # 一個來源壞掉不影響其他來源
            items = [p for p in prev if p.get("id", "").startswith({"tourism": "c-tour-", "moc": "c-moc-", "accupass": "c-acc-"}[name])]
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "kept_from_previous": len(items)}
        for e in items:
            k = title_key(e)
            if k in seen:
                continue
            seen.add(k)
            merged.append(e)

    if args.overrides and pathlib.Path(args.overrides).exists():
        fixes = json.loads(pathlib.Path(args.overrides).read_text(encoding="utf-8"))
        kept = []
        for e in merged:
            fx = fixes.get(e["id"]) or {}
            if fx.get("hidden"):
                continue
            for k, v in fx.items():
                if k in ("town", "venue", "address"):
                    e["locations"][0][k] = v
                elif k != "note":
                    e[k] = v
            kept.append(e)
        merged = kept

    result = {"generated_at": datetime.now(TW).isoformat(timespec="seconds"), "sources": status, "events": merged}
    (out_dir / "crawled.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for old in (out_dir / "docs").glob("*.json"):
        old.unlink()
    for e in merged:
        doc = {k: v for k, v in e.items() if k != "id"}
        (out_dir / "docs" / f"{e['id']}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))
    print(f"total {len(merged)} events")


if __name__ == "__main__":
    sys.exit(main())
