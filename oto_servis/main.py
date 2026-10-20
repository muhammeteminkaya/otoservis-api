import json
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import List, Optional

# --- 1. VERİ TABANI AYARLARI ---
# Eski veritabanı çakışmalarını önlemek için ismini v2 yaptık. Sıfırdan tertemiz kurulacak.
SQLALCHEMY_DATABASE_URL = "sqlite:///./otoservis_v2.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 2. VERİ TABANI MODELLERİ ---
class KullaniciDB(Base):
    __tablename__ = "kullanicilar"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    kullanici_adi = Column(String(50), unique=True, index=True)
    sifre = Column(String(50))
    rol = Column(String(20)) 

class StokDB(Base):
    __tablename__ = "stoklar"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    parca_adi = Column(String(100), index=True)
    miktar = Column(Integer, default=0)

class IsEmriDB(Base):
    __tablename__ = "is_emirleri"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    plaka = Column(String(20), index=True, nullable=False)
    marka_model = Column(String(100), nullable=False)
    yil = Column(Integer, nullable=True)
    telefon = Column(String(20), index=True, nullable=True)
    usta_notu = Column(Text, nullable=True)
    durum = Column(String(50), default="İşlemde")
    toplam_tutar = Column(Float, default=0.0) 
    kayit_tarihi = Column(DateTime(timezone=True), server_default=func.now())
    usta_id = Column(Integer, ForeignKey("kullanicilar.id"), default=1)
    alinacak_parcalar = Column(Text, default="[]") # Çırak parçaları JSON olarak burada tutulacak

Base.metadata.create_all(bind=engine)

# --- BAŞLANGIÇ VERİLERİ (SEED) ---
def veritabanini_hazirla():
    db = SessionLocal()
    if not db.query(KullaniciDB).first():
        db.add_all([
            KullaniciDB(kullanici_adi="usta", sifre="123", rol="usta"),
            KullaniciDB(kullanici_adi="cirak", sifre="123", rol="cirak"),
            KullaniciDB(kullanici_adi="depo", sifre="123", rol="depo"),
            KullaniciDB(kullanici_adi="muhasebe", sifre="123", rol="muhasebe") # Patron rolü temizlendi
        ])
        db.add_all([
            StokDB(parca_adi="5W-30 Motor Yağı (Litre)", miktar=50),
            StokDB(parca_adi="Yağ Filtresi", miktar=20),
            StokDB(parca_adi="Hava Filtresi", miktar=20)
        ])
        db.commit()
    db.close()

veritabanini_hazirla()

# --- 3. PYDANTIC ŞEMALARI ---
class LoginGiris(BaseModel):
    kullanici_adi: str
    sifre: str

class StokGuncelle(BaseModel):
    miktar: int

class MuhasebeGuncelle(BaseModel):
    durum: str
    toplam_tutar: float

# --- 4. FASTAPI KURULUMU ---
app = FastAPI(title="Kaya Oto Servis API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

# --- 5. ENDPOINTLER (API ROTALARI) ---

@app.post("/login")
def giris_yap(bilgiler: LoginGiris, db: Session = Depends(get_db)):
    kullanici = db.query(KullaniciDB).filter(KullaniciDB.kullanici_adi == bilgiler.kullanici_adi, KullaniciDB.sifre == bilgiler.sifre).first()
    if not kullanici: 
        raise HTTPException(status_code=401, detail="Hatalı giriş")
    return {"mesaj": "Giriş Başarılı", "rol": kullanici.rol, "kullanici_adi": kullanici.kullanici_adi}

@app.get("/arac-durumu/{plaka}")
def arac_durumu_sorgula(plaka: str, db: Session = Depends(get_db)):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.plaka == plaka.upper().replace(" ", "")).order_by(IsEmriDB.id.desc()).first()
    if not kayit: 
        raise HTTPException(status_code=404, detail="Bu plakaya ait kayıt bulunamadı.")
    return {
        "plaka": kayit.plaka,
        "islem": kayit.usta_notu if kayit.usta_notu else "İşlem detayları girilmedi.",
        "durum": kayit.durum,
        "tutar": kayit.toplam_tutar
    }

# --- USTA ROTALARI ---
@app.post("/usta/is-emri-olustur")
async def usta_is_emri_olustur(request: Request, db: Session = Depends(get_db)):
    veri = await request.json()
    yeni_is = IsEmriDB(
        plaka=veri.get("plaka", "").upper().replace(" ", ""),
        marka_model=veri.get("marka_model", ""),
        yil=veri.get("yil"),
        telefon=veri.get("telefon", ""),
        usta_notu=veri.get("usta_notu", ""),
        durum="İşlemde",
        usta_id=1,
        alinacak_parcalar=json.dumps(veri.get("alinacak_parcalar", []))
    )
    db.add(yeni_is)
    db.commit()
    return {"mesaj": "Oluşturuldu."}

@app.get("/usta/arac-gecmisi/{plaka}")
def arac_gecmisi_getir(plaka: str, db: Session = Depends(get_db)):
    gecmis = db.query(IsEmriDB).filter(
        IsEmriDB.plaka == plaka.upper().replace(" ", ""),
        IsEmriDB.durum == "Arşivlendi"
    ).order_by(IsEmriDB.id.desc()).limit(5).all()
    
    return [{"kayit_tarihi": k.kayit_tarihi, "usta_notu": k.usta_notu, "toplam_tutar": k.toplam_tutar} for k in gecmis]

