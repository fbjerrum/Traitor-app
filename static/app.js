"use strict";
const app = document.getElementById("app");
let token = localStorage.getItem("traitors_token") || "";
let newCode = null, authTab = "login";
let state = null, lastKey = "", picked = null, nightKind = "murder", timerAt = 0, stepKey = "";

const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

async function api(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(Object.assign({token}, body || {}))});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || "Error");
  return j;
}
function toast(m) {
  const t = document.getElementById("toast");
  t.textContent = m; t.className = "show";
  setTimeout(() => t.className = "", 2500);
}
async function call(path, body) {
  try {
    const j = await api(path, body);
    if (j.phase || j.home) setState(j);
    return j;
  } catch (e) { toast(e.message); }
}
const action = (a, data) => call("/api/action", {action: a, data: data || {}});

function setState(s) {
  state = s;
  timerAt = Date.now() + (s.remaining == null ? 0 : s.remaining * 1000);
  const step = s.phase + ":" + s.round;
  if (step !== stepKey) { stepKey = step; picked = null; }
  const key = JSON.stringify(Object.assign({}, s, {remaining: 0})) + "|" + picked + nightKind;
  if (key !== lastKey) { lastKey = key; render(); }
}

async function poll() {
  if (token && !newCode) {
    try { setState(await api("/api/state")); }
    catch (e) {
      if (/Not logged in/i.test(e.message)) resetSession();
    }
  }
  setTimeout(poll, 1000);
}
function resetSession() {
  localStorage.removeItem("traitors_token"); token = ""; state = null; lastKey = ""; render();
}
async function logout() {
  try { await api("/api/logout"); } catch (e) {}
  resetSession();
}
setInterval(() => {
  const el = document.getElementById("timer");
  if (el && state && state.remaining != null) {
    const s = Math.max(0, Math.ceil((timerAt - Date.now()) / 1000));
    el.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }
}, 250);

function render() {
  if (!token) return renderAuth();
  if (newCode) return renderNewCode();
  if (!state) return;
  if (state.home) return renderHome();
  const fn = {lobby, role: rolePhase, night, offer, dawn: dawnPhase, discussion, vote, result, decision, end}[state.phase];
  app.innerHTML = fn();
  bind();
}

function renderAuth() {
  const reg = authTab === "register";
  app.innerHTML = `<h1>Traitors</h1><div class="sub">Trust no one.</div>
  <div class="row"><button class="${reg ? "sec" : ""}" data-tab="login">Log in</button><button class="${reg ? "" : "sec"}" data-tab="register">New player</button></div>
  <div class="card"><label>Username</label><input id="user" maxlength="20" autocomplete="username" autocapitalize="off">
  ${reg ? '<div class="dim" style="margin-top:8px">You will get a personal login code. Save it to log in again.</div>'
        : '<label>Login code</label><input id="lcode" inputmode="numeric" maxlength="7" autocomplete="current-password">'}
  <button id="auth">${reg ? "Create account" : "Log in"}</button></div>`;
  app.querySelectorAll("[data-tab]").forEach(el => el.onclick = () => { authTab = el.dataset.tab; render(); });
  const submit = async () => {
    const name = document.getElementById("user").value;
    try {
      if (reg) {
        const r = await api("/api/register", {name});
        token = r.token; newCode = r.code; state = null;
        localStorage.setItem("traitors_token", token);
        render();
      } else {
        const r = await api("/api/login", {name, code: document.getElementById("lcode").value});
        token = r.token; localStorage.setItem("traitors_token", token);
        setState(await api("/api/state"));
      }
    } catch (e) { toast(e.message); }
  };
  document.getElementById("auth").onclick = submit;
}

function renderNewCode() {
  app.innerHTML = `<h1>Welcome</h1><div class="card"><div class="dim" style="text-align:center">Your login code — write it down. It is shown only once.</div>
  <div class="code">${newCode}</div></div>
  <button id="saved">I have saved it</button>`;
  document.getElementById("saved").onclick = async () => {
    newCode = null; lastKey = "";
    try { setState(await api("/api/state")); } catch (e) { toast(e.message); }
  };
}

