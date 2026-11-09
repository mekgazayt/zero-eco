
import base64
import hmac
import io
import math
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mysql.connector
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from mysql.connector import Error
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent


# ──────────────────────────────────────────────────────────
#  Konfigurasi
# ──────────────────────────────────────────────────────────
def muat_secret_key():
    """SECRET_KEY dari environment, atau dibuat sekali lalu disimpan di .secret_key."""
    dari_env = os.environ.get("SECRET_KEY")
    if dari_env:
        return dari_env
    berkas = BASE_DIR / ".secret_key"
    if berkas.exists():
        return berkas.read_text().strip()
    kunci = secrets.token_hex(32)
    try:
        berkas.write_text(kunci)
    except OSError:
        pass
    return kunci


app = Flask(__name__)
app.secret_key = muat_secret_key()
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB
app.config["UPLOAD_FOLDER"] = str(BASE_DIR / "static" / "uploads")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.permanent_session_lifetime = timedelta(days=14)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD", ""),
    "database": os.environ.get("DB_NAME", "zeroeco"),
    "charset": "utf8mb4",
}

DB_SSL_CA = os.environ.get("DB_SSL_CA")

if DB_SSL_CA:
    ca_path = Path(DB_SSL_CA)

    if not ca_path.is_absolute():
        ca_path = BASE_DIR / ca_path

    if not ca_path.is_file():
        raise RuntimeError(f"File CA tidak ditemukan: {ca_path}")

    DB_CONFIG.update({
        "ssl_ca": str(ca_path),
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    })

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin12345")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@zeroeco.local")
SUPERADMIN_NAMA = os.environ.get("SUPERADMIN_NAMA", "Super Admin")
SUPERADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME", "superadmin")
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "superadmin12345")
SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "superadmin@zeroeco.local")

# Peran akun: user < admin < superadmin
ROLE_LABEL = {"user": "Pengguna", "admin": "Admin", "superadmin": "Super Admin"}
ADMIN_ROLES = ("admin", "superadmin")  # boleh membuka panel /admin

# Matriks hak akses (ditampilkan di halaman super admin; ditegakkan lewat decorator di bawah)
MATRIKS_HAK = [
    ("Catat jejak karbon", "✔", "–", "–"),
    ("Buat laporan", "✔", "–", "–"),
    ("Lihat peta", "✔", "✔", "✔"),
    ("Ubah status laporan", "–", "✔", "✔"),
    ("Kelola pengguna", "–", "✔", "✔"),
    ("Kelola akun Admin", "–", "–", "✔"),
    ("Statistik sistem", "–", "ringkas", "✔"),
]

# Faktor emisi (kg CO2e per satuan)
KATEGORI = {
    "Transportasi": {"faktor": 0.21, "satuan": "km", "warna": "#60a5fa"},
    "Listrik": {"faktor": 0.87, "satuan": "kWh", "warna": "#d4a72c"},
    "Makanan": {"faktor": 2.50, "satuan": "porsi", "warna": "#a78bfa"},
    "Sampah": {"faktor": 0.50, "satuan": "kg", "warna": "#e69a57"},
}

# Jenis laporan lingkungan
JENIS_LAPORAN = {
    "sampah": {"label": "Sampah", "ikon": "♻", "warna": "#d4a72c"},
    "banjir": {"label": "Banjir", "ikon": "≈", "warna": "#3b7dd8"},
    "darurat": {"label": "Darurat", "ikon": "!", "warna": "#d64545"},
}
JENIS_WAJIB_FOTO = {"sampah", "banjir"}  # darurat: foto opsional agar tidak menghambat pelaporan

STATUS_LAPORAN = ["Menunggu verifikasi", "Diverifikasi", "Ditangani", "Selesai", "Ditolak"]
STATUS_KELAS = {
    "Menunggu verifikasi": "menunggu",
    "Diverifikasi": "verifikasi",
    "Ditangani": "ditangani",
    "Selesai": "selesai",
    "Ditolak": "ditolak",
}

