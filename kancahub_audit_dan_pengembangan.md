# Laporan Audit Arsitektur & Rekomendasi Pengembangan Kanca-Hub

Dokumen ini merangkum hasil peninjauan kode secara langsung pada repositori **[Ini-Amin/Kanca-Hub](https://github.com/Ini-Amin/Kanca-Hub)** beserta rekomendasi perbaikan arsitektur, keamanan, dan manajemen dependensi.

## 1. Ringkasan Temuan Utama

Berdasarkan pemeriksaan terhadap file inti (`package.json`, `cli.js`, `pyproject.toml`, `requirements.txt`, `web_ui.py`, `validate.py`, `KANCAHUB.md`, dan `cmd/kancaproxy/main.go`), berikut adalah evaluasi kondisi repositori saat ini:

| Komponen / File | Temuan Saat Ini | Dampak Teknis & Risiko | Prioritas |
| :--- | :--- | :--- | :--- |
| **`web_ui.py`** | Endpoint `/process` tidak memiliki autentikasi lokal maupun proteksi CSRF; kredensial disimpan plaintext ke `exports/cf_accounts.txt`; blok `except Exception as e` mengembalikan `str(e)` mentah ke klien. | Jika server Flask terekspos ke jaringan lokal/publik (`0.0.0.0`), pihak luar dapat memicu eksekusi subproses dan membaca jejak *path* sistem internal. | Tinggi |
| **`pyproject.toml` vs `requirements.txt`** | Terdapat 11 pustaka di `requirements.txt` (`fastapi`, `mcp`, `nodriver`, `DrissionPage`, `opencv-python-headless`, `customtkinter`, dll.) yang tidak tercatat di `pyproject.toml`. | Instalasi melalui `pip install .` atau `pyproject.toml` akan gagal saat memanggil modul yang bergantung pada pustaka di `requirements.txt`. | Tinggi |
| **Versi Dependensi** | Seluruh dependensi menggunakan batas bawah terbuka (`>=`) tanpa *version pinning* atau *upper bound*. | Pembaruan otomatis pada paket pihak ketiga berisiko memicu *breaking changes* dan kegagalan *build* yang tidak terduga. | Sedang |
| **`package.json` & `cli.js`** | Nama paket masih `"auto-freecf"` dengan binary `"moycf": "cli.js"` dan URL mengarah ke repositori *upstream* (`mocasus/Auto-FreeCF.git`), sedangkan dokumentasi `README.md` dan `KANCAHUB.md` menggunakan perintah `kancahub`. | Pengguna yang menginstal via npm tidak dapat memanggil perintah `kancahub` secara langsung karena binary yang terdaftar hanya `moycf`. | Sedang |
| **`cmd/kancaproxy/main.go`** | *Relay proxy* berbasis Go sudah menggunakan `sync.RWMutex` dan `defer net.Conn.Close()`, namun belum menerapkan *dial/read/write timeout* eksplisit serta *passive health check* untuk *upstream* yang mati. | Goroutine berisiko menggantung (*goroutine leak*) apabila koneksi *upstream proxy* macet tanpa memutus sesi TCP. | Sedang |

## 2. Analisis Detail & Alasan Pengembangan

### A. Pengamanan Antarmuka Web (`web_ui.py`)
* **Kondisi Saat Ini**: Server Flask pada `web_ui.py` menerima *request* POST pada `/process` tanpa memverifikasi token sesi, serta mengembalikan pesan eksepsi internal secara langsung melalui `jsonify({"error": str(e)})`. Selain itu, file hasil ekspor ditulis ke `exports/cf_accounts.txt` dengan izin file bawaan sistem operasi.
* **Alasan Pengembangan**:
  1. **Mencegah Eksekusi Tanpa Izin**: Menambahkan mekanisme token akses lokal (melalui *environment variable* `KANCAHUB_WEB_TOKEN` atau pembuatan token acak saat *startup*) memastikan hanya pengguna sah yang dapat memicu *job* dari browser.
  2. **Sanitasi Error Response**: Mencatat *stack trace* lengkap ke *log* server (`logging.exception`) sambil hanya mengembalikan pesan kesalahan generik ke klien mencegah kebocoran struktur direktori.
  3. **Pembatasan Izin File (`chmod 600`)**: Mengatur izin file ekspor agar hanya dapat dibaca oleh pemilik (*owner*) melindungi token API dari pembacaan oleh proses lain di mesin yang sama.

```python
import os
import secrets
import logging
from functools import wraps
from flask import Flask, request, jsonify, abort

app = Flask(__name__)
WEB_TOKEN = os.environ.get("KANCAHUB_WEB_TOKEN") or secrets.token_urlsafe(24)

def require_local_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-KancaHub-Token") or request.form.get("token")
        if not token or not secrets.compare_digest(token, WEB_TOKEN):
            abort(401, description="Unauthorized: Token akses tidak valid.")
        return f(*args, **kwargs)
    return decorated

def save_secure_export(filepath: str, content: str) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    with os.fdopen(os.open(filepath, flags, 0o600), "a", encoding="utf-8") as f:
        f.write(content + "\n")
```

### B. Sinkronisasi & Modularisasi Dependensi (`pyproject.toml` & `requirements.txt`)
* **Kondisi Saat Ini**: `pyproject.toml` hanya mencantumkan 8 dependensi dasar, sementara `requirements.txt` mencantumkan 17 paket termasuk pustaka GUI (`customtkinter`), visi komputer (`opencv-python-headless`), dan audio (`SpeechRecognition`, `pydub`).
* **Alasan Pengembangan**:
  1. **Instalasi Lebih Ringan & Konsisten**: Memindahkan dependensi berat ke dalam `[project.optional-dependencies]` di `pyproject.toml` (misalnya grup `gui`, `vision`, dan `web`) memungkinkan pengguna CLI di VPS Linux menginstal paket inti dengan cepat tanpa harus mengunduh pustaka GUI atau pemrosesan audio/gambar jika tidak diperlukan.
  2. **Reprodusibilitas**: Menambahkan batas atas versi mayor (contoh: `>=0.27.0,<1.0.0`) menjaga kompatibilitas antarmuka API.

```toml
[project]
name = "kancahub"
version = "4.3.1"
description = "Unified CLI toolkit and proxy/worker management utilities"
requires-python = ">=3.10"
dependencies = [
    "httpx>=0.27.0,<1.0.0",
    "curl_cffi>=0.7.0,<1.0.0",
    "beautifulsoup4>=4.12.0,<5.0.0",
    "patchright>=1.40.1,<2.0.0",
    "requests>=2.31.0,<3.0.0",
    "rich>=13.7.0,<14.0.0",
    "websocket-client>=1.7.0,<2.0.0",
]

[project.optional-dependencies]
web = [
    "flask>=3.0.0,<4.0.0",
    "fastapi>=0.110.0,<1.0.0",
    "mcp>=1.0.0,<2.0.0",
]
gui = [
    "customtkinter>=5.2.0,<6.0.0",
]
media = [
    "pillow>=10.0.0,<12.0.0",
    "numpy>=1.26.0,<3.0.0",
    "opencv-python-headless>=4.9.0,<5.0.0",
    "SpeechRecognition>=3.10.0,<4.0.0",
    "pydub>=0.25.1,<1.0.0",
]
```

### C. Penyelarasan Identitas CLI (`package.json`)
* **Kondisi Saat Ini**: File `package.json` masih mendefinisikan `"name": "auto-freecf"` dan hanya mendaftarkan `"moycf": "cli.js"`, padahal seluruh dokumentasi di `README.md` dan `KANCAHUB.md` menginstruksikan penggunaan perintah `kancahub`.
* **Alasan Pengembangan**: Menambahkan alias binary `"kancahub": "cli.js"` (sambil tetap mempertahankan `"moycf"` untuk kompatibilitas mundur) memastikan dokumentasi selaras dengan perilaku aktual saat paket diinstal.

```json
{
  "name": "kancahub",
  "version": "4.3.1",
  "description": "KancaHub Unified CLI Orchestrator",
  "bin": {
    "kancahub": "cli.js",
    "moycf": "cli.js"
  },
  "repository": {
    "type": "git",
    "url": "https://github.com/Ini-Amin/Kanca-Hub.git"
  },
  "engines": {
    "node": ">=18.0.0"
  },
  "license": "MIT"
}
```

### D. Peningkatan Ketahanan Koneksi pada `cmd/kancaproxy/main.go`
* **Kondisi Saat Ini**: Pemutar *upstream proxy* berbasis Go menggunakan *round-robin* di atas `sync.RWMutex`, namun koneksi TCP yang lambat atau menggantung berpotensi menahan *goroutine* tetap hidup terlalu lama.
* **Alasan Pengembangan**:
  1. **Mencegah Kebocoran Goroutine (*Goroutine Leak*)**: Menerapkan `net.DialTimeout` (misalnya 10 detik) dan batas waktu *idle* (`SetDeadline`) memastikan koneksi yang macet segera ditutup dan *memory footprint* tetap rendah.
  2. **Isolasi Upstream Bermasalah**: Menandai *upstream* yang gagal berkali-kali dengan masa *cooldown* sementara mencegah *traffic* terus-menerus dikirim ke *node proxy* yang sedang mati.

```go
package main

import (
	"io"
	"net"
	"time"
)

const (
	upstreamDialTimeout = 10 * time.Second
	connIdleTimeout     = 60 * time.Second
)

func pipeWithTimeout(dst net.Conn, src net.Conn) {
	defer dst.Close()
	defer src.Close()
	_ = dst.SetDeadline(time.Now().Add(connIdleTimeout))
	_ = src.SetDeadline(time.Now().Add(connIdleTimeout))
	_, _ = io.Copy(dst, src)
}
```

### E. Perluasan Pengujian Pra-Runtime (`validate.py` & `kancahub doctor`)
* **Kondisi Saat Ini**: `validate.py` menguji impor pustaka Python dan struktur kelas dasar (`CFAutoGrabber`).
* **Alasan Pengembangan**: Mengintegrasikan pemeriksaan ketersediaan *binary* eksternal (seperti `go`, `wg`/`warp-cli`, dan `Xvfb` di Linux) serta validasi skema `.env` ke dalam `validate.py` akan membuat perintah `kancahub doctor` mampu mendeteksi masalah konfigurasi lingkungan sejak awal sebelum subproses dijalankan.
