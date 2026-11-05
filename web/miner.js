const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const money = v => Number(v).toFixed(2);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const API = "/api/miner";
const CELL = 58, VIEW = 11, BASE_STEP = 250;

const BLOCK_SPRITE = { grass: "b02_grass", dirt: "b01_dirt", stone: "b03_stone", deepstone: "b04_deepstone", voidstone: "b05_voidstone", bedrock: "b06_bedrock" };
const ORE_SPRITE = { coal: "o01_coal", iron: "o02_iron", gold: "o03_gold", emerald: "o04_emerald", diamond: "o05_diamond", voidcrystal: "o06_voidcrystal" };
const PICK_SPRITE = ["p1_wood", "p2_stone", "p3_iron", "p4_gold", "p5_diamond", "p6_void"];
const BLOCK_COLOR = { grass: "#4f9a3c", dirt: "#7a5230", stone: "#8a8a8a", deepstone: "#4c4c5c", voidstone: "#2b1a45", bedrock: "#2a2a2a" };
const ORE_COLOR = { coal: "#222", iron: "#d8a080", gold: "#f5c542", emerald: "#2ecc71", diamond: "#5ff2ff", voidcrystal: "#b040ff" };
const REASONS = { broken: "Киркат се счупи", trapped: "Киркат е блокирана от бедрок", bottom: "Достигна дъното!", cap: "Максимална печалба за рунд" };

const sprites = {};
let st, betIdx = 3, busy = false, speedIdx = 0, holdBalance = null;
const SPEEDS = [1, 2, 4];
const speed = () => SPEEDS[speedIdx];

const R = {
  mine: null, air: null, width: 9, depth: 60, camY: -2, pick: null,
  particles: [], texts: [], fx: [], trails: [], shakeUntil: 0,
};

const cv = $("mine"), ctx = cv.getContext("2d");
ctx.imageSmoothingEnabled = false;

async function api(path, body) {
  const r = await fetch(API + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Грешка");
  return data;
}

// Pre-rasterise each SVG to its native pixel size so it can be scaled without smoothing.
function loadSprite(name) {
  return new Promise(res => {
    const img = new Image();
    img.onload = () => {
      const s = name.startsWith("p") ? 18 : 16;
      const c = document.createElement("canvas");
      c.width = c.height = s;
      c.getContext("2d").drawImage(img, 0, 0, s, s);
      sprites[name] = c;
      res();
    };
    img.onerror = () => res();
    img.src = `/assets/mine/${name}.svg`;
  });
}

// ---------------------------------------------------------------- rendering
const idx = (r, c) => r * R.width + c;
const isAir = (r, c) => R.air[idx(r, c)] === 1;
const setAir = (r, c) => { if (R.air) R.air[idx(r, c)] = 1; };

function cellSprite(cell) {
  if (cell === "table") return "s01_crafting_table";
  if (cell === "tnt") return "s02_tnt";
  if (cell.includes(":")) return ORE_SPRITE[cell.split(":")[1]];
  return BLOCK_SPRITE[cell];
}

function drawCell(r, c, y) {
  const x = c * CELL;
  if (r < 0) {
    ctx.fillStyle = r === -1 ? "#2c4a80" : "#1f3562";
    ctx.fillRect(x, y, CELL, CELL);
    return;
  }
  if (r >= R.depth) { ctx.fillStyle = "#000"; ctx.fillRect(x, y, CELL, CELL); return; }
  if (isAir(r, c)) {
    ctx.fillStyle = "#1c120c";
    ctx.fillRect(x, y, CELL, CELL);
    const h = ((r * 73856093) ^ (c * 19349663)) >>> 0;
    ctx.fillStyle = "#2c1b10";
    ctx.fillRect(x + 6 + (h % 30), y + 8 + ((h >> 5) % 30), 6, 4);
    ctx.fillRect(x + 30 + ((h >> 9) % 20), y + 36 + ((h >> 3) % 14), 5, 4);
    ctx.fillStyle = "#120a06";
    ctx.fillRect(x + 12 + ((h >> 7) % 28), y + 24 + ((h >> 11) % 20), 4, 4);
    return;
  }
  const s = sprites[cellSprite(R.mine[r][c])];
  if (s) ctx.drawImage(s, x, y, CELL, CELL);
}

function drawPick(now, camPx) {
  const p = R.pick;
  if (!p || !p.visible) return;
  const s = sprites[PICK_SPRITE[p.tier - 1]];
  const px = p.x * CELL, py = p.y * CELL - camPx;
  if (p.flashAt && now - p.flashAt < 700) {
    const u = (now - p.flashAt) / 700;
    ctx.strokeStyle = `rgba(245,197,66,${1 - u})`;
    ctx.lineWidth = 6;
    const g = u * CELL * 1.2;
    ctx.strokeRect(px - g / 2, py - g / 2, CELL + g, CELL + g);
  }
  if (s) {
    const sz = CELL * 1.1, off = (CELL - sz) / 2;
    ctx.drawImage(s, px + off, py + off, sz, sz);
  }
  if (p.crack > 0) {
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 3;
    ctx.beginPath();
    const cx = px + CELL / 2, cy = py + CELL / 2, n = Math.ceil(p.crack * 6);
    for (let i = 0; i < n; i++) {
      const a = (i / 6) * Math.PI * 2 + 0.4;
      ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(a) * 14, cy + Math.sin(a) * 10);
      ctx.lineTo(cx + Math.cos(a + 0.3) * 26, cy + Math.sin(a + 0.3) * 22);
    }
    ctx.stroke();
  }
}