function renderHome() {
  app.innerHTML = `<h1>Traitors</h1><div class="sub">Logged in as <b>${esc(state.name)}</b></div>
  <div class="card"><button id="create">Create new game</button></div>
  <div class="card"><label>Join with room code</label><input id="code" maxlength="4" autocapitalize="characters">
  <button id="join" class="sec">Join game</button></div>
  <button class="sec" id="logout">Log out</button>`;
  document.getElementById("create").onclick = () => call("/api/create");
  document.getElementById("join").onclick = () => call("/api/join", {code: document.getElementById("code").value});
  document.getElementById("logout").onclick = logout;
}

const header = () => `<div class="top"><span>Room ${esc(state.code)} · ${esc(state.me.name)} · <a data-logout style="text-decoration:underline">log out</a></span><span>${state.round ? "Round " + state.round : ""}</span></div>
  ${state.remaining != null ? '<div class="timer" id="timer"></div>' : ""}`;

const alive = () => state.players.filter(p => p.alive);
function tags(p) {
  let t = "";
  if (p.role) t += `<span class="tag ${p.role}">${p.role}</span>`;
  if (p.host) t += `<span class="tag host">host</span>`;
  return t;
}
function plist(extra) {
  return state.players.map(p => `<div class="p ${p.alive ? "" : "dead"}"><span>${esc(p.name)}${tags(p)}</span>${extra ? extra(p) : ""}</div>`).join("");
}
function pickList(ids, sel) {
  return ids.map(id => {
    const p = state.players.find(x => x.id === id);
    return `<div class="p pick-item ${sel === id ? "pick" : ""}" data-id="${id}"><span>${esc(p.name)}${tags(p)}</span><span>${sel === id ? "✔" : ""}</span></div>`;
  }).join("");
}
const logHtml = () => `<div class="card log"><b>Story so far</b>${state.log.slice().reverse().map(l => `<div>${esc(l.text)}</div>`).join("")}</div>`;
const skip = () => state.me.host && state.phase !== "lobby" && state.phase !== "end" ? `<button class="sec" data-skip>Host: skip phase</button>` : "";
const wait = () => state.players_needed ? `<div class="dim" style="text-align:center">Waiting for ${state.waiting} of ${state.players_needed}…</div>` : "";
const dead = () => !state.me.alive ? `<div class="card dim">💀 You are out of the game. Keep quiet and watch!</div>` : "";
const note = () => state.night_note ? `<div class="card"><b>Traitor news:</b> ${esc(state.night_note)}</div>` : "";

function lobby() {
  const s = state.settings, n = state.players.length, host = state.me.host;
  const secs = (key, opts) => `<select data-set="${key}" ${host ? "" : "disabled"}>${opts.map(o => `<option value="${o}" ${o == s[key] ? "selected" : ""}>${o >= 60 ? (o / 60) + " min" : o + " sec"}</option>`).join("")}</select>`;
  const tr = [`<option value="0" ${s.traitors == 0 ? "selected" : ""}>Auto (${state.auto_traitors})</option>`];
  for (let i = 1; i <= state.max_traitors; i++) tr.push(`<option value="${i}" ${s.traitors == i ? "selected" : ""}>${i}</option>`);
  return `<h1>Traitors</h1>
  <div class="card"><div class="dim" style="text-align:center">Room code</div><div class="code">${esc(state.code)}</div></div>
  <div class="card"><b>Players (${n}, min ${state.min_players})</b>${plist(p => host && !p.host ? `<a data-kick="${p.id}" style="color:var(--red)">remove</a>` : "")}</div>
  <div class="card"><b>Settings</b>${host ? "" : '<div class="dim">Only the host can change these.</div>'}
  <label>Discussion time</label>${secs("discussion_secs", [30, 60, 120, 180, 300, 600])}
  <label>Voting time</label>${secs("vote_secs", [20, 30, 60, 120, 180])}
  <label>Night time (traitors)</label>${secs("night_secs", [20, 30, 60, 90, 120])}
  <label>Number of traitors</label><select data-set="traitors" ${host ? "" : "disabled"}>${tr.join("")}</select>
  <label>Max tempts per game</label><select data-set="tempts" ${host ? "" : "disabled"}>${[0, 1, 2, 3, 5].map(o => `<option value="${o}" ${o == s.tempts ? "selected" : ""}>${o}</option>`).join("")}</select></div>
  ${host ? `<button id="start" ${n < state.min_players ? "disabled" : ""}>${n < state.min_players ? "Need " + (state.min_players - n) + " more player(s)" : "Start game"}</button>` : '<div class="dim" style="text-align:center">Waiting for the host to start…</div>'}
  <button class="sec" id="leave">Leave game</button><button class="sec" id="logout2">Log out (stay in game)</button>`;
}

