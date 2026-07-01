import json, re, hashlib, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

import requests, feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_NOW  = datetime.now(timezone.utc)
YEARS = [2025, 2026]
log.info(f"Target tahun: {YEARS}")

# Target minimum artikel keseluruhan
MIN_TARGET_ARTICLES = 10000

# Google News RSS HANYA akurat/lengkap untuk berita ~3 bulan terakhir (di luar itu
# hasilnya jarang & ga representatif). Makanya di-limit ketat: jendela waktu cuma
# 3 bulan terakhir, DAN dibatasi maksimal segini artikel total dari Google News.
# Sisa kebutuhan ke MIN_TARGET_ARTICLES diisi dari sitemap (sumber resmi, lastmod akurat,
# nyakup full-year) — bukan dari Google News yang di luar 3 bulan kurang valid.
MAX_GOOGLE_NEWS_ARTICLES = 3000
GOOGLE_NEWS_WINDOW_MONTHS = 3

CHECKPOINT_FILE  = "scraper_checkpoint.json"
DELAY_PAGE       = 0.6
DELAY_ARTICLE    = 0.25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Referer": "https://www.google.com/",
}


DISEASE_KEYWORDS = {
    "DBD / Dengue": [
        "dbd", "dengue", "demam berdarah", "aedes aegypti",
        "nyamuk demam berdarah", "dhf", "dengue hemorrhagic fever"
    ],
    "COVID-19": [
        "covid", "covid-19", "corona", "virus corona",
        "sars-cov", "sars-cov-2", "omicron",
        "varian baru covid", "pandemi covid"
    ],
    "HANTA VIRUS": [
        "hanta", "hantavirus", "virus hanta",
        "tikus", "penyakit tikus"
    ],
    "Tuberkulosis (TBC)": [
        "tbc", "tb", "tuberkulosis", "tuberculosis",
        "tb paru", "batuk berdarah tbc",
        "mdr tb", "tb ro", "tbc ro",
        "tuberkulosis resisten obat"
    ],
    "Mpox": [
        "mpox", "cacar monyet", "monkeypox"
    ],
    "Flu / Influenza": [
        "flu", "influenza", "influenza musiman",
        "h3n2", "h1n1", "flu musiman"
    ],

    "Flu Burung": [
        "flu burung", "avian influenza",
        "h5n1", "h5n6", "virus flu burung",
        "unggas terinfeksi"
    ],

    "HFMD / Flu Singapura": [
        "hfmd", "hand foot mouth disease",
        "flu singapura", "penyakit tangan kaki mulut",
        "virus coxsackie"
    ],

    "Malaria": [
        "malaria", "plasmodium",
        "anopheles", "nyamuk malaria"
    ],

    "Hepatitis": [
        "hepatitis", "hbsag", "sirosis",
        "hepatitis a", "hepatitis b",
        "hepatitis c", "hepatitis misterius",
        "radang hati"
    ],

    "Diabetes": [
        "diabetes", "diabetes melitus",
        "diabetes mellitus", "dm",
        "gula darah", "hiperglikemia",
        "kencing manis", "insulin"
    ],

    "Hipertensi": [
        "hipertensi", "htn",
        "tekanan darah tinggi",
        "darah tinggi", "tensi tinggi"
    ],

    "Kanker": [
        "kanker", "tumor ganas",
        "onkologi", "kemoterapi",
        "kanker payudara", "kanker serviks",
        "kanker paru", "kanker usus besar",
        "kanker darah", "leukemia",
        "tumor otak", "sel kanker",
        "kanker hati", "kanker prostat",
        "kanker kulit", "limfoma"
    ],

    "Diare / Kolera": [
        "diare", "kolera",
        "gastroenteritis", "muntaber",
        "disentri", "mencret"
    ],

    "Campak": [
        "campak", "measles",
        "morbili", "rubella",
        "campak jerman",
        "virus campak",
        "wabah campak"
    ],

    "Polio": [
        "polio", "poliovirus",
        "kelumpuhan polio"
    ],

    "Rabies": [
        "rabies", "gigitan anjing",
        "gigitan hewan rabies",
        "virus rabies"
    ],

    "Tifoid": [
        "tifoid", "tifus",
        "typhoid", "demam tifoid",
        "tipes"
    ],

    "Angina": [
        "angina", "angin duduk",
        "angina pectoris"
    ],

    "HIV / AIDS": [
        "hiv", "aids",
        "odha", "antiretroviral",
        "virus hiv"
    ],

    "Leptospirosis": [
        "leptospirosis",
        "penyakit tikus",
        "kencing tikus"
    ],

    "Chikungunya": [
        "chikungunya",
        "chikv",
        "chickv"
    ],

    "Stunting / Gizi": [
        "stunting",
        "gizi buruk",
        "malnutrisi",
        "wasting",
        "anak pendek"
    ],

    "Stroke": [
        "stroke",
        "stroke ringan",
        "stroke hemoragik",
        "stroke iskemik",
        "penyumbatan otak",
        "serebral"
    ],

    "Jantung": [
        "jantung",
        "serangan jantung",
        "infark",
        "kardiovaskular",
        "gagal jantung",
        "penyakit jantung koroner",
        "aritmia",
        "jantung bocor",
        "heart failure",
        "coronary artery disease"
    ],

    "Pneumonia": [
        "pneumonia",
        "paru-paru basah",
        "radang paru",
        "infeksi paru",
        "walking pneumonia",
        "mycoplasma pneumoniae"
    ],

    "Bronkitis": [
        "bronkitis",
        "bronchitis",
        "radang bronkus",
        "infeksi bronkus"
    ],

    "Asma": [
        "asma",
        "asma bronkial",
        "asma kambuh",
        "sesak napas"
    ],

    "Cacar Air": [
        "cacar air",
        "varicella",
        "varisela",
        "chicken pox",
        "chickenpox",
        "virus varicella"
    ],

    "Cacar Ular": [
        "cacar ular",
        "herpes zoster",
        "zoster",
        "shingles",
        "varicella zoster"
    ],

    "Cacar Api": [
        "cacar api",
        "herpes zoster",
        "cacar ular",
        "shingles"
    ],

    "ISPA": [
        "ispa",
        "infeksi saluran pernapasan atas",
        "batuk pilek",
        "infeksi saluran pernapasan"
    ],

    "Batuk": [
        "batuk",
        "batuk kronis",
        "batuk berkepanjangan",
        "batuk kering",
        "batuk berdahak",
        "batuk rejan",
        "pertusis",
        "whooping cough"
    ],

    "Kolesterol": [
        "kolesterol",
        "kolesterol tinggi",
        "trigliserida",
        "dislipidemia"
    ],

    "Asam Urat": [
        "asam urat",
        "gout",
        "uric acid"
    ],

    "Meningitis": [
        "meningitis",
        "radang selaput otak"
    ],

    "Obesitas": [
        "obesitas",
        "kegemukan",
        "overweight",
        "bmi tinggi",
        "berat badan berlebih"
    ],

    "Anemia": [
        "anemia",
        "kurang darah",
        "hemoglobin rendah",
        "talasemia"
    ],

    "Usus Buntu (Apendisitis)": [
        "usus buntu",
        "apendisitis",
        "appendix",
        "apendiks",
        "radang usus buntu"
    ],

    "Radang Usus (IBD)": [
        "radang usus",
        "crohn",
        "kolitis",
        "ibd",
        "ulcerative colitis",
        "kolitis ulseratif",
        "usus besar bermasalah"
    ],

    "GERD / Maag": [
        "maag",
        "gerd",
        "asam lambung",
        "tukak lambung",
        "gastritis",
        "asam lambung naik",
        "refluks asam lambung"
    ],

    "Wasir / Hemoroid": [
        "wasir",
        "hemoroid",
        "ambeien"
    ],

    "Usus / Pencernaan Lainnya": [
        "sembelit",
        "konstipasi",
        "ibs",
        "irritable bowel syndrome",
        "usus bocor",
        "polip usus",
        "divertikulitis"
    ],

    "Gagal Ginjal": [
        "gagal ginjal",
        "penyakit ginjal kronis",
        "ckd",
        "hemodialisis",
        "cuci darah",
        "dialisis",
        "transplantasi ginjal"
    ],

    "Batu Ginjal": [
        "batu ginjal",
        "kolik ginjal",
        "nefrolitiasis",
        "batu saluran kemih"
    ],

    "Infeksi Saluran Kemih (ISK)": [
        "isk",
        "uti",
        "infeksi saluran kemih",
        "infeksi kandung kemih",
        "sistitis",
        "anyang-anyangan"
    ],

    "Ginjal Lainnya": [
        "nefritis",
        "sindrom nefrotik",
        "radang ginjal",
        "fungsi ginjal menurun",
        "prostat",
        "pembesaran prostat"
    ],

    "Demam": [
        "demam",
        "demam tinggi",
        "panas tinggi",
        "demam misterius"
    ],

    "PMK": [
        "pmk",
        "penyakit mulut dan kuku",
        "foot and mouth disease"
    ]
}

