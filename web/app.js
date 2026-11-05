const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const money = v => Number(v).toFixed(2);

const GAME = location.pathname.replace(/\//g, "");
const ASSETS = { fruits: "/assets/symbols/", cats: "/assets/symbols/cats/" }[GAME];
const API = `/api/${GAME}`;
const BONUS = GAME === "cats";   // free spins exist only in Royal Cats

let manifest, st, betIdx = 3, busy = false, paused = false, autoRunning = false;
const reelEls = [];

async function api(path, body) {
  const r = await fetch(API + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Грешка");
  return data;
}

const cellHtml = s => `<div class="cell"><img src="${ASSETS}${manifest[s]}" alt="${s}" draggable="false"></div>`;
const column = (g, c) => g.map(row => row[c]);
const randomSymbol = () => {
  const names = Object.keys(manifest);
  return names[Math.floor(Math.random() * names.length)];
};

function setReel(c, col) {
  const strip = reelEls[c].querySelector(".strip");
  strip.style.transition = "none";
  strip.style.transform = "none";
  strip.innerHTML = col.map(cellHtml).join("");
}

async function spinReel(c, col, ms) {
  const reel = reelEls[c], strip = reel.querySelector(".strip");
  const filler = 12 + c * 4;
  const prev = [...reel.querySelectorAll(".cell img")].map(i => i.alt);
  const symbols = [...prev, ...Array.from({ length: filler }, randomSymbol), ...col];
  setReel(c, symbols);
  const cellH = reel.clientHeight / 3;
  strip.getBoundingClientRect();
  reel.classList.add("spinning");
  strip.style.transition = `transform ${ms}ms cubic-bezier(.25,.8,.3,1)`;
  strip.style.transform = `translateY(${-(symbols.length - 3) * cellH}px)`;
  await sleep(ms + 40);
  reel.classList.remove("spinning");
  setReel(c, col);
}

function highlight(wins) {
  for (const w of wins) {
    for (const [c, r] of w.positions) reelEls[c].querySelectorAll(".cell")[r].classList.add("win");
  }
}

function clearHighlights() {
  document.querySelectorAll(".cell.win").forEach(e => e.classList.remove("win"));
}

// ---- free spins state helpers (Royal Cats) -----------------------------------------------
const hasBonus = () => BONUS && !!st.bonus;                       // running or waiting for collect/gamble
const bonusRunning = () => hasBonus() && !st.bonus.ended;

function render(shownBalance) {
  $("balance").textContent = money(shownBalance ?? st.balance);
  $("win").textContent = money(st.pending);
  const bet = hasBonus() ? st.bonus.bet_per_line : st.bet_levels[betIdx];
  $("bet").textContent = bet;
  $("total-bet").textContent = money(bet * st.num_lines);
  for (const el of document.querySelectorAll(".jp")) el.querySelector("b").textContent = money(st.jackpot[el.dataset.level] ?? 0);
  const locked = hasBonus();
  $("gamble").hidden = !(st.can_gamble && !busy && !locked);
  $("gamble-amount").textContent = money(st.pending);
  $("spin").disabled = busy || locked;
  $("bet-down").disabled = busy || locked || betIdx === 0;
  $("bet-up").disabled = busy || locked || betIdx === st.bet_levels.length - 1;
  document.body.classList.toggle("bonus-active", bonusRunning());
  renderBonusBar();
}

function renderBonusBar(b = BONUS ? st.bonus : null) {
  if (!BONUS) return;
  $("bonus-bar").hidden = !b;
  if (!b) return;
  $("fs-counter").textContent = `${b.spins_played} / ${b.total_awarded}`;
  $("fs-mult").textContent = `x${Number(b.multiplier)}`;
  $("fs-total").textContent = money(b.total_win);
  $("fs-pause").textContent = paused ? "ПРОДЪЛЖИ" : "ПАУЗА";
  $("fs-pause").disabled = b.ended;
}

function say(html) { $("message").innerHTML = html; }

async function checkBankrupt() {
  if (Number(st.balance) + Number(st.pending) > 0) return;
  busy = true;
  render();
  say('<span class="big">golem si glupak</span>');
  await sleep(2500);
  await fetch("/api/reset", { method: "POST" });
  st = await api("/state");
  busy = false;
  render();
  say("Баланс ресетнат на 1000.00");
}

async function showBanner(text) {
  const b = $("banner");
  b.textContent = text;
  b.hidden = false;
  await sleep(2800);
  b.hidden = true;
}

async function jackpotBanner(res, prevBalance) {
  const j = res.jackpot_win;
  const el = document.querySelector(`.jp[data-level="${j.level}"]`);
  el.classList.add("hit");
  render(prevBalance);
  await showBanner(`ДЖАКПОТ ${j.level}: ${money(j.prize)}`);
  el.classList.remove("hit");
}

// Reel stop, wild expansion and win highlights for one result.
async function animateResult(r) {
  clearHighlights();
  await Promise.all(Array.from({ length: reelEls.length }, (_, c) => spinReel(c, column(r.grid_before, c), 900 + c * 350)));
  highlight(r.scatter_wins);
  if (!r.expanded) highlight(r.line_wins);
  if (r.expanded) {
    say(GAME === "cats" ? "Крал-котка се разширява!" : "Wild разширяване!");
    await sleep(800);
    for (const reel of r.expanded_reels) {
      setReel(reel - 1, column(r.grid_after, reel - 1));
      reelEls[reel - 1].classList.add("expanding");
    }
    await sleep(700);
    reelEls.forEach(e => e.classList.remove("expanding"));
    clearHighlights();
    highlight(r.line_wins);
    highlight(r.scatter_wins.filter(w => w.positions.every(([c]) => !r.expanded_reels.includes(c + 1))));
  }
}

// ---- paid spin ---------------------------------------------------------------------------
async function spin() {
  if (busy || hasBonus()) return;
  const bet = st.bet_levels[betIdx], total = bet * st.num_lines;
  if (Number(st.balance) + Number(st.pending) < total) { say("Недостатъчен баланс"); await checkBankrupt(); return; }
  busy = true;
  const before = st;
  st = { ...before, pending: "0", can_gamble: false };
  render(Number(before.balance) + Number(before.pending) - total);
  say("…");
  let res;
  try {
    res = await api("/spin", { bet });
  } catch (e) {
    st = before; busy = false; render(); say(e.message); return;
  }
  const r = res.result;
  await animateResult(r);

  const win = Number(r.total_win);
  say(win > 0 ? `<span class="big">ПЕЧАЛБА ${money(win)}${r.capped ? " (МАКС.)" : ""}</span>` : "Няма печалба");
  const jp = res.jackpot_win ? Number(res.jackpot_win.prize) : 0;
  st = res;
  if (jp) await jackpotBanner(res, Number(res.balance) - jp);

  if (BONUS && res.free_spins_awarded > 0 && !res.is_free_spin) {
    // The triggering YARN stay highlighted, then the intro overlay.
    clearHighlights();
    highlight(r.scatter_wins.filter(w => w.symbol === "YARN"));
    say(`<span class="big">${res.free_spins_awarded} БЕЗПЛАТНИ ЗАВЪРТАНИЯ!</span>`);
    await sleep(1800);
    busy = false;
    render();
    showIntro(res.free_spins_awarded, "СТАРТ");
    return;
  }
  busy = false;
  render();
  await checkBankrupt();
}

// ---- free spins (Royal Cats) -------------------------------------------------------------
function showOverlay({ title, amount = "", sub = "", buttons = [] }) {
  $("ov-title").textContent = title;
  $("ov-amount").textContent = amount;
  $("ov-sub").textContent = sub;
  for (const id of ["ov-start", "ov-red", "ov-black", "ov-collect"]) $(id).hidden = !buttons.includes(id);
  $("overlay").hidden = false;
}

const hideOverlay = () => { $("overlay").hidden = true; };

function showIntro(n, label) {
  $("ov-start").textContent = label;
  showOverlay({ title: `${n} БЕЗПЛАТНИ ЗАВЪРТАНИЯ`, sub: `Всички печалби x${Number(st.bonus.multiplier)}`, buttons: ["ov-start"] });
}

function startBonus() {
  hideOverlay();
  clearHighlights();
  say("Безплатни завъртания!");
  runFreeSpins();
}

async function playFreeSpin() {
  const prev = st.bonus;
  const res = await api("/spin", { bet: prev.bet_per_line });
  const r = res.result;
  const jp = res.jackpot_win ? Number(res.jackpot_win.prize) : 0;
  // The counter shows the spin being played; the running total grows once the reels have stopped.
  renderBonusBar({ ...res.bonus, total_win: prev.total_win, total_awarded: prev.total_awarded });
  await animateResult(r);
  const win = Number(r.total_win);
  say(win > 0 ? `<span class="big">+${money(win)}</span>` : "Няма печалба");
  st = res;
  renderBonusBar();
  if (res.free_spins_awarded > 0) {
    clearHighlights();
    highlight(r.scatter_wins.filter(w => w.symbol === "YARN"));
    const t = $("retrigger");
    t.textContent = `+${res.free_spins_awarded} ЗАВЪРТАНИЯ`;
    t.hidden = false;
    t.style.animation = "none"; t.offsetWidth; t.style.animation = "";
    await sleep(2000);
    t.hidden = true;
  }
  if (jp) await jackpotBanner(res, Number(res.balance) - jp);
  render();
}

async function runFreeSpins() {
  if (autoRunning) return;
  autoRunning = true;
  busy = true;
  render();
  try {
    while (bonusRunning()) {
      await sleep(st.auto_delay_ms);
      while (paused) await sleep(200);
      if (!bonusRunning()) break;
      await playFreeSpin();
    }
  } catch (e) {
    say(e.message);
    paused = true;
  }
  autoRunning = false;
  busy = false;
  render();
  if (st.bonus && st.bonus.ended) showSummary();
}

function togglePause() {
  paused = !paused;
  renderBonusBar();
  if (!paused && !autoRunning && bonusRunning()) runFreeSpins();
}

function showSummary() {
  const b = st.bonus;
  const won = st.pending !== undefined && Number(st.pending) > 0;
  showOverlay({
    title: "БОНУСЪТ ЗАВЪРШИ",
    amount: money(st.pending),
    sub: `${b.spins_played} завъртания · x${Number(b.multiplier)}${b.capped ? " · МАКС. ПЕЧАЛБА" : ""}`,
    buttons: ["ov-collect", ...(st.can_gamble && won ? ["ov-red", "ov-black"] : [])],
  });
}

// ---- gamble / collect --------------------------------------------------------------------
async function pick(color) {
  if (busy) return;
  const inSummary = hasBonus();
  busy = true;
  render();
  try {
    const res = await api("/gamble", { color });
    st = res;
    say(res.gamble_won ? `<span class="big">Спечели! ${money(res.pending)}</span>` : "Загуби");
    if (inSummary) {
      busy = false;
      if (res.gamble_won) { showSummary(); render(); } else { hideOverlay(); render(); }
      return;
    }
  } catch (e) { say(e.message); }
  busy = false;
  render();
  await checkBankrupt();
}

async function collect() {
  if (busy) return;
  try { st = await api("/collect", {}); say("Взето"); } catch (e) { say(e.message); }
  hideOverlay();
  clearHighlights();
  render();
  await checkBankrupt();
}

async function init() {
  if (!ASSETS) { location.href = "/"; return; }
  document.body.classList.add(GAME);
  [manifest, st] = await Promise.all([
    fetch(ASSETS + "manifest.json").then(r => r.json()),
    api("/state"),
  ]);
  document.title = st.title;
  $("title").textContent = st.title;
  const rows = 3, reels = 5;
  const names = Object.keys(manifest);
  for (let c = 0; c < reels; c++) {
    const reel = document.createElement("div");
    reel.className = "reel";
    reel.innerHTML = '<div class="strip"></div>';
    $("reels").appendChild(reel);
    reelEls.push(reel);
    setReel(c, Array.from({ length: rows }, () => names[Math.floor(Math.random() * names.length)]));
  }
  betIdx = Math.min(3, st.bet_levels.length - 1);
  $("spin").onclick = spin;
  $("collect").onclick = collect;
  $("ov-collect").onclick = collect;
  $("ov-start").onclick = startBonus;
  $("fs-pause").onclick = togglePause;
  $("bet-down").onclick = () => { betIdx--; render(); };
  $("bet-up").onclick = () => { betIdx++; render(); };
  document.querySelectorAll("[data-color]").forEach(b => b.onclick = () => pick(b.dataset.color));
  document.addEventListener("keydown", e => {
    if (e.code === "Space") { e.preventDefault(); spin(); }
  });
  render();
  await checkBankrupt();
  // A reload mid-bonus resumes it; a finished bonus shows its summary again.
  if (hasBonus()) {
    if (bonusRunning()) { say("Бонусът продължава"); showIntro(st.bonus.spins_left, "ПРОДЪЛЖИ"); }
    else showSummary();
  }
}

init();
