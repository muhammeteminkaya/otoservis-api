import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.sql import func

# --- LOGLAMA YAPILANDIRMASI ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("otoservis")

# --- 1. VERİ TABANI AYARLARI (SQLite / PostgreSQL Uyumlu) ---
raw_db_url = os.getenv("DATABASE_URL", "sqlite:///./otoservis_v2.db")
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
SQLALCHEMY_DATABASE_URL = raw_db_url

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 2. VERİ TABANI MODELLERİ ---
class KullaniciDB(Base):
    __tablename__ = "kullanicilar"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    kullanici_adi = Column(String(50), unique=True, index=True, nullable=False)
    sifre = Column(String(255), nullable=False)
    rol = Column(String(20), nullable=False)


class StokDB(Base):
    __tablename__ = "stoklar"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    parca_adi = Column(String(100), index=True, nullable=False)
    miktar = Column(Integer, default=0, nullable=False)


class IsEmriDB(Base):
    __tablename__ = "is_emirleri"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    plaka = Column(String(20), index=True, nullable=False)
    marka_model = Column(String(100), nullable=False)
    yil = Column(Integer, nullable=True)
    telefon = Column(String(20), index=True, nullable=True)
    usta_notu = Column(Text, nullable=True)
    durum = Column(String(50), default="İşlemde", nullable=False)
    toplam_tutar = Column(Float, default=0.0, nullable=False)
    kayit_tarihi = Column(DateTime(timezone=True), server_default=func.now())
    usta_id = Column(Integer, ForeignKey("kullanicilar.id"), default=1)
    alinacak_parcalar = Column(Text, default="[]")


Base.metadata.create_all(bind=engine)

# --- 3. ŞİFRELEME (PBKDF2-SHA256) VE JWT (HS256) ---
PBKDF2_ITERATIONS = 100_000
JWT_SECRET = os.getenv("JWT_SECRET", "kaya-otoservis-jwt-secret-2026-prod")
JWT_EXPIRE_SECONDS = int(os.getenv("JWT_EXPIRE_SECONDS", "43200"))  # 12 saat
security = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${salt}${dk}"


def verify_password(plain: str, stored: str) -> bool:
    parts = (stored or "").split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected_hex = parts
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        plain.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return hmac.compare_digest(dk, expected_hex)


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + padding)


def create_access_token(user_id: int, kullanici_adi: str, rol: str) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "uid": user_id,
        "sub": kullanici_adi,
        "rol": rol,
        "iat": now,
        "exp": now + JWT_EXPIRE_SECONDS,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Geçersiz token biçimi")
    header_b64, payload_b64, sig_b64 = parts
    header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    if header.get("alg") != "HS256":
        raise ValueError("Desteklenmeyen algoritma")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual_sig = _b64url_decode(sig_b64)
    if not hmac.compare_digest(actual_sig, expected_sig):
        raise ValueError("Token imzası geçersiz")
    payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    if int(payload.get("exp", 0)) <= int(time.time()):
        raise ValueError("Token süresi dolmuş")
    return payload