function frame(now) {
  const p = R.pick;
  if (p && p.dur > 0) {
    const u = Math.min(1, (now - p.t0) / p.dur);
    p.x = p.fx + (p.tx - p.fx) * u;
    p.y = p.fy + (p.ty - p.fy) * u;
    if (u >= 1) p.dur = 0;
  }
  const target = p && p.visible ? p.y - 7 : R.camY;
  R.camY += (clamp(target, -2, R.depth - VIEW + 1) - R.camY) * 0.14;
  const camPx = R.camY * CELL;
  ctx.fillStyle = "#1a1a3a";
  ctx.fillRect(0, 0, cv.width, cv.height);
  const r0 = Math.floor(R.camY);
  for (let r = r0; r <= r0 + VIEW + 1; r++) {
    const y = Math.round(r * CELL - camPx);
    for (let c = 0; c < R.width; c++) drawCell(r, c, y);
  }
  // falling trail
  R.trails = R.trails.filter(t => now - t.t0 < t.life);
  for (const t of R.trails) {
    const a = 1 - (now - t.t0) / t.life;
    const yEnd = p && p.tx === t.x ? Math.min(t.y1, p.y) : t.y1;
    ctx.fillStyle = `rgba(200,161,101,${a * 0.9})`;
    ctx.fillRect((t.x + 0.5) * CELL - 2, (t.y0 + 0.5) * CELL - camPx, 4, (yEnd - t.y0) * CELL);
  }
  drawPick(now, camPx);
  drawFx(now, camPx);
  const shake = $("shake");
  shake.style.transform = now < R.shakeUntil
    ? `translate(${(Math.random() - 0.5) * 12}px, ${(Math.random() - 0.5) * 12}px)` : "none";
  requestAnimationFrame(frame);
}

