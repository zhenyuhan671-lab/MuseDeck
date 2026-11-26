/* MuseDeck · Interactive Frontend Controller */

const PALETTE = [
  "#FFE100", "#38BDF8", "#34D399", "#FB923C",
  "#F87171", "#A78BFA", "#60A5FA", "#94A3B8"
];

let state = {
  cfg: {},
  accounts: [],
  runtime: {}, // { acc_id: { running, seconds, mem_mb } }
  probes: {},  // { acc_id: { ok, ms, country, ip } }
  filter: "all",
  search: "",
  editingId: null,
  selectedColor: PALETTE[0],
  plugins: [],
  privacyLocked: false,
  viewMode: "grid" // grid | table
};

const $ = (id) => document.getElementById(id);

function api() {
  return window.pywebview && window.pywebview.api ? window.pywebview.api : null;
}

function toast(msg, kind = "ok") {
  const stack = $("toastStack");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function fmtDuration(sec) {
  const m = String(Math.floor(sec / 60)).padStart(2, "0");
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function vipDaysLeft(dateStr) {
  if (!dateStr) return null;
  const target = new Date(`${dateStr}T23:59:59`);
  if (Number.isNaN(target.getTime())) return null;
  return Math.ceil((target.getTime() - Date.now()) / 86400000);
}

function applyTheme(themeKey) {
  const isLight = String(themeKey || "").includes("light");
  document.documentElement.setAttribute("data-theme", isLight ? "light" : "dark");
}

/* ---------- 渲染工作台 ---------- */

function renderFilterChips() {
  const wrap = $("filterChips");
  const groups = [...new Set(state.accounts.map((a) => a.group || "默认分组"))].sort();
  const items = [
    { key: "all", label: "全部" },
    { key: "running", label: "运行中" },
    ...groups.map((g) => ({ key: `g:${g}`, label: g }))
  ];
  wrap.innerHTML = "";
  items.forEach((it) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `chip ${state.filter === it.key ? "active" : ""}`;
    btn.textContent = it.label;
    btn.addEventListener("click", () => {
      state.filter = it.key;
      renderFilterChips();
      renderCards();
    });
    wrap.appendChild(btn);
  });
}

function renderBookmarks() {
  const wrap = $("bookmarksRow");
  const bms = (state.cfg && state.cfg.bookmarks) || [];
  wrap.innerHTML = "";
  bms.forEach((bm, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bm-chip";
    btn.textContent = bm.name;
    btn.title = `左键同步打开 ${bm.url} · 右键删除书签`;
    btn.addEventListener("click", () => broadcastUrl(bm.url));
    btn.addEventListener("contextmenu", async (e) => {
      e.preventDefault();
      if (!confirm(`移除快捷书签「${bm.name}」？`)) return;
      const r = await api().remove_bookmark(idx);
      if (r.ok) {
        state.cfg.bookmarks = r.bookmarks;
        renderBookmarks();
        toast(r.message);
      }
    });
    wrap.appendChild(btn);
  });

  const addBtn = document.createElement("button");
  addBtn.type = "button";
  addBtn.className = "bm-add-btn";
  addBtn.textContent = "+";
  addBtn.title = "添加自定义快捷书签";
  addBtn.addEventListener("click", async () => {
    const name = prompt("输入快捷书签名称（如 ChatGPT / Midjourney）：");
    if (!name) return;
    const url = prompt("输入网址（如 https://chatgpt.com）：");
    if (!url) return;
    const r = await api().add_bookmark(name, url);
    if (r.ok) {
      state.cfg.bookmarks = r.bookmarks;
      renderBookmarks();
      toast(r.message);
    } else {
      toast(r.message, "err");
    }
  });
  wrap.appendChild(addBtn);
}

function renderTopStats() {
  const total = state.accounts.length;
  const runningList = Object.values(state.runtime).filter((r) => r.running);
  const running = runningList.length;
  const totalMem = Math.round(runningList.reduce((acc, r) => acc + (Number(r.mem_mb) || 0), 0));
  $("statTotal").textContent = `${total} 节点`;
  $("statRunning").textContent = `${running} 运行`;
  const memEl = $("statMem");
  if (totalMem > 0) {
    memEl.hidden = false;
    memEl.textContent = `${totalMem} MB`;
  } else {
    memEl.hidden = true;
  }

  const mode = state.cfg.proxy_mode || "local";
  if (mode === "local") {
    const t = (state.cfg.proxy_type || "http").toUpperCase();
    $("statProxy").textContent = `${t}:${state.cfg.proxy_port || 7892}`;
  } else if (mode === "system") {
    $("statProxy").textContent = "系统代理";
  } else {
    $("statProxy").textContent = "直连";
  }
}

function renderCards() {
  const grid = $("cardsGrid");
  const tableWrap = $("tableWrap");
  const tbody = $("tableBody");
  const kw = state.search.trim().toLowerCase();

  const visible = state.accounts
    .filter((acc) => {
      const rt = state.runtime[acc.id] || {};
      if (state.filter === "running" && !rt.running) return false;
      if (state.filter.startsWith("g:") && (acc.group || "默认分组") !== state.filter.slice(2)) return false;
      if (kw) {
        const hay = `${acc.name} ${acc.note || ""} ${acc.group || ""} ${acc.login_email || ""}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    })
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));

  const isTable = state.viewMode === "table";
  grid.hidden = isTable;
  tableWrap.hidden = !isTable;
  $("btnViewMode").textContent = isTable ? "卡片" : "表格";

  if (isTable) {
    tbody.innerHTML = "";
    if (!visible.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-hint">没有匹配的节点</td></tr>`;
      return;
    }
    visible.forEach((acc) => {
      const rt = state.runtime[acc.id] || { running: false, seconds: 0, mem_mb: 0 };
      const pr = state.probes[acc.id];
      const displayName = state.privacyLocked ? `NODE-${String(acc.id).slice(0, 4).toUpperCase()}` : acc.name;
      const proxyTxt = acc.proxy === "direct" ? "直连" : (acc.proxy && acc.proxy.port ? `${(acc.proxy.type || "http").toUpperCase()}:${acc.proxy.port}` : "全局");
      const latencyTxt = pr && pr.ok ? ` · ${pr.ms}ms` : "";
      const tr = document.createElement("tr");
      tr.dataset.rowId = acc.id;
      tr.innerHTML = `
        <td><strong></strong> ${acc.pinned ? "★" : ""}</td>
        <td><span class="meta-pill ${rt.running ? "ok" : ""}">${rt.running ? fmtDuration(rt.seconds) : "待机"}</span> ${rt.need_login ? '<span class="meta-pill warn">⚠ 掉登录</span>' : ""}</td>
        <td>${rt.running && rt.mem_mb > 0 ? `${rt.mem_mb} MB` : "—"}</td>
        <td class="tbl-title-cell"></td>
        <td>${proxyTxt}${latencyTxt}</td>
        <td><button type="button" class="meta-pill cdp" data-act="cdp">:${rt.cdp_port || acc.cdp_port || ""}</button></td>
        <td style="text-align:right;white-space:nowrap">
          <button type="button" class="pill-btn ghost" style="padding:5px 11px;font-size:12px" data-act="edit">检视</button>
          <button type="button" class="pill-btn ${rt.running ? "danger" : "primary"}" style="padding:5px 12px;font-size:12px" data-act="toggle">${rt.running ? "关闭" : "启动"}</button>
        </td>
      `;
      tr.querySelector("strong").textContent = displayName;
      tr.querySelector(".tbl-title-cell").textContent = rt.page_title || acc.note || "—";
      tr.querySelector('[data-act="cdp"]').addEventListener("click", () => copyCdp(acc.id));
      tr.querySelector('[data-act="edit"]').addEventListener("click", () => openEditDialog(acc));
      tr.querySelector('[data-act="toggle"]').addEventListener("click", () => toggleAccount(acc.id));
      tbody.appendChild(tr);
    });
    return;
  }

  if (!visible.length) {
    grid.innerHTML = `<div class="empty-hint" style="grid-column:1/-1">没有匹配的账号节点</div>`;
    return;
  }

  grid.innerHTML = "";
  visible.forEach((acc) => {
    const rt = state.runtime[acc.id] || { running: false, seconds: 0, mem_mb: 0 };
    const pr = state.probes[acc.id];
    const color = acc.color || "#FFE100";
    const initial = (acc.name || "M").slice(0, 1).toUpperCase();
    const vipDays = vipDaysLeft(acc.vip_expire);

    const card = document.createElement("article");
    card.dataset.cardId = acc.id;
    card.className = `account-card glass ${rt.running ? "running" : ""} ${acc.pinned ? "pinned" : ""}`;
    card.addEventListener("dblclick", () => toggleAccount(acc.id));

    const proxyBadge =
      acc.proxy === "direct"
        ? "直连"
        : acc.proxy && typeof acc.proxy === "object"
        ? `${(acc.proxy.type || "http").toUpperCase()} ${acc.proxy.port}${acc.proxy.username ? "·鉴权" : ""}`
        : "";

    let staticPills = "";
    const isSeed = state.cfg && state.cfg.seed_account_id === acc.id;
    if (isSeed) staticPills += `<span class="meta-pill seed">默认模板</span>`;
    if (acc.agent_role) staticPills += `<span class="meta-pill role"></span>`;

    const planMap = { free: "Free", power: "Power", max: "Max" };
    const qMap = { ok: ["充裕", "ok"], low: ["偏低", "warn"], empty: ["已耗尽", "err"] };
    const planLabel = planMap[acc.muse_plan] || "Free";
    const [qText, qCls] = qMap[acc.quota_status] || qMap.ok;
    const hasPct = acc.quota_used_pct !== null && acc.quota_used_pct !== undefined;
    const hasExtraPct = acc.extra_used_pct !== null && acc.extra_used_pct !== undefined;
    const qDisplay = hasPct ? `${planLabel} · 已用 ${acc.quota_used_pct}%` : `${planLabel} · 点击同步额度`;
    const resetTip = acc.quota_reset_day ? ` · 重置: ${acc.quota_reset_day}` : "";
    staticPills += `<button type="button" class="meta-pill clickable rt-quota ${qCls}" data-act="quota" title="点击立即静默同步 Muse 周额度与赠送额度${resetTip}">${qDisplay}</button>`;

    if (proxyBadge) staticPills += `<span class="meta-pill">${proxyBadge}</span>`;
    if (pr) {
      if (pr.loading) staticPills += `<span class="meta-pill warn">测速中...</span>`;
      else if (pr.ok) staticPills += `<span class="meta-pill ok" title="${pr.ip || ""}">${pr.ms}ms${pr.country ? " · " + pr.country : ""}</span>`;
      else staticPills += `<span class="meta-pill err">超时</span>`;
    }
    if (vipDays !== null) {
      if (vipDays < 0) staticPills += `<span class="meta-pill err">VIP 已过期</span>`;
      else if (vipDays <= 7) staticPills += `<span class="meta-pill warn">VIP 剩 ${vipDays} 天</span>`;
    }

    const subText = acc.note || acc.login_email || (acc.browser === "edge" ? "Edge 独立环境" : "Chrome 独立环境");
    const displayName = state.privacyLocked ? `NODE-${String(acc.id).slice(0, 4).toUpperCase()}` : acc.name;
    const hasPrompt = Boolean(acc.agent_prompts && acc.agent_prompts.trim());

    card.innerHTML = `
      <div class="card-top">
        <div class="avatar-circle" style="background:${color}22;color:${color};border:1px solid ${color}44">${state.privacyLocked ? "•" : initial}</div>
        <div class="card-title-col">
          <div class="card-name-row">
            <span class="card-name"></span>
            <span class="status-dot ${rt.running ? "on" : ""}"></span>
          </div>
          <div class="card-sub">
            <span class="card-note-text"></span>
            <span class="meta-pill ok rt-timer" ${rt.running ? "" : "hidden"}>${rt.running ? fmtDuration(rt.seconds) : ""}</span>
            <span class="meta-pill mem rt-mem" ${rt.running && rt.mem_mb > 0 ? "" : "hidden"}>${rt.mem_mb > 0 ? `${rt.mem_mb} MB` : ""}</span>
            <button type="button" class="meta-pill cdp rt-cdp" data-act="cdp" title="点击复制 Agent/CDP 调试地址" ${rt.running && rt.cdp_port ? "" : "hidden"}>CDP:${rt.cdp_port || acc.cdp_port || ""}</button>
            <span class="meta-pill warn rt-login" ${rt.running && rt.need_login ? "" : "hidden"}>掉登录</span>
            <span class="meta-pill attention rt-attn" ${rt.running && rt.needs_attention ? "" : "hidden"}>${rt.attention_label || "待审批"}</span>
            ${staticPills}
          </div>
          <div class="live-title rt-title" ${rt.running && rt.page_title ? "" : "hidden"}></div>
          <div class="quota-bars rt-qbars" ${hasPct || hasExtraPct ? "" : "hidden"}>
            <div class="quota-bar-wrap rt-qbar" ${hasPct ? "" : "hidden"}>
              <span class="quota-lbl">周额</span>
              <div class="quota-track"><div class="quota-fill rt-qfill ${qCls}" style="width:${hasPct ? acc.quota_used_pct : 0}%"></div></div>
              <span class="quota-extra rt-qextra">${hasPct ? `${acc.quota_used_pct}%` : ""}${acc.quota_reset_day ? ` · ${acc.quota_reset_day}` : ""}</span>
            </div>
            <div class="quota-bar-wrap rt-ebar" ${hasExtraPct ? "" : "hidden"}>
              <span class="quota-lbl">赠送</span>
              <div class="quota-track"><div class="quota-fill cyan rt-efill" style="width:${hasExtraPct ? acc.extra_used_pct : 0}%"></div></div>
              <span class="quota-extra rt-eextra">${hasExtraPct ? `${acc.extra_used_pct}%` : ""}${acc.extra_tokens_text ? ` · ${acc.extra_tokens_text}` : ""}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="card-foot">
        <div class="card-tools">
          <button type="button" class="tool-btn ${acc.pinned ? "pinned-star" : ""}" data-act="pin" title="${acc.pinned ? "取消置顶" : "置顶常用节点"}">
            <svg viewBox="0 0 24 24" fill="${acc.pinned ? "currentColor" : "none"}" stroke="currentColor" stroke-width="1.8"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
          </button>
          ${hasPrompt ? `<button type="button" class="tool-btn" data-act="prompt" title="一键复制此号派活指令 / Soul 规则"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : ""}
          <button type="button" class="tool-btn" data-act="probe" title="代理测速 & 查出口 IP">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          </button>
          <button type="button" class="tool-btn" data-act="edit" title="打开右侧检视与管理面板">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
          </button>
        </div>
        <button type="button" class="pill-btn ${rt.running ? "danger" : "primary"}" data-act="toggle">
          ${rt.running ? "关闭窗口" : "启动"}
        </button>
      </div>
    `;

    card.querySelector(".card-name").textContent = displayName;
    card.querySelector(".card-note-text").textContent = subText;
    const roleEl = card.querySelector(".meta-pill.role");
    if (roleEl) roleEl.textContent = acc.agent_role;
    const titleEl = card.querySelector(".rt-title");
    if (titleEl && rt.page_title) titleEl.textContent = `当前页：${rt.page_title}`;

    card.querySelector('[data-act="pin"]').addEventListener("click", () => togglePin(acc.id));
    card.querySelector('[data-act="quota"]').addEventListener("click", (e) => { e.stopPropagation(); cycleQuota(acc.id); });
    card.querySelector('[data-act="cdp"]').addEventListener("click", (e) => { e.stopPropagation(); copyCdp(acc.id); });
    card.querySelector('[data-act="toggle"]').addEventListener("click", () => toggleAccount(acc.id));
    card.querySelector('[data-act="probe"]').addEventListener("click", () => probeAccount(acc.id));
    card.querySelector('[data-act="edit"]').addEventListener("click", () => openEditDialog(acc));
    const promptBtn = card.querySelector('[data-act="prompt"]');
    if (promptBtn) promptBtn.addEventListener("click", () => copyAgentPrompt(acc.id));

    grid.appendChild(card);
  });
}

/* 原地增量更新运行态 DOM，彻底杜绝悬浮闪烁 */
function patchRuntimeUI() {
  renderTopStats();
  if (state.filter === "running" || state.viewMode === "table") {
    renderCards();
    return;
  }
  const cards = document.querySelectorAll(".account-card[data-card-id]");
  cards.forEach((card) => {
    const id = card.dataset.cardId;
    const rt = state.runtime[id] || { running: false, seconds: 0, mem_mb: 0 };
    card.classList.toggle("running", Boolean(rt.running));
    const dot = card.querySelector(".status-dot");
    if (dot) dot.classList.toggle("on", Boolean(rt.running));
    const timer = card.querySelector(".rt-timer");
    if (timer) {
      timer.hidden = !rt.running;
      if (rt.running) timer.textContent = fmtDuration(rt.seconds);
    }
    const mem = card.querySelector(".rt-mem");
    if (mem) {
      const showMem = Boolean(rt.running && rt.mem_mb > 0);
      mem.hidden = !showMem;
      if (showMem) mem.textContent = `${rt.mem_mb} MB`;
    }
    const cdpEl = card.querySelector(".rt-cdp");
    if (cdpEl) {
      cdpEl.hidden = !(rt.running && rt.cdp_port);
      if (rt.cdp_port) cdpEl.textContent = `CDP:${rt.cdp_port}`;
    }
    const loginEl = card.querySelector(".rt-login");
    if (loginEl) loginEl.hidden = !(rt.running && rt.need_login);
    const attnEl = card.querySelector(".rt-attn");
    if (attnEl) {
      attnEl.hidden = !(rt.running && rt.needs_attention);
      if (rt.attention_label) attnEl.textContent = rt.attention_label;
    }
    const titleEl = card.querySelector(".rt-title");
    if (titleEl) {
      titleEl.hidden = !(rt.running && rt.page_title);
      if (rt.page_title) titleEl.textContent = `当前页：${rt.page_title}`;
    }
    // 同步自动读取到的周额度与赠送额度双进度条
    const hasPct = rt.quota_used_pct !== null && rt.quota_used_pct !== undefined;
    const hasExtraPct = rt.extra_used_pct !== null && rt.extra_used_pct !== undefined;
    const qMap = { ok: ["点击同步额度", "ok"], low: ["偏低", "warn"], empty: ["已耗尽", "err"] };
    const planMap = { free: "Free", power: "Power", max: "Max" };
    const [qText, qCls] = qMap[rt.quota_status] || qMap.ok;
    const qPill = card.querySelector(".rt-quota");
    if (qPill && rt.muse_plan) {
      qPill.classList.remove("ok", "warn", "err");
      qPill.classList.add(qCls);
      qPill.textContent = hasPct
        ? `${planMap[rt.muse_plan] || "Free"} · 已用 ${rt.quota_used_pct}%`
        : `${planMap[rt.muse_plan] || "Free"} · ${qText}`;
    }
    const qBars = card.querySelector(".rt-qbars");
    if (qBars) qBars.hidden = !(hasPct || hasExtraPct);
    const qBar = card.querySelector(".rt-qbar");
    if (qBar) {
      qBar.hidden = !hasPct;
      if (hasPct) {
        const fill = qBar.querySelector(".rt-qfill");
        if (fill) {
          fill.style.width = `${rt.quota_used_pct}%`;
          fill.classList.remove("ok", "warn", "err");
          fill.classList.add(qCls);
        }
        const extra = qBar.querySelector(".rt-qextra");
        if (extra) extra.textContent = `${rt.quota_used_pct}%${rt.quota_reset_day ? ` · ${rt.quota_reset_day}` : ""}`;
      }
    }
    const eBar = card.querySelector(".rt-ebar");
    if (eBar) {
      eBar.hidden = !hasExtraPct;
      if (hasExtraPct) {
        const eFill = eBar.querySelector(".rt-efill");
        if (eFill) eFill.style.width = `${rt.extra_used_pct}%`;
        const eExtra = eBar.querySelector(".rt-eextra");
        if (eExtra) eExtra.textContent = `${rt.extra_used_pct}%${rt.extra_tokens_text ? ` · ${rt.extra_tokens_text}` : ""}`;
      }
    }
    const btn = card.querySelector('[data-act="toggle"]');
    if (btn) {
      btn.classList.toggle("danger", Boolean(rt.running));
      btn.classList.toggle("primary", !rt.running);
      btn.textContent = rt.running ? "关闭窗口" : "启动";
    }
  });
}

/* ---------- 交互动作 ---------- */

async function loadAll() {
  if (!api()) return;
  const data = await api().get_initial_state();
  state.cfg = data.cfg || {};
  state.accounts = data.accounts || [];
  state.runtime = data.runtime || {};
  applyTheme(state.cfg.theme || "dark");
  fillSettingsForm();
  renderUnpackedExtensions();
  renderFilterChips();
  renderBookmarks();
  renderTopStats();
  renderCards();
  refreshPluginTargetSelect();
}

function renderUnpackedExtensions() {
  const wrap = $("unpackedList");
  const list = (state.cfg && state.cfg.unpacked_extensions) || [];
  if (!list.length) {
    wrap.innerHTML = `<div class="empty-hint">暂未挂载本地未打包扩展</div>`;
    return;
  }
  wrap.innerHTML = "";
  list.forEach((p, idx) => {
    const row = document.createElement("div");
    row.className = "plugin-row";
    row.innerHTML = `<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12.5px"></span><button type="button" class="tool-btn danger-hover" title="移除">✕</button>`;
    row.querySelector("span").textContent = p;
    row.querySelector("button").addEventListener("click", async () => {
      const r = await api().remove_unpacked_extension(idx);
      if (r.ok) {
        state.cfg.unpacked_extensions = r.unpacked_extensions;
        renderUnpackedExtensions();
        toast(r.message);
      }
    });
    wrap.appendChild(row);
  });
}

async function pollRuntime() {
  if (!api()) return;
  try {
    state.runtime = await api().get_runtime_state();
    patchRuntimeUI();
  } catch (_) {}
}

async function togglePin(accId) {
  const res = await api().toggle_pin(accId);
  if (res.ok) {
    toast(res.message);
    await loadAll();
  }
}

async function cloneAccount(accId) {
  const res = await api().clone_account(accId);
  if (res.ok) {
    toast(res.message);
    await loadAll();
  }
}

async function importCookieSession(accId) {
  const res = await api().import_session(accId);
  if (res.cancelled) return;
  toast(res.message, res.ok ? "ok" : "err");
}

async function toggleSeed(accId) {
  const res = await api().set_seed_account(accId);
  if (res.ok) {
    state.cfg.seed_account_id = res.seed_account_id;
    toast(res.message);
    renderCards();
  }
}

async function cycleQuota(accId) {
  toast("正在后台静默读取 Muse 双额度，请稍候...");
  const res = await api().sync_muse_quota(accId);
  toast(res.message, res.ok ? "ok" : "err");
  if (res.ok) await loadAll();
}

async function copyAgentPrompt(accId) {
  const res = await api().copy_agent_prompt(accId);
  toast(res.message, res.ok ? "ok" : "err");
}

async function copyCdp(accId) {
  const res = await api().copy_cdp_endpoint(accId);
  toast(res.message, res.ok ? "ok" : "err");
}

async function toggleAccount(accId) {
  const rt = state.runtime[accId];
  let res = rt && rt.running ? await api().close_account(accId) : await api().open_account(accId, null, false);
  if (!res.ok && res.ip_conflict) {
    const force = confirm(`${res.message}\n\n继续启动可能触发平台同 IP 关联风控，是否仍要强制启动？`);
    if (!force) return;
    res = await api().open_account(accId, null, true);
  }
  if (res.ok) toast(res.message);
  else toast(res.message || "操作失败", "err");
  await pollRuntime();
}

async function probeAccount(accId) {
  state.probes[accId] = { loading: true };
  renderCards();
  const res = await api().probe_account(accId);
  state.probes[accId] = res;
  renderCards();
}

async function copyCredentials(acc) {
  const res = await api().copy_credentials(acc.id);
  toast(res.message || "已复制账密", res.ok ? "ok" : "err");
}

async function exportCookieSession(accId) {
  const res = await api().export_session(accId);
  if (res.cancelled) return;
  toast(res.message, res.ok ? "ok" : "err");
}

async function cleanAccount(accId) {
  const res = await api().clean_cache(accId);
  toast(res.message, res.ok ? "ok" : "err");
}

async function deleteAccount(accId) {
  const acc = state.accounts.find((a) => a.id === accId);
  if (!acc || !confirm(`确定彻底删除账号「${acc.name}」吗？登录态将被永久清除。`)) return;
  const res = await api().delete_account(accId);
  if (res.ok) {
    toast(res.message);
    await loadAll();
  }
}

async function broadcastUrl(rawUrl) {
  let u = (rawUrl || $("broadcastUrl").value || "").trim();
  if (!u) return;
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  const res = await api().broadcast_url(u);
  toast(res.message, res.ok ? "ok" : "err");
  if (!rawUrl) $("broadcastUrl").value = "";
}

/* ---------- 原生 <dialog> 编辑弹窗 ---------- */

function renderSwatches() {
  const wrap = $("colorSwatches");
  wrap.innerHTML = "";
  PALETTE.forEach((c) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `swatch ${state.selectedColor === c ? "selected" : ""}`;
    b.style.background = c;
    b.addEventListener("click", () => {
      state.selectedColor = c;
      renderSwatches();
    });
    wrap.appendChild(b);
  });
}