def require_roles(*allowed_roles: str):
    def role_checker(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> dict:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Kimlik doğrulama gerekli.")
        try:
            payload = decode_access_token(credentials.credentials)
        except Exception:
            raise HTTPException(status_code=401, detail="Geçersiz veya süresi dolmuş oturum.")
        if payload.get("rol") not in allowed_roles:
            logger.warning(
                "Yetkisiz rol erişim denemesi: kullanici=%s rol=%s beklenen=%s",
                payload.get("sub"),
                payload.get("rol"),
                allowed_roles,
            )
            raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz bulunmuyor.")
        return payload

    return role_checker


# --- 4. BAŞLANGIÇ VERİLERİ (SEED & MİGRASYON) ---
def veritabanini_hazirla() -> None:
    db = SessionLocal()
    try:
        kullanicilar = db.query(KullaniciDB).all()
        if not kullanicilar:
            db.add_all(
                [
                    KullaniciDB(kullanici_adi="usta", sifre=hash_password("123"), rol="usta"),
                    KullaniciDB(kullanici_adi="cirak", sifre=hash_password("123"), rol="cirak"),
                    KullaniciDB(kullanici_adi="depo", sifre=hash_password("123"), rol="depo"),
                    KullaniciDB(kullanici_adi="muhasebe", sifre=hash_password("123"), rol="muhasebe"),
                ]
            )
            db.add_all(
                [
                    StokDB(parca_adi="5W-30 Motor Yağı (Litre)", miktar=50),
                    StokDB(parca_adi="Yağ Filtresi", miktar=20),
                    StokDB(parca_adi="Hava Filtresi", miktar=20),
                ]
            )
            db.commit()
            logger.info("Başlangıç kullanıcıları (hashlenmiş parolalar) ve stok verileri oluşturuldu.")
        else:
            migrated = 0
            for k in kullanicilar:
                if not (k.sifre or "").startswith("pbkdf2_sha256$"):
                    k.sifre = hash_password(k.sifre)
                    migrated += 1
            if migrated:
                db.commit()
                logger.info("%d mevcut kullanıcı parolası PBKDF2-SHA256 formatına yükseltildi.", migrated)
    finally:
        db.close()


veritabanini_hazirla()

# --- 5. PYDANTIC v2 ŞEMALARI ---
ALLOWED_STATUSES = {
    "İşlemde",
    "Parça Bekliyor",
    "Tamamlandı - Teslime Hazır",
    "Arşivlendi",
}


class LoginGiris(BaseModel):
    kullanici_adi: str = Field(..., min_length=1, max_length=50)
    sifre: str = Field(..., min_length=1, max_length=128)


class ParcaItem(BaseModel):
    alinacak_parca: str = Field(..., min_length=1, max_length=150)
    alindi_mi: bool = False


class IsEmriOlustur(BaseModel):
    plaka: str = Field(..., min_length=3, max_length=20)
    marka_model: str = Field(..., min_length=2, max_length=100)
    yil: Optional[int] = Field(None, ge=1950, le=2035)
    telefon: Optional[str] = Field("", max_length=20)
    usta_notu: Optional[str] = Field("", max_length=2000)
    alinacak_parcalar: List[ParcaItem] = Field(default_factory=list, max_length=50)


class CirakParcaGuncelle(BaseModel):
    is_emri_id: int = Field(..., ge=1)
    parca_index: int = Field(..., ge=0)
    durum: bool


class StokGuncelle(BaseModel):
    miktar: int = Field(..., ge=0, le=100000)


class MuhasebeGuncelle(BaseModel):
    durum: str = Field(..., min_length=2, max_length=50)
    toplam_tutar: float = Field(..., ge=0.0)

    @field_validator("durum")
    @classmethod
    def validate_durum(cls, value: str) -> str:
        if value not in ALLOWED_STATUSES:
            raise ValueError("Geçersiz iş emri durumu.")
        return value


# --- 6. FASTAPI KURULUMU, RATE LIMITING, LOGLAMA VE CORS ---
app = FastAPI(title="Kaya Oto Servis API", version="2.0")

LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "15"))
GLOBAL_RATE_LIMIT = int(os.getenv("GLOBAL_RATE_LIMIT", "120"))
_rate_buckets: dict[tuple[str, str], deque] = defaultdict(deque)
_rate_lock = threading.Lock()


@app.middleware("http")
async def rate_limit_and_logging_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    path = request.url.path

    if request.method != "OPTIONS" and path != "/health":
        bucket = "login" if path == "/login" else "global"
        limit = LOGIN_RATE_LIMIT if bucket == "login" else GLOBAL_RATE_LIMIT
        now = time.monotonic()
        with _rate_lock:
            q = _rate_buckets[(client_ip, bucket)]
            while q and q[0] <= now - 60.0:
                q.popleft()
            if len(q) >= limit:
                logger.warning("Rate limit aşıldı: ip=%s path=%s bucket=%s", client_ip, path, bucket)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Çok fazla istek gönderildi. Lütfen kısa süre sonra tekrar deneyin."},
                    headers={"Retry-After": "60"},
                )
            q.append(now)

    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000.0
    logger.info("%s %s -> %d (%.1f ms) ip=%s", request.method, path, response.status_code, duration_ms, client_ip)
    return response


allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "*").strip()
allowed_origins = (
    ["*"]
    if allowed_origins_env == "*"
    else [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=(allowed_origins != ["*"]),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Beklenmeyen sunucu hatası: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Sunucu içi beklenmeyen bir hata oluştu."},
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_parcalar(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


# --- 7. ENDPOINTLER (15 API ROTASI) ---

@app.get("/health")
def saglik_kontrolu():
    return {"status": "ok", "service": "Kaya Oto Servis API", "version": "2.0"}


@app.post("/login")
def giris_yap(bilgiler: LoginGiris, db: Session = Depends(get_db)):
    kullanici_adi = bilgiler.kullanici_adi.strip()
    kullanici = db.query(KullaniciDB).filter(KullaniciDB.kullanici_adi == kullanici_adi).first()
    if not kullanici or not verify_password(bilgiler.sifre, kullanici.sifre):
        logger.warning("Başarısız giriş denemesi: kullanici_adi=%s", kullanici_adi)
        raise HTTPException(status_code=401, detail="Hatalı kullanıcı adı veya şifre.")
    token = create_access_token(kullanici.id, kullanici.kullanici_adi, kullanici.rol)
    logger.info("Başarılı giriş: kullanici=%s rol=%s", kullanici.kullanici_adi, kullanici.rol)
    return {
        "mesaj": "Giriş Başarılı",
        "rol": kullanici.rol,
        "kullanici_adi": kullanici.kullanici_adi,
        "access_token": token,
        "token_type": "bearer",
    }


@app.get("/arac-durumu/{plaka}")
def arac_durumu_sorgula(plaka: str, db: Session = Depends(get_db)):
    temiz_plaka = plaka.upper().replace(" ", "")
    kayit = (
        db.query(IsEmriDB)
        .filter(IsEmriDB.plaka == temiz_plaka)
        .order_by(IsEmriDB.id.desc())
        .first()
    )
    if not kayit:
        raise HTTPException(status_code=404, detail="Bu plakaya ait kayıt bulunamadı.")
    return {
        "plaka": kayit.plaka,
        "islem": kayit.usta_notu if kayit.usta_notu else "İşlem detayları girilmedi.",
        "durum": kayit.durum,
        "tutar": kayit.toplam_tutar,
    }


# --- USTA ROTALARI ---
@app.post("/usta/is-emri-olustur")
def usta_is_emri_olustur(
    veri: IsEmriOlustur,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("usta")),
):
    plaka_norm = veri.plaka.upper().replace(" ", "")
    if len(plaka_norm) < 3:
        raise HTTPException(status_code=422, detail="Geçerli bir plaka giriniz.")

    parcalar_json = json.dumps(
        [
            {"alinacak_parca": p.alinacak_parca.strip(), "alindi_mi": bool(p.alindi_mi)}
            for p in veri.alinacak_parcalar
            if p.alinacak_parca.strip()
        ],
        ensure_ascii=False,
    )
    yeni_is = IsEmriDB(
        plaka=plaka_norm,
        marka_model=veri.marka_model.strip(),
        yil=veri.yil,
        telefon=(veri.telefon or "").strip(),
        usta_notu=(veri.usta_notu or "").strip(),
        durum="İşlemde",
        usta_id=current_user.get("uid", 1),
        alinacak_parcalar=parcalar_json,
    )
    db.add(yeni_is)
    db.commit()
    db.refresh(yeni_is)
    logger.info("Yeni iş emri oluşturuldu: id=%d plaka=%s usta=%s", yeni_is.id, yeni_is.plaka, current_user.get("sub"))
    return {"mesaj": "Oluşturuldu.", "id": yeni_is.id}


@app.get("/usta/arac-gecmisi/{plaka}")
def arac_gecmisi_getir(
    plaka: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("usta")),
):
    temiz_plaka = plaka.upper().replace(" ", "")
    gecmis = (
        db.query(IsEmriDB)
        .filter(
            IsEmriDB.plaka == temiz_plaka,
            IsEmriDB.durum == "Arşivlendi",
        )
        .order_by(IsEmriDB.id.desc())
        .limit(5)
        .all()
    )
    return [
        {
            "kayit_tarihi": k.kayit_tarihi,
            "usta_notu": k.usta_notu,
            "toplam_tutar": k.toplam_tutar,
        }
        for k in gecmis
    ]


# --- ÇIRAK ROTALARI ---
@app.get("/cirak/is-emirleri")
def cirak_is_emirleri(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("cirak")),
):
    islemde_olanlar = db.query(IsEmriDB).filter(IsEmriDB.durum != "Arşivlendi").order_by(IsEmriDB.id.desc()).all()
    return [
        {
            "id": islem.id,
            "plaka": islem.plaka,
            "marka_model": islem.marka_model,
            "durum": islem.durum,
            "alinacak_parcalar": _parse_parcalar(islem.alinacak_parcalar),
        }
        for islem in islemde_olanlar
    ]


@app.put("/cirak/parca-guncelle")
def cirak_parca_guncelle(
    veri: CirakParcaGuncelle,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("cirak")),
):
    islem = db.query(IsEmriDB).filter(IsEmriDB.id == veri.is_emri_id).first()
    if not islem:
        raise HTTPException(status_code=404, detail="İş emri bulunamadı.")
    parcalar = _parse_parcalar(islem.alinacak_parcalar)
    if veri.parca_index >= len(parcalar):
        raise HTTPException(status_code=400, detail="Geçersiz parça indeksi.")
    if not isinstance(parcalar[veri.parca_index], dict):
        raise HTTPException(status_code=400, detail="Parça kaydı biçimi geçersiz.")
    parcalar[veri.parca_index]["alindi_mi"] = veri.durum
    islem.alinacak_parcalar = json.dumps(parcalar, ensure_ascii=False)
    db.commit()
    logger.info("Çırak parça güncelledi: is_emri_id=%d index=%d durum=%s", islem.id, veri.parca_index, veri.durum)
    return {"status": "ok"}


