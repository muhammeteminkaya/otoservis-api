from fastapi import HTTPException
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import func
from pydantic import BaseModel
from typing import List, Optional

# --- 1. VERİ TABANI AYARLARI ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./otoservis_v1.1.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- 2. VERİ TABANI MODELLERİ ---
class KullaniciDB(Base):
    __tablename__ = "kullanicilar"
    id = Column(Integer, primary_key=True, index=True)
    kullanici_adi = Column(String, unique=True, index=True)
    sifre = Column(String)
    rol = Column(String) # 'usta', 'cirak', 'depo', 'muhasebe'

class StokDB(Base):
    __tablename__ = "stoklar"
    id = Column(Integer, primary_key=True, index=True)
    parca_adi = Column(String, index=True)
    miktar = Column(Integer, default=0)

class IsEmriDB(Base):
    __tablename__ = "is_emirleri"
    id = Column(Integer, primary_key=True, index=True)
    plaka = Column(String, index=True)
    marka_model = Column(String)
    yil = Column(Integer)
    telefon = Column(String, index=True, nullable=True)
    usta_notu = Column(String, nullable=True)
    durum = Column(String, default="İşlemde")
    toplam_tutar = Column(Float, default=0.0) # Muhasebe için eklendi
    kayit_tarihi = Column(DateTime(timezone=True), server_default=func.now())

class CirakGorevDB(Base):
    __tablename__ = "cirak_gorevleri"
    id = Column(Integer, primary_key=True, index=True)
    is_emri_id = Column(Integer, ForeignKey("is_emirleri.id"))
    plaka = Column(String)
    alinacak_parca = Column(String)
    durum = Column(String, default="Bekliyor")

Base.metadata.create_all(bind=engine)
# --- TABLOLAR OLUŞTURULDUKTAN HEMEN SONRA BU KODU EKLE ---
def veritabanini_hazirla():
    db = SessionLocal()
    if not db.query(KullaniciDB).first():
        db.add_all([
            KullaniciDB(kullanici_adi="usta", sifre="123", rol="usta"),
            KullaniciDB(kullanici_adi="cirak", sifre="123", rol="cirak"),
            KullaniciDB(kullanici_adi="depo", sifre="123", rol="depo"),
            KullaniciDB(kullanici_adi="muhasebe", sifre="123", rol="muhasebe"),
            KullaniciDB(kullanici_adi="patron", sifre="123", rol="patron")
        ])
        db.add_all([
            StokDB(parca_adi="5W-30 Motor Yağı (Litre)", miktar=50),
            StokDB(parca_adi="Yağ Filtresi", miktar=20),
            StokDB(parca_adi="Hava Filtresi", miktar=20)
        ])
        db.commit()
    db.close()

veritabanini_hazirla() # Sunucu başlarken 1 kez otomatik çalışır
# ---------------------------------------------------------

# --- 3. PYDANTIC ŞEMALARI ---
class LoginGiris(BaseModel):
    kullanici_adi: str
    sifre: str

class CirakGorevEkle(BaseModel):
    alinacak_parca: str

class IsEmriEkle(BaseModel):
    plaka: str
    marka_model: str
    yil: int
    telefon: str = None
    usta_notu: Optional[str] = None
    alinacak_parcalar: List[CirakGorevEkle] = []

class StokGuncelle(BaseModel):
    miktar: int

class MuhasebeGuncelle(BaseModel):
    durum: str
    toplam_tutar: float

# --- 4. FASTAPI KURULUMU ---
app = FastAPI(title="Gelişmiş Oto Servis API")
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

# --- 5. ENDPOINTLER ---

@app.get("/kurulum")
def veritabanini_hazirla(db: Session = Depends(get_db)):
    if not db.query(KullaniciDB).first():
        db.add_all([
            KullaniciDB(kullanici_adi="usta", sifre="123", rol="usta"),
            KullaniciDB(kullanici_adi="cirak", sifre="123", rol="cirak"),
            KullaniciDB(kullanici_adi="depo", sifre="123", rol="depo"),
            KullaniciDB(kullanici_adi="muhasebe", sifre="123", rol="muhasebe") # Muhasebe kullanıcısı
        ])
        db.add_all([
            StokDB(parca_adi="5W-30 Motor Yağı (Litre)", miktar=50),
            StokDB(parca_adi="Yağ Filtresi", miktar=20),
            StokDB(parca_adi="Hava Filtresi", miktar=20)
        ])
        db.commit()
        return {"mesaj": "Sistem başarıyla kuruldu."}
    return {"mesaj": "Sistem zaten kurulu."}

@app.post("/login")
def giris_yap(bilgiler: LoginGiris, db: Session = Depends(get_db)):
    kullanici = db.query(KullaniciDB).filter(KullaniciDB.kullanici_adi == bilgiler.kullanici_adi, KullaniciDB.sifre == bilgiler.sifre).first()
    if not kullanici: raise HTTPException(status_code=401, detail="Hatalı giriş")
    return {"mesaj": "Giriş Başarılı", "rol": kullanici.rol, "kullanici_adi": kullanici.kullanici_adi}

# Müşteri Paneli İçin Sorgulama (Not Found Hatasını Çözen Kısım)
@app.get("/arac-durumu/{plaka}")
def arac_durumu_sorgula(plaka: str, db: Session = Depends(get_db)):
    kayit = db.query(IsEmriDB).filter(IsEmriDB.plaka == plaka).order_by(IsEmriDB.id.desc()).first()
    if not kayit: raise HTTPException(status_code=404, detail="Bu plakaya ait kayıt bulunamadı.")
    return {
        "plaka": kayit.plaka,
        "islem": kayit.usta_notu if kayit.usta_notu else "İşlem detayları girilmedi.",
        "durum": kayit.durum,
        "tutar": kayit.toplam_tutar
    }