LOCATIONS = {
    "Aceh":                ["aceh", "banda aceh", "lhokseumawe"],
    "Sumatera Utara":      ["sumatera utara", "sumut", "medan", "binjai"],
    "Sumatera Barat":      ["sumatera barat", "sumbar", "padang", "bukittinggi"],
    "Riau":                ["riau", "pekanbaru", "dumai"],
    "Kepulauan Riau":      ["kepri", "kepulauan riau", "batam", "tanjungpinang"],
    "Jambi":               ["jambi"],
    "Sumatera Selatan":    ["sumatera selatan", "sumsel", "palembang"],
    "Bangka Belitung":     ["bangka", "belitung", "pangkal pinang"],
    "Bengkulu":            ["bengkulu"],
    "Lampung":             ["lampung", "bandar lampung"],
    "DKI Jakarta":         ["jakarta", "dki jakarta", "jakarta selatan", "jakarta utara",
                            "jakarta timur", "jakarta barat", "jakarta pusat"],
    "Jawa Barat":          ["jawa barat", "jabar", "bandung", "bogor", "bekasi",
                            "depok", "cirebon", "tasikmalaya", "sukabumi"],
    "Banten":              ["banten", "serang", "tangerang", "cilegon"],
    "Jawa Tengah":         ["jawa tengah", "jateng", "semarang", "surakarta", "solo",
                            "magelang", "pekalongan", "tegal"],
    "DI Yogyakarta":       ["yogyakarta", "jogja", "diy", "sleman", "bantul"],
    "Jawa Timur":          ["jawa timur", "jatim", "surabaya", "malang", "kediri",
                            "blitar", "probolinggo", "madiun"],
    "Bali":                ["bali", "denpasar", "singaraja", "ubud", "gianyar"],
    "Nusa Tenggara Barat": ["ntb", "nusa tenggara barat", "mataram", "lombok", "sumbawa"],
    "Nusa Tenggara Timur": ["ntt", "nusa tenggara timur", "kupang", "flores", "ende"],
    "Kalimantan Barat":    ["kalimantan barat", "kalbar", "pontianak", "singkawang"],
    "Kalimantan Tengah":   ["kalimantan tengah", "kalteng", "palangkaraya"],
    "Kalimantan Selatan":  ["kalimantan selatan", "kalsel", "banjarmasin"],
    "Kalimantan Timur":    ["kalimantan timur", "kaltim", "samarinda", "balikpapan"],
    "Kalimantan Utara":    ["kalimantan utara", "kaltu", "tarakan"],
    "Sulawesi Utara":      ["sulawesi utara", "sulut", "manado", "bitung"],
    "Gorontalo":           ["gorontalo"],
    "Sulawesi Tengah":     ["sulawesi tengah", "sulteng", "palu"],
    "Sulawesi Selatan":    ["sulawesi selatan", "sulsel", "makassar", "parepare"],
    "Sulawesi Barat":      ["sulawesi barat", "sulbar", "mamuju"],
    "Sulawesi Tenggara":   ["sulawesi tenggara", "sultra", "kendari"],
    "Maluku":              ["maluku", "ambon"],
    "Maluku Utara":        ["maluku utara", "malut", "ternate", "tidore"],
    "Papua":               ["papua", "jayapura", "mimika", "merauke"],
    "Papua Barat":         ["papua barat", "manokwari", "sorong"],
}


def make_id(url):  return hashlib.md5(url.encode()).hexdigest()[:12]

# ── Precompile keyword regex dengan WORD BOUNDARY (\b) ───────────────────────
# BUG YANG DIPERBAIKI: sebelumnya pakai substring check biasa (`k in text`),
# jadi keyword pendek macam "isk" ke-detect juga di tengah kata lain yang
# nggak ada hubungannya, misal "miskin", "disko", dll. Sekarang setiap keyword
# di-compile jadi regex dengan \b di awal & akhir, supaya cuma match kalau
# itu KATA UTUH (atau frasa utuh untuk keyword 2+ kata seperti "demam berdarah"),
# bukan potongan dari kata lain.
def _compile_keyword_patterns(keyword_dict):
    compiled = {}
    for category, kws in keyword_dict.items():
        compiled[category] = [
            re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
            for kw in kws
        ]
    return compiled

def _any_keyword_match(patterns, text):
    return any(p.search(text) for p in patterns)

def _count_keyword_matches(patterns, text):
    return sum(len(p.findall(text)) for p in patterns)

_DISEASE_PATTERNS  = _compile_keyword_patterns(DISEASE_KEYWORDS)
_LOCATION_PATTERNS = _compile_keyword_patterns(LOCATIONS)


