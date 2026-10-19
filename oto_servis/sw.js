self.addEventListener('install', (e) => {
    console.log('[Service Worker] Uygulama Kuruldu');
});

self.addEventListener('fetch', (e) => {
    // PWA indirme şartını sağlamak için boş bir dinleyici yeterlidir.
});