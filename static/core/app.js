'use strict';
document.querySelector('.mobile-menu')?.addEventListener('click', (event) => {
  const opened = document.querySelector('#sidebar').classList.toggle('open');
  event.currentTarget.setAttribute('aria-expanded', String(opened));
});
document.addEventListener('click', event => {
  if (!event.target.closest('.sidebar, .mobile-menu')) {
    document.querySelector('#sidebar')?.classList.remove('open');
    document.querySelector('.mobile-menu')?.setAttribute('aria-expanded', 'false');
  }
});
document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
  const input = document.getElementById(button.dataset.copy);
  try { await navigator.clipboard.writeText(input.value); button.textContent = 'Ссылка скопирована ✓'; }
  catch { input.select(); button.textContent = 'Нажмите Ctrl+C / ⌘C'; }
}));
document.querySelectorAll('[data-score]').forEach(bar => {
  const score = Number(bar.dataset.score.replace(',', '.'));
  bar.style.width = `${Math.min(100, Math.max(0, score / 9 * 100))}%`;
});
const chartData = document.getElementById('chart-data');
if (chartData) {
  const points = JSON.parse(chartData.textContent);
  const root = document.getElementById('progress-chart');
  const ns = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs = {}, text = '') => {
    const item = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, value));
    if (text) item.textContent = text;
    return item;
  };
  const svg = node('svg', {viewBox: '0 0 560 225', role: 'img', 'aria-label': points.map(p => `${p.label}: ${p.score}`).join('; ')});
  for (let n = 0; n <= 9; n += 3) {
    const y = 190 - n / 9 * 166;
    svg.append(node('line', {x1: 28, x2: 550, y1: y, y2: y, stroke: '#edf1e7'}));
    svg.append(node('text', {x: 4, y: y + 3, fill: '#a1ad98', 'font-size': 9}, String(n)));
  }
  const coords = points.map((p, i) => ({x: points.length === 1 ? 285 : 38 + i / (points.length - 1) * 500, y: 190 - p.score / 9 * 166, ...p}));
  const path = coords.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' ');
  if (coords.length > 1) {
    svg.append(node('path', {d: `${path} L${coords.at(-1).x},190 L${coords[0].x},190 Z`, fill: '#eef4e6'}));
    svg.append(node('path', {d: path, fill: 'none', stroke: '#83a566', 'stroke-width': 2.5, 'stroke-linejoin': 'round'}));
  }
  coords.forEach((p, i) => {
    const dot = node('circle', {cx: p.x, cy: p.y, r: 4.5, fill: '#83a566', stroke: '#fff', 'stroke-width': 2});
    dot.append(node('title', {}, `${p.title} · ${p.score}`)); svg.append(dot);
    svg.append(node('text', {x: p.x, y: p.y - 13, fill: '#526c43', 'font-size': 10, 'text-anchor': 'middle'}, p.score.toFixed(1)));
    if (points.length < 8 || i % 2 === 0) svg.append(node('text', {x: p.x, y: 214, fill: '#95a28b', 'font-size': 9, 'text-anchor': 'middle'}, p.label));
  });
  root.append(svg);
}
document.getElementById('test-sound')?.addEventListener('click', async () => {
  const status = document.getElementById('device-status');
  try {
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    const context = new AudioContext(); await context.resume();
    const oscillator = context.createOscillator(); const gain = context.createGain();
    gain.gain.value = 0.08; oscillator.frequency.value = 440;
    oscillator.connect(gain).connect(context.destination); oscillator.start();
    oscillator.stop(context.currentTime + 0.6); oscillator.onended = () => context.close();
    status.textContent = 'Прозвучал короткий сигнал. Убедитесь, что вы его слышите.';
  } catch { status.textContent = 'Браузер не смог включить звук. Проверьте настройки устройства.'; }
});
document.getElementById('test-mic')?.addEventListener('click', async () => {
  const status = document.getElementById('device-status');
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    stream.getTracks().forEach(track => track.stop());
    status.textContent = typeof MediaRecorder === 'undefined' ? 'Микрофон доступен, но браузер не поддерживает запись. Используйте загрузку файла.' : 'Микрофон доступен. Браузер поддерживает запись.';
  } catch { status.textContent = 'Нет доступа к микрофону. Разрешите запись в настройках браузера. Требуется HTTPS или localhost.'; }
});
const gradingForm = document.getElementById('grading-form');
if (gradingForm) {
  let changed = false;
  gradingForm.addEventListener('input', () => { changed = true; });
  gradingForm.addEventListener('submit', () => { changed = false; });
  window.addEventListener('beforeunload', event => { if (changed) { event.preventDefault(); event.returnValue = ''; } });
}
document.querySelector('form[action="/logout/"]')?.addEventListener('submit', () => {
  try { Object.keys(localStorage).filter(key => key.startsWith('zg-')).forEach(key => localStorage.removeItem(key)); indexedDB.deleteDatabase('zg-recordings'); } catch { /* Storage may be unavailable in private mode. */ }
});