function openEditDialog(acc) {
  state.editingId = acc ? acc.id : null;
  $("dlgTitle").textContent = acc ? `编辑账号 · ${acc.name}` : "新建隔离账号";
  $("accName").value = acc ? acc.name : "";
  $("accGroup").value = acc ? (acc.group || "默认分组") : "默认分组";
  $("accBrowser").value = acc ? (acc.browser || "chrome") : "chrome";
  $("accVipExpire").value = acc ? (acc.vip_expire || "") : "";
  $("accNote").value = acc ? (acc.note || "") : "";
  $("accMusePlan").value = acc ? (acc.muse_plan || "free") : "free";
  $("accQuotaStatus").value = acc ? (acc.quota_status || "ok") : "ok";
  $("accResetDay").value = acc ? (acc.quota_reset_day || "") : "";
  $("accAgentRole").value = acc ? (acc.agent_role || "") : "";
  $("accAgentPrompts").value = acc ? (acc.agent_prompts || "") : "";
  $("accLoginEmail").value = acc ? (acc.login_email || "") : "";
  $("accLoginPass").value = acc ? (acc.login_password || "") : "";
  $("accCustomUrl").value = acc ? (acc.custom_url || "") : "";
  $("accWinSize").value = acc ? (acc.window_size || "") : "";
  state.selectedColor = (acc && acc.color) || PALETTE[state.accounts.length % PALETTE.length];

  const p = acc ? acc.proxy : "";
  if (p === "direct") {
    $("accProxyMode").value = "direct";
  } else if (p && typeof p === "object") {
    $("accProxyMode").value = "custom";
    $("accProxyType").value = p.type || "http";
    $("accProxyHost").value = p.host || "127.0.0.1";
    $("accProxyPort").value = p.port || 7892;
    $("accProxyUser").value = p.username || "";
    $("accProxyPass").value = p.password || "";
  } else {
    $("accProxyMode").value = "global";
  }
  $("customProxyFields").hidden = $("accProxyMode").value !== "custom";
  const hasSeed = Boolean(state.cfg && state.cfg.seed_account_id);
  $("seedInheritRow").hidden = Boolean(acc) || !hasSeed;
  $("accInheritSeed").checked = hasSeed;
  const qa = $("drawerQuickActions");
  qa.hidden = !acc;
  if (acc) {
    const isSeed = state.cfg && state.cfg.seed_account_id === acc.id;
    $("actDrawerSeed").textContent = isSeed ? "取消默认插件模板" : "设为默认插件模板";
    $("actDrawerCopyCred").hidden = !(acc.login_email || acc.login_password);
    $("actDrawerCopyPrompt").hidden = !(acc.agent_prompts && acc.agent_prompts.trim());
  }
  renderSwatches();
  $("accountDialog").showModal();
}

