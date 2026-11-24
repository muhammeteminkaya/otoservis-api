# Bulut Tabanlı Oto Servis ve Müşteri Takip Otomasyonu

Tokat Gaziosmanpaşa Üniversitesi — Bilgisayar Teknolojileri Bölümü **Mesleki Çözümleme (BP207)** dersi kapsamında geliştirilmiş, rol tabanlı ve bulut destekli oto servis yönetim ve müşteri araç takip sistemidir.

- **Geliştirici:** Muhammet Emin Kaya (`254309024`)
- **Canlı API (Render):** `https://otoservis-api-4ta4.onrender.com`
- **GitHub Deposu:** `https://github.com/muhammeteminkaya/otoservis-api`

---

## Temel Özellikler

- **Rol Bazlı Çalışma Panelleri:**
  - **Usta Paneli (`usta.html`):** Plaka bazlı geçmiş servis sicili sorgulama, yeni iş emri oluşturma ve çırak için parça talebi ekleme.
  - **Çırak Paneli (`cirak.html`):** Aktif iş emirlerine ait alınacak parça listesini görüntüleme ve alınan parçaları anlık işaretleme.
  - **Depo Paneli (`depo.html`):** Yedek parça stok miktarlarını görüntüleme, güncelleme ve `<= 5` kritik stok uyarısı.
  - **Muhasebe & Yönetim Paneli (`muhasebe.html`):** Kasa cirosu ve tamamlanan araç istatistikleri, durum/tutar güncelleme, yazdırılabilir (PDF) servis fişi oluşturma, WhatsApp Web üzerinden müşteriye hazır bilgi mesajı yönlendirmesi, `Soft-Delete` (`Arşivlendi`) arşive kaldırma ve plaka ile geçmiş sicil arama.
  - **Müşteri Araç Takip (`takip.html`):** Müşterilerin araç plakası ile güncel onarım durumunu ve tutarı şifresiz sorgulayabilmesi.
- **PWA ve Tema Desteği:** `manifest.json`, `sw.js`, `icon-192.png` / `icon-512.png` ile mobil cihazlara kurulabilir yapı ve karanlık/aydınlık tema (`dark-mode`).

---

## Arka Plan (Backend) Mimarisi ve Güvenlik

- **Çatı & ORM:** Python, **FastAPI**, **SQLAlchemy**, **Pydantic v2**, **Uvicorn**.
- **Veritabanı Taşınabilirliği:** Varsayılan olarak `SQLite` (`otoservis_v2.db`), `DATABASE_URL` ortam değişkeni tanımlandığında ise `PostgreSQL` (`pool_pre_ping=True`) ile çalışır.
- **Parola Güvenliği:** Tüm kullanıcı parolaları 100.000 iterasyonlu **PBKDF2-HMAC-SHA256** ile tuzlanarak hashlenir; eski düz metin kayıtlar açılışta otomatik olarak yükseltilir.
- **Yetkilendirme:** Tüm personel uç noktaları sunucu taraflı **HS256 JWT (`Authorization: Bearer <token>`)** ve rol doğrulamasıyla korunur (`401 Unauthorized` / `403 Forbidden`).
- **Dayanıklılık & Hata Yönetimi:**
  - İstek gövdelerinde katı `Pydantic v2` doğrulaması ve `404` / `400` sınır kontrolleri.
  - Kayan pencereli IP bazlı **Rate Limiting** (`429 Too Many Requests` + `Retry-After`).
  - Hata yanıtlarında dahi başlıkları koruyan en dış katman **CORS (`CORSMiddleware`)**.
  - Uygulama geneli `ExceptionHandler` (HTTP `500`) ve yapısal istek/uyarı loglaması (`logging`).

---

## Veritabanı Tabloları

1. **`kullanicilar` (`KullaniciDB`):** `id`, `kullanici_adi`, `sifre` (PBKDF2-SHA256 hash), `rol`
2. **`stoklar` (`StokDB`):** `id`, `parca_adi`, `miktar`
3. **`is_emirleri` (`IsEmriDB`):** `id`, `plaka` (indeksli), `marka_model`, `yil`, `telefon` (indeksli), `usta_notu`, `durum`, `toplam_tutar`, `kayit_tarihi`, `usta_id` (`FK -> kullanicilar.id`), `alinacak_parcalar` (JSON)

---

## API Uç Noktaları (15 Endpoint)