let lastT = performance.now();
function drawFx(now, camPx) {
  const dt = Math.min(0.05, (now - lastT) / 1000);
  lastT = now;
  R.particles = R.particles.filter(q => now - q.t0 < q.life);
  for (const q of R.particles) {
    q.vy += 520 * dt;
    q.wx += q.vx * dt;
    q.wy += q.vy * dt;
    ctx.globalAlpha = 1 - (now - q.t0) / q.life;
    ctx.fillStyle = q.color;
    ctx.fillRect(Math.round(q.wx), Math.round(q.wy - camPx), q.size, q.size);
  }
  ctx.globalAlpha = 1;
  R.fx = R.fx.filter(f => now - f.t0 < f.life);
  for (const f of R.fx) {
    const u = (now - f.t0) / f.life;
    if (u < 0.25) {
      ctx.fillStyle = `rgba(255,255,255,${(0.25 - u) * 3})`;
      ctx.fillRect(f.wx - CELL * 1.5, f.wy - CELL * 1.5 - camPx, CELL * 3, CELL * 3);
    }
    for (let k = 0; k < 28; k++) {
      const a = (k / 28) * Math.PI * 2, rad = u * CELL * (1.2 + (k % 3) * 0.5);
      const sz = Math.max(2, (1 - u) * 12);
      ctx.fillStyle = ["#ff7a1a", "#ffd23a", "#fff3b0", "#d83a1a"][k % 4];
      ctx.globalAlpha = 1 - u;
      ctx.fillRect(Math.round(f.wx + Math.cos(a) * rad - sz / 2), Math.round(f.wy + Math.sin(a) * rad - sz / 2 - camPx), sz, sz);
    }
    ctx.globalAlpha = 1;
  }
  R.texts = R.texts.filter(t => now - t.t0 < t.life);
  ctx.font = "bold 22px 'Courier New', monospace";
  ctx.textAlign = "center";
  for (const t of R.texts) {
    const u = (now - t.t0) / t.life;
    ctx.globalAlpha = u < 0.7 ? 1 : 1 - (u - 0.7) / 0.3;
    const y = t.wy - u * 46 - camPx;
    ctx.lineWidth = 5;
    ctx.strokeStyle = "#000";
    ctx.strokeText(t.text, t.wx, y);
    ctx.fillStyle = t.color;
    ctx.fillText(t.text, t.wx, y);
  }
  ctx.globalAlpha = 1;
}

// ---------------------------------------------------------------- effects
function burst(r, c, colors, n = 10) {
  const now = performance.now();
  for (let i = 0; i < n; i++) {
    R.particles.push({
      wx: (c + 0.5) * CELL + (Math.random() - 0.5) * 20, wy: (r + 0.5) * CELL + (Math.random() - 0.5) * 20,
      vx: (Math.random() - 0.5) * 220, vy: -Math.random() * 220, t0: now, life: 500 + Math.random() * 400,
      size: 4 + Math.floor(Math.random() * 5), color: colors[i % colors.length],
    });
  }
}
const floatText = (r, c, text, color = "#f5c542") =>
  R.texts.push({ wx: (c + 0.5) * CELL, wy: (r + 0.4) * CELL, text, color, t0: performance.now(), life: 1300 });

function tween(ms, fn) {
  return new Promise(res => {
    const t0 = performance.now();
    const step = now => {
      const u = Math.min(1, (now - t0) / ms);
      fn(u);
      u < 1 ? requestAnimationFrame(step) : res();
    };
    requestAnimationFrame(step);
  });
}

async function moveTo(r, c, ms) {
  const p = R.pick, d = ms / speed();
  if (p.tx === c && p.ty === r) { await sleep(Math.min(d, 110)); return; }
  p.fx = p.x; p.fy = p.y; p.tx = c; p.ty = r;
  p.t0 = performance.now(); p.dur = d;
  if (r > p.fy) R.trails.push({ x: c, y0: p.fy, y1: r, t0: p.t0, life: 900 });
  await sleep(d);
}

// ---------------------------------------------------------------- panel
let roundWin = 0, curDepth = 0, curTier = 0, curDur = 0;

