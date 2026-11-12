// Service worker do Cabine RFID. Objetivo: tornar o app instalável e mostrar uma página amigável sem rede.
// NÃO guarda respostas da API nem do painel (dados ao vivo e autenticados): só ícones e a página offline.
const VERSAO = 'cabine-v2';
const ESTATICOS = ['/offline.html', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSAO).then(c => c.addAll(ESTATICOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSAO).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

// Notificações push (alertas do servidor: evasão, antena offline...). O conteúdo vem do próprio servidor, assinado com a chave VAPID.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data.json(); } catch (_) { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Cabine RFID', {
    body: d.body || '', icon: '/icons/icon-192.png', badge: '/icons/icon-192.png', tag: d.tag || undefined,
    renotify: !!d.tag, data: { url: d.url || '/' } }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
    for (const w of ws) { if ('focus' in w) { if (w.navigate) w.navigate(url); return w.focus(); } }
    return self.clients.openWindow(url);
  }));
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