| Metot | Uç Nokta | Yetki / Rol | Açıklama |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Herkese Açık | Servis sağlık ve sürüm kontrolü |
| `POST` | `/login` | Herkese Açık | Kullanıcı doğrulama ve HS256 JWT token üretimi |
| `GET` | `/arac-durumu/{plaka}` | Herkese Açık | Müşteri plaka sorgulama (boşluk/harf normalizasyonlu) |
| `POST` | `/usta/is-emri-olustur` | `usta` | Yeni iş emri ve parça talebi kaydı |
| `GET` | `/usta/arac-gecmisi/{plaka}` | `usta` | Plakaya ait son 5 arşivlenmiş servis kaydı |
| `GET` | `/cirak/is-emirleri` | `cirak` | Arşivlenmemiş iş emirleri ve parça listesi |
| `PUT` | `/cirak/parca-guncelle` | `cirak` | Parça alındı durumunu güncelleme (`404`/`400` kontrollü) |
| `GET` | `/depo/stoklar` | `depo` | Tüm yedek parça stok listesi |
| `PUT` | `/depo/stok-guncelle/{stok_id}` | `depo` | Stok miktarını güncelleme (`<= 5` kritik log uyarısı) |
| `GET` | `/muhasebe/is-emirleri` | `muhasebe` | Aktif (arşivlenmemiş) iş emirleri listesi |
| `PUT` | `/muhasebe/is-emri-guncelle/{is_emri_id}` | `muhasebe` | Durum ve toplam tutar güncelleme |
| `PUT` | `/muhasebe/is-emri-arsivle/{id}` | `muhasebe` | Kaydı silmeden arşive alma (`Soft-Delete`) |
| `DELETE` | `/muhasebe/is-emri-sil/{id}` | `muhasebe` | İş emrini veritabanından kalıcı silme |
| `GET` | `/muhasebe/istatistik` | `muhasebe` | Toplam ciro, tamamlanan araç sayısı ve kritik stok listesi |
| `GET` | `/muhasebe/arama?plaka=...` | `muhasebe` | Arşivlenmiş geçmiş servis sicili arama |

---

## Klasör Yapısı

```text
oto_servis/
├── main.py              # FastAPI backend, JWT, PBKDF2, Rate Limiting ve SQLAlchemy modelleri
├── requirements.txt     # Python bağımlılıkları (fastapi, uvicorn, sqlalchemy)
├── .gitignore           # Git tarafından izlenmeyecek geçici ve .db dosyaları
├── README.md            # Proje dokümantasyonu
├── index.html           # Karşılama ekranı (Müşteri / Personel seçimi)
├── login.html           # Personel giriş ekranı
├── takip.html           # Müşteri plaka durum sorgulama ekranı
├── usta.html            # Usta iş emri ve parça talep paneli
├── cirak.html           # Çırak parça takip paneli
├── depo.html            # Depo stok yönetim paneli
├── muhasebe.html        # Muhasebe, fatura fişi, WhatsApp ve arşiv paneli
├── manifest.json        # PWA yapılandırması
├── sw.js                # PWA Service Worker
├── icon-192.png         # PWA uygulama ikonu (192x192)
└── icon-512.png         # PWA uygulama ikonu (512x512)
```

---

## Yerel Kurulum ve Çalıştırma

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API dokümantasyonuna (Swagger UI) tarayıcıdan `http://127.0.0.1:8000/docs` adresinden erişilebilir.

### İsteğe Bağlı Ortam Değişkenleri

| Değişken | Varsayılan | Açıklama |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./otoservis_v2.db` | PostgreSQL veya SQLite bağlantı adresi |
| `JWT_SECRET` | Yerleşik varsayılan anahtar | JWT imzalama gizli anahtarı (üretimde değiştiriniz) |
| `JWT_EXPIRE_SECONDS` | `43200` | Oturum token geçerlilik süresi (saniye, 12 saat) |
| `ALLOWED_ORIGINS` | `*` | İzin verilen CORS kaynakları (virgülle ayrılmış) |
| `LOGIN_RATE_LIMIT` | `15` | IP başına dakikadaki maksimum `/login` isteği |
| `GLOBAL_RATE_LIMIT` | `120` | IP başına dakikadaki maksimum genel API isteği |

---

## Varsayılan Demo Hesapları

Sunucu ilk kez başlatıldığında aşağıdaki test kullanıcıları hashlenmiş şifreleriyle otomatik oluşturulur:

| Rol | Kullanıcı Adı | Şifre |
| :--- | :--- | :--- |
| Usta | `usta` | `123` |
| Çırak | `cirak` | `123` |
| Depo | `depo` | `123` |
| Muhasebe | `muhasebe` | `123` |