function setDur(dur) {
  curDur = dur;
  const max = curTier ? st.pickaxes[curTier - 1].durability : 0;
  const pct = max ? clamp(dur / max, 0, 1) * 100 : 0;
  const f = $("dur-fill");
  f.style.width = pct + "%";
  f.classList.toggle("low", pct <= 50 && pct > 25);
  f.classList.toggle("crit", pct <= 25);
  $("dur-num").textContent = `${dur} / ${max}`;
}

function setTier(tier, pop = false) {
  curTier = tier;
  const img = $("pick-img");
  if (tier) {
    const p = st.pickaxes[tier - 1];
    img.src = `/assets/mine/${PICK_SPRITE[tier - 1]}.svg`;
    $("pick-name").textContent = p.name;
    $("pick-tier").textContent = `Ниво ${tier} / ${st.pickaxes.length}`;
  } else {
    img.removeAttribute("src");
    $("pick-name").textContent = "ГОТОВ ЗА КОПАНЕ";
    $("pick-tier").textContent = `Ниво – / ${st.pickaxes.length}`;
  }
  document.querySelectorAll(".path-item").forEach((el, i) => {
    el.classList.toggle("current", i + 1 === tier);
    el.classList.toggle("future", tier === 0 || i + 1 > tier);
  });
  if (pop) {
    const ic = $("pick-icon");
    ic.classList.remove("pop");
    void ic.offsetWidth;
    ic.classList.add("pop");
  }
}

function setWin(v) { roundWin = v; $("round-win").textContent = money(v); }
function setDepth(d) {
  curDepth = Math.max(curDepth, d);
  $("depth").textContent = `${curDepth} / ${R.depth} м`;
  $("depth-head").textContent = `Дълбочина ${curDepth} м`;
}

function render() {
  $("balance").textContent = money(holdBalance ?? st.balance);
  $("bet").textContent = money(st.bet_levels[betIdx]);
  $("dig").disabled = busy;
  $("bet-down").disabled = busy || betIdx === 0;
  $("bet-up").disabled = busy || betIdx === st.bet_levels.length - 1;
}

const msg = t => { $("message").textContent = t; };

async function showBanner(text, ms = 1800) {
  const b = $("banner");
  b.textContent = text;
  b.hidden = false;
  await sleep(ms / speed());
  b.hidden = true;
}

// ---------------------------------------------------------------- pickaxe draw reel
async function runDraw(tier) {
  const items = ["NONE", ...st.pickaxes.map((_, i) => i + 1)];
  const strip = $("draw-strip");
  const ITEM = 120, N = 30;
  const list = Array.from({ length: N }, () => Math.floor(Math.random() * 7));
  const P = N - 2;
  list[P] = tier;
  strip.innerHTML = list.map(t => t === 0
    ? '<div class="draw-item"><div class="empty-slot"></div></div>'
    : `<div class="draw-item"><img src="/assets/mine/${PICK_SPRITE[t - 1]}.svg" alt="" draggable="false"></div>`).join("");
  strip.style.transition = "none";
  strip.style.transform = "translateY(0)";
  $("draw-result").textContent = "";
  $("draw-result").className = "draw-result";
  $("draw").hidden = false;
  strip.getBoundingClientRect();
  const ms = 2300 / speed();
  strip.style.transition = `transform ${ms}ms cubic-bezier(.15,.7,.2,1)`;
  strip.style.transform = `translateY(${-(P - 1) * ITEM}px)`;
  await sleep(ms + 60);
  const res = $("draw-result");
  if (tier === 0) { res.textContent = "НЯМА КИРКА"; res.classList.add("none"); }
  else res.textContent = st.pickaxes[tier - 1].name;
  await sleep(1100 / speed());
  $("draw").hidden = true;
}