def detect_disease(title, content=""):
    """
    Logika deteksi penyakit:
      1. Cari keyword (WHOLE-WORD — harus diapit spasi/batas kata, bukan
         potongan kata; "isk" TIDAK match di "miskin") di JUDUL.
      2. Kalau judul cocok dengan keyword dari TEPAT 1 kategori → itu yang dipilih.
      3. Kalau judul cocok dengan keyword dari 2+ KATEGORI BERBEDA → hitung
         frekuensi kemunculan keyword tiap kategori di ISI ARTIKEL (whole-word,
         exact, kiri-kanan harus spasi/batas kata). Ambil kategori yang
         keyword-nya muncul PALING BANYAK di isi.
      4. Kalau frekuensi di isi sama (atau isi kosong) → fallback ke posisi
         paling kiri di judul (logic lama).
      5. Kalau judul TIDAK match keyword apa pun → "Umum / Lainnya".
    """
    # Cari semua kategori yang match di judul + posisi kemunculan paling awal.
    earliest_pos_per_category = {}
    for d, patterns in _DISEASE_PATTERNS.items():
        best_pos = None
        for p in patterns:
            m = p.search(title)
            if m and (best_pos is None or m.start() < best_pos):
                best_pos = m.start()
        if best_pos is not None:
            earliest_pos_per_category[d] = best_pos

    if not earliest_pos_per_category:
        return "Umum / Lainnya"

    # Hanya 1 kategori match di judul → langsung return.
    if len(earliest_pos_per_category) == 1:
        return next(iter(earliest_pos_per_category))

    # 2+ kategori match di judul → hitung frekuensi di isi artikel.
    if content:
        freq_per_category = {
            d: _count_keyword_matches(_DISEASE_PATTERNS[d], content)
            for d in earliest_pos_per_category
        }
        max_freq = max(freq_per_category.values())
        if max_freq > 0:
            # Ambil semua kandidat dengan frekuensi tertinggi.
            top_candidates = [d for d, f in freq_per_category.items() if f == max_freq]
            if len(top_candidates) == 1:
                return top_candidates[0]
            # Masih seri setelah hitung frekuensi → fallback posisi judul.
            return min(top_candidates, key=lambda d: earliest_pos_per_category[d])

    # Isi kosong atau semua frekuensi 0 → fallback posisi paling kiri di judul.
    return min(earliest_pos_per_category, key=earliest_pos_per_category.get)

def detect_location(t):
    # Sama seperti detect_disease: whole-word matching, supaya nama lokasi
    # pendek (misal "riau", "bali") nggak ke-detect dari potongan kata lain.
    for p, patterns in _LOCATION_PATTERNS.items():
        if _any_keyword_match(patterns, t):
            return p
    return "Tidak Terdeteksi"
def clean(el):
    if el is None: return ""
    if isinstance(el, str): raw = el
    else:
        for tag in el.find_all(["script","style","figure","aside","iframe","nav","noscript"]): tag.decompose()
        raw = el.get_text()
    return re.sub(r"\s+", " ", raw).strip()

# ── Translasi nama bulan/hari Indonesia → Inggris ────────────────────────────
# dateutil TIDAK paham "Maret", "Mei", "Agustus", dst — kalau dibiarkan,
# tanggal seperti "12 Maret 2026" gagal parse total (return None), bukan cuma
# salah bulan. Ini sumber lain dari data tanggal yang ngaco/hilang.
_ID_MONTHS = {
    "januari": "January", "februari": "February", "maret": "March",
    "april": "April", "mei": "May", "juni": "June",
    "juli": "July", "agustus": "August", "september": "September",
    "oktober": "October", "november": "November", "desember": "December",
    # singkatan umum di situs berita
    "jan": "Jan", "feb": "Feb", "mar": "Mar", "apr": "Apr",
    "jun": "Jun", "jul": "Jul", "agu": "Aug", "ags": "Aug",
    "sep": "Sep", "sept": "Sep", "okt": "Oct", "nov": "Nov", "des": "Dec",
}
_ID_DAYS = {
    "senin": "Monday", "selasa": "Tuesday", "rabu": "Wednesday",
    "kamis": "Thursday", "jumat": "Friday", "jum'at": "Friday",
    "sabtu": "Saturday", "minggu": "Sunday",
}
_ID_DATE_WORD_RE = re.compile(
    r"(?<![a-zA-Z])(" + "|".join(sorted(set(_ID_MONTHS) | set(_ID_DAYS), key=len, reverse=True)) + r")(?![a-zA-Z])",
    re.IGNORECASE
)

def _translate_id_date(raw_str):
    def _repl(m):
        word = m.group(1).lower()
        return _ID_MONTHS.get(word) or _ID_DAYS.get(word) or m.group(1)
    return _ID_DATE_WORD_RE.sub(_repl, raw_str)


# ── Validasi tanggal: berita TIDAK BOLEH dari masa depan ─────────────────────
# Buffer 1 hari untuk toleransi selisih timezone (WIB/WITA/WIT vs UTC).
_FUTURE_BUFFER = timedelta(days=1)

def _is_future(dt):
    if dt is None: return False
    return dt > (datetime.now(timezone.utc) + _FUTURE_BUFFER)

def parse_date(raw):
    """
    Parse tanggal dari berbagai format (numerik ambigu "12/03/2026" ATAU
    nama bulan "12 Mar 2026" / "12 Maret 2026").

    FIX BUG TANGGAL NGACO: format numerik day/month itu ambigu — "07/06/2026"
    bisa berarti 7 Juni ATAU 6 Juli, tergantung situs sumbernya. Kalau cuma
    pakai dayfirst=True secara mentah, ada kasus tanggal yang ke-swap jadi
    bulan yang belum lewat (misal Juli–Desember 2026 padahal sekarang masih
    Juni 2026). Solusinya: coba dayfirst=True DAN dayfirst=False, lalu pilih
    hasil yang TIDAK di masa depan. Kalau kedua interpretasi sama-sama jatuh
    di masa depan → tanggal dianggap tidak valid (dibuang, return None),
    supaya artikel itu tidak salah dikategorikan ke bulan yang belum terjadi.
    """
    if not raw: return None
    raw_str = str(raw).strip()
    if not raw_str: return None
    raw_str = _translate_id_date(raw_str)  # "12 Maret 2026" -> "12 March 2026"

    candidates = []
    for dayfirst in (True, False):
        try:
            dt = dateparser.parse(raw_str, dayfirst=dayfirst)
        except Exception:
            dt = None
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt not in candidates:
                candidates.append(dt)

    if not candidates:
        return None

    # Prioritaskan kandidat yang TIDAK di masa depan.
    valid = [d for d in candidates if not _is_future(d)]
    if valid:
        # dayfirst=True dicoba pertama → konvensi Indonesia (dd/mm/yyyy),
        # jadi kalau dia valid (bukan masa depan), itu yang dipakai.
        return valid[0]

    # Semua interpretasi jatuh di masa depan → tanggal tidak masuk akal, buang.
    return None

def date_from_url(url):
    m = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            if _is_future(dt):
                return None
            return dt
        except Exception:
            pass
    return None

def get_week_fields(pub_dt):
    """
    Hitung info minggu dari sebuah tanggal:
      - week_iso       : nomor minggu ISO dalam setahun (1-53)
      - week_of_month  : minggu ke berapa DALAM bulan itu (1-5), berguna untuk
                          analisis tren mingguan per bulan
      - week_start     : tanggal Senin dari minggu itu (YYYY-MM-DD)
      - week_label     : label gabungan "2026-W03" (tahun-minggu ISO), enak buat sorting/grouping
    """
    if not pub_dt:
        return None, None, None, None
    iso_year, iso_week, _ = pub_dt.isocalendar()
    week_of_month = (pub_dt.day - 1) // 7 + 1
    week_start = (pub_dt - timedelta(days=pub_dt.weekday())).strftime("%Y-%m-%d")
    week_label = f"{iso_year}-W{iso_week:02d}"
    return iso_week, week_of_month, week_start, week_label