# --- DEPO ROTALARI ---
@app.get("/depo/stoklar")
def stok_listesi(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("depo")),
):
    return db.query(StokDB).order_by(StokDB.id.asc()).all()


@app.put("/depo/stok-guncelle/{stok_id}")
def stok_guncelle(
    stok_id: int,
    veri: StokGuncelle,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("depo")),
):
    stok = db.query(StokDB).filter(StokDB.id == stok_id).first()
    if not stok:
        raise HTTPException(status_code=404, detail="Stok kaydı bulunamadı.")
    stok.miktar = veri.miktar
    db.commit()
    if stok.miktar <= 5:
        logger.warning("KRİTİK STOK UYARISI: parca=%s miktar=%d", stok.parca_adi, stok.miktar)
    else:
        logger.info("Stok güncellendi: id=%d parca=%s miktar=%d", stok.id, stok.parca_adi, stok.miktar)
    return {"mesaj": "Güncellendi"}


# --- MUHASEBE VE YÖNETİM ROTALARI ---
@app.get("/muhasebe/is-emirleri")
def muhasebe_is_emirleri(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    islemler = db.query(IsEmriDB).filter(IsEmriDB.durum != "Arşivlendi").order_by(IsEmriDB.id.desc()).all()
    return [
        {
            "id": islem.id,
            "plaka": islem.plaka,
            "marka_model": islem.marka_model,
            "usta_notu": islem.usta_notu,
            "durum": islem.durum,
            "toplam_tutar": islem.toplam_tutar,
            "telefon": islem.telefon,
            "alinacak_parcalar": _parse_parcalar(islem.alinacak_parcalar),
        }
        for islem in islemler
    ]


@app.put("/muhasebe/is-emri-guncelle/{is_emri_id}")
def muhasebe_guncelle(
    is_emri_id: int,
    veri: MuhasebeGuncelle,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    is_emri = db.query(IsEmriDB).filter(IsEmriDB.id == is_emri_id).first()
    if not is_emri:
        raise HTTPException(status_code=404, detail="İş emri bulunamadı.")
    is_emri.durum = veri.durum
    is_emri.toplam_tutar = veri.toplam_tutar
    db.commit()
    logger.info("Muhasebe iş emrini güncelledi: id=%d durum=%s tutar=%.2f", is_emri.id, veri.durum, veri.toplam_tutar)
    return {"mesaj": "Muhasebe güncellendi"}


@app.put("/muhasebe/is-emri-arsivle/{id}")
def is_emri_arsivle(
    id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.id == id).first()
    if not kayit:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı.")
    kayit.durum = "Arşivlendi"
    db.commit()
    logger.info("İş emri arşivlendi (Soft-Delete): id=%d plaka=%s", kayit.id, kayit.plaka)
    return {"mesaj": "Kayıt arşive kaldırıldı"}


@app.delete("/muhasebe/is-emri-sil/{id}")
def is_emri_sil(
    id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.id == id).first()
    if not kayit:
        raise HTTPException(status_code=404, detail="Kayıt bulunamadı.")
    db.delete(kayit)
    db.commit()
    logger.info("İş emri silindi: id=%d", id)
    return {"mesaj": "Kayıt başarıyla silindi"}


@app.get("/muhasebe/istatistik")
def muhasebe_istatistik(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    tamamlananlar = (
        db.query(IsEmriDB)
        .filter(IsEmriDB.durum.in_(["Tamamlandı - Teslime Hazır", "Arşivlendi"]))
        .all()
    )
    toplam_ciro = sum(islem.toplam_tutar for islem in tamamlananlar if islem.toplam_tutar)
    kritik_stoklar = db.query(StokDB).filter(StokDB.miktar <= 5).all()
    return {
        "toplam_ciro": toplam_ciro,
        "arac_sayisi": len(tamamlananlar),
        "kritik_stok_sayisi": len(kritik_stoklar),
        "kritik_stok_listesi": [
            {"parca_adi": s.parca_adi, "miktar": s.miktar} for s in kritik_stoklar
        ],
    }


@app.get("/muhasebe/arama")
def muhasebe_arama(
    plaka: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_roles("muhasebe")),
):
    temiz_plaka = plaka.upper().replace(" ", "")
    kayitlar = (
        db.query(IsEmriDB)
        .filter(
            IsEmriDB.plaka == temiz_plaka,
            IsEmriDB.durum == "Arşivlendi",
        )
        .order_by(IsEmriDB.id.desc())
        .all()
    )
    if not kayitlar:
        raise HTTPException(status_code=404, detail="Bu plakaya ait geçmiş sicil bulunamadı.")
    return [
        {
            "plaka": k.plaka,
            "marka_model": k.marka_model,
            "usta_notu": k.usta_notu,
            "toplam_tutar": k.toplam_tutar,
        }
        for k in kayitlar
    ]
