'use strict';
const byId = id => document.getElementById(id);
const escapeText = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
byId('base').textContent = location.origin;
async function request(path, body) {
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || data.detail || '请求失败');
  return data;
}
async function refresh() {
  const session = await request('/api/session');
  if (session.role !== 'user') return;
  const account = await request('/api/me');
  byId('account').innerHTML = `<p><b>${escapeText(account.username)}</b> · 余量 ${escapeText(account.wallet)} 虚拟币</p><p>${account.agents.length ? '已关联身份 '+escapeText(account.agents.map(a=>a.name).join('、')) : '账号已准备好。在九问里配置连接器后，会关联你的本地助手。'}</p>`;
  const orders = await request('/api/orders');
  byId('orders').innerHTML = orders.length ? orders.map(o=>`<p><code>${escapeText(o.card_digest.slice(0,24))}…</code> · ${escapeText(o.state)} · ${escapeText(o.total)} 虚拟币</p>`).join('') : '还没有交易。向你的九问说一句需求试试。';
}
async function authenticate(action) {
  try {
    await request('/api/auth/'+action, {username:byId('name').value, password:byId('password').value, invite:byId('invite').value});
    byId('password').value='';byId('message').textContent='账号连接成功';await refresh();
  } catch(e) { byId('message').textContent=e.message; }
}
byId('auth').onsubmit=e=>{e.preventDefault();authenticate('login');};
byId('register').onclick=()=>authenticate('register');
refresh().catch(e=>{byId('message').textContent=e.message;});
