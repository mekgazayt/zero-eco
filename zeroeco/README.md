# ZERO ECO — Environmental Care Platform

Website Flask + MySQL dengan dua dashboard (user dan admin) dan lima fitur:
pelaporan sampah, monitoring banjir, laporan darurat, peta kondisi lingkungan,
dan carbon footprint tracking.

## Cara menjalankan

1. **Database** — nyalakan MySQL (XAMPP), buka phpMyAdmin, tab *Import*, pilih `zeroeco.sql`, klik *Kirim*.
2. **Install library** (disarankan di virtualenv):
   ```
   pip install -r requirements.txt
   ```
3. **Jalankan**:
   ```
   python app.py
   ```
   Buka http://127.0.0.1:5000

Saat pertama jalan, `app.py` otomatis menyiapkan kolom `role`, tabel `laporan`, dan membuat akun admin:

| | |
|---|---|
| Username | `admin` |
| Password | `admin12345` |

**Ganti password ini** sebelum dipakai di luar komputer sendiri, dengan mengatur environment variable
`ADMIN_PASSWORD` sebelum menjalankan pertama kali (atau ubah di tabel `users`).

### Environment variable (opsional)

| Variabel | Fungsi | Default |
|---|---|---|
| `DB_HOST` `DB_USER` `DB_PASSWORD` `DB_NAME` | koneksi MySQL | `localhost` / `root` / kosong / `zeroeco` |
| `SECRET_KEY` | kunci sesi | dibuat otomatis di `.secret_key` |
| `ADMIN_USERNAME` `ADMIN_PASSWORD` `ADMIN_EMAIL` | akun admin awal | `admin` / `admin12345` / `admin@zeroeco.local` |
| `FLASK_DEBUG` | `1` untuk mode debug saat pengembangan | `0` |

Peta memakai Leaflet + OpenStreetMap, jadi halaman peta dan form laporan butuh koneksi internet.

## Akun awal

Saat `app.py` pertama jalan (dan MySQL menyala), akun berikut dibuat otomatis bila belum ada akun dengan peran itu:

| Peran | Username | Password |
|---|---|---|
| Admin | `admin` | `admin12345` |
| Super Admin | `superadmin` | `superadmin12345` |

Ganti lewat environment variable `ADMIN_*` / `SUPERADMIN_USERNAME`, `SUPERADMIN_PASSWORD`, `SUPERADMIN_EMAIL`, `SUPERADMIN_NAMA`.
Super Admin juga bisa mengganti passwordnya di menu **Pengaturan**.
Bila username/email itu sudah dipakai akun biasa, akun tidak dibuat (lihat pesan di terminal). Alternatif lewat phpMyAdmin:
`UPDATE users SET role='superadmin' WHERE username='...';`

Database lama otomatis dimigrasi saat start: kolom `role` dilebarkan jadi `VARCHAR(20)` dan kolom `aktif` (1 = aktif, 0 = nonaktif) ditambahkan.

## Alur sistem

**Pengguna** (`/halaman`, `/laporan`, `/peta`, `/profil`)
- Daftar dan masuk, lalu mencatat aktivitas karbon (transportasi, listrik, makanan, sampah) dan melihat grafik serta saran.
- Membuat laporan: pilih jenis, isi judul dan deskripsi, unggah foto, tandai titik di peta (klik peta, geser penanda, atau "Gunakan lokasi saya").
  - **Sampah**: foto wajib.
  - **Banjir**: foto dan tinggi air (cm) wajib.
  - **Darurat**: foto opsional supaya pelaporan tidak terhambat; ada pengingat untuk menghubungi 112 bila ada bahaya langsung.
- Memantau status laporan sendiri dan membaca tanggapan admin. Laporan yang belum diproses masih bisa dihapus.
- Melihat peta semua laporan (kecuali yang ditolak), dengan filter jenis dan status.

**Admin** (`/admin`, `/admin/laporan`, `/admin/pengguna`)
- Ringkasan: jumlah pengguna, laporan per jenis dan status, laporan terbaru, emisi karbon seluruh pengguna.
- Kelola laporan: cari dan filter, lihat foto dan lokasi, ubah status
  (Menunggu verifikasi → Diverifikasi → Ditangani → Selesai, atau Ditolak), tulis tanggapan, hapus spam.
- Pengguna: lihat jumlah laporan dan emisi tiap akun, jadikan admin atau cabut hak admin, hapus akun.

Peran dibaca dari database di setiap permintaan, jadi perubahan peran langsung berlaku.

## Struktur

