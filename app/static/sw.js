// Service worker do Cabine RFID. Objetivo: tornar o app instalável e mostrar uma página amigável sem rede.
// NÃO guarda respostas da API nem do painel (dados ao vivo e autenticados): só ícones e a página offline.
const VERSAO = 'cabine-v1';
const ESTATICOS = ['/offline.html', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSAO).then(c => c.addAll(ESTATICOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSAO).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  if (req.mode === 'navigate') {  // páginas: sempre da rede; sem rede, a página offline
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
    return;
  }
  if (url.pathname.startsWith('/icons/')) {  // ícones: cache primeiro
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
  // /api/*, login etc.: segue direto para a rede, sem cache
});