async function submitAccountDialog() {
  const pmode = $("accProxyMode").value;
  let proxy = "";
  if (pmode === "direct") proxy = "direct";
  else if (pmode === "custom") {
    proxy = {
      type: $("accProxyType").value,
      host: $("accProxyHost").value.trim() || "127.0.0.1",
      port: Number($("accProxyPort").value) || 7892,
      username: $("accProxyUser").value.trim(),
      password: $("accProxyPass").value
    };
  }
  const payload = {
    id: state.editingId,
    name: $("accName").value.trim(),
    group: $("accGroup").value.trim() || "默认分组",
    browser: $("accBrowser").value,
    vip_expire: $("accVipExpire").value.trim(),
    note: $("accNote").value.trim(),
    muse_plan: $("accMusePlan").value,
    quota_status: $("accQuotaStatus").value,
    quota_reset_day: $("accResetDay").value,
    agent_role: $("accAgentRole").value.trim(),
    agent_prompts: $("accAgentPrompts").value,
    login_email: $("accLoginEmail").value.trim(),
    login_password: $("accLoginPass").value,
    custom_url: $("accCustomUrl").value.trim(),
    window_size: $("accWinSize").value,
    proxy,
    color: state.selectedColor,
    inherit_seed: !state.editingId && $("accInheritSeed").checked
  };
  const res = await api().save_account(payload);
  toast(res.message, res.ok ? "ok" : "err");
  await loadAll();
}

