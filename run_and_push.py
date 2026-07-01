"""
run_and_push.py
---------------
1. Cek apakah hari ini adalah hari yang tepat untuk run:
   - Tanggal 28 → hanya jalan kalau bulan ini punya < 30 hari (Februari)
   - Tanggal 30 → jalan untuk semua bulan yang punya hari ke-30
2. Jalankan scraper (health_scraper_final.py)
3. Simpan hasil ke CSV
4. Push CSV ke Hugging Face Datasets

Dijalankan otomatis oleh GitHub Actions sesuai jadwal.
"""

import os
import sys
import calendar
from datetime import datetime, timezone

# ── 0. Cek apakah hari ini giliran run ───────────────────────────────────────
now = datetime.now(timezone.utc)
today = now.day
days_in_month = calendar.monthrange(now.year, now.month)[1]

if today == 28:
    # Tanggal 28 hanya jalan untuk bulan yang < 30 hari (Februari)
    if days_in_month >= 30:
        print(f"⏭️  Hari ini tanggal 28, tapi bulan ini punya {days_in_month} hari.")
        print(f"   Akan jalan di tanggal 30. Skip.")
        sys.exit(0)
    else:
        print(f"✅ Februari terdeteksi ({days_in_month} hari) — lanjut scraping di tanggal 28...")

elif today == 30:
    # Tanggal 30 hanya jalan untuk bulan yang punya hari ke-30
    if days_in_month < 30:
        print(f"⏭️  Bulan ini hanya punya {days_in_month} hari, tidak ada tanggal 30. Skip.")
        sys.exit(0)
    else:
        print(f"✅ Tanggal 30 bulan {now.strftime('%B')} — lanjut scraping...")

else:
    # Dipanggil manual (workflow_dispatch) atau kondisi tidak terduga
    print(f"ℹ️  Dijalankan manual pada {now.strftime('%Y-%m-%d')} — lanjut scraping...")

# ── 1. Jalankan scraper ───────────────────────────────────────────────────────
print("🚀 Menjalankan scraper...")
from health_scraper_final import scrape_all

df = scrape_all(
    output_json      = "health_news_raw.json",
    checkpoint_every = 300,
)

if df.empty:
    print("❌ DataFrame kosong, scraping gagal. Hentikan.")
    sys.exit(1)

print(f"✅ Scraping selesai: {len(df):,} artikel")

# ── 2. Simpan CSV ─────────────────────────────────────────────────────────────
csv_path = "health_news.csv"
df.to_csv(csv_path, index=True, encoding="utf-8-sig")
print(f"💾 Tersimpan: {csv_path}")

# ── 3. Push ke Hugging Face Datasets ─────────────────────────────────────────
HF_TOKEN        = os.environ.get("HF_TOKEN")
HF_DATASET_REPO = os.environ.get("HF_DATASET_REPO")  # contoh: "argodinata/indonesian-health-news"

if not HF_TOKEN:
    print("❌ HF_TOKEN tidak ditemukan di environment variables.")
    sys.exit(1)

if not HF_DATASET_REPO:
    print("❌ HF_DATASET_REPO tidak ditemukan di environment variables.")
    sys.exit(1)

from huggingface_hub import HfApi

api = HfApi(token=HF_TOKEN)

# Buat repo kalau belum ada
try:
    api.create_repo(
        repo_id   = HF_DATASET_REPO,
        repo_type = "dataset",
        exist_ok  = True,    # tidak error kalau sudah ada
        private   = False,   # ganti True kalau mau private
    )
    print(f"📦 Dataset repo: https://huggingface.co/datasets/{HF_DATASET_REPO}")
except Exception as e:
    print(f"⚠️  Buat repo gagal (mungkin sudah ada): {e}")

# Upload CSV
scraped_at = now.strftime("%Y-%m-%d %H:%M UTC")
api.upload_file(
    path_or_fileobj = csv_path,
    path_in_repo    = "health_news.csv",
    repo_id         = HF_DATASET_REPO,
    repo_type       = "dataset",
    commit_message  = f"Auto-update scraping: {len(df):,} artikel ({scraped_at})",
)
print(f"✅ CSV berhasil di-push ke HF Datasets!")
print(f"🔗 https://huggingface.co/datasets/{HF_DATASET_REPO}")

# Upload juga JSON backup
api.upload_file(
    path_or_fileobj = "health_news_raw.json",
    path_in_repo    = "health_news_raw.json",
    repo_id         = HF_DATASET_REPO,
    repo_type       = "dataset",
    commit_message  = f"Auto-update JSON backup ({scraped_at})",
)
print("✅ JSON backup juga di-push.")