DDL_LAPORAN = """
CREATE TABLE IF NOT EXISTS laporan (
    id          INT          AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(50)  NOT NULL,
    jenis       VARCHAR(20)  NOT NULL,
    judul       VARCHAR(150) NOT NULL,
    deskripsi   TEXT         NOT NULL,
    lokasi      VARCHAR(255) NOT NULL,
    latitude    DOUBLE       NOT NULL,
    longitude   DOUBLE       NOT NULL,
    tinggi_air  FLOAT        NULL,
    foto        VARCHAR(255) NULL,
    status      VARCHAR(30)  NOT NULL DEFAULT 'Menunggu verifikasi',
    tanggapan   TEXT         NULL,
    dibuat      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    diperbarui  DATETIME     NULL,
    KEY idx_laporan_username (username),
    KEY idx_laporan_jenis (jenis),
    KEY idx_laporan_status (status),
    CONSTRAINT fk_laporan_user
        FOREIGN KEY (username) REFERENCES users(username)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


# ──────────────────────────────────────────────────────────
#  Database
# ──────────────────────────────────────────────────────────
def get_db():
    conn = mysql.connector.connect(**DB_CONFIG)
    return conn, conn.cursor(dictionary=True)


def close_db(conn, cursor):
    try:
        cursor.close()
        conn.close()
    except Exception:
        pass


def db_all(sql, params=()):
    conn, cur = get_db()
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        close_db(conn, cur)


def db_one(sql, params=()):
    conn, cur = get_db()
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        close_db(conn, cur)


def db_exec(sql, params=()):
    """INSERT/UPDATE/DELETE + commit. Mengembalikan jumlah baris terdampak."""
    conn, cur = get_db()
    try:
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount
    except Error:
        conn.rollback()
        raise
    finally:
        close_db(conn, cur)


def init_db():
    """Pastikan skema terbaru ada (kolom role/aktif, tabel laporan) dan akun admin + super admin tersedia."""
    try:
        conn, cur = get_db()
    except Error as exc:
        print(f"[ZERO ECO] Tidak dapat terhubung ke database: {exc}")
        print("[ZERO ECO] Pastikan MySQL berjalan dan zeroeco.sql sudah di-import.")
        return
    try:
        cur.execute("SHOW COLUMNS FROM users LIKE 'role'")
        kolom_role = cur.fetchall()
        if not kolom_role:
            cur.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'")
        elif "(10)" in str(kolom_role[0].get("Type", "")):
            cur.execute("ALTER TABLE users MODIFY role VARCHAR(20) NOT NULL DEFAULT 'user'")
        cur.execute("SHOW COLUMNS FROM users LIKE 'aktif'")
        if not cur.fetchall():
            cur.execute("ALTER TABLE users ADD COLUMN aktif TINYINT(1) NOT NULL DEFAULT 1")
        cur.execute(DDL_LAPORAN)
        conn.commit()

        buat_akun_awal(cur, conn, "admin", ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_EMAIL,
                       "Administrator", "ADMIN")
        buat_akun_awal(cur, conn, "superadmin", SUPERADMIN_USERNAME, SUPERADMIN_PASSWORD,
                       SUPERADMIN_EMAIL, SUPERADMIN_NAMA, "SUPERADMIN")
    except Error as exc:
        conn.rollback()
        print(f"[ZERO ECO] Inisialisasi database gagal: {exc}")
    finally:
        close_db(conn, cur)


def buat_akun_awal(cur, conn, role, username, password, email, nama, prefix_env):
    """Buat akun pertama untuk sebuah peran bila belum ada satu pun akun dengan peran itu."""
    cur.execute("SELECT id FROM users WHERE role=%s LIMIT 1", (role,))
    if cur.fetchone():
        return
    cur.execute("SELECT id FROM users WHERE username=%s OR email=%s", (username, email))
    if cur.fetchone():
        print(f"[ZERO ECO] Username/email {role} '{username}' sudah dipakai akun lain. "
              f"Ubah {prefix_env}_USERNAME / {prefix_env}_EMAIL atau ubah kolom role lewat phpMyAdmin.")
        return
    cur.execute(
        "INSERT INTO users (nama,email,username,password,bergabung,role) VALUES (%s,%s,%s,%s,%s,%s)",
        (nama, email, username, generate_password_hash(password), datetime.now().date(), role),
    )
    conn.commit()
    print(f"[ZERO ECO] Akun {role} dibuat -> username: {username} | password: {password}  (segera ganti di produksi)")


# ──────────────────────────────────────────────────────────
#  Keamanan: CSRF, sesi, hak akses
# ──────────────────────────────────────────────────────────
def csrf_token():
    token = session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def aman_internal(url):
    """Hanya izinkan redirect ke path internal (cegah open redirect)."""
    return bool(url) and url.startswith("/") and not url.startswith("//") and "\\" not in url


def kembali_aman(default_endpoint="index"):
    ref = request.referrer
    if ref:
        parsed = urlparse(ref)
        if parsed.netloc == request.host:
            return redirect(ref)
    return redirect(url_for(default_endpoint))


@app.before_request
def periksa_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        dikirim = request.form.get("csrf_token", "")
        diharapkan = session.get("_csrf", "")
        if not diharapkan or not hmac.compare_digest(dikirim, diharapkan):
            flash("Sesi formulir sudah kedaluwarsa. Muat ulang halaman lalu coba lagi.", "error")
            return kembali_aman()


def get_current_user():
    """Pengguna yang sedang login (dibaca dari DB, di-cache per request)."""
    if "user" in g:
        return g.user
    g.user = None
    username = session.get("username")
    if username:
        g.user = db_one(
            "SELECT id,nama,email,username,role,bergabung,aktif FROM users WHERE username=%s",
            (username,),
        )
        if g.user and not g.user["aktif"]:
            g.user = None  # akun dinonaktifkan -> sesi dianggap tidak berlaku
    return g.user


def beranda_untuk(user):
    if user and user["role"] == "superadmin":
        return url_for("superadmin_dashboard")
    if user and user["role"] == "admin":
        return url_for("admin_dashboard")
    return url_for("halaman")


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            session.clear()
            flash("Silakan masuk terlebih dahulu.", "error")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        if get_current_user()["role"] not in ADMIN_ROLES:
            flash("Halaman tersebut khusus admin.", "error")
            return redirect(url_for("halaman"))
        return func(*args, **kwargs)
    return wrapper


def superadmin_required(func):
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if user["role"] != "superadmin":
            flash("Halaman tersebut khusus super admin.", "error")
            return redirect(beranda_untuk(user))
        return func(*args, **kwargs)
    return wrapper


def khusus_pengguna(func):
    """Fitur warga (catat karbon, buat laporan): admin & super admin tidak memakainya."""
    @wraps(func)
    @login_required
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if user["role"] in ADMIN_ROLES:
            flash("Fitur ini hanya untuk akun Pengguna.", "error")
            return redirect(beranda_untuk(user))
        return func(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_globals():
    return {
        "current_user": get_current_user(),
        "JENIS_LAPORAN": JENIS_LAPORAN,
        "STATUS_LAPORAN": STATUS_LAPORAN,
        "ROLE_LABEL": ROLE_LABEL,
        "MATRIKS_HAK": MATRIKS_HAK,
        "beranda_url": lambda: beranda_untuk(get_current_user()),
    }


@app.template_filter("status_kelas")
def status_kelas(status):
    return STATUS_KELAS.get(status, "menunggu")


@app.template_filter("tgl")
def format_tanggal(nilai):
    if isinstance(nilai, datetime):
        return nilai.strftime("%d-%m-%Y %H:%M")
    if isinstance(nilai, date):
        return nilai.strftime("%d-%m-%Y")
    return nilai or "-"


BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


@app.template_filter("tanggal_singkat")
def tanggal_singkat(nilai):
    """1 Okt 2026 (dipakai tabel akun super admin)."""
    if isinstance(nilai, (datetime, date)):
        return f"{nilai.day} {BULAN_SINGKAT[nilai.month - 1]} {nilai.year}"
    return format_tanggal(nilai)


@app.template_filter("waktu_singkat")
def waktu_singkat(nilai):
    """5 Nov 07.12 (dipakai tabel dashboard admin)."""
    if isinstance(nilai, datetime):
        return f"{nilai.day} {BULAN_SINGKAT[nilai.month - 1]} {nilai:%H.%M}"
    return format_tanggal(nilai)


@app.template_filter("waktu_lengkap")
def waktu_lengkap(nilai):
    """5 Nov 2026 07.12 (dipakai halaman detail laporan admin)."""
    if isinstance(nilai, datetime):
        return f"{nilai.day} {BULAN_SINGKAT[nilai.month - 1]} {nilai.year} {nilai:%H.%M}"
    return format_tanggal(nilai)


@app.template_filter("foto_url")
def foto_url(path):
    """Path foto dari DB -> URL static. Toleran: 'uploads/x.jpg', '/static/uploads/x.jpg',
    'static\\uploads\\x.jpg', atau hanya 'x.jpg'."""
    if not path:
        return ""
    p = str(path).strip().replace("\\", "/").lstrip("/")
    if p.startswith("static/"):
        p = p[len("static/"):]
    if "/" not in p:
        p = f"uploads/{p}"
    return url_for("static", filename=p)


@app.template_filter("emisi_fmt")
def emisi_fmt(kg):
    """Emisi dalam kg -> '9,4 t CO₂e' bila >= 1000 kg, selain itu '420 kg CO₂e'."""
    kg = float(kg or 0)
    if kg >= 1000:
        teks, satuan = f"{kg / 1000:.1f}", "t"
    else:
        teks, satuan = f"{kg:.0f}", "kg"
    return f"{teks.replace('.', ',')} {satuan} CO₂e"


# ──────────────────────────────────────────────────────────
#  Utilitas
# ──────────────────────────────────────────────────────────
def ke_float(nilai):
    """Ubah ke float; None bila kosong/bukan angka/tak terhingga."""
    try:
        angka = float(str(nilai).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return angka if math.isfinite(angka) else None


def ekstensi_diizinkan(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def isi_gambar_valid(stream):
    """Cek tanda (magic bytes) agar file benar-benar PNG/JPEG/WEBP."""
    kepala = stream.read(12)
    stream.seek(0)
    return (kepala.startswith(b"\xff\xd8\xff")
            or kepala.startswith(b"\x89PNG\r\n\x1a\n")
            or (kepala[:4] == b"RIFF" and kepala[8:12] == b"WEBP"))


def simpan_foto(berkas):
    """Simpan foto upload. Mengembalikan (path_relatif, pesan_error)."""
    if not berkas or not berkas.filename:
        return None, None
    if not ekstensi_diizinkan(berkas.filename) or not isi_gambar_valid(berkas.stream):
        return None, "Foto harus berupa gambar PNG, JPG, JPEG, atau WEBP."
    ekstensi = berkas.filename.rsplit(".", 1)[1].lower()
    nama = f"{uuid.uuid4().hex}.{ekstensi}"
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    berkas.save(os.path.join(app.config["UPLOAD_FOLDER"], nama))
    return f"uploads/{nama}", None


def hapus_foto(path_relatif):
    if not path_relatif:
        return
    target = Path(app.config["UPLOAD_FOLDER"]) / Path(path_relatif).name
    try:
        target.unlink()
    except OSError:
        pass


# ──────────────────────────────────────────────────────────
#  Carbon footprint
# ──────────────────────────────────────────────────────────
def hitung_emisi(kategori, jumlah):
    return round(jumlah * KATEGORI.get(kategori, {}).get("faktor", 0), 4)


def buat_saran(total, kontribusi):
    if total == 0:
        return "Belum ada emisi yang tercatat. Mulailah mencatat aktivitas untuk melihat analisis."
    terbesar = max(kontribusi, key=kontribusi.get)
    pesan = {
        "Transportasi": "Emisi terbesar berasal dari transportasi. Pertimbangkan berjalan kaki, bersepeda, atau menggunakan transportasi umum.",
        "Listrik": "Konsumsi listrik menjadi penyumbang emisi terbesar. Matikan perangkat saat tidak digunakan dan gunakan lampu hemat energi.",
        "Makanan": "Emisi makanan menjadi penyumbang terbesar. Kurangi pemborosan makanan dan variasikan sumber pangan.",
        "Sampah": "Sampah menjadi penyumbang emisi terbesar. Pilah sampah dan olah sampah organik bila memungkinkan.",
    }
    return pesan[terbesar]


def ambil_kontribusi(username):
    rows = db_all("SELECT jenis, SUM(karbon) AS sub FROM riwayat WHERE username=%s GROUP BY jenis", (username,))
    hasil = {k: 0.0 for k in KATEGORI}
    for row in rows:
        if row["jenis"] in hasil:
            hasil[row["jenis"]] = round(float(row["sub"] or 0), 2)
    return hasil, round(sum(hasil.values()), 2)


def buat_chart(kontribusi):
    labels = list(KATEGORI)
    values = [kontribusi.get(k, 0) for k in labels]
    colors = [KATEGORI[k]["warna"] for k in labels]
    fig, ax = plt.subplots(figsize=(8, 3.3))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    bars = ax.bar(labels, values, color=colors, width=.52, zorder=3)
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{val:.2f}",
                    ha="center", va="bottom", color="#34443a", fontsize=9)
    ax.set_ylabel("kg CO₂e", color="#68736b")
    ax.tick_params(colors="#526057")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#e5e9e2")
    ax.yaxis.grid(True, color="#edf0eb", linewidth=.7, zorder=0)
    ax.set_axisbelow(True)
    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def hitung_stats(username):
    row = db_one("""SELECT SUM(karbon) AS total, COUNT(*) AS jumlah,
                    COUNT(DISTINCT DATE(tanggal)) AS hari
                    FROM riwayat WHERE username=%s""", (username,))
    laporan = db_one("SELECT COUNT(*) AS n FROM laporan WHERE username=%s", (username,))
    total = round(float(row["total"] or 0), 2)
    if total == 0:
        level = "Hijau"
    elif total < 10:
        level = "Baik"
    elif total < 50:
        level = "Waspada"
    else:
        level = "Perlu perhatian"
    return {
        "total_aktivitas": int(row["jumlah"] or 0),
        "total_karbon": total,
        "hari_aktif": int(row["hari"] or 0),
        "peringkat": level,
        "total_laporan": int(laporan["n"] or 0),
    }


# ──────────────────────────────────────────────────────────
#  Halaman publik & autentikasi
# ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("gambar2.html")


@app.route("/gambar2.html")
def landing_alias():
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    user_aktif = get_current_user()
    if user_aktif and request.method == "GET":
        return redirect(beranda_untuk(user_aktif))

    tujuan = request.values.get("next", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db_one("SELECT * FROM users WHERE username=%s", (username,))
        valid = False
        if user:
            tersimpan = user["password"]
            try:
                valid = check_password_hash(tersimpan, password)
            except (ValueError, TypeError):
                valid = False
            # Kompatibilitas akun lama yang password-nya masih teks biasa -> otomatis di-hash.
            sudah_hash = tersimpan.startswith(("scrypt:", "pbkdf2:"))
            if not valid and not sudah_hash and hmac.compare_digest(tersimpan.encode(), password.encode()):
                valid = True
                db_exec("UPDATE users SET password=%s WHERE id=%s",
                        (generate_password_hash(password), user["id"]))
        if user and valid and not user.get("aktif", 1):
            return render_template("login.html", error="Akun ini dinonaktifkan. Hubungi administrator.", next=tujuan)
        if user and valid:
            session.clear()  # sesi baru setiap login (cegah session fixation)
            session["username"] = user["username"]
            session["_csrf"] = secrets.token_urlsafe(32)
            session.permanent = bool(request.form.get("remember"))
            if aman_internal(tujuan):
                return redirect(tujuan)
            return redirect(beranda_untuk(user))
        return render_template("login.html", error="Username atau password salah.", next=tujuan)
    return render_template("login.html", error=None, next=tujuan)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        if get_current_user():
            return redirect(beranda_untuk(get_current_user()))

        return render_template(
            "signup.html",
            error=None,
            success=None,
            form={}
        )

    nama = request.form.get("nama", "").strip()
    email = request.form.get("email", "").strip().lower()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    form = {
        "nama": nama,
        "email": email,
        "username": username
    }

    def gagal(pesan):
        return render_template(
            "signup.html",
            error=pesan,
            success=None,
            form=form
        )

    # Validasi
    if not all([nama, email, username, password]):
        return gagal("Semua kolom wajib diisi.")

    if len(nama) > 100:
        return gagal("Nama maksimal 100 karakter.")

    if len(email) > 150:
        return gagal("Email maksimal 150 karakter.")

    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return gagal("Format email tidak valid.")

    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", username):
        return gagal(
            "Username hanya boleh 3-30 karakter berupa huruf, angka, titik, garis bawah, atau strip."
        )

    if len(password) < 8:
        return gagal("Password minimal 8 karakter.")

    try:

        # cek username/email
        ada = db_one(
            """
            SELECT id
            FROM users
            WHERE username=%s OR email=%s
            LIMIT 1
            """,
            (username, email)
        )

        if ada:
            return gagal("Username atau email sudah digunakan.")

        # generate id unik
        while True:
            user_id = secrets.randbelow(2147483647)

            cek_id = db_one(
                "SELECT id FROM users WHERE id=%s",
                (user_id,)
            )

            if not cek_id:
                break

        sql = """
        INSERT INTO users
        (
            id,
            nama,
            email,
            username,
            password,
            bergabung,
            role,
            aktif
        )
        VALUES
        (
            %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        """

        params = (
            user_id,
            nama,
            email,
            username,
            generate_password_hash(password),
            datetime.now().date(),
            "user",
            1
        )

        # DEBUG
        print("=== SIGNUP DEBUG ===")
        print("SQL =", sql)
        print("PARAMS =", params)
        print("PARAM COUNT =", len(params))
        print("====================")

        db_exec(sql, params)

    except Error as exc:

        print("=== MYSQL ERROR ===")
        print(repr(exc))
        print("===================")

        if getattr(exc, "errno", None) == 1062:
            return gagal("Username atau email sudah digunakan.")

        app.logger.exception("ERROR REGISTRASI ZERO ECO")

        return gagal(
            f"Pendaftaran gagal: {str(exc)}"
        )

    return render_template(
        "signup.html",
        error=None,
        success="Akun berhasil dibuat! Silakan login.",
        form={}
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ──────────────────────────────────────────────────────────
#  Dashboard USER: carbon footprint tracking
# ──────────────────────────────────────────────────────────
@app.route("/halaman")
@khusus_pengguna
def halaman():
    user = get_current_user()
    username = user["username"]
    riwayat = db_all("""SELECT jenis,jumlah,karbon,tanggal FROM riwayat
                        WHERE username=%s ORDER BY tanggal DESC LIMIT 30""", (username,))
    for row in riwayat:
        row["tanggal"] = format_tanggal(row.get("tanggal"))
    kontribusi, total = ambil_kontribusi(username)
    jumlah_laporan = db_one("SELECT COUNT(*) AS n FROM laporan WHERE username=%s", (username,))["n"]
    return render_template(
        "halaman.html", user=user, riwayat=riwayat, total_karbon=total,
        karbon_transportasi=kontribusi["Transportasi"], karbon_listrik=kontribusi["Listrik"],
        karbon_makanan=kontribusi["Makanan"], karbon_sampah=kontribusi["Sampah"],
        saran=buat_saran(total, kontribusi),
        chart_data=buat_chart(kontribusi) if total > 0 else None,
        pohon_offset=round(total / 21, 1) if total else 0,
        kategori_info=KATEGORI, jumlah_laporan=jumlah_laporan,
    )


@app.route("/tambah", methods=["POST"])
@khusus_pengguna
def tambah():
    kategori = request.form.get("jenis", "")
    jumlah = ke_float(request.form.get("jumlah", "0"))
    if kategori not in KATEGORI or jumlah is None or jumlah <= 0 or jumlah > 1_000_000:
        flash("Kategori atau jumlah aktivitas tidak valid.", "error")
        return redirect(url_for("halaman"))
    try:
        db_exec("""INSERT INTO riwayat (username,jenis,jumlah,karbon,tanggal)
                   VALUES (%s,%s,%s,%s,%s)""",
                (session["username"], kategori, jumlah, hitung_emisi(kategori, jumlah), datetime.now()))
        flash("Aktivitas berhasil dicatat.", "success")
    except Error:
        flash("Aktivitas gagal disimpan.", "error")
    return redirect(url_for("halaman"))


# ──────────────────────────────────────────────────────────
#  Pelaporan: sampah, banjir, darurat
# ──────────────────────────────────────────────────────────
def laporan_milik(username):
    return db_all("""SELECT id,jenis,judul,lokasi,foto,status,tanggapan,tinggi_air,dibuat
                     FROM laporan WHERE username=%s ORDER BY dibuat DESC LIMIT 50""", (username,))


def tampil_laporan(form=None, status=200):
    user = get_current_user()
    return render_template("laporan.html", form=form or {}, riwayat_laporan=laporan_milik(user["username"])), status


@app.route("/laporan", methods=["GET", "POST"])
@khusus_pengguna
def laporan():
    if request.method == "GET":
        return tampil_laporan()

    form = request.form
    jenis = form.get("jenis", "").lower()
    judul = form.get("judul", "").strip()
    deskripsi = form.get("deskripsi", "").strip()
    lokasi = form.get("lokasi", "").strip()

    def gagal(pesan):
        flash(pesan, "error")
        return tampil_laporan(form, 400)

    if jenis not in JENIS_LAPORAN:
        return gagal("Pilih jenis laporan: sampah, banjir, atau darurat.")
    if not judul or not deskripsi or not lokasi:
        return gagal("Lengkapi judul, deskripsi, dan lokasi laporan.")
    if len(judul) > 150 or len(lokasi) > 255 or len(deskripsi) > 3000:
        return gagal("Judul maksimal 150 karakter, lokasi 255 karakter, dan deskripsi 3000 karakter.")

    latitude, longitude = ke_float(form.get("latitude")), ke_float(form.get("longitude"))
    if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return gagal("Titik lokasi belum valid. Klik peta, atau isi latitude dan longitude dengan benar.")

    tinggi = None
    if jenis == "banjir":
        tinggi = ke_float(form.get("tinggi_air"))
        if tinggi is None or not (0 <= tinggi <= 1000):
            return gagal("Masukkan tinggi air banjir dalam sentimeter (0–1000).")

    foto = request.files.get("foto")
    ada_foto = bool(foto and foto.filename)
    if jenis in JENIS_WAJIB_FOTO and not ada_foto:
        return gagal("Laporan sampah dan banjir wajib menyertakan foto.")
    foto_path, pesan_foto = simpan_foto(foto) if ada_foto else (None, None)
    if pesan_foto:
        return gagal(pesan_foto)

    try:
        db_exec("""INSERT INTO laporan (username,jenis,judul,deskripsi,lokasi,latitude,longitude,
                                        tinggi_air,foto,status,dibuat)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'Menunggu verifikasi',%s)""",
                (session["username"], jenis, judul, deskripsi, lokasi,
                 latitude, longitude, tinggi, foto_path, datetime.now()))
    except Error:
        hapus_foto(foto_path)
        return gagal("Laporan gagal disimpan. Pastikan database sudah diperbarui dari zeroeco.sql.")
    flash("Laporan berhasil dikirim dan menunggu verifikasi admin.", "success")
    return redirect(url_for("laporan"))


@app.route("/laporan/<int:laporan_id>/hapus", methods=["POST"])
@khusus_pengguna
def laporan_hapus(laporan_id):
    row = db_one("SELECT foto,status FROM laporan WHERE id=%s AND username=%s",
                 (laporan_id, session["username"]))
    if not row:
        abort(404)
    if row["status"] != "Menunggu verifikasi":
        flash("Laporan yang sudah diproses admin tidak bisa dihapus.", "error")
        return redirect(url_for("laporan"))
    db_exec("DELETE FROM laporan WHERE id=%s", (laporan_id,))
    hapus_foto(row["foto"])
    flash("Laporan dihapus.", "success")
    return redirect(url_for("laporan"))


# ──────────────────────────────────────────────────────────
#  Peta kondisi lingkungan
# ──────────────────────────────────────────────────────────
@app.route("/peta")
@login_required
def peta():
    return render_template("peta.html")


@app.route("/api/laporan")
@login_required
def api_laporan():
    admin = get_current_user()["role"] in ADMIN_ROLES
    sql = """SELECT l.id,l.jenis,l.judul,l.deskripsi,l.lokasi,l.latitude,l.longitude,
                    l.tinggi_air,l.foto,l.status,l.tanggapan,l.dibuat,u.nama AS pelapor
             FROM laporan l JOIN users u ON u.username = l.username"""
    if not admin:
        sql += " WHERE l.status <> 'Ditolak'"  # laporan ditolak hanya terlihat oleh admin
    sql += " ORDER BY l.dibuat DESC LIMIT 1000"
    rows = db_all(sql)
    for row in rows:
        row["dibuat"] = format_tanggal(row.get("dibuat"))
        row["foto"] = url_for("static", filename=row["foto"]) if row.get("foto") else None
        if not admin:
            row.pop("pelapor", None)  # privasi: nama pelapor hanya untuk admin
    return jsonify(rows)


# ──────────────────────────────────────────────────────────
#  Profil
# ──────────────────────────────────────────────────────────
@app.route("/profil")
@login_required
def profil():
    user = get_current_user()
    return render_template("profil.html", user=user, stats=hitung_stats(user["username"]))


# ──────────────────────────────────────────────────────────
#  Dashboard ADMIN
# ──────────────────────────────────────────────────────────
def cari_laporan_admin(jenis="", status="", q="", limit=300):
    """Daftar laporan untuk admin (dipakai dashboard dan halaman Kelola Laporan)."""
    kondisi, params = [], []
    if jenis in JENIS_LAPORAN:
        kondisi.append("l.jenis=%s")
        params.append(jenis)
    if status in STATUS_LAPORAN:
        kondisi.append("l.status=%s")
        params.append(status)
    if q:
        kondisi.append("(l.judul LIKE %s OR l.lokasi LIKE %s OR u.nama LIKE %s OR u.username LIKE %s)")
        params.extend([f"%{q}%"] * 4)
    where = (" WHERE " + " AND ".join(kondisi)) if kondisi else ""
    return db_all(f"""SELECT l.id,l.jenis,l.judul,l.lokasi,l.status,l.dibuat,u.nama AS pelapor,u.username
                      FROM laporan l JOIN users u ON u.username=l.username{where}
                      ORDER BY l.dibuat DESC LIMIT {int(limit)}""", tuple(params))


@app.route("/admin")
@admin_required
def admin_dashboard():
    jenis = request.args.get("jenis", "")
    jenis = jenis if jenis in JENIS_LAPORAN else ""
    status_arg = request.args.get("status")
    if status_arg is None:
        status = "Menunggu verifikasi"  # tampilan awal: yang perlu diperiksa dulu
    else:
        status = status_arg if status_arg in STATUS_LAPORAN else ""  # "" = semua status
    q = request.args.get("q", "").strip()

    jumlah_user = db_one("SELECT COUNT(*) AS n FROM users WHERE role='user'")["n"]
    per_jenis = {k: 0 for k in JENIS_LAPORAN}
    for r in db_all("SELECT jenis, COUNT(*) AS n FROM laporan GROUP BY jenis"):
        if r["jenis"] in per_jenis:
            per_jenis[r["jenis"]] = r["n"]
    per_status = {s: 0 for s in STATUS_LAPORAN}
    for r in db_all("SELECT status, COUNT(*) AS n FROM laporan GROUP BY status"):
        if r["status"] in per_status:
            per_status[r["status"]] = r["n"]
    awal_bulan = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    laporan_bulan_ini = db_one("SELECT COUNT(*) AS n FROM laporan WHERE dibuat >= %s", (awal_bulan,))["n"]
    emisi_kat = {k: 0.0 for k in KATEGORI}
    for r in db_all("SELECT jenis, SUM(karbon) AS total FROM riwayat GROUP BY jenis"):
        if r["jenis"] in emisi_kat:
            emisi_kat[r["jenis"]] = round(float(r["total"] or 0), 2)
    return render_template(
        "admin/dashboard.html",
        jumlah_user=jumlah_user, per_jenis=per_jenis, per_status=per_status,
        total_laporan=sum(per_jenis.values()), laporan_bulan_ini=laporan_bulan_ini,
        emisi_kat=emisi_kat, total_emisi=round(sum(emisi_kat.values()), 2),
        rows=cari_laporan_admin(jenis, status, q, limit=50),
        f_jenis=jenis, f_status=status, f_q=q,
    )


@app.route("/admin/laporan")
@admin_required
def admin_laporan():
    jenis = request.args.get("jenis", "")
    jenis = jenis if jenis in JENIS_LAPORAN else ""
    status = request.args.get("status", "")
    status = status if status in STATUS_LAPORAN else ""
    q = request.args.get("q", "").strip()
    rows = cari_laporan_admin(jenis, status, q, limit=300)
    return render_template("admin/laporan.html", rows=rows, f_jenis=jenis, f_status=status, f_q=q)


def ambil_laporan_admin(laporan_id):
    row = db_one("""SELECT l.*,u.nama AS pelapor,u.email AS email_pelapor
                    FROM laporan l JOIN users u ON u.username=l.username WHERE l.id=%s""", (laporan_id,))
    if not row:
        abort(404)
    return row


@app.route("/admin/laporan/<int:laporan_id>")
@admin_required
def admin_laporan_detail(laporan_id):
    return render_template("admin/laporan_detail.html", l=ambil_laporan_admin(laporan_id))


@app.route("/admin/laporan/<int:laporan_id>/status", methods=["POST"])
@admin_required
def admin_laporan_status(laporan_id):
    ambil_laporan_admin(laporan_id)
    status = request.form.get("status", "")
    tanggapan = request.form.get("tanggapan", "").strip()
    if status not in STATUS_LAPORAN:
        flash("Status tidak valid.", "error")
    elif len(tanggapan) > 1000:
        flash("Tanggapan maksimal 1000 karakter.", "error")
    else:
        db_exec("UPDATE laporan SET status=%s, tanggapan=%s, diperbarui=%s WHERE id=%s",
                (status, tanggapan or None, datetime.now(), laporan_id))
        flash("Status laporan diperbarui.", "success")
    return redirect(url_for("admin_laporan_detail", laporan_id=laporan_id))


@app.route("/admin/laporan/<int:laporan_id>/hapus", methods=["POST"])
@admin_required
def admin_laporan_hapus(laporan_id):
    row = ambil_laporan_admin(laporan_id)
    db_exec("DELETE FROM laporan WHERE id=%s", (laporan_id,))
    hapus_foto(row["foto"])
    flash("Laporan dihapus.", "success")
    return redirect(url_for("admin_laporan"))


def ambil_user_target(user_id, boleh=("user",)):
    """Akun yang boleh dikelola oleh pemanggil. Peran di luar `boleh` diperlakukan seperti tidak ada."""
    target = db_one("SELECT id,nama,email,username,role,aktif FROM users WHERE id=%s", (user_id,))
    if not target or target["role"] not in boleh:
        abort(404)
    return target


def hapus_akun_beserta_data(target):
    fotos = db_all("SELECT foto FROM laporan WHERE username=%s AND foto IS NOT NULL", (target["username"],))
    db_exec("DELETE FROM users WHERE id=%s", (target["id"],))  # riwayat & laporan ikut terhapus (CASCADE)
    for f in fotos:
        hapus_foto(f["foto"])


@app.route("/admin/pengguna")
@admin_required
def admin_pengguna():
    """Admin hanya mengelola akun Pengguna. Akun Admin dikelola super admin."""
    rows = db_all("""SELECT u.id,u.nama,u.email,u.username,u.role,u.bergabung,u.aktif,
                            (SELECT COUNT(*) FROM laporan l WHERE l.username=u.username) AS jumlah_laporan,
                            (SELECT COALESCE(SUM(r.karbon),0) FROM riwayat r WHERE r.username=u.username) AS total_emisi
                     FROM users u WHERE u.role='user' ORDER BY u.id DESC""")
    for r in rows:
        r["total_emisi"] = round(float(r["total_emisi"] or 0), 2)
    return render_template("admin/pengguna.html", rows=rows)


@app.route("/admin/pengguna/<int:user_id>/status", methods=["POST"])
@admin_required
def admin_pengguna_status(user_id):
    target = ambil_user_target(user_id)
    baru = 0 if target["aktif"] else 1
    db_exec("UPDATE users SET aktif=%s WHERE id=%s", (baru, user_id))
    flash(f"Akun {target['username']} {'diaktifkan' if baru else 'dinonaktifkan'}.", "success")
    return redirect(url_for("admin_pengguna"))


@app.route("/admin/pengguna/<int:user_id>/hapus", methods=["POST"])
@admin_required
def admin_pengguna_hapus(user_id):
    target = ambil_user_target(user_id)
    hapus_akun_beserta_data(target)
    flash(f"Akun {target['username']} beserta datanya dihapus.", "success")
    return redirect(url_for("admin_pengguna"))


# ──────────────────────────────────────────────────────────
#  Dashboard SUPER ADMIN
# ──────────────────────────────────────────────────────────
def cek_data_akun(nama, email, password=None, password_wajib=False):
    """Validasi form akun (tambah/ubah). Mengembalikan pesan error atau None."""
    if not nama or not email:
        return "Nama dan email wajib diisi."
    if len(nama) > 100 or len(email) > 150:
        return "Nama maksimal 100 karakter dan email maksimal 150 karakter."
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "Format email tidak valid."
    if password_wajib and not password:
        return "Password wajib diisi."
    if password and len(password) < 8:
        return "Password minimal 8 karakter."
    return None


def enam_bulan_terakhir():
    now = datetime.now()
    hasil = []
    for i in range(5, -1, -1):
        bulan, tahun = now.month - i, now.year
        while bulan <= 0:
            bulan += 12
            tahun -= 1
        hasil.append((tahun, bulan))
    return hasil


@app.route("/superadmin")
@superadmin_required
def superadmin_dashboard():
    per_role = {k: 0 for k in ROLE_LABEL}
    for r in db_all("SELECT role, COUNT(*) AS n FROM users GROUP BY role"):
        if r["role"] in per_role:
            per_role[r["role"]] = r["n"]
    nonaktif = db_one("SELECT COUNT(*) AS n FROM users WHERE aktif=0")["n"]
    per_status = {s: 0 for s in STATUS_LAPORAN}
    for r in db_all("SELECT status, COUNT(*) AS n FROM laporan GROUP BY status"):
        if r["status"] in per_status:
            per_status[r["status"]] = r["n"]
    terbaru = db_all("""SELECT id,nama,username,role,bergabung,aktif FROM users
                        ORDER BY bergabung DESC, id DESC LIMIT 6""")
    return render_template(
        "superadmin/dashboard.html",
        per_role=per_role, total_akun=sum(per_role.values()), nonaktif=nonaktif,
        per_status=per_status, total_laporan=sum(per_status.values()), terbaru=terbaru,
    )


@app.route("/superadmin/akun")
@superadmin_required
def superadmin_akun():
    peran = request.args.get("peran", "")
    peran = peran if peran in ROLE_LABEL else ""
    where, params = "", ()
    if peran:
        where, params = " WHERE role=%s", (peran,)
    rows = db_all(f"""SELECT id,nama,username,role,bergabung,aktif FROM users{where}
                      ORDER BY FIELD(role,'superadmin','admin','user'), id DESC""", params)
    return render_template("superadmin/akun.html", rows=rows, f_peran=peran)


@app.route("/superadmin/akun/baru", methods=["GET", "POST"])
@superadmin_required
def superadmin_akun_baru():
    form = {"nama": "", "email": "", "username": ""}
    error = None
    if request.method == "POST":
        form = {k: request.form.get(k, "").strip() for k in form}
        password = request.form.get("password", "")
        error = cek_data_akun(form["nama"], form["email"], password, password_wajib=True)
        if not error and not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", form["username"]):
            error = "Username 3–30 karakter: huruf, angka, titik, garis bawah, atau strip."
        if not error and db_one("SELECT id FROM users WHERE username=%s OR email=%s",
                                (form["username"], form["email"])):
            error = "Username atau email sudah dipakai."
        if not error:
            try:
                db_exec("""INSERT INTO users (nama,email,username,password,bergabung,role)
                           VALUES (%s,%s,%s,%s,%s,'admin')""",
                        (form["nama"], form["email"], form["username"],
                         generate_password_hash(password), datetime.now().date()))
            except Error as exc:
                error = ("Username atau email sudah dipakai." if getattr(exc, "errno", None) == 1062
                         else "Gagal menyimpan akun. Periksa koneksi database.")
            else:
                flash(f"Akun admin {form['username']} dibuat.", "success")
                return redirect(url_for("superadmin_akun"))
    return render_template("superadmin/akun_baru.html", form=form, error=error)


@app.route("/superadmin/akun/<int:user_id>/ubah", methods=["GET", "POST"])
@superadmin_required
def superadmin_akun_ubah(user_id):
    target = ambil_user_target(user_id, boleh=("user", "admin"))
    error = None
    if request.method == "POST":
        nama = request.form.get("nama", "").strip()
        email = request.form.get("email", "").strip()
        peran = request.form.get("peran", target["role"])
        password = request.form.get("password", "")
        error = cek_data_akun(nama, email, password)
        if not error and peran not in ("user", "admin"):
            error = "Peran tidak valid."
        if not error and db_one("SELECT id FROM users WHERE email=%s AND id<>%s", (email, user_id)):
            error = "Email sudah dipakai akun lain."
        if not error:
            db_exec("UPDATE users SET nama=%s, email=%s, role=%s WHERE id=%s", (nama, email, peran, user_id))
            if password:
                db_exec("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(password), user_id))
            flash(f"Akun {target['username']} diperbarui.", "success")
            return redirect(url_for("superadmin_akun"))
        target = dict(target, nama=nama, email=email, role=peran if peran in ("user", "admin") else target["role"])
    return render_template("superadmin/akun_ubah.html", t=target, error=error)


@app.route("/superadmin/akun/<int:user_id>/status", methods=["POST"])
@superadmin_required
def superadmin_akun_status(user_id):
    target = ambil_user_target(user_id, boleh=("user", "admin"))
    baru = 0 if target["aktif"] else 1
    db_exec("UPDATE users SET aktif=%s WHERE id=%s", (baru, user_id))
    flash(f"Akun {target['username']} {'diaktifkan' if baru else 'dinonaktifkan'}.", "success")
    return redirect(request.form.get("kembali") if aman_internal(request.form.get("kembali", ""))
                    else url_for("superadmin_akun"))


@app.route("/superadmin/akun/<int:user_id>/hapus", methods=["POST"])
@superadmin_required
def superadmin_akun_hapus(user_id):
    target = ambil_user_target(user_id, boleh=("user", "admin"))
    hapus_akun_beserta_data(target)
    flash(f"Akun {target['username']} beserta datanya dihapus.", "success")
    return redirect(url_for("superadmin_akun"))


@app.route("/superadmin/hak-akses")
@superadmin_required
def superadmin_hak_akses():
    pengelola = db_all("""SELECT id,nama,username,role,aktif FROM users WHERE role IN ('superadmin','admin')
                          ORDER BY FIELD(role,'superadmin','admin'), id""")
    return render_template("superadmin/hak_akses.html", pengelola=pengelola)


@app.route("/superadmin/statistik")
@superadmin_required
def superadmin_statistik():
    bulan = enam_bulan_terakhir()
    awal = date(bulan[0][0], bulan[0][1], 1)
    akun_bln = {r["b"]: r["n"] for r in db_all(
        "SELECT DATE_FORMAT(bergabung,'%Y-%m') AS b, COUNT(*) AS n FROM users WHERE bergabung >= %s GROUP BY b", (awal,))}
    lap_bln = {r["b"]: r["n"] for r in db_all(
        "SELECT DATE_FORMAT(dibuat,'%Y-%m') AS b, COUNT(*) AS n FROM laporan WHERE dibuat >= %s GROUP BY b", (awal,))}
    seri = [{"label": f"{BULAN_SINGKAT[m - 1]} {t}", "akun": akun_bln.get(f"{t}-{m:02d}", 0),
             "laporan": lap_bln.get(f"{t}-{m:02d}", 0)} for t, m in bulan]
    per_jenis = {k: 0 for k in JENIS_LAPORAN}
    for r in db_all("SELECT jenis, COUNT(*) AS n FROM laporan GROUP BY jenis"):
        if r["jenis"] in per_jenis:
            per_jenis[r["jenis"]] = r["n"]
    per_status = {s: 0 for s in STATUS_LAPORAN}
    for r in db_all("SELECT status, COUNT(*) AS n FROM laporan GROUP BY status"):
        if r["status"] in per_status:
            per_status[r["status"]] = r["n"]
    emisi_kat = {k: 0.0 for k in KATEGORI}
    for r in db_all("SELECT jenis, SUM(karbon) AS total FROM riwayat GROUP BY jenis"):
        if r["jenis"] in emisi_kat:
            emisi_kat[r["jenis"]] = round(float(r["total"] or 0), 2)
    penyumbang = db_all("""SELECT u.nama,u.username,SUM(r.karbon) AS total
                           FROM users u JOIN riwayat r ON r.username=u.username
                           GROUP BY u.id,u.nama,u.username ORDER BY total DESC LIMIT 5""")
    return render_template(
        "superadmin/statistik.html", seri=seri, per_jenis=per_jenis, per_status=per_status,
        emisi_kat=emisi_kat, total_emisi=round(sum(emisi_kat.values()), 2),
        penyumbang=penyumbang, kategori_info=KATEGORI,
    )


@app.route("/superadmin/pengaturan", methods=["GET", "POST"])
@superadmin_required
def superadmin_pengaturan():
    user = get_current_user()
    if request.method == "POST":
        aksi = request.form.get("aksi", "")
        if aksi == "profil":
            nama = request.form.get("nama", "").strip()
            if not nama or len(nama) > 100:
                flash("Nama wajib diisi (maksimal 100 karakter).", "error")
            else:
                db_exec("UPDATE users SET nama=%s WHERE id=%s", (nama, user["id"]))
                flash("Nama diperbarui.", "success")
        elif aksi == "password":
            lama = request.form.get("lama", "")
            baru = request.form.get("baru", "")
            ulang = request.form.get("ulang", "")
            tersimpan = db_one("SELECT password FROM users WHERE id=%s", (user["id"],))["password"]
            try:
                cocok = check_password_hash(tersimpan, lama)
            except (ValueError, TypeError):
                cocok = False
            if not cocok:
                flash("Password lama salah.", "error")
            elif len(baru) < 8:
                flash("Password baru minimal 8 karakter.", "error")
            elif baru != ulang:
                flash("Ulangi password tidak sama dengan password baru.", "error")
            else:
                db_exec("UPDATE users SET password=%s WHERE id=%s", (generate_password_hash(baru), user["id"]))
                flash("Password diganti.", "success")
        return redirect(url_for("superadmin_pengaturan"))
    try:
        jumlah_foto = sum(1 for f in Path(app.config["UPLOAD_FOLDER"]).iterdir() if f.is_file() and f.name != ".gitkeep")
    except OSError:
        jumlah_foto = 0
    info = {"Database": f"{DB_CONFIG['database']} @ {DB_CONFIG['host']}",
            "Foto laporan tersimpan": jumlah_foto,
            "Mode debug": "aktif" if app.debug else "mati"}
    return render_template("superadmin/pengaturan.html", info=info)


# ──────────────────────────────────────────────────────────
#  Error handler
# ──────────────────────────────────────────────────────────
@app.errorhandler(413)
def file_too_large(_error):
    flash("Ukuran foto terlalu besar. Maksimal 5 MB.", "error")
    return redirect(url_for("laporan"))


@app.errorhandler(404)
def tidak_ditemukan(_error):
    return render_template("404.html"), 404


if os.environ.get("ZEROECO_SKIP_INIT") != "1":
    init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