# Usta ve Çırak İşlemleri (Öncekiyle Aynı)
@app.post("/usta/is-emri-olustur")
def usta_is_emri_olustur(veri: IsEmriEkle, db: Session = Depends(get_db)):
    #TELEFON EŞLEŞTİRMESİ BURAYA EKLENDİ
    yeni_is_emri = IsEmriDB(
        plaka=veri.plaka,
        marka_model=veri.marka_model,
        yil=veri.yil,
        telefon=veri.telefon,
        usta_notu=veri.usta_notu
    )
    db.add(yeni_is_emri)
    db.commit()
    db.refresh(yeni_is_emri)
    for parca in veri.alinacak_parcalar:
        db.add(CirakGorevDB(is_emri_id=yeni_is_emri.id, plaka=veri.plaka, alinacak_parca=parca.alinacak_parca))
    db.commit()
    return {"mesaj": "Oluşturuldu."}

@app.get("/usta/arac-gecmisi/{plaka}")
def arac_gecmisi_getir(plaka: str, db: Session = Depends(get_db)):
    # Hem teslime hazır olanları hem de arşive kalkanları listele
    gecmis = db.query(IsEmriDB).filter(
        IsEmriDB.plaka == plaka,
        IsEmriDB.durum.in_(["Tamamlandı - Teslime Hazır", "Arşivlendi"])
    ).order_by(IsEmriDB.id.desc()).limit(5).all()
    return gecmis

@app.get("/cirak/gorevler")
def cirak_gorev_listesi(db: Session = Depends(get_db)):
    return db.query(CirakGorevDB).filter(CirakGorevDB.durum == "Bekliyor").all()

@app.put("/cirak/gorev-tamamla/{gorev_id}")
def cirak_gorev_tamamla(gorev_id: int, db: Session = Depends(get_db)):
    gorev = db.query(CirakGorevDB).filter(CirakGorevDB.id == gorev_id).first()
    gorev.durum = "Alındı"
    db.commit()
    return {"mesaj": "Tamamlandı"}

@app.get("/depo/stoklar")
def stok_listesi(db: Session = Depends(get_db)):
    return db.query(StokDB).all()

@app.put("/depo/stok-guncelle/{stok_id}")
def stok_guncelle(stok_id: int, veri: StokGuncelle, db: Session = Depends(get_db)):
    stok = db.query(StokDB).filter(StokDB.id == stok_id).first()
    stok.miktar = veri.miktar
    db.commit()
    return {"mesaj": "Güncellendi"}

# YENİ EKLENEN: MUHASEBE İŞLEMLERİ
@app.get("/muhasebe/is-emirleri")
def muhasebe_is_emirleri(db: Session = Depends(get_db)):
    return db.query(IsEmriDB).filter(IsEmriDB.durum != "Arşivlendi").order_by(IsEmriDB.id.desc()).all()

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



@app.get("/patron/istatistik")
def patron_istatistik(db: Session = Depends(get_db)):
    # 1. Ciro ve Araç Sayısı Hesaplama
    tamamlananlar = db.query(IsEmriDB).filter(
        IsEmriDB.durum.in_(["Tamamlandı - Teslime Hazır", "Arşivlendi"])
    ).all()
    toplam_ciro = sum(islem.toplam_tutar for islem in tamamlananlar if islem.toplam_tutar)
    
    # 2. Stok Durumu ve KRİTİK STOK Alarmı
    stoklar = db.query(StokDB).all()
    stok_adlari = [stok.parca_adi for stok in stoklar]
    stok_miktarlari = [stok.miktar for stok in stoklar]
    
    # Miktarı 5 ve altında olan parçaları tespit et
    kritik_stok_listesi = [stok.parca_adi for stok in stoklar if stok.miktar <= 5]

    # 3. Personel Performansı / Ciro Dağılımı
    # (Veritabanında usta_adi ayrı bir sütun olana kadar grafiğin boş kalmaması için ciroyu temsili 2 ustaya bölüyoruz)
    personel_adlari = ["Ahmet Usta (Motor)", "Mehmet Usta (Elektrik)"]
    personel_cirolari = [toplam_ciro * 0.6, toplam_ciro * 0.4] 

    return {
        "toplam_ciro": toplam_ciro,
        "arac_sayisi": len(tamamlananlar),
        "stok_adlari": stok_adlari,
        "stok_miktarlari": stok_miktarlari,
        "kritik_stok_listesi": kritik_stok_listesi,
        "personel_adlari": personel_adlari,
        "personel_cirolari": personel_cirolari
    }

@app.get("/patron/arama")
def patron_arama(plaka: str, db: Session = Depends(get_db)):
    # Sadece o plakaya ait ve muhasebenin "Arşive" kaldırdığı (Soft-Delete) kayıtları getir
    kayitlar = db.query(IsEmriDB).filter(
        IsEmriDB.plaka == plaka.upper().replace(" ", ""), # Boşlukları silip büyük harfe çevirir (örn: 34 abc 12 -> 34ABC12)
        IsEmriDB.durum == "Arşivlendi"
    ).all()
    
    if not kayitlar:
        # Kayıt yoksa frontend'e 404 hatası fırlat ki "Kayıt bulunamadı" yazısı çıksın
        raise HTTPException(status_code=404, detail="Bu plakaya ait geçmiş sicil bulunamadı.")
        
    sonuclar = []
    for kayit in kayitlar:
        sonuclar.append({
            "plaka": kayit.plaka,
            "marka_model": kayit.marka_model,
            "usta_notu": kayit.usta_notu,
            "toplam_tutar": kayit.toplam_tutar
        })
        
    return sonuclar