function rolePhase() {
  const r = state.me.role, mates = state.players.filter(p => p.role === "traitor" && p.id !== state.me.id);
  return header() + `<div class="role ${r}"><div class="dim">You are a</div><b>${r}</b></div>
  <div class="card dim">${r === "traitor"
    ? "Each night, choose a loyalist to murder — or tempt them to join you. Stay hidden by day." + (mates.length ? `<br><br>Your fellow traitors: <b>${mates.map(m => esc(m.name)).join(", ")}</b>` : "<br><br>You are the only traitor.")
    : "Find the traitors and banish them by vote. Don't get murdered — and beware of tempting offers."}</div>
  ${state.ready ? '<div class="dim" style="text-align:center">Ready ✔</div>' : '<button data-ready>I understand — ready</button>'}${wait()}${skip()}`;
}

function night() {
  const sleep = header() + `<div class="big">🌙</div><h2 style="text-align:center">Night falls…</h2>`;
  if (!state.me.alive) return sleep + dead() + skip();
  if (state.me.role !== "traitor") return sleep + `<div class="card dim">Close your eyes. The traitors are choosing…</div>` + skip();
  const n = state.night, mine = n.mine, sel = picked || (mine && mine[1]);
  if (nightKind === "tempt" && n.tempts_left < 1) nightKind = "murder";
  return header() + `<h2>🗡️ Traitors' turn</h2>
  <div class="row"><button class="${nightKind === "murder" ? "red" : "sec"}" data-kind="murder">Murder</button>
  <button class="${nightKind === "tempt" ? "red" : "sec"}" data-kind="tempt" ${n.tempts_left < 1 ? "disabled" : ""}>Tempt (${n.tempts_left})</button></div>
  <div class="dim" style="margin:8px 0">${nightKind === "murder" ? "Pick a loyalist to murder." : "Offer a loyalist the chance to become a traitor. If they refuse, they are murdered."} If traitors disagree, the most votes wins; ties are random.</div>
  ${pickList(n.targets, sel)}
  <button data-confirm ${picked ? "" : "disabled"}>${mine ? "Change choice" : "Confirm"}</button>
  ${mine ? `<div class="dim" style="text-align:center">Submitted: ${mine[0]} ${esc(state.players.find(p => p.id === mine[1]).name)}</div>` : ""}${wait()}${skip()}`;
}

function offer() {
  if (state.offer_for_me) {
    return header() + `<div class="role traitor"><b>🔥 A secret offer</b></div>
    <div class="card">The traitors have chosen you. Join them and become a <b>traitor</b> — or refuse and be <b>murdered</b>.</div>
    ${state.answered ? '<div class="dim" style="text-align:center">Answer sent. Wait…</div>' : '<button class="red" data-offer="1">Accept — become a traitor</button><button class="sec" data-offer="0">Refuse</button>'}`;
  }
  return header() + `<div class="big">🌙</div><h2 style="text-align:center">Night falls…</h2><div class="card dim">${state.me.role === "traitor" ? "Your tempt offer awaits an answer…" : "Close your eyes."}</div>` + skip();
}

function dawnPhase() {
  return header() + `<div class="big">🌅</div><h2 style="text-align:center">Dawn</h2>
  ${note()}${dead()}<div class="card">${plist()}</div>${logHtml()}
  ${state.ready || !state.me.alive ? "" : '<button data-ready>Continue</button>'}${wait()}${skip()}`;
}

function discussion() {
  return header() + `<h2>💬 Discussion</h2><div class="card dim">Talk it out. Who is acting suspicious? Voting starts when the timer ends.</div>
  ${note()}${dead()}<div class="card">${plist()}</div>${logHtml()}${skip()}`;
}