// ---------------------------------------------------------------- round playback
function startMine(r) {
  R.mine = r.mine;
  R.width = r.mine[0].length;
  R.depth = r.mine.length;
  R.air = new Uint8Array(R.depth * R.width);
  R.particles = []; R.texts = []; R.fx = []; R.trails = [];
  R.camY = -2;
  R.pick = { x: r.start_col, y: -5, fx: r.start_col, fy: -5, tx: r.start_col, ty: -5, t0: 0, dur: 0,
             tier: r.start_tier, visible: true, crack: 0, flashAt: 0 };
  curDepth = 0;
  setWin(0);
  setTier(r.start_tier);
  setDur(st.pickaxes[r.start_tier - 1].durability);
  setDepth(0);
}

async function handle(ev) {
  const p = R.pick;
  switch (ev.e) {
    case "move":
      await moveTo(ev.r, ev.c, BASE_STEP);
      setDepth(ev.r + 1);
      break;
    case "slide":
      await moveTo(ev.r, ev.to, BASE_STEP * 0.8);
      break;
    case "dig": {
      await moveTo(ev.r, ev.c, BASE_STEP);
      const cols = [BLOCK_COLOR[ev.block] || "#888"];
      if (ev.ore) cols.push(ORE_COLOR[ev.ore]);
      burst(ev.r, ev.c, cols, ev.ore ? 14 : 8);
      setAir(ev.r, ev.c);
      setDur(ev.dur);
      setDepth(ev.r + 1);
      const w = Number(ev.win);
      if (w > 0) { floatText(ev.r, ev.c, "+" + money(w)); setWin(roundWin + w); }
      break;
    }
    case "table":
      await moveTo(ev.r, ev.c, BASE_STEP);
      setAir(ev.r, ev.c);
      setDepth(ev.r + 1);
      if (ev.upgraded) {
        burst(ev.r, ev.c, ["#f5c542", "#fff3b0", "#c8961f"], 18);
        p.tier = ev.tier;
        p.flashAt = performance.now();
        setTier(ev.tier, true);
        setDur(ev.dur);
        floatText(ev.r, ev.c, "ПОДОБРЕНИЕ!", "#ffffff");
        msg(`Подобрение: ${st.pickaxes[ev.tier - 1].name}`);
        await sleep(800 / speed());
      } else {
        floatText(ev.r, ev.c, "МАКС. НИВО", "#ffffff");
        await sleep(300 / speed());
      }
      break;
    case "tnt": {
      await moveTo(ev.r, ev.c, BASE_STEP);
      R.shakeUntil = performance.now() + 500;
      R.fx.push({ wx: (ev.c + 0.5) * CELL, wy: (ev.r + 0.5) * CELL, t0: performance.now(), life: 650 });
      for (const [y, x] of ev.cells) { setAir(y, x); burst(y, x, ["#ff7a1a", "#ffd23a", "#555"], 5); }
      setDepth(ev.r + 1);
      for (const o of ev.ores) { floatText(o.r, o.c, "+" + money(o.win)); }
      const w = Number(ev.win);
      if (w > 0) setWin(roundWin + w);
      await sleep(650 / speed());
      break;
    }
    case "break": {
      if (ev.reason === "trapped") floatText(ev.r, ev.c, "БЛОКИРАНА", "#ff8a8a");
      await tween(550 / speed(), u => { p.crack = u; });
      const col = ["#c8c8d0", "#8a5a2a", "#555566"];
      burst(ev.r, ev.c, col, 22);
      p.visible = false;
      await sleep(450 / speed());
      break;
    }
    case "bottom":
      setDepth(R.depth);
      await showBanner(`ДЪНО! +${money(ev.bonus)}`);
      setWin(roundWin + Number(ev.bonus));
      break;
    case "cap":
      await showBanner("МАКСИМАЛНА ПЕЧАЛБА!");
      break;
  }
}