def make_row(source, title, content, url, pub_dt):
    # Guard tambahan: kalau somehow pub_dt yang masuk sudah lolos dari masa depan
    # (misal dari known_date sitemap lastmod), buang lagi di titik akhir ini.
    if _is_future(pub_dt):
        pub_dt = None

    combined = f"{title} {content}"
    week_iso, week_of_month, week_start, week_label = get_week_fields(pub_dt)
    return {
        "id":            make_id(url),
        "Source":        source,
        "Title":         title.strip(),
        "Content":       content.strip()[:2000],
        "url":           url,
        "Year":          pub_dt.year  if pub_dt else None,
        "Month":         pub_dt.month if pub_dt else None,
        "Week":          week_iso,        # nomor minggu ISO (1-53)
        "Minggu_ke":     week_of_month,   # minggu ke-N dalam bulan tersebut (1-5)
        "Minggu_Mulai":  week_start,      # tanggal Senin awal minggu itu
        "Week_Label":    week_label,      # contoh: "2026-W12"
        "Penyakit":      detect_disease(title, content),
        "Lokasi":        detect_location(combined),
    }

def audit_future_dates(df):
    """
    Audit EKSPLISIT: cek ulang per baris apakah Year/Month/Minggu_Mulai-nya
    "lebih besar" dari hari ini — kalau iya, itu tanda tanggalnya salah parse
    (kesasar ke bulan/minggu yang belum terjadi).

    Ini dipanggil dari notebook analisis sebagai LAPISAN KEDUA (defense-in-depth)
    setelah validasi yang sudah ada di parse_date(). Kenapa masih perlu dicek
    ulang di sini? Karena kalau kamu load df dari health_news_raw.json /
    health_news.csv hasil run LAMA (sebelum bug tanggal diperbaiki), baris-baris
    bermasalah itu sudah kepatri di file dan tidak lewat parse_date() lagi.

    Urutan cek (sesuai permintaan): TAHUN dulu → kalau tahun masih sama dengan
    sekarang, baru cek BULAN → kalau bulan juga masih sama, baru cek MINGGU
    (pakai Minggu_Mulai, tanggal Senin awal minggu itu).

    Return: (df_bersih, df_bermasalah)
    """
    now       = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    def _row_is_future(row):
        y, m, wk_start = row.get("Year"), row.get("Month"), row.get("Minggu_Mulai")
        if y is None or m is None:
            return False
        if y > now.year:
            return True
        if y == now.year and m > now.month:
            return True
        if y == now.year and m == now.month and wk_start and str(wk_start) > today_str:
            return True
        return False

    mask = df.apply(_row_is_future, axis=1)
    df_bad   = df[mask].copy()
    df_clean = df[~mask].copy()
    log.info(f"🔍 Audit tanggal: {len(df_bad)} baris bermasalah (tahun/bulan/minggu di masa depan) dari {len(df)} total.")
    return df_clean, df_bad


def load_checkpoint():
    p = Path(CHECKPOINT_FILE)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rows, seen = data.get("rows",[]), set(data.get("seen",[]))
            log.info(f"✅ Resume checkpoint: {len(rows)} artikel")
            return rows, seen
        except: pass
    return [], set()