```
app.py                 backend (rute user + admin)
zeroeco.sql            skema database
requirements.txt
static/css/app.css     gaya bersama
static/uploads/        foto laporan
templates/
  gambar2.html         landing page
  login.html signup.html
  layout.html          kerangka halaman setelah login
  halaman.html         dashboard user (carbon footprint)
  laporan.html peta.html profil.html
  admin/base.html      kerangka admin & super admin (memakai layout.html)
  admin/dashboard.html laporan.html laporan_detail.html pengguna.html
  admin/_tabel_laporan.html  tabel "Kelola laporan" (dipakai dashboard & Kelola Laporan)
static/css/admin.css   tambahan gaya admin (di atas app.css)
```

## Perubahan dari kode awal

- `tambah.py` menjadi `app.py`; template `laporan.html` dan `peta.html` yang sebelumnya belum ada sudah dibuat.
- Kolom `role` pada `users`, tabel `laporan`, dan seluruh bagian admin ditambahkan.
- `profil.html` memakai tema hijau yang sama dengan halaman lain (sebelumnya tema gelap sendiri).
- Landing page: layanan ditambah *Carbon Footprint*, legenda peta disesuaikan (darurat, sampah, banjir), tombol berubah menjadi "Buka Dashboard" jika sudah login.
- Keamanan: proteksi CSRF pada semua form, validasi isi file gambar (bukan hanya ekstensi), redirect `next` hanya ke alamat internal, koordinat dan angka NaN/inf ditolak, popup peta anti-XSS, hash password tidak lagi diteruskan ke template, nama pelapor hanya terlihat admin, "Ingat saya" sekarang berfungsi, mode debug mati secara default.

## Catatan

- Target emisi bulanan di dashboard user masih disimpan di browser (`localStorage`), sama seperti kode awal.
- Untuk dipakai publik, jalankan di balik server WSGI (gunakan Gunicorn atau Waitress) dengan HTTPS, bukan `python app.py`.

## Catatan UI Admin & Super Admin

- Halaman admin dan super admin memakai **`layout.html` + `app.css` yang sama dengan landing/user** (navbar putih,
  judul Georgia dengan eyebrow, kartu metrik, panel, tombol hijau, tabel, badge). Kerangkanya `templates/admin/base.html`
  (blok: `eyebrow`, `heading`, `subtitle`, `actions`, `body`, `head_extra`, `scripts`); super admin memakai
  `templates/superadmin/base.html` yang mewarisinya.
- `static/css/admin.css` hanya **tambahan kecil** (pilihan status, matriks hak akses, label peran, kartu 5 kolom) dan
  memakai variabel `app.css` (`--green`, `--line`, ...), jadi ikut berubah bila tema user/landing diubah.
  Menu navbar admin & super admin ada di `templates/layout.html`.
- Perubahan backend di `app.py`: filter Jinja `waktu_singkat`, `waktu_lengkap`, `tanggal_singkat`, `emisi_fmt`, `foto_url`;
  helper `cari_laporan_admin()`; `admin_dashboard` mengirim `laporan_bulan_ini`, tabel + filter (`q`, `jenis`, `status`;
  tampilan awal status *Menunggu verifikasi*, `?status=` untuk semua).

## Super Admin (`/superadmin`)

Menu: Dashboard Super Admin, Kelola Akun, Hak Akses, Statistik Sistem, Pengaturan, Keluar.

- **Kelola Akun**: daftar semua akun + filter peran, matriks hak akses, tombol *+ Tambah akun Admin*,
  *Ubah* / *Ubah peran* (nama, email, peran Pengguna ↔ Admin, reset password, hapus akun),
  *Nonaktifkan* / *Aktifkan*. Akun nonaktif tidak bisa login dan sesi yang sedang berjalan ikut berhenti.
  Akun Super Admin tidak bisa diubah/dihapus dari antarmuka.
- **Hak Akses**: matriks + penjelasan peran + daftar pemegang hak pengelola.
- **Statistik Sistem**: akun & laporan per bulan (6 bulan), laporan per jenis/status, emisi karbon, penyumbang tertinggi.
- **Pengaturan**: ubah nama, ganti password, info sistem.

Matriks hak akses (`MATRIKS_HAK` di `app.py`) ditegakkan di kode:
Admin hanya mengelola akun **Pengguna** (nonaktifkan/aktifkan/hapus); akun Admin hanya oleh Super Admin.
Fitur warga (catat karbon, buat laporan) hanya untuk peran Pengguna.

Struktur tambahan: `templates/superadmin/*` (mewarisi `admin/base.html`), CSS tambahan di `static/css/admin.css`.