function showSummary(r) {
  const win = Number(r.total_win);
  $("sum-title").textContent = win > 0 ? "РУНДЪТ ПРИКЛЮЧИ" : "РУНДЪТ ПРИКЛЮЧИ";
  $("sum-reason").textContent = REASONS[r.reason] || "";
  $("sum-win").textContent = money(win);
  const up = r.upgrades ? ` · подобрения: ${r.upgrades}` : "";
  $("sum-stats").textContent = `Дълбочина ${r.depth} м · ${st.pickaxes[r.start_tier - 1].name}${up}`;
  $("summary").hidden = false;
}
const hideSummary = () => { $("summary").hidden = true; };

async function dig() {
  if (busy) return;
  hideSummary();
  const bet = st.bet_levels[betIdx];
  if (Number(st.balance) < Number(bet)) { msg("Недостатъчен баланс"); return; }
  busy = true;
  msg("");
  holdBalance = Number(st.balance) - Number(bet);
  render();
  let res;
  try {
    res = await api("/play", { bet });
  } catch (e) {
    holdBalance = null; busy = false; render(); msg(e.message); return;
  }
  const r = res.result;
  await runDraw(r.start_tier);
  if (r.start_tier === 0) {
    msg("Няма кирка. Опитай отново!");
    st = res; holdBalance = null; busy = false; render();
    return;
  }
  startMine(r);
  await moveTo(-1, r.start_col, 700);
  for (const ev of r.events) await handle(ev);
  setWin(Number(r.total_win));
  st = res;
  holdBalance = null;
  busy = false;
  render();
  showSummary(r);
}

// ---------------------------------------------------------------- init
function decoMine() {
  // Purely decorative idle picture (not a game result).
  const rows = [];
  for (let r = 0; r < 60; r++) {
    const block = r === 0 ? "grass" : r === 1 ? "dirt" : r < 25 ? "stone" : r < 50 ? "deepstone" : "voidstone";
    rows.push(Array.from({ length: 9 }, () => {
      const x = Math.random();
      if (r < 2) return block;
      if (x < 0.07) return `${block}:coal`;
      if (x < 0.11) return `${block}:iron`;
      if (x < 0.125) return `${block}:gold`;
      if (x < 0.135 && r > 24) return `${block}:emerald`;
      if (x < 0.14) return "tnt";
      if (x < 0.15) return "table";
      return block;
    }));
  }
  return rows;
}

async function init() {
  const names = [...Object.values(BLOCK_SPRITE), ...Object.values(ORE_SPRITE), ...PICK_SPRITE, "s01_crafting_table", "s02_tnt"];
  const [state] = await Promise.all([api("/state"), ...names.map(loadSprite)]);
  st = state;
  document.title = st.title;
  R.mine = decoMine();
  R.depth = st.depth; R.width = st.width;
  R.air = new Uint8Array(R.depth * R.width);

  $("path").innerHTML = st.pickaxes.map((p, i) =>
    `${i ? '<span class="path-arrow">›</span>' : ""}<div class="path-item" title="${p.name}"><img src="/assets/mine/${PICK_SPRITE[i]}.svg" alt="" draggable="false"></div>`).join("");
  $("ores").innerHTML = Object.entries(st.ore_pay).map(([k, v]) =>
    `<div class="ore"><img src="/assets/mine/${ORE_SPRITE[k]}.svg" alt="${k}" draggable="false">${Number(v)}x</div>`).join("");
  $("depth").textContent = `0 / ${st.depth} м`;
  betIdx = Math.min(3, st.bet_levels.length - 1);
  setTier(0);
  setDur(0);

  $("dig").onclick = dig;
  $("sum-ok").onclick = hideSummary;
  $("bet-down").onclick = () => { betIdx--; render(); };
  $("bet-up").onclick = () => { betIdx++; render(); };
  $("speed").onclick = () => { speedIdx = (speedIdx + 1) % SPEEDS.length; $("speed").textContent = `⏩ ×${speed()}`; };
  document.addEventListener("keydown", e => { if (e.code === "Space") { e.preventDefault(); dig(); } });
  render();
  requestAnimationFrame(frame);
}

init();