/* ---------- 诊断 & 设置 ---------- */

async function runHealthCheck() {
  $("btnRunHealth").disabled = true;
  $("diagList").innerHTML = `<div class="empty-hint">正在检测本地浏览器与代理连通性...</div>`;
  $("portChips").innerHTML = "";
  const res = await api().run_health_check();
  $("btnRunHealth").disabled = false;

  $("diagList").innerHTML = "";
  (res.items || []).forEach((it) => {
    const row = document.createElement("div");
    row.className = "diag-row";
    row.innerHTML = `<span class="diag-tag ${it.status}"></span><span class="diag-detail"></span>`;
    row.querySelector(".diag-tag").textContent = it.title;
    row.querySelector(".diag-detail").textContent = it.detail;
    $("diagList").appendChild(row);
  });

  const ports = res.ports || [];
  $("portScanStatus").textContent = ports.length
    ? `探测到活跃本地代理端口：${ports.join(", ")}（点击一键应用）`
    : "未扫描到常见本地代理端口（TUN 模式可忽略）";
  ports.forEach((p) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = `使用端口 ${p}`;
    btn.addEventListener("click", async () => {
      const r = await api().apply_port(p);
      toast(r.message);
      await loadAll();
    });
    $("portChips").appendChild(btn);
  });
}

