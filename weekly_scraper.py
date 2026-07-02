"""
weekly_scraper.py
=================
Scraper INCREMENTAL mingguan — hanya mengambil artikel dari minggu berjalan,
lalu merge ke health_news.csv yang sudah ada di Hugging Face Dataset.

Jadwal: setiap MINGGU (Hari Minggu) via GitHub Actions.

Alur:
  1. Download health_news.csv dari HF Dataset (existing data)
  2. Tentukan rentang tanggal minggu berjalan (Senin s/d Minggu)
  3. Scrape hanya artikel minggu itu dari semua sumber
  4. Merge + deduplikasi dengan data lama
  5. Upload kembali ke HF Dataset
"""

import json
import logging
import os
import re
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
import feedparser
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from tqdm import tqdm

# ── Impor semua fungsi dari scraper utama ────────────────────────────────────
from health_scraper_final import (
    DISEASE_KEYWORDS,
    LOCATIONS,
    SOURCES_CONFIG,
    RSS_FEEDS,
    DISEASE_QUERIES,
    HEADERS,
    DELAY_PAGE,
    DELAY_ARTICLE,
    MAX_GOOGLE_NEWS_ARTICLES,
    make_id,
    make_row,
    detect_disease,
    detect_location,
    clean,
    parse_date,
    date_from_url,
    get_week_fields,
    audit_future_dates,
    _is_future,
    make_session,
    fetch,
    get_urls_from_sitemap,
    fetch_and_parse_article,
    scrape_from_sitemap,
    scrape_rss_feeds,
    scrape_google_news_rss,
    log,
)

# ── Hugging Face config (dari environment variable / GitHub Secrets) ──────────
HF_TOKEN      = os.environ.get("HF_TOKEN", "")
HF_REPO_ID    = os.environ.get("HF_REPO_ID", "your-username/health-news-indonesia")
CSV_FILENAME  = "health_news.csv"
HF_REPO_TYPE  = "dataset"


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Hitung rentang minggu berjalan (Senin–Minggu)
# ═══════════════════════════════════════════════════════════════════════════════

def get_current_week_range():
    """
    Return (week_start, week_end) untuk minggu BERJALAN.
    - week_start: Senin minggu ini (00:00:00 UTC)
    - week_end  : Minggu minggu ini (23:59:59 UTC) = hari ini saat dijalankan
    """
    now        = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())          # Senin
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end   = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


def is_in_current_week(pub_dt, week_start, week_end):
    """Cek apakah pub_dt masuk dalam rentang minggu berjalan."""
    if pub_dt is None:
        return False
    if pub_dt.tzinfo is None:
        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
    return week_start <= pub_dt <= week_end


# ═══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD dari Hugging Face Dataset
# ═══════════════════════════════════════════════════════════════════════════════

def download_existing_csv(local_path: str = CSV_FILENAME) -> pd.DataFrame:
    """
    Download health_news.csv dari Hugging Face Dataset.
    Kalau gagal (repo baru / file belum ada), return DataFrame kosong.
    """
    url = f"https://huggingface.co/datasets/{HF_REPO_ID}/resolve/main/{CSV_FILENAME}"
    headers = {}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"

    log.info(f"📥 Download existing CSV dari HF: {url}")
    try:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            with open(local_path, "wb") as f:
                f.write(r.content)
            df = pd.read_csv(local_path, index_col=0, encoding="utf-8-sig")
            log.info(f"   ✅ Berhasil: {len(df):,} baris existing")
            return df
        elif r.status_code == 404:
            log.warning("   ⚠️  File belum ada di HF (repo baru). Mulai dari kosong.")
            return pd.DataFrame()
        else:
            log.error(f"   ❌ HTTP {r.status_code} saat download. Lanjut dengan DataFrame kosong.")
            return pd.DataFrame()
    except Exception as e:
        log.error(f"   ❌ Error download: {e}. Lanjut dengan DataFrame kosong.")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPE MINGGU BERJALAN
# ═══════════════════════════════════════════════════════════════════════════════

