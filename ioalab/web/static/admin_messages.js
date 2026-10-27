"use strict";
async function loadMessages() {
  if (REPLAY) {
    $("adminMessages").textContent =
      "回放模式请查看消息活动元数据；正文审计仅在实时管理会话中开放。";
    return;
  }
  try {
    const rows = await api("/api/admin/messages");
    $("adminMessages").innerHTML = rows.length
      ? '<table class="tbl"><tr><th>发送账号</th><th>接收账号</th><th>接口</th><th>状态</th><th>时间</th><th>审计</th></tr>' +
        rows
          .map(
            (m) =>
              `<tr><td>${esc(m.senderUsername)}</td><td>${esc(m.receiverUsername)}</td><td>${esc(m.interface)}</td><td>${esc(m.state)}</td><td>${ymdhm(m.created)}</td><td><button class="btn sm" data-audit="${esc(m.id)}">查看正文</button></td></tr>`,
          )
          .join("") +
        "</table>"
      : '<div class="empty">尚无消息</div>';
    document.querySelectorAll("[data-audit]").forEach(
      (b) =>
        (b.onclick = async () => {
          try {
            const m = await api("/api/admin/messages/" + b.dataset.audit);
            $("messageAuditBody").textContent = JSON.stringify(m, null, 2);
            $("messageAudit").showModal();
          } catch (e) {
            toast(e.message, "bad");
          }
        }),
    );
  } catch (e) {
    toast(e.message, "bad");
  }
}
LOADERS.messages = loadMessages;