function fillSettingsForm() {
  const c = state.cfg;
  $("cfgProxyMode").value = c.proxy_mode || "local";
  $("cfgProxyType").value = c.proxy_type || "http";
  $("cfgProxyHost").value = c.proxy_host || "127.0.0.1";
  $("cfgProxyPort").value = c.proxy_port || 7892;
  $("cfgHomeUrl").value = c.home_url || "https://muse.ai";
  $("cfgBatchInterval").value = c.batch_interval || 1.5;
  $("cfgWebrtc").checked = c.webrtc_protect !== false;
  $("cfgLangEn").checked = c.lang_en !== false;
  $("cfgAntiBot").checked = c.anti_fingerprint !== false;
  $("cfgCdpEnabled").checked = c.cdp_enabled !== false;
  $("cfgChromePath").value = c.chrome_path || "";
  $("cfgEdgePath").value = c.edge_path || "";
}

function refreshPluginTargetSelect() {
  const sel = $("pluginTargetAcc");
  sel.innerHTML = "";
  state.accounts.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.id;
    opt.textContent = a.name;
    sel.appendChild(opt);
  });
}

async function scanPlugins() {
  const list = await api().scan_plugins();
  state.plugins = list || [];
  const wrap = $("pluginList");
  if (!state.plugins.length) {
    wrap.innerHTML = `<div class="empty-hint">未在默认 Chrome 目录扫描到扩展</div>`;
    return;
  }
  wrap.innerHTML = "";
  state.plugins.forEach((e) => {
    const row = document.createElement("label");
    row.className = "plugin-row";
    row.innerHTML = `<span><b></b> <small style="color:var(--text-3)"></small></span><input type="checkbox" value="${e.id}" />`;
    row.querySelector("b").textContent = e.name;
    row.querySelector("small").textContent = e.version ? `v${e.version}` : e.id.slice(0, 12);
    wrap.appendChild(row);
  });
}