def scrape_current_week(week_start: datetime, week_end: datetime) -> list[dict]:
    """
    Scrape semua artikel yang terbit dalam rentang [week_start, week_end].
    Hanya tahun berjalan (dari week_start.year) yang jadi target.
    """
    target_years = list({week_start.year, week_end.year})
    log.info(f"\n🗓️  Scraping minggu: {week_start.date()} s/d {week_end.date()}")
    log.info(f"   Target tahun: {target_years}")

    rows: list[dict] = []
    seen: set[str]   = set()
    session = make_session()
    pbar    = tqdm(total=2000, desc="Weekly scrape", unit="art", dynamic_ncols=True)

    def _filter_week(row: dict) -> bool:
        """Hanya simpan artikel yang pub_dt-nya masuk minggu berjalan."""
        if not row:
            return False
        y = row.get("Year")
        wk_start_str = row.get("Minggu_Mulai")
        if y not in target_years:
            return False
        # Cara paling akurat: cek Minggu_Mulai == ISO week_start minggu ini
        current_week_start_str = week_start.strftime("%Y-%m-%d")
        if wk_start_str and wk_start_str != current_week_start_str:
            return False
        return True

    # ── FASE A: RSS Feeds (cepat, real-time) ─────────────────────────────────
    log.info("\n── FASE A: RSS Feeds ──")
    count_rss = 0
    for source, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            try:
                r = session.get(feed_url, timeout=12)
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.content)
                for entry in feed.entries:
                    url    = entry.get("link", "").strip()
                    if not url or make_id(url) in seen:
                        continue
                    title  = entry.get("title", "").strip()
                    body   = clean(entry.get("summary", "") or entry.get("description", ""))[:2000]
                    pub_dt = parse_date(entry.get("published", "") or entry.get("updated", ""))
                    if not is_in_current_week(pub_dt, week_start, week_end):
                        continue
                    row = make_row(source, title, body, url, pub_dt)
                    if _filter_week(row):
                        rows.append(row)
                        seen.add(row["id"])
                        count_rss += 1
                        pbar.update(1)
                break
            except Exception as e:
                log.debug(f"RSS error {source}: {e}")
    log.info(f"  [RSS] ✅ {count_rss} artikel minggu ini")

    # ── FASE B: Google News RSS (hanya window 7 hari terakhir) ───────────────
    log.info("\n── FASE B: Google News RSS (window minggu ini) ──")
    start_str = week_start.strftime("%Y-%m-%d")
    end_str   = (week_end + timedelta(days=1)).strftime("%Y-%m-%d")   # eksklusif
    count_gnews = 0

    for query in tqdm(DISEASE_QUERIES, desc="  Google News", leave=False):
        if count_gnews >= MAX_GOOGLE_NEWS_ARTICLES:
            break
        full_query = f"{query} after:{start_str} before:{end_str}"
        url = (
            f"https://news.google.com/rss/search?"
            f"q={requests.utils.quote(full_query)}"
            f"&hl=id&gl=ID&ceid=ID:id"
        )
        try:
            r = session.get(url, timeout=12)
            feed = feedparser.parse(r.content)
            for entry in feed.entries:
                art_url = entry.get("link", "").strip()
                if not art_url or make_id(art_url) in seen:
                    continue
                title   = entry.get("title", "").strip()
                pub_dt  = parse_date(entry.get("published", ""))
                if not is_in_current_week(pub_dt, week_start, week_end):
                    continue
                domain  = re.search(r"https?://(?:www\.)?([^/]+)", art_url)
                source  = domain.group(1).replace(".com", "").replace(".co.id", "").title() if domain else "News"
                summary = clean(entry.get("summary", ""))[:2000]
                row = make_row(source, title, summary, art_url, pub_dt)
                if _filter_week(row):
                    rows.append(row)
                    seen.add(row["id"])
                    count_gnews += 1
                    pbar.update(1)
            time.sleep(0.25)
        except Exception as e:
            log.debug(f"Google News error ({query}): {e}")

    log.info(f"  [Google News] ✅ {count_gnews} artikel minggu ini")

    # ── FASE C: Sitemap per sumber (hanya artikel minggu berjalan) ───────────
    log.info("\n── FASE C: Sitemap-based Scraping (filter minggu ini) ──")
    count_sitemap = 0
    for source_name, cfg in SOURCES_CONFIG.items():
        log.info(f"  [{source_name}]")
        # Kumpulkan URLs dari sitemap
        all_urls = []
        for sm_url in cfg["sitemaps"]:
            urls = get_urls_from_sitemap(session, sm_url, target_years, cfg.get("keyword_filter"))
            all_urls.extend(urls)
            time.sleep(0.3)

        # Deduplikasi URL
        seen_urls: set[str] = set()
        unique_urls = []
        for url, dt in all_urls:
            if url not in seen_urls:
                seen_urls.add(url)
                unique_urls.append((url, dt))

        # Filter hanya yang tanggalnya masuk minggu berjalan (dari sitemap lastmod)
        week_urls = [
            (url, dt) for url, dt in unique_urls
            if dt is None or is_in_current_week(dt, week_start, week_end)
        ]
        log.info(f"     {len(week_urls)} URL kandidat minggu ini dari sitemap")

        for url, known_dt in tqdm(week_urls, desc=f"  {source_name}", leave=False):
            if make_id(url) in seen:
                continue
            row = fetch_and_parse_article(
                session, url, source_name,
                cfg["title_sels"], cfg["body_sels"], cfg["date_sels"],
                date_attr=cfg.get("date_attr"),
                known_date=known_dt,
            )
            if not row:
                continue
            # Validasi ketat: harus benar-benar minggu ini
            row_pub_dt = None
            if row.get("Minggu_Mulai"):
                try:
                    row_pub_dt = datetime.strptime(row["Minggu_Mulai"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            if not _filter_week(row):
                continue

            rows.append(row)
            seen.add(row["id"])
            count_sitemap += 1
            pbar.update(1)
            time.sleep(DELAY_ARTICLE)

        time.sleep(1)

    log.info(f"  [Sitemap total] ✅ {count_sitemap} artikel minggu ini")
    pbar.close()

    log.info(f"\n📊 Total artikel baru minggu ini: {len(rows)}")
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# MERGE & DEDUPLIKASI
# ═══════════════════════════════════════════════════════════════════════════════

def merge_and_deduplicate(df_existing: pd.DataFrame, new_rows: list[dict]) -> pd.DataFrame:
    """
    Gabungkan data lama dengan data baru, deduplikasi berdasarkan kolom 'url'.
    Data baru di-prioritaskan (update baris yang sudah ada jika URL sama).
    """
    if not new_rows:
        log.info("📝 Tidak ada artikel baru — data tidak berubah.")
        return df_existing

    df_new = pd.DataFrame(new_rows)

    # Reset index df_existing agar kolom 'No' tidak jadi index
    if not df_existing.empty:
        df_existing_reset = df_existing.reset_index(drop=True)
    else:
        df_existing_reset = pd.DataFrame()

    # Gabungkan
    df_combined = pd.concat([df_existing_reset, df_new], ignore_index=True)

    # Deduplikasi berdasarkan URL (keep='last' → baris baru menang)
    before = len(df_combined)
    df_combined = df_combined.drop_duplicates(subset=["url"], keep="last")
    after  = len(df_combined)
    dupes  = before - after
    log.info(f"🔗 Merge: {len(df_existing_reset):,} lama + {len(df_new):,} baru → {after:,} unik (skip {dupes} duplikat)")

    # Sort: terbaru dulu
    df_combined = df_combined.sort_values(
        ["Year", "Month", "Week"], ascending=[False, False, False]
    ).reset_index(drop=True)

    # Re-numbering kolom No
    df_combined.index += 1
    df_combined.index.name = "No"

    # Pastikan urutan kolom tetap konsisten
    cols = ["Source", "Title", "Content", "Year", "Month", "Week", "Minggu_ke",
            "Minggu_Mulai", "Week_Label", "Penyakit", "Lokasi", "url"]
    df_combined = df_combined[[c for c in cols if c in df_combined.columns]]

    # Audit final: buang yang tanggalnya "dari masa depan"
    df_combined, df_bad = audit_future_dates(df_combined)
    if len(df_bad) > 0:
        log.warning(f"⚠️  {len(df_bad)} artikel dibuang karena tanggal di masa depan.")

    return df_combined


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD ke Hugging Face Dataset
# ═══════════════════════════════════════════════════════════════════════════════

def upload_to_huggingface(df: pd.DataFrame, local_path: str = CSV_FILENAME):
    """
    Simpan DataFrame ke CSV, lalu upload ke Hugging Face Dataset via API.
    Membutuhkan HF_TOKEN dan HF_REPO_ID di environment variable.
    """
    if not HF_TOKEN:
        log.error("❌ HF_TOKEN tidak ditemukan! Set environment variable HF_TOKEN.")
        return False
    if not HF_REPO_ID or HF_REPO_ID == "your-username/health-news-indonesia":
        log.error("❌ HF_REPO_ID belum diset! Update file atau set env variable HF_REPO_ID.")
        return False

    # Simpan lokal dulu
    df.to_csv(local_path, index=True, encoding="utf-8-sig")
    log.info(f"💾 CSV disimpan lokal: {local_path} ({len(df):,} baris)")

    # Upload via HF API
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=HF_TOKEN)

        # Pastikan repo ada (buat jika belum)
        try:
            api.repo_info(repo_id=HF_REPO_ID, repo_type=HF_REPO_TYPE)
        except Exception:
            log.info(f"   Repo belum ada, membuat: {HF_REPO_ID}")
            api.create_repo(repo_id=HF_REPO_ID, repo_type=HF_REPO_TYPE, private=False)

        # Upload file
        week_start, _ = get_current_week_range()
        week_label    = week_start.strftime("W%W-%Y")
        commit_msg    = f"Weekly update {week_label}: +{len(df):,} total rows"

        api.upload_file(
            path_or_fileobj=local_path,
            path_in_repo=CSV_FILENAME,
            repo_id=HF_REPO_ID,
            repo_type=HF_REPO_TYPE,
            commit_message=commit_msg,
        )
        log.info(f"✅ Upload berhasil → https://huggingface.co/datasets/{HF_REPO_ID}")
        log.info(f"   Commit: {commit_msg}")
        return True

    except ImportError:
        log.error("❌ huggingface_hub tidak terinstall. Jalankan: pip install huggingface-hub")
        return False
    except Exception as e:
        log.error(f"❌ Upload gagal: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def run_weekly_update():
    """
    Entry point utama. Dipanggil oleh GitHub Actions setiap Minggu.
    """
    log.info("=" * 60)
    log.info("🚀 WEEKLY HEALTH NEWS SCRAPER — Incremental Update")
    log.info("=" * 60)

    # 1. Hitung rentang minggu berjalan
    week_start, week_end = get_current_week_range()
    log.info(f"📅 Minggu berjalan: {week_start.date()} (Senin) s/d {week_end.date()} (Minggu)")

    # 2. Download data yang sudah ada dari HF
    df_existing = download_existing_csv()

    # 3. Cek apakah minggu ini sudah pernah di-scrape (opsional, safety check)
    current_week_label = f"{week_start.isocalendar()[0]}-W{week_start.isocalendar()[1]:02d}"
    if not df_existing.empty and "Week_Label" in df_existing.columns:
        already_scraped = df_existing[df_existing["Week_Label"] == current_week_label]
        if len(already_scraped) > 0:
            log.warning(f"⚠️  Minggu {current_week_label} sudah ada {len(already_scraped):,} artikel. Tetap lanjut untuk top-up.")

    # 4. Scrape minggu berjalan
    new_rows = scrape_current_week(week_start, week_end)

    # 5. Merge dengan data lama
    df_final = merge_and_deduplicate(df_existing, new_rows)

    # 6. Tampilkan summary
    log.info("\n" + "=" * 60)
    log.info(f"📊 SUMMARY:")
    log.info(f"   Data lama : {len(df_existing):,} baris")
    log.info(f"   Baru minggu ini: {len(new_rows):,} artikel")
    log.info(f"   Total final : {len(df_final):,} baris")
    if not df_final.empty and "Week_Label" in df_final.columns:
        week_dist = df_final[df_final["Week_Label"] == current_week_label]
        log.info(f"   Artikel {current_week_label}: {len(week_dist):,} baris")
    log.info("=" * 60)

    # 7. Upload ke HF
    success = upload_to_huggingface(df_final)
    if not success:
        log.error("❌ Upload gagal! CSV tetap tersimpan lokal.")
        # Tetap exit 0 agar GitHub Actions tidak dianggap gagal permanen
    else:
        log.info("🎉 Weekly update selesai!")

    return df_final


if __name__ == "__main__":
    run_weekly_update()