function vote() {
  if (!state.me.alive) return header() + `<h2>🗳️ Vote</h2>` + dead() + `<div class="dim">${state.voted} of ${state.players_needed} voted.</div>` + skip();
  const sel = picked !== null ? picked : state.my_vote;
  const others = alive().filter(p => p.id !== state.me.id).map(p => p.id);
  return header() + `<h2>🗳️ Who is the traitor?</h2>${pickList(others, sel)}
  <button data-confirm ${picked && picked !== state.my_vote ? "" : "disabled"}>${state.my_vote ? "Change vote" : "Cast vote"}</button>
  <button class="sec" data-abstain>Abstain</button>
  <div class="dim" style="text-align:center">${state.voted} of ${state.players_needed} voted${state.ready ? " · your vote is in ✔" : ""}</div>${skip()}`;
}

function result() {
  const v = state.last_vote, banished = v && v.out ? state.players.find(p => p.id === v.out) : null;
  return header() + `<h2>⚖️ The votes are in</h2>
  ${banished ? `<div class="role ${banished.role}"><div class="dim">${esc(banished.name)} was banished. They were a</div><b>${banished.role}</b></div>` : '<div class="card">Tied or empty vote — nobody is banished.</div>'}
  <div class="card"><b>Tally</b>${v.tally.map(t => `<div class="p"><span>${esc(t.name)}</span><b>${t.votes}</b></div>`).join("") || '<div class="dim">No votes</div>'}
  <div class="dim" style="margin-top:8px">${v.votes.map(x => esc(x.voter) + " → " + (x.for ? esc(x.for) : "abstain")).join("<br>")}</div></div>
  ${state.ready || !state.me.alive ? "" : '<button data-ready>Continue</button>'}${wait()}${skip()}`;
}

function decision() {
  const me = state.me;
  if (me.role !== "loyalist" || !me.alive) {
    return header() + `<div class="big">🤔</div><h2 style="text-align:center">The loyalists decide…</h2><div class="card dim">Traitors still lurk. The loyalists are choosing whether to continue or stop the game now.</div>` + skip();
  }
  const d = state.my_decision;
  return header() + `<h2>Continue or stop?</h2><div class="card">A traitor remains among you. <b>Continue</b> to another night and vote — or <b>stop</b> and end the game now (traitors win if any are left!). Stop needs a majority of loyalists.</div>
  <button class="green" data-dec="continue">${d === "continue" ? "✔ " : ""}Continue</button>
  <button class="red" data-dec="stop">${d === "stop" ? "✔ " : ""}Stop the game</button>
  ${wait()}${skip()}`;
}

function end() {
  const lw = state.winner === "loyalists";
  return `<h1>${lw ? "Loyalists win" : "Traitors win"}</h1><div class="big">${lw ? "🛡️" : "🗡️"}</div>
  <div class="card"><b>Everyone's role</b>${state.roles.map(r => `<div class="p"><span>${esc(r.name)}</span><span class="tag ${r.role}">${r.role}</span></div>`).join("")}</div>
  ${logHtml()}
  ${state.me.host ? '<button id="restart">Play again (same room)</button>' : '<div class="dim" style="text-align:center">Waiting for the host…</div>'}
  <button class="sec" id="exit">Leave room</button>`;
}

function bind() {
  const q = (sel, fn) => app.querySelectorAll(sel).forEach(el => el.onclick = () => fn(el));
  q("[data-logout]", logout);
  q("[data-ready]", () => action("ready"));
  q("[data-skip]", () => action("skip"));
  q("[data-kick]", el => call("/api/kick", {id: el.dataset.kick}));
  q("[data-offer]", el => action("offer", {accept: el.dataset.offer === "1"}));
  q("[data-dec]", el => action("decision", {choice: el.dataset.dec}));
  q("[data-kind]", el => { nightKind = el.dataset.kind; render(); });
  q(".pick-item", el => { picked = el.dataset.id; render(); });
  q("[data-abstain]", () => { picked = null; action("vote", {target: ""}); });
  q("[data-confirm]", async () => {
    const t = picked;
    if (state.phase === "night") await action("night", {kind: nightKind, target: t});
    else await action("vote", {target: t});
    picked = null; render();
  });
  app.querySelectorAll("[data-set]").forEach(el => el.onchange = () => call("/api/settings", {settings: {[el.dataset.set]: el.value}}));
  const on = (id, fn) => { const e = document.getElementById(id); if (e) e.onclick = fn; };
  on("start", () => call("/api/start"));
  on("restart", () => call("/api/restart"));
  on("leave", leave); on("exit", leave); on("logout2", logout);
}
async function leave() {
  await call("/api/leave");
}

render();
poll();
