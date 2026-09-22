/* megamendung web client - talks JSON-RPC over WebSocket to the paired agent. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const CODE_KEY = "mg_pair_code";
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    code: localStorage.getItem(CODE_KEY) || "",
    pairId: "",
    ws: null,
    agentOnline: false,
    agentName: null,
    busy: new Set(),
    accounts: [],
    wssUrl: "",
  };

  // ---- toast ------------------------------------------------------------
  function toast(msg, kind = "info") {
    const el = document.createElement("div");
    const color = { info: "bg-slate-800 border-slate-700", ok: "bg-teal-900 border-teal-700", err: "bg-rose-900 border-rose-700" }[kind] || "bg-slate-800 border-slate-700";
    const text = { ok: "text-teal-200", err: "text-rose-200", info: "text-slate-200" }[kind] || "text-slate-200";
    el.className = `border ${color} rounded-lg px-4 py-2 text-sm shadow-lg ${text}`;
    el.textContent = msg;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), kind === "err" ? 8000 : 5000);
  }

  // ---- RPC --------------------------------------------------------------
  const pending = new Map();
  let seq = 1;

  function call(method, params = {}, { timeout = 300000 } = {}) {
    return new Promise((resolve, reject) => {
      if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
        return reject(new Error("no device connection"));
      }
      const id = "c" + seq++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`timeout (${method})`));
      }, timeout);
      pending.set(id, { resolve, reject, timer, method });
      state.busy.add(method);
      state.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async function rpc(method, params = {}, opts) {
    try {
      const res = await call(method, params, opts);
      return res;
    } catch (err) {
      toast(String(err.err || err.message || err), "err");
      throw err;
    } finally {
      state.busy.delete(method);
      const btn = document.querySelector(`[data-busy="${method}"]`);
      if (btn) { btn.disabled = false; btn.textContent = btn.dataset.busyText || btn.textContent; }
    }
  }

  // ---- websocket --------------------------------------------------------
  function connect() {
    if (!state.code) return;
    const { pairId } = splitCode(state.code);
    state.pairId = pairId;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    state.wssUrl = `${proto}//${location.host}/relay/${pairId}`;
    const ws = new WebSocket(state.wssUrl);
    state.ws = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", role: "client", code: state.code }));
    };
    ws.onmessage = (ev) => {
      let f;
      try { f = JSON.parse(ev.data); } catch { return; }
      if (f.type === "hello" && f.reply) {
        setAgent(f.agent_online, f.agent);
        showApp();
        if (!f.agent_online) toast("device offline - machine not connected", "info");
        refreshAll();
        return;
      }
      if (f.type === "event" && f.name === "agent_status") {
        setAgent(f.online, f.agent);
        toast(f.online ? `device online (${f.agent})` : "device offline", f.online ? "ok" : "err");
        return;
      }
      if (f.type === "error") {
        toast("relay error: " + f.error, "err");
        return;
      }
      if (f.type === "reply" && f.id) {
        const job = pending.get(f.id);
        if (!job) return;
        clearTimeout(job.timer);
        pending.delete(f.id);
        if (f.ok) job.resolve(f.result); else job.reject({ err: f.error });
        return;
      }
      if (f.type === "reply") return;
    };
    ws.onclose = () => {
      setAgent(false, null);
      state.ws = null;
      if (state.code) {
        toast("disconnected - reconnecting…", "info");
        setTimeout(connect, 5000);
      }
    };
    ws.onerror = () => ws.close();
  }

  function setAgent(online, name) {
    state.agentOnline = !!online;
    state.agentName = name || null;
    $("device-dot").className = "w-2.5 h-2.5 rounded-full " + (online ? "bg-teal-400" : "bg-slate-600");
    $("device-status").textContent = online ? "online" : "offline";
    $("device-status").className = online ? "text-teal-400" : "text-slate-400";
    $("device-name").textContent = name || "—";
  }

  function splitCode(code) {
    const i = code.indexOf(":");
    if (i <= 0) throw new Error("invalid pairing code");
    return { pairId: code.slice(0, i), secret: code.slice(i + 1) };
  }

  // ---- screens ----------------------------------------------------------
  function showPair() {
    $("app").classList.add("hidden");
    $("pair-screen").classList.remove("hidden");
  }
  function showApp() {
    $("pair-screen").classList.add("hidden");
    $("app").classList.remove("hidden");
  }

  // ---- data -------------------------------------------------------------
  async function loadAccounts() {
    try {
      state.accounts = await rpc("accounts.list");
    } catch { state.accounts = []; }
    return state.accounts;
  }

  async function dashStats() {
    let rows;
    try { rows = await rpc("df"); } catch { rows = []; }
    $("dash-cards").innerHTML = [
      card("Accounts", String(state.accounts.filter((a) => a.managed).length), "teal"),
      card("Free storage", fmtBytes(rows.reduce((n, r) => n + (r.free || 0), 0)), "sky"),
      card("Used", fmtBytes(rows.reduce((n, r) => n + (r.used || 0), 0)), "amber"),
    ].join("");
    renderDashboardAccounts(state.accounts, rows);
  }

  function card(label, value, color) {
    const map = { teal: "text-teal-400", sky: "text-sky-400", amber: "text-amber-400" };
    return `<div class="bg-slate-900 border border-slate-800 rounded-xl p-4">
      <div class="text-xs text-slate-500 uppercase">${esc(label)}</div>
      <div class="text-2xl font-semibold ${map[color]}">${esc(value)}</div></div>`;
  }

  function fmtBytes(n) {
    if (!Number.isFinite(n) || n < 0) return "-";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let v = n, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? String(Math.round(v)) : v.toFixed(1)) + " " + units[i];
  }

  function statusBadge(s) {
    if (!s) return '<span class="text-slate-500">-</span>';
    if (s === "ok") return '<span class="text-teal-400">ok</span>';
    return `<span class="text-rose-400">${esc(s)}</span>`;
  }

  function renderDashboardAccounts(accounts, rows) {
    const byName = Object.fromEntries((rows || []).map((r) => [r.name, r]));
    if (!accounts.length) {
      $("dash-accounts").innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center text-slate-500">no accounts yet - pair a device and import/create accounts</td></tr>';
      return;
    }
    $("dash-accounts").innerHTML = accounts.map((a) => {
      const d = byName[a.name];
      return `<tr class="border-t border-slate-800">
        <td class="px-4 py-2 font-medium">${esc(a.name)}</td>
        <td class="px-4 py-2 text-slate-400">${esc(a.email)}</td>
        <td class="px-4 py-2">${a.verified ? '<span class="text-teal-400">yes</span>' : '<span class="text-amber-400">no</span>'}</td>
        <td class="px-4 py-2 text-slate-500">${esc(a.last_login || "-")}</td>
        <td class="px-4 py-2">${statusBadge(a.last_status)} ${d ? `<span class="text-slate-500 text-xs">${fmtBytes(d.free)} free</span>` : ""}</td></tr>`;
    }).join("");
  }

  async function refreshAll() {
    await Promise.all([loadAccounts(), dashStats(), loadFiles(), loadSync(), loadAccountsPanel()]);
  }

  async function refreshLogins() {
    const btn = $("refresh-all");
    btn.disabled = true; btn.dataset.busyText = btn.textContent; btn.textContent = "…";
    try {
      const res = await rpc("accounts.refresh");
      toast(`refresh: ${res.ok.length} ok, ${res.failed.length} failed`, res.failed.length ? "err" : "ok");
      for (const [name, err] of Object.entries(res.errors || {})) toast(`${name}: ${err}`, "err");
    } finally {
      btn.disabled = false; btn.textContent = btn.dataset.busyText;
    }
    await loadAccounts();
    await dashStats();
  }

  // ---- files ------------------------------------------------------------
  let filesSel = null;
  async function loadFiles(account, path) {
    const accs = state.accounts.filter((a) => a.managed);
    $("files-account").innerHTML = accs.map((a) => `<option value="${esc(a.name)}" ${a.name === account ? "selected" : ""}>${esc(a.name)}</option>`).join("") || '<option value="">no accounts</option>';
    account = account || $("files-account").value;
    path = path ?? ($("files-path").value || "/");
    if (!account) { $("files-body").innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-slate-500">no accounts</td></tr>'; return; }
    $("files-path").value = path;
    let entries;
    try { entries = await rpc("ls", { name: account, path }); } catch { entries = []; }
    filesSel = account;
    $("files-body").innerHTML = entries.map((e) => {
      const isDir = e.IsDir;
      const icon = isDir ? "📁" : "📄";
      const row = `<tr class="border-t border-slate-800 cursor-pointer ${isDir ? "hover:bg-slate-800/50" : "hover:bg-slate-800/30"}" data-p="${esc(e.Name)}" data-d="${isDir ? "1" : ""}">
        <td class="px-2 py-2 text-center">${icon}</td>
        <td class="px-2 py-2">${esc(e.Name)}</td>
        <td class="px-4 py-2 text-right text-slate-400">${isDir ? "" : fmtBytes(e.Size)}</td>
        <td class="px-4 py-2 text-slate-500">${esc((e.ModTime || e.modTime || "").slice(0, 16))}</td></tr>`;
      return row;
    }).join("") || '<tr><td colspan="4" class="px-4 py-6 text-center text-slate-500">empty</td></tr>';

    document.querySelectorAll("#files-body tr[data-p]").forEach((tr) => {
      tr.addEventListener("click", () => {
        const name = tr.dataset.p;
        const isDir = tr.dataset.d === "1";
        if (isDir) {
          const base = $("files-path").value.replace(/\/+$/, "");
          loadFiles(null, base + "/" + name);
        } else {
          document.querySelectorAll("#files-body tr").forEach((t) => t.classList.remove("bg-slate-800/70"));
          tr.classList.add("bg-slate-800/70");
        }
      });
    });
  }

  async function filesAction(what) {
    const account = filesSel || $("files-account").value;
    const path = $("files-path").value;
    const sel = document.querySelector("#files-body tr.bg-slate-800/70");
    const name = sel ? sel.dataset.p : null;
    if (what === "mkdir") {
      const dir = prompt("New folder name:", "new-folder");
      if (!dir) return;
      await rpc("mkdir", { name: account, path: path.replace(/\/+$/, "") + "/" + dir });
      toast("created", "ok");
    } else if (what === "rm") {
      if (!name) { toast("select a file/dir first", "err"); return; }
      const target = path.replace(/\/+$/, "") + "/" + name;
      if (!confirm(`Remove ${target}?`)) return;
      await rpc("rm", { name: account, path: target, recursive: true });
      toast("removed", "ok");
    } else if (what === "upload") {
      const local = prompt("Full path on the device to upload:", "/data/");
      if (!local) return;
      await rpc("upload", { name: account, local, path });
      toast("uploaded", "ok");
    } else if (what === "download") {
      if (!name) { toast("select a file first", "err"); return; }
      const local = prompt("Destination full path on the device:", "/data/" + name);
      if (!local) return;
      await rpc("download", { name: account, path: path.replace(/\/+$/, "") + "/" + name, local });
      toast("downloaded", "ok");
    }
    await loadFiles(null, path);
  }

  // ---- sync -------------------------------------------------------------
  function renderSync(cfg) {
    const jobs = cfg.jobs || [];
    $("sync-body").innerHTML = jobs.map((j) => `<tr class="border-t border-slate-800">
      <td class="px-4 py-2 font-medium">${esc(j.name)}</td>
      <td class="px-4 py-2">${esc(j.account)}</td>
      <td class="px-4 py-2 text-slate-400 font-mono text-xs">${esc(j.local)}</td>
      <td class="px-4 py-2 text-slate-400 font-mono text-xs">${esc(j.remote)}</td>
      <td class="px-4 py-2">${esc(j.mode)}</td>
      <td class="px-4 py-2 text-right whitespace-nowrap">
        <button data-run="${esc(j.name)}" class="run-btn text-teal-400 hover:text-teal-300 text-sm mr-2">run</button>
        <button data-rm-sync="${esc(j.name)}" class="text-rose-400 hover:text-rose-300 text-sm">remove</button>
      </td></tr>`).join("") || '<tr><td colspan="6" class="px-4 py-6 text-center text-slate-500">no sync jobs</td></tr>';
    document.querySelectorAll("[data-run]").forEach((b) => b.addEventListener("click", async () => {
      const btn = b; btn.disabled = true;
      toast(`running ${b.dataset.run}…`, "info");
      const res = await rpc("sync.jobs", { jobs: [b.dataset.run] }).catch(() => null);
      if (res && res[0]) toast(res[0].ok ? `${res[0].job} ok` : `${res[0].job}: ${res[0].error}`, res[0].ok ? "ok" : "err");
      btn.disabled = false;
    }));
    document.querySelectorAll("[data-rm-sync]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`Remove sync job ${b.dataset.rmSync}?`)) return;
      await rpc("sync.remove", { name: b.dataset.rmSync });
      toast("removed", "ok");
      loadSync();
    }));
  }

  async function loadSync() {
    try {
      const cfg = await rpc("config.show");
      renderSync(cfg);
    } catch { }
  }

  // ---- accounts panel ---------------------------------------------------
  function renderAccountsPanel() {
    $("acc-body").innerHTML = state.accounts.map((a) => `<tr class="border-t border-slate-800">
      <td class="px-4 py-2 font-medium">${esc(a.name)}</td>
      <td class="px-4 py-2 text-slate-400">${esc(a.email)}</td>
      <td class="px-4 py-2 text-right">
        <button data-rm-acc="${esc(a.name)}" class="text-rose-400 hover:text-rose-300 text-sm">remove</button>
      </td></tr>`).join("") || '<tr><td colspan="3" class="px-4 py-6 text-center text-slate-500">none</td></tr>';
    document.querySelectorAll("[data-rm-acc]").forEach((b) => b.addEventListener("click", async () => {
      if (!confirm(`Remove account ${b.dataset.rmAcc} from the registry?`)) return;
      await rpc("accounts.remove", { name: b.dataset.rmAcc });
      toast("removed", "ok");
      refreshAll();
    }));
  }

  async function loadAccountsPanel() {
    renderAccountsPanel();
    try {
      const cfg = await rpc("config.show");
      $("acc-base-email").placeholder = cfg.settings.base_email || "base email (you@gmail.com)";
      $("acc-base-name").placeholder = cfg.settings.base_name || "base name (mega)";
      $("acc-base-email").value = cfg.settings.base_email || "";
      $("acc-base-name").value = cfg.settings.base_name || "";
    } catch { }
  }

  // ---- screens wiring ---------------------------------------------------
  function setupEvents() {
    document.querySelectorAll(".nav-btn").forEach((b) => {
      b.addEventListener("click", () => {
        document.querySelectorAll(".nav-btn").forEach((x) => x.classList.remove("bg-slate-800", "text-white"));
        b.classList.add("bg-slate-800", "text-white");
        document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
        $("panel-" + b.dataset.panel).classList.remove("hidden");
      });
    });

    $("pair-btn").addEventListener("click", pairSubmit);
    $("pair-input").addEventListener("keydown", (e) => { if (e.key === "Enter") pairSubmit(); });

    $("refresh-all").addEventListener("click", refreshLogins);

    $("files-go").addEventListener("click", () => loadFiles(null, null));
    $("files-account").addEventListener("change", () => loadFiles(null, "/"));
    $("files-mkdir").addEventListener("click", () => filesAction("mkdir"));
    $("files-rm").addEventListener("click", () => filesAction("rm"));
    $("files-upload").addEventListener("click", () => filesAction("upload"));
    $("files-download").addEventListener("click", () => filesAction("download"));

    $("sync-run-all").addEventListener("click", async () => {
      toast("running all jobs…", "info");
      const res = await rpc("sync.jobs", {}).catch(() => []);
      toast(`done: ${res.filter((r) => r.ok).length} ok, ${res.filter((r) => !r.ok).length} failed`, res.some((r) => !r.ok) ? "err" : "ok");
      loadSync();
    });
    $("sync-add").addEventListener("click", async () => {
      try {
        await rpc("sync.add", {
          name: $("sync-name").value.trim(), account: $("sync-account").value.trim(),
          local: $("sync-local").value.trim(), remote: $("sync-remote").value.trim(),
          mode: $("sync-mode").value,
        });
        toast("job added", "ok");
        $("sync-name").value = ""; $("sync-local").value = ""; $("sync-remote").value = "";
        loadSync();
      } catch { }
    });

    $("acc-save-settings").addEventListener("click", async () => {
      const r = await rpc("config.set", {
        base_email: $("acc-base-email").value.trim() || undefined,
        base_name: $("acc-base-name").value.trim() || undefined,
      });
      $("acc-out").textContent = JSON.stringify(r.settings, null, 2);
      toast("settings saved", "ok");
    });
    $("acc-create").addEventListener("click", async () => {
      const r = await rpc("accounts.create", {
        count: parseInt($("acc-count").value, 10) || 1,
        base: $("acc-base").value.trim() || undefined,
      });
      $("acc-out").textContent = `created:\n${(r.emails || []).map((e) => "  " + e).join("\n")}\n\nVerify each with the #confirm link from MEGA's email.`;
      toast("created - check your inbox", "ok");
    });
    $("acc-verify").addEventListener("click", async () => {
      const r = await rpc("accounts.verify", { name: $("acc-vname").value.trim(), link: $("acc-vlink").value.trim() });
      $("acc-out").textContent = `verified ${r.name} (${r.email})`;
      toast("verified", "ok");
      refreshAll();
    });

    $("comp-run").addEventListener("click", async () => {
      const path = $("comp-path").value.trim();
      if (!path) { toast("enter a directory path", "err"); return; }
      const out = $("comp-out");
      out.textContent = "running… (this can take a while)";
      const params = {
        path, images: $("comp-images").checked, videos: $("comp-videos").checked,
        force: $("comp-force").checked,
        quality: parseInt($("comp-quality").value, 10) || 85,
        max_dimension: $("comp-maxdim").value ? parseInt($("comp-maxdim").value, 10) : undefined,
        preset: $("comp-preset").value.trim() || "fast",
        crf: parseInt($("comp-crf").value, 10) || 23,
      };
      try {
        const r = await rpc("compress", params, { timeout: 3600000 });
        const lines = [];
        if (r.images) lines.push(`images: ${r.images.images_done} done, ${r.images.skipped} skipped, ${r.images.images_failed} failed - saved ${fmtBytes(r.images.saved_bytes)}`);
        if (r.videos) lines.push(`videos: ${r.videos.videos_done} done, ${r.videos.skipped} skipped, ${r.videos.videos_failed} failed - saved ${fmtBytes(r.videos.saved_bytes)}`);
        out.textContent = lines.join("\n") || "nothing to do";
        toast("compression finished", "ok");
      } catch (err) {
        out.textContent = "failed: " + String(err.err || err.message || err);
      }
    });

    $("unpair").addEventListener("click", () => {
      localStorage.removeItem(CODE_KEY);
      state.code = "";
      if (state.ws) state.ws.close();
      showPair();
    });
  }

  function pairSubmit() {
    const code = $("pair-input").value.trim();
    try { splitCode(code); } catch (err) { $("pair-error").textContent = String(err.message || err); return; }
    $("pair-error").textContent = "";
    state.code = code;
    localStorage.setItem(CODE_KEY, code);
    connect();
  }

  // ---- boot -------------------------------------------------------------
  setupEvents();
  if (state.code) {
    try { splitCode(state.code); connect(); } catch { showPair(); }
  } else {
    showPair();
  }
})();