# --- ÇIRAK ROTALARI ---
@app.get("/cirak/is-emirleri")
def cirak_is_emirleri(db: Session = Depends(get_db)):
    islemde_olanlar = db.query(IsEmriDB).filter(IsEmriDB.durum != "Arşivlendi").all()
    sonuc = []
    for islem in islemde_olanlar:
        parcalar = []
        if islem.alinacak_parcalar:
            try: parcalar = json.loads(islem.alinacak_parcalar)
            except: pass
        sonuc.append({
            "id": islem.id,
            "plaka": islem.plaka,
            "marka_model": islem.marka_model,
            "durum": islem.durum,
            "alinacak_parcalar": parcalar
        })
    return sonuc

@app.put("/cirak/parca-guncelle")
async def cirak_parca_guncelle(request: Request, db: Session = Depends(get_db)):
    veri = await request.json()
    islem = db.query(IsEmriDB).filter(IsEmriDB.id == veri["is_emri_id"]).first()
    if islem and islem.alinacak_parcalar:
        parcalar = json.loads(islem.alinacak_parcalar)
        parcalar[veri["parca_index"]]["alindi_mi"] = veri["durum"]
        islem.alinacak_parcalar = json.dumps(parcalar)
        db.commit()
    return {"status": "ok"}

# --- DEPO ROTALARI ---
@app.get("/depo/stoklar")
def stok_listesi(db: Session = Depends(get_db)):
    return db.query(StokDB).all()

@app.put("/depo/stok-guncelle/{stok_id}")
def stok_guncelle(stok_id: int, veri: StokGuncelle, db: Session = Depends(get_db)):
    stok = db.query(StokDB).filter(StokDB.id == stok_id).first()
    stok.miktar = veri.miktar
    db.commit()
    return {"mesaj": "Güncellendi"}

# --- MUHASEBE VE YÖNETİM ROTALARI ---
@app.get("/muhasebe/is-emirleri")
def muhasebe_is_emirleri(db: Session = Depends(get_db)):
    islemler = db.query(IsEmriDB).filter(IsEmriDB.durum != "Arşivlendi").order_by(IsEmriDB.id.desc()).all()
    sonuc = []
    for islem in islemler:
        parcalar = []
        if islem.alinacak_parcalar:
            try: parcalar = json.loads(islem.alinacak_parcalar)
            except: pass
        sonuc.append({
            "id": islem.id,
            "plaka": islem.plaka,
            "marka_model": islem.marka_model,
            "usta_notu": islem.usta_notu,
            "durum": islem.durum,
            "toplam_tutar": islem.toplam_tutar,
            "telefon": islem.telefon,
            "alinacak_parcalar": parcalar
        })
    return sonuc

@app.put("/muhasebe/is-emri-guncelle/{is_emri_id}")
def muhasebe_guncelle(is_emri_id: int, veri: MuhasebeGuncelle, db: Session = Depends(get_db)):
    is_emri = db.query(IsEmriDB).filter(IsEmriDB.id == is_emri_id).first()
    is_emri.durum = veri.durum
    is_emri.toplam_tutar = veri.toplam_tutar
    db.commit()
    return {"mesaj": "Muhasebe güncellendi"}

@app.delete("/muhasebe/is-emri-sil/{id}")
def is_emri_sil(id: int, db: Session = Depends(get_db)):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.id == id).first()
    if kayit:
        db.delete(kayit)
        db.commit()
        return {"mesaj": "Kayıt başarıyla silindi"}
    raise HTTPException(status_code=404, detail="Kayıt bulunamadı")

@app.put("/muhasebe/is-emri-arsivle/{id}")
def is_emri_arsivle(id: int, db: Session = Depends(get_db)):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.id == id).first()
    if kayit:
        kayit.durum = "Arşivlendi"
        db.commit()
        return {"mesaj": "Kayıt arşive kaldırıldı"}
    raise HTTPException(status_code=404, detail="Kayıt bulunamadı")

@app.get("/muhasebe/istatistik")
def muhasebe_istatistik(db: Session = Depends(get_db)):
    tamamlananlar = db.query(IsEmriDB).filter(
        IsEmriDB.durum.in_(["Tamamlandı - Teslime Hazır", "Arşivlendi"])
    ).all()
    toplam_ciro = sum(islem.toplam_tutar for islem in tamamlananlar if islem.toplam_tutar)
    
    return {
        "toplam_ciro": toplam_ciro,
        "arac_sayisi": len(tamamlananlar)
    }

@app.get("/muhasebe/arama")
def muhasebe_arama(plaka: str, db: Session = Depends(get_db)):
    kayitlar = db.query(IsEmriDB).filter(
        IsEmriDB.plaka == plaka.upper().replace(" ", ""),
        IsEmriDB.durum == "Arşivlendi"
    ).all()
    
    if not kayitlar:
        raise HTTPException(status_code=404, detail="Bu plakaya ait geçmiş sicil bulunamadı.")
        
    return [{"plaka": k.plaka, "marka_model": k.marka_model, "usta_notu": k.usta_notu, "toplam_tutar": k.toplam_tutar} for k in kayitlar]