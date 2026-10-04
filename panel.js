const $ = (id) => document.getElementById(id);
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function toast(message, type = '') {
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.textContent = message;
  $('toastWrap').appendChild(node);
  setTimeout(() => node.remove(), 2800);
}
async function api(path) {
  const response = await fetch(path);
  const type = response.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) throw new Error(data?.detail || `خطای ${response.status}`);
  return data;
}
async function load() {
  try {
    const [configs, stats] = await Promise.all([api('/api/configs'), api('/api/stats')]);
    $('domainLabel').textContent = stats.domain;
    $('connections').textContent = stats.connections;
    $('uptime').textContent = stats.uptime;
    $('configGrid').innerHTML = configs.map(config => `<article class="config-card">
      <div class="config-head"><h3>${escapeHtml(config.name)}</h3><span class="badge ok">نامحدود</span></div>
      <div class="config-details"><span>حجم: نامحدود</span><span>زمان: دائمی</span></div>
      <label class="link-label">لینک اتصال</label><div class="link-box" dir="ltr">${escapeHtml(config.link)}</div>
      <div class="actions"><button class="button primary" data-copy="${config.slot}">کپی کانفیگ</button><button class="button secondary" data-copy-sub="${config.slot}">کپی سابسکریپشن</button></div>
      <input type="hidden" id="link-${config.slot}" value="${escapeHtml(config.link)}">
      <input type="hidden" id="sub-${config.slot}" value="${escapeHtml(config.subscription)}">
    </article>`).join('');
  } catch (error) {
    $('configGrid').innerHTML = '<div class="empty">بارگذاری ناموفق بود؛ دوباره تلاش کنید.</div>';
    toast(error.message, 'error');
  }
}
async function copy(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast('کپی شد', 'success');
  } catch {
    toast('کپی خودکار در دسترس نیست؛ لینک را انتخاب و کپی کنید.', 'error');
  }
}
$('configGrid').addEventListener('click', (event) => {
  const linkButton = event.target.closest('[data-copy]');
  const subButton = event.target.closest('[data-copy-sub]');
  if (linkButton) copy($(`link-${linkButton.dataset.copy}`).value);
  if (subButton) copy($(`sub-${subButton.dataset.copySub}`).value);
});
$('refresh').addEventListener('click', load);
load();
setInterval(async () => {
  try {
    const stats = await api('/api/stats');
    $('connections').textContent = stats.connections;
    $('uptime').textContent = stats.uptime;
  } catch {}
}, 15000);