/* ---------- 事件绑定与初始化 ---------- */

function bindEvents() {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
      btn.classList.add("active");
      $(`view-${btn.dataset.view}`).classList.add("active");
    });
  });

  $("btnThemeToggle").addEventListener("click", async () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "light" ? "dark" : "light";
    applyTheme(next);
    if (api()) await api().set_theme(next);
  });

  $("searchInput").addEventListener("input", (e) => {
    state.search = e.target.value;
    renderCards();
  });

  $("btnNewAccount").addEventListener("click", () => openEditDialog(null));
  $("btnNextIdle").addEventListener("click", async () => {
    const r = await api().open_next_available();
    toast(r.message, r.ok ? "ok" : "err");
    await pollRuntime();
  });
  $("btnViewMode").addEventListener("click", () => {
    state.viewMode = state.viewMode === "grid" ? "table" : "grid";
    renderCards();
  });
  $("btnPrivacyLock").addEventListener("click", async () => {
    if (!state.privacyLocked) {
      if (!state.cfg.privacy_pin) {
        const pin = prompt("首次使用隐私隐身锁，请设置解锁 PIN 码（留空则无需密码直接解锁）：");
        if (pin === null) return;
        await api().set_privacy_pin(pin);
        state.cfg.privacy_pin = pin.trim();
      }
      state.privacyLocked = true;
      document.body.classList.add("privacy-locked");
      $("btnPrivacyLock").textContent = "已锁定";
      renderCards();
      toast("已开启隐私隐身模式");
    } else {
      if (state.cfg.privacy_pin) {
        const pin = prompt("请输入隐私解锁 PIN 码：");
        if (pin === null) return;
        const r = await api().verify_privacy_pin(pin);
        if (!r.ok) return toast(r.message, "err");
      }
      state.privacyLocked = false;
      document.body.classList.remove("privacy-locked");
      $("btnPrivacyLock").textContent = "隐私锁";
      renderCards();
      toast("已解除隐私隐身模式");
    }
  });
  $("btnAddUnpacked").addEventListener("click", async () => {
    const r = await api().add_unpacked_extension();
    if (!r.cancelled) {
      toast(r.message, r.ok ? "ok" : "err");
      if (r.ok) {
        state.cfg.unpacked_extensions = r.unpacked_extensions;
        renderUnpackedExtensions();
      }
    }
  });
  $("btnTile").addEventListener("click", async () => {
    const r = await api().tile_windows();
    toast(r.message, r.ok ? "ok" : "err");
  });
  $("btnOpenAll").addEventListener("click", async () => {
    const r = await api().open_all();
    toast(r.message, r.ok ? "ok" : "err");
  });
  $("btnCloseAll").addEventListener("click", async () => {
    const r = await api().close_all();
    toast(r.message, r.ok ? "ok" : "err");
  });

  $("btnBroadcast").addEventListener("click", () => broadcastUrl());
  $("broadcastUrl").addEventListener("keydown", (e) => {
    if (e.key === "Enter") broadcastUrl();
  });

  $("btnRunHealth").addEventListener("click", runHealthCheck);
  $("btnOpenIpSite").addEventListener("click", () => api().open_external("https://ipinfo.io"));

  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const [t, h, p] = btn.dataset.preset.split(",");
      $("cfgProxyMode").value = "local";
      $("cfgProxyType").value = t;
      $("cfgProxyHost").value = h;
      $("cfgProxyPort").value = p;
    });
  });

  $("btnSaveSettings").addEventListener("click", async () => {
    const res = await api().save_settings({
      proxy_mode: $("cfgProxyMode").value,
      proxy_type: $("cfgProxyType").value,
      proxy_host: $("cfgProxyHost").value.trim() || "127.0.0.1",
      proxy_port: Number($("cfgProxyPort").value) || 7892,
      home_url: $("cfgHomeUrl").value.trim() || "https://muse.ai",
      batch_interval: Number($("cfgBatchInterval").value) || 1.5,
      webrtc_protect: $("cfgWebrtc").checked,
      lang_en: $("cfgLangEn").checked,
      anti_fingerprint: $("cfgAntiBot").checked,
      cdp_enabled: $("cfgCdpEnabled").checked,
      chrome_path: $("cfgChromePath").value.trim(),
      edge_path: $("cfgEdgePath").value.trim()
    });
    toast(res.message, res.ok ? "ok" : "err");
    await loadAll();
  });

  $("btnExportLight").addEventListener("click", async () => {
    const r = await api().export_backup(false);
    if (!r.cancelled) toast(r.message, r.ok ? "ok" : "err");
  });
  $("btnExportFull").addEventListener("click", async () => {
    const r = await api().export_backup(true);
    if (!r.cancelled) toast(r.message, r.ok ? "ok" : "err");
  });
  $("btnImportBackup").addEventListener("click", async () => {
    const r = await api().import_backup();
    if (!r.cancelled) {
      toast(r.message, r.ok ? "ok" : "err");
      if (r.ok) await loadAll();
    }
  });

  $("btnScanPlugins").addEventListener("click", scanPlugins);
  $("btnInstallPlugins").addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("#pluginList input:checked")].map((i) => i.value);
    if (!ids.length) return toast("请先勾选要同步的扩展", "err");
    const r = await api().install_plugins($("pluginTargetAcc").value, ids);
    toast(r.message, r.ok ? "ok" : "err");
  });

  // Dialog 事件与 closedby 兼容兜底
  const dlg = $("accountDialog");
  $("accProxyMode").addEventListener("change", () => {
    $("customProxyFields").hidden = $("accProxyMode").value !== "custom";
  });
  $("btnDlgClose").addEventListener("click", () => dlg.close(""));
  $("btnDlgCancel").addEventListener("click", () => dlg.close(""));
  $("actDrawerSeed").addEventListener("click", async () => {
    if (!state.editingId) return;
    await toggleSeed(state.editingId);
    const isSeed = state.cfg && state.cfg.seed_account_id === state.editingId;
    $("actDrawerSeed").textContent = isSeed ? "取消默认插件模板" : "设为默认插件模板";
  });
  $("actDrawerCopyPrompt").addEventListener("click", async () => {
    if (state.editingId) await copyAgentPrompt(state.editingId);
  });
  $("actDrawerCopyCred").addEventListener("click", async () => {
    const acc = state.accounts.find((a) => a.id === state.editingId);
    if (acc) await copyCredentials(acc);
  });
  $("actDrawerCookieExp").addEventListener("click", () => state.editingId && exportCookieSession(state.editingId));
  $("actDrawerCookieImp").addEventListener("click", () => state.editingId && importCookieSession(state.editingId));
  $("actDrawerClone").addEventListener("click", async () => {
    if (!state.editingId) return;
    dlg.close("");
    await cloneAccount(state.editingId);
  });
  $("actDrawerClean").addEventListener("click", () => state.editingId && cleanAccount(state.editingId));
  $("actDrawerDelete").addEventListener("click", async () => {
    if (!state.editingId) return;
    const id = state.editingId;
    dlg.close("");
    await deleteAccount(id);
  });
  dlg.addEventListener("close", async () => {
    const rv = dlg.returnValue;
    dlg.returnValue = "";
    if (rv === "save") await submitAccountDialog();
  });

  if (!("closedBy" in HTMLDialogElement.prototype)) {
    const outside = (e) => {
      const r = dlg.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    let downOut = false;
    dlg.addEventListener("pointerdown", (e) => { downOut = outside(e); });
    dlg.addEventListener("pointerup", (e) => { if (downOut && outside(e)) dlg.close(""); });
  }
}

bindEvents();
window.addEventListener("pywebviewready", async () => {
  await loadAll();
  setInterval(pollRuntime, 1500);
});
