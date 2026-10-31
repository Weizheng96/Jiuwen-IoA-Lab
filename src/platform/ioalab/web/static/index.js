"use strict";
const $ = (id) => document.getElementById(id),
  esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
const labels = {
  queued: "平台暂存",
  pending: "等待处理",
  failed: "处理失败",
  synced: "已同步到助手",
  processing: "处理中",
  completed: "已处理",
  rejected: "已拒绝",
  expired: "已过期",
  started: "履约中",
  accepted: "已验收",
  signed: "已签约",
  drafted: "草拟中",
  forming: "待确认提案",
  offered: "待确认提案",
  finished: "已完成",
  invite_submitted: "邀请邮件已提交，等待接受",
  delivered: "已交付",
  no_time: "该时段不可预约",
  unknown: "结果待核对",
};
let data = null,
  definitions = [],
  chosen = null,
  active = "assistant",
  publicServices = [], catalogChecked = 0;
$("base").textContent = location.origin;
async function api(path, body, method) {
  const r = await fetch(path, {
    method: method || (body ? "POST" : "GET"),
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json();
  if (!r.ok) { const error=Error(d.error?.message||"请求失败"); error.status=r.status; throw error; }
  return d;
}
const badge = (s, text = labels[s] || s) =>
  `<span class="badge ${["completed", "accepted", "finished"].includes(s) ? "done" : ["rejected", "expired", "unknown"].includes(s) ? "warn" : ""}">${esc(text)}</span>`;
const when = (t) =>
  t == null ? "时点未保留" : new Date(t * 1000).toLocaleString("zh-CN", { hour12: false });
function intentRows(rows) {
  return rows.map(r=>`<div class="row"><div><b>${esc(r.providerName)}</b><small>委托意向 · 尚未形成合同</small></div><div>${esc(r.phase)}</div><div>已付拟卡费 ${esc(r.proposalFeePaid)} 虚拟币<small>${esc(when(r.created))}</small></div><button class="btn" data-intent="${esc(r.originMessageId)}">详情</button></div>`).join("");
}
function orders(rows) {
  return rows.length
    ? rows
        .map(
          (o) =>
            `<div class="row"><div><b>${esc(o.providerName || o.provider_id)}</b><small>${esc(o.card_digest?.slice(0, 23))}…</small></div><div>${badge(o.state, o.phase)}</div><div>${o.total===null?'报价未知':esc(o.total)+' 虚拟币'}<small>已付 ${esc(o.paid || "0.00")} · ${esc(when(o.created))}</small></div><button class="btn" data-order="${esc(o.card_digest)}">详情</button></div>`,
        )
        .join("")
    : '<div class="empty">还没有委托记录。向九问描述需求，确认合同后即可开始。</div>';
}
function render() {
  $("assistant-title").textContent = `${data.account.display_name || data.account.username}的个人助手`;
  $("identity").textContent = data.account.username;
  $("credit").textContent = `余量 ${data.wallet} 虚拟币`;
  $("admin-link").hidden = !data.roles.includes("admin");
  $("connection").textContent =
    data.identities
      .filter((x) => x.role === "assistant")
      .map((x) => `${x.name} · ${x.status}`)
      .join(" / ") || "账号已就绪，等待本地九问关联助手身份。";
  $("metrics").innerHTML = [
    ["已签委托", data.orders.filter(o=>o.effective).length],
    [
      "履约中",
      data.orders.filter(
        (o) => o.effective && !IoaNetwork.done.has(o.state),
      ).length,
    ],
    ["交互消息", data.messages.length],
    ["交互接口", data.interfaces.filter((i) => i.enabled).length],
  ]
    .map(([n, v]) => `<div class="metric"><b>${v}</b><span>${n}</span></div>`)
    .join("");
  IoaNetwork.render(data);
  $("orders").innerHTML = orders(
    data.orders.filter((o) => o.effective && !IoaNetwork.done.has(o.state)),
  );
  $('proposal-orders').innerHTML=intentRows(data.intents)+orders(data.orders.filter(o=>!o.effective&&!IoaNetwork.done.has(o.state)));
  $("order-history").innerHTML = orders(
    data.orders.filter((o) => IoaNetwork.done.has(o.state)),
  );
  const openInterfaces = new Set([...$("interfaces").querySelectorAll("details[open]")].map(n => n.dataset.interface));
  $("interfaces").innerHTML = data.interfaces.length
    ? data.interfaces
        .map(
          (i) =>
            `<details class="interface-definition" data-interface="${esc(i.name)}" ${openInterfaces.has(i.name) ? "open" : ""}><summary><b>${esc(i.name)}</b> <span class="badge">修订 ${i.revision} · ${i.enabled ? "启用" : "停用"}</span></summary><p class="interface-scope">允许账号：${esc(i.allowedSenders === null ? "所有登录账号" : i.allowedSenders?.join("、") || "无允许账号")}</p><p>${esc(i.description || "")}</p><pre>${esc(JSON.stringify(i.inputSchema, null, 2))}</pre></details>`,
        )
        .join("")
    : '<div class="empty">尚未登记接口</div>';
  $("messages").innerHTML = data.messages.length
    ? data.messages
        .map(
          (m) =>
            `<div class="row"><div><b>${esc(m.senderDisplayName || m.senderUsername)} → ${esc(m.receiverDisplayName || m.receiverUsername)}</b><small>${esc(m.interface)}</small></div><div>${badge(m.deliveryState, {queued:"平台暂存",delivered:"已送达助手",expired:"投递已过期"}[m.deliveryState] || m.deliveryState)} ${badge(m.requestState)}<small>用户阅读状态未知</small></div><div>${esc(when(m.created))}</div><button class="btn" data-message="${m.id}">查看消息</button></div>`,
        )
        .join("")
    : '<div class="empty">尚无交互。查询对方账号对你开放的交互接口，按输入格式发送。</div>';
  ProviderWorkbench.render(data);
  document.querySelectorAll('[data-intent]').forEach(b=>b.onclick=()=>show('委托意向',
    [...data.intents,...data.services.flatMap(s=>s.intents)].find(r=>r.originMessageId===b.dataset.intent)));
  document.querySelectorAll("[data-message]").forEach(
    (b) =>
      (b.onclick = () =>
        show(
          "消息与处理回执",
          data.messages.find((m) => m.id === b.dataset.message),
        )),
  );
  document.querySelectorAll("[data-order]").forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          const all = [
            ...data.orders,
            ...data.services.flatMap((s) => s.orders),
          ];
          IoaNetwork.chooseOrder(b.dataset.order);
          show(
            "委托详情",
            all.find((o) => o.card_digest === b.dataset.order),
          );
        } catch (e) {
          notice(e.message);
        }
      }),
  );
}
function show(title, body) {
  $("detail-title").textContent = title;
  $("detail-body").textContent = JSON.stringify(body, null, 2);
  if ($("detail").open) $("detail").close();
  $("detail").classList.toggle("assistant-detail", active === "assistant");
  if (active === "assistant") {
    const bounds = document.querySelector(".assistant-content").getBoundingClientRect();
    $("detail").style.top = `${bounds.top}px`;
    $("detail").style.height = `${Math.max(100, bounds.height - 10)}px`;
    $("detail").show();
  } else {
    $("detail").style.top = "";
    $("detail").style.height = "";
    $("detail").showModal();
  }
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("detail").open) $("detail").close(); });
function notice(text) {
  $("notice").textContent = text;
}
async function refresh() {
  const s = await api("/api/session");
  if (!s.id) return;
  data = await api("/api/workbench");
  if(!catalogChecked||Date.now()-catalogChecked>30000){publicServices=await api('/api/catalog');catalogChecked=Date.now();}
  data.publicServices=publicServices;
  $("welcome").hidden = true;
  $("workspace").hidden = false;
  document.body.classList.add("workbench-connected");
  $("logout").hidden = false;
  render();
}
let pendingAuth = null;
async function auth(action) {
  try {
    const fields = {
      username: $("name").value.trim(),
      password: $("password").value,
      invite: $("invite").value,
    };
    const fingerprint=JSON.stringify([action,fields]);
    if (!pendingAuth || pendingAuth.fingerprint!==fingerprint) {
      let saved=null;
      try { saved=JSON.parse(sessionStorage.getItem('ioa-auth-operation')); } catch (_) {}
      const operationId=saved&&saved.action===action&&saved.username===fields.username?saved.operationId:
        (crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join(''));
      pendingAuth={fingerprint,operationId};
      sessionStorage.setItem('ioa-auth-operation',JSON.stringify({action,username:fields.username,operationId}));
    }
    await api("/api/auth/" + action, {...fields,operationId:pendingAuth.operationId});
    pendingAuth=null;
    sessionStorage.removeItem('ioa-auth-operation');
    $("password").value = "";
    notice("账号连接成功");
    await refresh();
    const list = await api("/api/catalog");
    $("catalog").innerHTML = list
      .map(
        (s) =>
          `<article class="service-card"><h3>${esc(s.name)}</h3><p class="hint">${esc(s.description)}</p><span class="badge">${esc(s.price ?? "按合同报价")} 虚拟币</span></article>`,
      )
      .join("");
  } catch (e) {
    if (e.status>=400 && e.status<500) { pendingAuth=null; sessionStorage.removeItem('ioa-auth-operation'); }
    notice(e.message);
  }
}
$("auth").onsubmit = (e) => {
  e.preventDefault();
  auth("login");
};
$("register").onclick = () => auth("register");
$("logout").onclick = async () => {
  await api("/api/auth/logout", {});
  location.reload();
};
document.querySelectorAll("[data-view]").forEach(
  (b) =>
    (b.onclick = () => {
      active = b.dataset.view;
      document
        .querySelectorAll("[data-view]")
        .forEach((x) => x.classList.toggle("active", x === b));
      $("assistant-view").hidden = active !== "assistant";
      $("provider-view").hidden = active !== "provider";
    }),
);
$("lookup").onsubmit = async (e) => {
  e.preventDefault();
  try {
    definitions = await api(
      "/api/v1/messaging/accounts/" +
        encodeURIComponent($("recipient").value.trim()) +
        "/interfaces",
    );
    $("compose").hidden = !definitions.length;
    $("send-status").textContent = definitions.length
      ? ""
      : "没有对你开放的接口";
    $("interface").innerHTML = definitions
      .map(
        (i) =>
          `<option value="${esc(i.name)}">${esc(i.name)} · 修订 ${i.revision}</option>`,
      )
      .join("");
    fields();
  } catch (e) {
    $("send-status").textContent = e.message;
  }
};
function fields() {
  chosen = definitions.find((i) => i.name === $("interface").value);
  if (!chosen) return;
  $("interface-description").textContent = chosen.description;
  const schema = chosen.inputSchema;
  if (schema.type !== "object" || !schema.properties) {
    $("fields").innerHTML =
      '<label class="schema-field">JSON 输入<textarea id="raw-json" rows="6">{}</textarea></label>';
    return;
  }
  $("fields").innerHTML = Object.entries(schema.properties)
    .map(([name, s]) => {
      const required = (schema.required || []).includes(name);
      let control = s.enum
        ? `<select data-field="${esc(name)}">${s.enum.map((v) => `<option>${esc(v)}</option>`).join("")}</select>`
        : `<input data-field="${esc(name)}" type="${s.type === "boolean" ? "checkbox" : ["integer", "number"].includes(s.type) ? "number" : s.format === "email" ? "email" : "text"}" ${required ? "required" : ""} ${s.maxLength ? 'maxlength="' + s.maxLength + '"' : ""} placeholder="${esc(s.format === "date-time" ? "2026-10-27T10:00:00+08:00" : s.description || name)}">`;
      if (["object", "array"].includes(s.type))
        control = `<textarea data-field="${esc(name)}" rows="3" ${required ? "required" : ""}></textarea>`;
      return `<label class="schema-field">${esc(s.description || name)}${required ? " *" : ""}${control}</label>`;
    })
    .join("");
}
$("interface").onchange = fields;
$("send").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  try {
    const payload = $("raw-json") ? JSON.parse($("raw-json").value) : {};
    for (const input of document.querySelectorAll("[data-field]")) {
      const name = input.dataset.field,
        s = chosen.inputSchema.properties[name];
      if (
        input.value === "" &&
        !(chosen.inputSchema.required || []).includes(name)
      )
        continue;
      payload[name] =
        s.type === "boolean"
          ? input.checked
          : ["number", "integer"].includes(s.type)
            ? Number(input.value)
            : ["array", "object"].includes(s.type)
              ? JSON.parse(input.value)
              : input.value;
    }
    const draft = {to: $("recipient").value.trim(), interface: chosen.name, revision: chosen.revision, input: payload};
    $("message-draft").textContent = JSON.stringify(draft, null, 2);
    $("message-draft").hidden = false;
    $("send-status").textContent = "消息草稿已整理。请交给自己的九问个人助手发送；此页面不会代替助手发信。";
  } catch (e) {
    $("send-status").textContent = e.message;
  } finally {
    button.disabled = false;
  }
};
refresh()
  .then(async () => {
    if (data) {
      const list = await api("/api/catalog");
      $("catalog").innerHTML = list
        .map(
          (s) =>
            `<article class="service-card"><h3>${esc(s.name)}</h3><p class="hint">${esc(s.description)}</p></article>`,
        )
        .join("");
    }
  })
  .catch((e) => notice(e.message));
setInterval(() => {
  if (data) refresh().catch(() => {});
}, 4000);
