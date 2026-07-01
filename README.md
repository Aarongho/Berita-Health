# Indonesian Health News Scraper — Auto Update ke HF Datasets

Data berita kesehatan Indonesia (2025–2026), di-scrape otomatis tiap minggu
dan di-push ke Hugging Face Datasets.

---

## Setup (sekali aja)

### 1. Buat repo di GitHub
Upload semua file ini ke 1 repo GitHub (public atau private terserah).

Struktur folder:
```
repo/
├── health_scraper_final.py   ← scraper utama
├── run_and_push.py           ← script otomatis
├── requirements.txt
├── README.md
└── .github/
    └── workflows/
        └── scrape_and_push.yml
```

### 2. Buat HF Dataset repo
- Buka https://huggingface.co/new-dataset
- Kasih nama, misal: `indonesian-health-news`
- Visibility: Public (atau Private kalau mau)

### 3. Buat HF Token
- Buka https://huggingface.co/settings/tokens
- Klik **New token** → pilih **Write** permission
- Copy token-nya

### 4. Tambahin Secrets di GitHub
Di repo GitHub-mu → **Settings → Secrets and variables → Actions → New repository secret**

Tambahkan 2 secrets:

| Name | Value |
|------|-------|
| `HF_TOKEN` | token HF kamu (dari langkah 3) |
| `HF_DATASET_REPO` | `username-hf-kamu/indonesian-health-news` |

### 5. Aktifkan GitHub Actions
- Buka tab **Actions** di repo GitHub-mu
- Kalau ada peringatan "Workflows aren't being run", klik **Enable**
- Coba jalankan manual: **Actions → Health News Scraper → Run workflow**

---

## Jadwal update
Default: **tiap Senin jam 01:00 WIB**

Ganti di `.github/workflows/scrape_and_push.yml` bagian `cron`:
```yaml
- cron: "0 18 * * 0"   # tiap Senin 01:00 WIB
- cron: "0 18 * * *"   # tiap hari 01:00 WIB
```

---

## Load data di notebook/Colab

Setelah data ada di HF Datasets, bisa langsung load dari mana aja:

```python
import pandas as pd

# Ganti dengan username dan nama dataset kamu
HF_DATASET_REPO = "username-kamu/indonesian-health-news"

url = f"https://huggingface.co/datasets/{HF_DATASET_REPO}/resolve/main/health_news.csv"
df = pd.read_csv(url)

print(f"Total artikel: {len(df):,}")
print(df.head())
```

---

## Catatan
- GitHub Actions gratis untuk repo public (2000 menit/bulan untuk repo private)
- Scraping 1 run biasanya ~60–90 menit
- Data lama otomatis ter-replace tiap update (bukan append) — kalau mau simpan histori, ubah `path_in_repo` di `run_and_push.py` pakai tanggal, misal `health_news_2026_W27.csv`
