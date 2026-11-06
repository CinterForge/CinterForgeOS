/* Shared CFOS-owned KDE-style desktop presentation.
 * Browser mode: self-contained demos and product pages. Native mode: only the
 * embedding Swift host may opt into the explicitly registered launch bridge.
 * Never execute commands, synthesize download URLs, or claim a product is installed.
 */
'use strict';
const toast = document.getElementById('toast');
const preview = document.getElementById('cfos-app-preview');
const frame = document.getElementById('cfos-app-frame');
const windowTitle = document.getElementById('cfos-window-title');
const modeLabel = document.getElementById('desktop-mode');
const params = new URLSearchParams(window.location.search);
const nativeMode = params.get('mode') === 'native';
const host = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.cfosDesktop;
const demoRoutes = Object.freeze({
  Code: '/websites/dev/codeoss/out/vs/code/browser/workbench/workbench.html',
  Voxel: '/websites/dev/blockbench/index.html',
  Desktop: '/websites/dev/index.html',
  Lagomorph: '/websites/dev/lagomorph/index.html',
});
const productRoutes = Object.freeze({
  '3D': '/products/cinterforge-3d/', Image: '/products/cinterforge-image/',
  Paint: '/products/cinterforge-paint/', Vector: '/products/cinterforge-vector/',
  Audio: '/products/cinterforge-audio/', Video: '/products/cinterforge-video/',
  Document: '/products/cinterforge-document/', Converter: '/products/cinterforge-converter/',
  Photo: '/products/cinterforge-photo/'
});
let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.style.display = 'none'; }, 2800);
}
function openPage(path, title) {
  if (!preview || typeof preview.showModal !== 'function') return;
  const url = new URL(path, location.origin);
  // Only own-origin routes from the fixed allowlist may appear in the preview.
  if (url.origin !== location.origin || !url.pathname.startsWith('/')) return;
  windowTitle.textContent = title;
  frame.src = url.pathname + url.search;
  preview.showModal();
}
async function launchLagomorph() {
  // The browser and native WebView use this same CFOS-owned launcher. The
  // authorized game/emulator package is a separately staged WebApp asset;
  // never serve the uploaded System 7 ROM/disks as a convenient fallback.
  const url = new URL(demoRoutes.Lagomorph, location.origin);
  try {
    const response = await fetch(url.href, { method: 'HEAD', cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok || !(response.headers.get('content-type') || '').toLowerCase().includes('text/html')) {
      throw new Error('Approved Lagomorph browser package is unavailable');
    }
    openPage(url.pathname, 'Lagomorph 1 — Web');
  } catch (_) {
    showToast('Lagomorph 1 Web is not included in this authorized build.');
  }
}
function launch(name) {
  // Lagomorph is a web game opened INSIDE the OS desktop, not a native
  // executable request or a separate Website launcher.
  if (name === 'Lagomorph') {
    launchLagomorph();
    return;
  }
  if (nativeMode && host && typeof host.postMessage === 'function') {
    host.postMessage({ action: 'launch', app: name });
    return;
  }
  if (Object.hasOwn(demoRoutes, name)) {
    openPage(demoRoutes[name], 'CinterForge ' + name + ' — Demo');
  } else if (Object.hasOwn(productRoutes, name)) {
    openPage(productRoutes[name], 'CinterForge ' + name + ' — Product details');
  } else {
    showToast(name + ' requires an installed CFOS desktop.');
  }
}
modeLabel.textContent = nativeMode && host ? 'CinterForge OS · Native' : 'CinterForge OS · Web Demo';
for (const button of document.querySelectorAll('.app')) {
  button.addEventListener('click', () => launch(button.getAttribute('data-app')));
}
for (const [id, label] of [['linux', 'Linux'], ['windows', 'Windows']]) {
  document.getElementById(id).addEventListener('click', () => showToast(label + ' desktop selected — native CFOS host required to switch OS.'));
}
document.getElementById('system').addEventListener('click', () => showToast('CFOS application menu'));
document.getElementById('tray').addEventListener('click', () => showToast('System tray is managed by the native desktop.'));
document.getElementById('cfos-window-close').addEventListener('click', () => preview.close());
preview.addEventListener('close', () => { frame.src = 'about:blank'; });
preview.addEventListener('click', (event) => { if (event.target === preview) preview.close(); });
