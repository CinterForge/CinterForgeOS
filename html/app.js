/* HTML visual reference only: SteamOS uses its native KDE Plasma desktop. */
const toast = document.getElementById('toast');
let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.style.display = 'block';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.style.display = 'none'; }, 2300);
}
for (const [id, label] of [['linux', 'Linux'], ['windows', 'Windows']]) {
  document.getElementById(id).addEventListener('click', () => {
    showToast(`${label} mode selected — KDE preview only`);
  });
}
for (const button of document.querySelectorAll('.app')) {
  button.addEventListener('click', () => {
    const name = button.getAttribute('aria-label');
    showToast(`${name} — KDE launcher preview`);
  });
}
document.getElementById('system').addEventListener('click', () => showToast('KDE application menu — preview'));
document.getElementById('tray').addEventListener('click', () => showToast('KDE system tray — preview'));