def save_checkpoint(rows, seen):
    Path(CHECKPOINT_FILE).write_text(
        json.dumps({"rows": rows, "seen": list(seen),
                    "saved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False),
        encoding="utf-8"
    )
    log.info(f"  💾 Checkpoint saved: {len(rows)} artikel")

# ── HTTP session ──────────────────────────────────────────────────────────────
def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

def fetch(session, url, timeout=15):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200 and len(r.text) > 200:
            return r
    except Exception as e:
        log.debug(f"fetch error {url}: {e}")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# SITEMAP CRAWLER — inti scraper
# ═══════════════════════════════════════════════════════════════════════════════

def get_urls_from_sitemap(session, sitemap_url, target_years, keyword_filter=None):
    """
    Parse sitemap XML (bisa sitemap index atau sitemap biasa).
    Return list of (url, pub_date_or_None) yang masuk target_years.
    """
    results = []
    r = fetch(session, sitemap_url)
    if not r:
        log.warning(f"  Sitemap tidak bisa diakses: {sitemap_url}")
        return results

    # Parse dengan BeautifulSoup XML mode
    try:
        soup = BeautifulSoup(r.content, "xml")
    except:
        soup = BeautifulSoup(r.content, "lxml")

    # Sitemap index → rekursif
    sub_sitemaps = soup.find_all("sitemap")
    if sub_sitemaps:
        log.info(f"  Sitemap index: {len(sub_sitemaps)} sub-sitemaps di {sitemap_url.split('/')[-1]}")
        for sm in sub_sitemaps:
            loc = sm.find("loc")
            if not loc: continue
            loc_url = loc.text.strip()
            # filter: hanya sitemap yg relevan (bukan image/video)
            if re.search(r'image|video|foto', loc_url, re.I): continue
            child = get_urls_from_sitemap(session, loc_url, target_years, keyword_filter)
            results.extend(child)
            time.sleep(0.3)
        return results

    # Sitemap biasa → ekstrak URL
    urls_in_sitemap = soup.find_all("url")
    if not urls_in_sitemap:
        # fallback: regex
        locs  = re.findall(r'<loc>(.*?)</loc>', r.text)
        dates = re.findall(r'<lastmod>(.*?)</lastmod>', r.text)
        urls_in_sitemap = list(zip(locs, dates + [""] * len(locs)))
        for loc, lastmod in urls_in_sitemap:
            loc = loc.strip()
            if re.search(r'\.(jpg|png|mp4|gif|jpeg|webp)$', loc): continue
            if keyword_filter and not re.search(keyword_filter, loc, re.I): continue
            pub_dt = parse_date(lastmod) or date_from_url(loc)
            if pub_dt and pub_dt.year in target_years:
                results.append((loc, pub_dt))
            elif not pub_dt:
                results.append((loc, None))  # tanggal unknown, fetch dulu
        return results

    for u in urls_in_sitemap:
        loc_el = u.find("loc")
        if not loc_el: continue
        loc = loc_el.text.strip()
        if re.search(r'\.(jpg|png|mp4|gif|jpeg|webp)$', loc): continue
        if keyword_filter and not re.search(keyword_filter, loc, re.I): continue
        lastmod_el = u.find("lastmod") or u.find("news:publication_date")
        pub_dt = parse_date(lastmod_el.text if lastmod_el else "") or date_from_url(loc)
        if pub_dt and pub_dt.year in target_years:
            results.append((loc, pub_dt))
        elif not pub_dt:
            results.append((loc, None))

    log.info(f"  {sitemap_url.split('/')[-1]}: {len(results)} URLs masuk target tahun")
    return results


def fetch_and_parse_article(session, url, source_name,
                             title_sels, body_sels,
                             date_sels=None, date_attr=None,
                             known_date=None):
    """Fetch artikel dan return row dict atau None."""
    r = fetch(session, url)
    if not r: return None
    soup = BeautifulSoup(r.content, "lxml")

    # title
    title = ""
    for sel in title_sels:
        el = soup.select_one(sel)
        if el and len(el.get_text().strip()) > 5:
            title = el.get_text().strip()
            break
    if not title:
        el = soup.find("h1")
        title = el.get_text().strip() if el else ""
    if not title: return None

    # body
    body = ""
    for sel in body_sels:
        el = soup.select_one(sel)
        if el:
            body = clean(el)[:2000]
            break
    if not body:
        paras = [p.get_text().strip() for p in soup.find_all("p") if len(p.get_text()) > 60]
        body = " ".join(paras)[:2000]

    # date — known_date (dari sitemap) divalidasi ulang, JANGAN langsung dipercaya
    pub_dt = known_date if (known_date and not _is_future(known_date)) else None
    if not pub_dt and date_sels:
        for sel in date_sels:
            el = soup.select_one(sel)
            if el:
                raw = el.get(date_attr) if date_attr else el.get_text()
                pub_dt = parse_date(raw)
                if pub_dt: break
    if not pub_dt:
        for prop in ["article:published_time","datePublished","og:published_time"]:
            meta = soup.find("meta", property=prop) or soup.find("meta", itemprop=prop)
            if meta:
                pub_dt = parse_date(meta.get("content",""))
                if pub_dt: break
    if not pub_dt:
        pub_dt = date_from_url(url)

    return make_row(source_name, title, body, url, pub_dt)


# ═══════════════════════════════════════════════════════════════════════════════
# PER-SOURCE SCRAPERS (sitemap-based)
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_from_sitemap(session, source_name, sitemap_urls,
                         title_sels, body_sels, date_sels,
                         rows, seen, pbar, target_years,
                         keyword_filter=None, date_attr=None):
    """Generic sitemap scraper — dipakai semua sumber."""
    # 1. Kumpulkan semua URL dari sitemap
    all_urls = []
    for sm_url in sitemap_urls:
        log.info(f"  [{source_name}] Parsing sitemap: {sm_url}")
        urls = get_urls_from_sitemap(session, sm_url, target_years, keyword_filter)
        all_urls.extend(urls)
        time.sleep(0.3)

    # Deduplikasi URL
    seen_urls = set()
    unique_urls = []
    for url, dt in all_urls:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_urls.append((url, dt))

    log.info(f"  [{source_name}] Total URL dari sitemap: {len(unique_urls)}")

    # 2. Fetch tiap artikel
    count = 0
    for url, known_dt in tqdm(unique_urls, desc=f"  {source_name}", leave=False):
        if make_id(url) in seen: continue

        # Skip kalau tanggal sudah pasti di luar range
        if known_dt and known_dt.year not in target_years:
            continue

        row = fetch_and_parse_article(
            session, url, source_name,
            title_sels, body_sels, date_sels,
            date_attr=date_attr,
            known_date=known_dt,
        )
        if not row: continue
        if row.get("Year") not in target_years: continue

        rows.append(row)
        seen.add(row["id"])
        count += 1
        pbar.update(1)
        time.sleep(DELAY_ARTICLE)

    log.info(f"  [{source_name}] ✅ {count} artikel berhasil di-scrape")
    return count


# ── Google News RSS per penyakit ─────────────────────────────────────────────
DISEASE_QUERIES = [
    "penyakit DBD dengue indonesia",
    "penyakit COVID corona indonesia",
    "tuberkulosis TBC indonesia",
    "malaria indonesia",
    "hepatitis indonesia",
    "diabetes indonesia",
    "hipertensi darah tinggi indonesia",
    "kanker tumor indonesia",
    "stunting gizi buruk indonesia",
    "flu influenza ISPA indonesia",
    "mpox cacar monyet indonesia",
    "polio campak indonesia",
    "gagal ginjal cuci darah indonesia",
    "stroke jantung indonesia",
    "pneumonia radang paru indonesia",
    "kolesterol obesitas indonesia",
    "tifoid tifus indonesia",
    "leptospirosis chikungunya indonesia",
    "HIV AIDS indonesia",
    "rabies indonesia",
    "wabah penyakit indonesia",
    "kasus penyakit menular indonesia",
    "kesehatan masyarakat indonesia penyakit",
    "epidemi pandemi indonesia",
    "vaksin imunisasi penyakit indonesia",
    "Kemenkes kasus penyakit indonesia",
    "dinas kesehatan wabah indonesia",
    "penyakit anak bayi indonesia",
    # ── tambahan: usus & ginjal ──────────────────────────────────────────────
    "usus buntu apendisitis indonesia",
    "radang usus kolitis crohn indonesia",
    "maag gerd asam lambung indonesia",
    "wasir ambeien hemoroid indonesia",
    "batu ginjal kolik ginjal indonesia",
    "infeksi saluran kemih ISK indonesia",
    "gagal ginjal kronis dialisis indonesia",
    "penyakit ginjal prostat indonesia",
    # ── tambahan: kategori baru dari list terbaru ────────────────────────────
    "hantavirus penyakit tikus indonesia",
    "flu burung h5n1 indonesia",
    "flu singapura hfmd indonesia",
    "asma bronkitis sesak napas indonesia",
    "cacar ular herpes zoster indonesia",
    "demam misterius anak indonesia",
    "PMK penyakit mulut kuku indonesia",
    "batuk rejan pertusis indonesia",
]

def _month_ranges(years):
    """Generate (start_date, end_date) string per bulan untuk semua tahun target.
    Dipakai oleh proses SITEMAP (full-year, akurat by lastmod) — BUKAN oleh
    Google News (lihat _last_n_months_ranges di bawah)."""
    ranges = []
    now = datetime.now(timezone.utc)
    for y in sorted(years):
        for m in range(1, 13):
            start = datetime(y, m, 1, tzinfo=timezone.utc)
            if start > now:
                break  # jangan generate bulan yang belum terjadi
            end = datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(y, m + 1, 1, tzinfo=timezone.utc)
            ranges.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    return ranges

def _last_n_months_ranges(n=GOOGLE_NEWS_WINDOW_MONTHS):
    """Khusus untuk Google News: cuma generate window N bulan terakhir
    (bukan seluruh tahun), karena Google News cuma reliable untuk berita baru-baru
    ini. Dipecah per bulan supaya query after:/before: tetap presisi."""
    now = datetime.now(timezone.utc)
    # titik awal: tanggal 1 dari bulan (now.month - n + 1)
    start_month_count = now.year * 12 + (now.month - 1) - (n - 1)
    ranges = []
    for i in range(n):
        mc = start_month_count + i
        y, m = divmod(mc, 12)
        m += 1
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        if start > now:
            break
        end = datetime(y + 1, 1, 1, tzinfo=timezone.utc) if m == 12 else datetime(y, m + 1, 1, tzinfo=timezone.utc)
        ranges.append((start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")))
    return ranges


def scrape_google_news_rss(session, rows, seen, pbar, target_years):
    """
    Ambil artikel dari Google News RSS per keyword penyakit.

    DIBATASI SENGAJA (akurasi > kuantitas):
      - Cuma window GOOGLE_NEWS_WINDOW_MONTHS bulan TERAKHIR (default 3 bulan),
        karena di luar itu Google News kurang reliable / hasil makin jarang & acak.
      - Total artikel dari fase ini di-cap di MAX_GOOGLE_NEWS_ARTICLES (default 3000).
        Begitu cap tercapai, fase ini langsung berhenti — sisa kuota ke
        MIN_TARGET_ARTICLES diisi dari sitemap (FASE 3/4) yang datanya lebih akurat
        (lastmod resmi, nyakup full-year, bukan tebak-tebakan).
    """
    count = 0
    month_ranges = _last_n_months_ranges(GOOGLE_NEWS_WINDOW_MONTHS)
    log.info(f"  Window Google News: {len(month_ranges)} bulan terakhir "
             f"({month_ranges[0][0]} s/d {month_ranges[-1][1]}), cap {MAX_GOOGLE_NEWS_ARTICLES:,} artikel")

    for query in tqdm(DISEASE_QUERIES, desc="  Google News (per keyword)"):
        if count >= MAX_GOOGLE_NEWS_ARTICLES:
            break
        for start, end in month_ranges:
            if count >= MAX_GOOGLE_NEWS_ARTICLES:
                break
            full_query = f"{query} after:{start} before:{end}"
            url = (f"https://news.google.com/rss/search?"
                   f"q={requests.utils.quote(full_query)}"
                   f"&hl=id&gl=ID&ceid=ID:id")
            try:
                r = session.get(url, timeout=12)
                feed = feedparser.parse(r.content)
                for entry in feed.entries:
                    if count >= MAX_GOOGLE_NEWS_ARTICLES:
                        break
                    art_url = entry.get("link","").strip()
                    if not art_url or make_id(art_url) in seen: continue
                    title   = entry.get("title","").strip()
                    pub_dt  = parse_date(entry.get("published",""))
                    if not pub_dt or pub_dt.year not in target_years: continue
                    # source dari domain
                    domain  = re.search(r'https?://(?:www\.)?([^/]+)', art_url)
                    source  = domain.group(1).replace(".com","").replace(".co.id","").title() if domain else "News"
                    summary = clean(entry.get("summary",""))[:2000]
                    row = make_row(source, title, summary, art_url, pub_dt)
                    rows.append(row); seen.add(row["id"])
                    count += 1; pbar.update(1)
                time.sleep(0.25)
            except Exception as e:
                log.debug(f"Google News RSS error ({full_query}): {e}")

    log.info(f"  [Google News RSS] ✅ {count} artikel (cap {MAX_GOOGLE_NEWS_ARTICLES:,}, window {GOOGLE_NEWS_WINDOW_MONTHS} bulan terakhir)")
    return count




# ── Detik Archive Search (FULL-YEAR backfill, by date range) ───────────────
# DIAGNOSIS dari run sebelumnya: sitemap.xml banyak situs ternyata ROLLING/
# RECENT-ONLY (cuma berisi ~100-300 URL TERBARU per kategori, BUKAN arsip
# penuh) — makanya 2025 nyaris kosong walau kodenya udah benar nge-filter
# tahun. Datanya emang nggak ada lagi di sitemap live.
#
# SOLUSI: endpoint search resmi detik.com punya filter rentang tanggal asli
# (fromdatex/todatex, format dd/mm/yyyy) yang BISA narik artikel lama, bukan
# cuma yang baru. Pola ini sudah TERVERIFIKASI dipakai di project open-source
# lain (referensi: github.com/harishartanto/detikcom-scraper), jadi bukan
# tebak-tebakan. Ini jadi sumber UTAMA buat backfill 2025 + awal 2026.
DETIK_SEARCH_QUERIES = [
    "demam berdarah", "covid", "tuberkulosis", "malaria", "hepatitis",
    "diabetes", "hipertensi", "kanker", "diare", "campak", "polio", "rabies",
    "tifus", "hiv aids", "leptospirosis", "chikungunya", "stunting",
    "stroke", "jantung", "pneumonia", "asma", "ispa", "kolesterol",
    "asam urat", "meningitis", "obesitas", "anemia", "usus buntu",
    "radang usus", "maag", "wasir", "batu ginjal", "infeksi saluran kemih",
    "gagal ginjal", "flu burung", "flu singapura", "cacar", "demam",
    "mpox", "hanta virus",
]
DETIK_MAX_PAGES_PER_QUERY = 15   # 1 halaman = 10 artikel -> max 150 artikel/keyword

def scrape_detik_archive_search(session, rows, seen, pbar, target_years):
    """
    Backfill full-year via https://www.detik.com/search/searchnews dengan
    filter fromdatex/todatex (dd/mm/yyyy). Tidak dibatasi siteid (cari di
    SELURUH detik.com, bukan cuma health.detik.com) — sama seperti filosofi
    top-up sitemap: discovery boleh luas, LABEL penyakit tetap dihitung dari
    judul+isi artikel asli via detect_disease(), jadi tetap akurat.
    """
    from_date = datetime(min(target_years), 1, 1)
    to_date   = datetime.now(timezone.utc)
    from_str  = from_date.strftime("%d/%m/%Y")
    to_str    = to_date.strftime("%d/%m/%Y")
    count = 0

    for query in tqdm(DETIK_SEARCH_QUERIES, desc="  Detik Archive Search"):
        for page in range(1, DETIK_MAX_PAGES_PER_QUERY + 1):
            url = (f"https://www.detik.com/search/searchnews?"
                   f"query={requests.utils.quote(query)}&sortby=time"
                   f"&fromdatex={from_str}&todatex={to_str}&page={page}&result_type=latest")
            try:
                r = session.get(url, timeout=12)
                if r.status_code != 200:
                    break
                soup = BeautifulSoup(r.content, "lxml")
                article_list = soup.find("div", {"class": "list-content"})
                if not article_list:
                    break
                articles = article_list.find_all("article", class_="list-content__item")
                if not articles:
                    break  # halaman kosong = sudah habis

                for art in articles:
                    link_el = art.find("a", {"class": "media__link"})
                    if not link_el: continue
                    art_url = link_el.get("href", "").strip()
                    if not art_url or make_id(art_url) in seen: continue

                    title = link_el.get("dtr-ttl") or link_el.get_text(strip=True)
                    if not title: continue

                    date_span = art.find("span", title=True)
                    raw_date  = date_span.get("title") if date_span else None
                    pub_dt    = parse_date(raw_date) if raw_date else None
                    if not pub_dt or pub_dt.year not in target_years: continue

                    # fetch isi lengkap artikel (selector sama dengan Detik Health)
                    row = fetch_and_parse_article(
                        session, art_url, "Detik (archive search)",
                        ["h1.detail__title", "h1.title", "h1"],
                        [".detail__body-text", ".detail__body", "article"],
                        [".detail__date", "time", "[class*='date']"],
                        known_date=pub_dt,
                    )
                    if not row: continue
                    rows.append(row); seen.add(row["id"])
                    count += 1; pbar.update(1)
                    time.sleep(DELAY_ARTICLE)

                time.sleep(DELAY_PAGE)
            except Exception as e:
                log.debug(f"Detik archive search error ({query} hal.{page}): {e}")
                break
        if len(rows) >= MIN_TARGET_ARTICLES:
            log.info(f"  ✅ Target {MIN_TARGET_ARTICLES:,} tercapai, hentikan Detik Archive Search.")
            break

    log.info(f"  [Detik Archive Search] ✅ {count} artikel (full-year, by tanggal asli)")
    return count



RSS_FEEDS = {
    "Detik Health":        ["https://health.detik.com/rss",
                            "https://rss.detik.com/index.php/detikhealth"],
    "Republika Kesehatan": ["https://www.republika.co.id/rss/nasional/kesehatan"],
    "Tribun Kesehatan":    ["https://www.tribunnews.com/rss/kesehatan"],
    "Antara Kesehatan":    ["https://www.antaranews.com/rss/kesehatan"],
    "Medcom Kesehatan":    ["https://www.medcom.id/rss/kesehatan"],
    "Kompas Health":       ["https://health.kompas.com/rss"],
}

def scrape_rss_feeds(session, rows, seen, pbar, target_years):
    count = 0
    for source, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            try:
                r = session.get(feed_url, timeout=12)
                if r.status_code != 200: continue
                feed = feedparser.parse(r.content)
                if not feed.entries: continue
                for entry in feed.entries:
                    url    = entry.get("link","").strip()
                    if not url or make_id(url) in seen: continue
                    title  = entry.get("title","").strip()
                    body   = clean(entry.get("summary","") or entry.get("description",""))[:2000]
                    pub_dt = parse_date(entry.get("published","") or entry.get("updated",""))
                    if not pub_dt or pub_dt.year not in target_years: continue
                    row = make_row(source, title, body, url, pub_dt)
                    rows.append(row); seen.add(row["id"])
                    count += 1; pbar.update(1)
                log.info(f"  [RSS] {source}: {count} artikel")
                break
            except Exception as e:
                log.debug(f"RSS error {source}: {e}")
    log.info(f"  [RSS total] ✅ {count} artikel")
    return count


# ═══════════════════════════════════════════════════════════════════════════════
# SITEMAP CONFIG TIAP SUMBER (berdasarkan debug nyata)
# ═══════════════════════════════════════════════════════════════════════════════

SOURCES_CONFIG = {

    "Merdeka Sehat": {
        "sitemaps": ["https://www.merdeka.com/sitemap.xml"],
        "keyword_filter": r"merdeka\.com/sehat/",
        "title_sels":  ["h1.article-title", "h1.title", "h1"],
        "body_sels":   [".article-content", ".content-detail", "article"],
        "date_sels":   ["time", ".article-date", "[class*='date']"],
        "date_attr":   None,
    },

    "Suara Health": {
        "sitemaps": [
            "https://www.suara.com/sitemap.xml",
            "https://www.suara.com/health/sitemap-web.xml",
            "https://www.suara.com/health/sitemap-news.xml",
        ],
        "keyword_filter": r"suara\.com/health/",
        "title_sels":  ["h1.article__title", "h1.detail-title", "h1"],
        "body_sels":   [".article__body", ".detail-news__body", "article"],
        "date_sels":   ["time", "[class*='date']", "[class*='time']"],
        "date_attr":   "datetime",
    },

    "Tempo Kesehatan": {
        "sitemaps": ["https://www.tempo.co/sitemap.xml"],
        "keyword_filter": r"tempo\.co/(gaya|kesehatan|health|topik)",
        "title_sels":  ["h1.title", "h1.detail-title", "h1.judul", "h1"],
        "body_sels":   [".detail-konten", ".artikel", ".content-detail", "article"],
        "date_sels":   ["time", ".detail__footer-date", "[class*='date']"],
        "date_attr":   "datetime",
    },

    "Okezone Health": {
        "sitemaps": ["https://health.okezone.com/sitemap.xml"],
        "keyword_filter": r"okezone\.com/read/\d{4}/\d{2}/\d{2}/",
        "title_sels":  ["h1.title-artikel", "h1.detail-title", "h1"],
        "body_sels":   ["#contentx", ".detail-news", "article"],
        "date_sels":   ["time", ".post-date", "[class*='date']"],
        "date_attr":   None,
    },

    "Detik Health": {
        "sitemaps": ["https://health.detik.com/sitemap.xml"],
        "keyword_filter": r"health\.detik\.com/",
        "title_sels":  ["h1.detail__title", "h1.title", "h1"],
        "body_sels":   [".detail__body-text", ".detail__body", "article"],
        "date_sels":   [".detail__date", "time", "[class*='date']"],
        "date_attr":   None,
    },

    "CNN Indonesia": {
        "sitemaps": ["https://www.cnnindonesia.com/sitemap.xml"],
        "keyword_filter": r"cnnindonesia\.com/gaya-hidup/",
        "title_sels":  ["h1.title", "h1.detail-title", "h1"],
        "body_sels":   [".detail-wrap .content", ".detail-text", "article"],
        "date_sels":   [".update-date", "time", "[class*='date']"],
        "date_attr":   None,
    },

    "Liputan6 Kesehatan": {
        "sitemaps": [
            "https://kesehatan.liputan6.com/sitemap.xml",
            "https://kesehatan.liputan6.com/info-sehat/sitemap.xml",
            "https://kesehatan.liputan6.com/ibu-dan-anak/sitemap.xml",
        ],
        "keyword_filter": r"liputan6\.com/.+/read/\d+/",
        "title_sels":  ["h1.read-page--header--title", "h1.article-title", "h1"],
        "body_sels":   [".article-content-body__item-content", ".article-content-body", "article"],
        "date_sels":   [".read-page--header--author__datetime", "time", "[class*='date']"],
        "date_attr":   None,
    },

    # CATATAN JUJUR: domain & sitemap Kompas SUDAH dikonfirmasi ada
    # (https://www.kompas.com/sitemap.xml ketemu via pencarian web), TAPI selector
    # HTML di bawah ini BEST-EFFORT — tidak bisa di-live-test karena robots.txt
    # Kompas blokir fetch otomatis dari sisi tool ini. Kalau pas dijalankan hasil
    # "Kompas Health" 0 artikel atau body-nya kepotong/kosong, kirim contoh 1 URL
    # artikel Kompas yang gagal supaya selector-nya saya sesuaikan.
    "Kompas Health": {
        "sitemaps": [
            "https://health.kompas.com/sitemap.xml",
            "https://www.kompas.com/sitemap.xml",
        ],
        "keyword_filter": r"(health\.kompas\.com/|kompas\.com/sains/)",
        "title_sels":  ["h1.read__title", "h1.article__title", "h1"],
        "body_sels":   [".read__content", ".article__body", "article"],
        "date_sels":   [".read__time", "time", "[class*='date']"],
        "date_attr":   None,
    },
}


# ── Top-up (HANYA jika belum capai MIN_TARGET_ARTICLES) ─────────────────────
# CATATAN PENTING (akurasi): top-up TIDAK pakai Google News lagi (di luar window
# 3 bulan, Google News kurang reliable). Sebagai gantinya, sapu ULANG sitemap dari
# sumber yang SAMA (FASE 3) tapi dengan filter URL lebih lebar: cari artikel yang
# slug URL-nya mengandung nama penyakit (misal ".../diabetes-tipe-2-..." atau
# ".../demam-berdarah-meningkat/"), walau artikelnya dipublikasikan di luar
# section "/health/" (kadang berita outbreak/wabah nongol di section nasional,
# regional, atau megapolitan, bukan di vertical kesehatan).
#
# Ini tetap AKURAT karena:
#   1. Tanggalnya tetap dari lastmod sitemap resmi (bukan tebakan).
#   2. Label penyakit tetap dihitung dari JUDUL+ISI artikel asli via detect_disease()
#      — pola URL di sini HANYA dipakai untuk MENEMUKAN kandidat artikel, BUKAN
#      untuk menentukan label penyakitnya. Jadi kalau ternyata artikelnya nggak
#      relevan, dia otomatis ke-tag "Umum / Lainnya", bukan dipaksa salah label.

def _disease_keyword_url_pattern():
    """Bangun 1 regex gabungan dari semua keyword penyakit (≥4 huruf, biar ga
    nyangkut ke singkatan pendek yang rawan false-positive macam 'tb'/'dm'/'isk'),
    dalam bentuk slug URL (spasi -> [-_.])."""
    slugs = set()
    for kws in DISEASE_KEYWORDS.values():
        for k in kws:
            if len(k.replace(" ", "")) < 4:
                continue
            slugs.add(re.sub(r"\s+", "[-_.]", k.strip()))
    return r"(" + "|".join(sorted(slugs, key=len, reverse=True)) + r")"

DISEASE_URL_PATTERN = _disease_keyword_url_pattern()

def scrape_sitemap_topup(session, rows, seen, pbar, target_years):
    """Sapuan ke-2 atas sitemap sumber yang sama, filter URL diperlebar pakai
    DISEASE_URL_PATTERN, supaya nangkep artikel penyakit yang dipublikasikan
    di luar vertical '/health/'."""
    total = 0
    for source_name, cfg in SOURCES_CONFIG.items():
        log.info(f"\n  [Top-up sitemap] {source_name} (filter diperlebar)")
        c = scrape_from_sitemap(
            session       = session,
            source_name   = f"{source_name} (top-up)",
            sitemap_urls  = cfg["sitemaps"],
            title_sels    = cfg["title_sels"],
            body_sels     = cfg["body_sels"],
            date_sels     = cfg["date_sels"],
            rows          = rows,
            seen          = seen,
            pbar          = pbar,
            target_years  = target_years,
            keyword_filter= DISEASE_URL_PATTERN,
            date_attr     = cfg.get("date_attr"),
        )
        total += c
        if len(rows) >= MIN_TARGET_ARTICLES:
            log.info(f"  ✅ Target {MIN_TARGET_ARTICLES:,} tercapai, hentikan top-up.")
            break
        time.sleep(1)
    log.info(f"  [Top-up sitemap total] ✅ {total} artikel tambahan")
    return total



# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_all(output_json="health_news_raw.json", checkpoint_every=300):
    """
    Jalankan semua scraper → return pandas DataFrame.

    Kolom: No | Source | Title | Content | Year | Month | Week | Minggu_ke |
           Minggu_Mulai | Week_Label | Penyakit | Lokasi | url

    Cara pakai:
        from health_scraper_final import scrape_all
        df = scrape_all()
        df.to_csv('health_news.csv', index=True, encoding='utf-8-sig')
    """
    import pandas as pd

    log.info(f"🚀 Target tahun: {YEARS}")
    rows, seen = load_checkpoint()
    session    = make_session()
    pbar       = tqdm(total=10000, desc="Total artikel", unit="art",
                      initial=len(rows), dynamic_ncols=True)
    last_ckpt  = len(rows)

    def maybe_ckpt():
        nonlocal last_ckpt
        if len(rows) - last_ckpt >= checkpoint_every:
            save_checkpoint(rows, seen)
            last_ckpt = len(rows)

    # ── 1. RSS feeds (cepat, sebagai warm-up) ────────────────────────────────
    log.info("\n── FASE 1: RSS Feeds ──")
    scrape_rss_feeds(session, rows, seen, pbar, YEARS)
    maybe_ckpt()

    # ── 2. Google News RSS per penyakit ──────────────────────────────────────
    log.info("\n── FASE 2: Google News RSS (per penyakit) ──")
    scrape_google_news_rss(session, rows, seen, pbar, YEARS)
    maybe_ckpt()

    # ── 2.5. Detik Archive Search (BACKFILL UTAMA full-year, termasuk 2025) ──
    log.info("\n── FASE 2.5: Detik Archive Search (full-year, by tanggal asli) ──")
    scrape_detik_archive_search(session, rows, seen, pbar, YEARS)
    maybe_ckpt()

    # ── 3. Sitemap-based scraping (utama) ────────────────────────────────────
    log.info("\n── FASE 3: Sitemap-based Scraping ──")
    for source_name, cfg in SOURCES_CONFIG.items():
        log.info(f"\n  [{source_name}]")
        scrape_from_sitemap(
            session       = session,
            source_name   = source_name,
            sitemap_urls  = cfg["sitemaps"],
            title_sels    = cfg["title_sels"],
            body_sels     = cfg["body_sels"],
            date_sels     = cfg["date_sels"],
            rows          = rows,
            seen          = seen,
            pbar          = pbar,
            target_years  = YEARS,
            keyword_filter= cfg.get("keyword_filter"),
            date_attr     = cfg.get("date_attr"),
        )
        maybe_ckpt()
        time.sleep(1)  # jeda antar sumber

    # ── 4. Top-up sitemap widening (HANYA jika belum capai target minimum) ──
    if len(rows) < MIN_TARGET_ARTICLES:
        log.info(f"\n── FASE 4: Top-up sitemap, filter diperlebar (saat ini {len(rows):,}, target {MIN_TARGET_ARTICLES:,}) ──")
        pbar.total = max(pbar.total, len(rows) + 2000)
        scrape_sitemap_topup(session, rows, seen, pbar, YEARS)
        maybe_ckpt()
    else:
        log.info(f"\n✅ Sudah capai target minimum {MIN_TARGET_ARTICLES:,} artikel, skip FASE 4.")

    pbar.close()

    # ── Deduplikasi & filter ──────────────────────────────────────────────────
    seen_ids, unique = set(), []
    for r in rows:
        if r["id"] not in seen_ids and r.get("Year") in YEARS:
            seen_ids.add(r["id"])
            unique.append(r)
    unique.sort(key=lambda x: (-(x.get("Year") or 0), -(x.get("Month") or 0), -(x.get("Week") or 0)))

    # ── Simpan JSON backup ────────────────────────────────────────────────────
    Path(output_json).write_text(
        json.dumps({"scraped_at": datetime.now(timezone.utc).isoformat(),
                    "years": YEARS, "total": len(unique), "rows": unique},
                   ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    if Path(CHECKPOINT_FILE).exists():
        Path(CHECKPOINT_FILE).unlink()

    # ── Build DataFrame ───────────────────────────────────────────────────────
    df = pd.DataFrame(unique)
    if df.empty:
        log.warning("⚠️ DataFrame kosong! Cek koneksi internet dan coba lagi.")
        return df

    df = df.reset_index(drop=True)
    df.index += 1
    df.index.name = "No"
    cols = ["Source","Title","Content","Year","Month","Week","Minggu_ke",
            "Minggu_Mulai","Week_Label","Penyakit","Lokasi","url"]
    df   = df[[c for c in cols if c in df.columns]]

    # ── Audit final: buang baris yang ternyata "dari masa depan" ─────────────
    df, df_bad = audit_future_dates(df)
    if len(df_bad) > 0:
        log.warning(f"⚠️ {len(df_bad)} artikel dibuang karena Tahun/Bulan/Minggu-nya di masa depan (salah parse).")

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info(f"\n{'='*55}")
    log.info(f"✅ SELESAI: {len(df):,} artikel")
    log.info(f"{'─'*55}")
    for yr, cnt in df.groupby("Year").size().sort_index(ascending=False).items():
        log.info(f"   {yr}: {cnt:,} artikel")
    log.info(f"{'─'*55}")
    log.info(f"Top sumber:")
    for src, cnt in df.groupby("Source").size().sort_values(ascending=False).head(10).items():
        log.info(f"   {src:<30} {cnt:>5}")
    log.info(f"{'='*55}")

    return df
