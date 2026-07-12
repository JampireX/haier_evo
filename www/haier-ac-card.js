/**
 * Haier Evo — кастомная карточка управления кондиционером.
 *
 * Установка:
 *   1) Скопируйте этот файл в  <config>/www/haier-ac-card.js
 *   2) Settings → Dashboards → ⋮ → Resources → Add resource
 *        URL: /local/haier-ac-card.js   Type: JavaScript Module
 *      (или добавьте в lovelace ресурс вручную для YAML-режима)
 *   3) Добавьте карточку:
 *        type: custom:haier-ac-card
 *        entity: climate.kondicioner          # обязательно
 *        name: Кондиционер                     # необязательно
 *        subtitle: Спальня                     # необязательно
 *        eco_sensor: select.kondicioner_eco_sensor   # необязательно
 *        features:                             # необязательно — переключатели функций
 *          - switch.kondicioner_turbo
 *          - switch.kondicioner_quiet
 *          - switch.kondicioner_health
 *          - switch.kondicioner_light
 *          - switch.kondicioner_sound
 *
 * Тёмная тема используется по умолчанию; цвета берутся из переменных темы HA
 * с тёмными фолбэками, поэтому карточка корректно выглядит в любой теме.
 */

const MODE = {
  cool:     { ru: "Холод",  accent: "#3ea6ff", icon: "snow" },
  heat:     { ru: "Тепло",  accent: "#ff7a45", icon: "sun"  },
  auto:     { ru: "Авто",   accent: "#9b8cff", icon: "auto" },
  dry:      { ru: "Сушка",  accent: "#36c6c0", icon: "drop" },
  fan_only: { ru: "Обдув",  accent: "#7bd88f", icon: "fan"  },
  off:      { ru: "Выкл",   accent: "#878d99", icon: "auto" },
};
const FAN_RU   = { low: "Низкий", medium: "Средний", high: "Высокий", auto: "Авто",
                   mid: "Средний", quiet: "Тихий", turbo: "Турбо" };
const SWING_RU = { off: "Выкл", auto: "Авто", on: "Вкл" };

const ICONS = {
  snow: '<path d="M12 3v18M4.5 7.5l15 9M19.5 7.5l-15 9" stroke-linecap="round"/>',
  sun:  '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.4 1.4M17.6 17.6L19 19M19 5l-1.4 1.4M6.4 17.6L5 19" stroke-linecap="round"/>',
  drop: '<path d="M12 3c4 6 6 8.5 6 11a6 6 0 1 1-12 0c0-2.5 2-5 6-11Z"/>',
  fan:  '<circle cx="12" cy="12" r="2"/><path d="M12 12c0-5 1-8 4-8s2 5-4 8M12 12c-4 3-7 3.5-8 1s2-4 8-1M12 12c4 3 4 6 1.5 7.5S9 17 12 12"/>',
  auto: '',
};

function svgIcon(kind, color, size = 22) {
  if (kind === "auto") {
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}"><text x="12" y="17" text-anchor="middle"
      font-size="15" font-weight="700" fill="${color}" font-family="inherit">A</text></svg>`;
  }
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none"
    stroke="${color}" stroke-width="2">${ICONS[kind] || ""}</svg>`;
}

// 270° gauge geometry
function arcPath(cx, cy, r, a0deg, a1deg) {
  const p = (a) => [cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)];
  const [x0, y0] = p(a0deg);
  const [x1, y1] = p(a1deg);
  const large = a1deg - a0deg > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

class HaierAcCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.entity || !config.entity.startsWith("climate.")) {
      throw new Error("Укажите climate-сущность: entity: climate.xxx");
    }
    this._config = config;
    // setConfig can be called again on the same element (card editor / config change);
    // attachShadow throws if a shadow root already exists, so reuse it.
    this._root = this.shadowRoot || this.attachShadow({ mode: "open" });
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  getCardSize() { return 9; }

  _st(id) { return this._hass && id ? this._hass.states[id] : undefined; }

  _call(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  _build() {
    this._root.innerHTML = `
      <style>
        :host { --ha-card-border-radius: 26px; }
        .card {
          background: var(--ha-card-background, var(--card-background-color, #1b1e24));
          color: var(--primary-text-color, #f3f5f8);
          border: 1px solid var(--divider-color, rgba(255,255,255,.07));
          border-radius: 26px; padding: 20px 20px 22px;
          box-shadow: var(--ha-card-box-shadow, 0 6px 22px rgba(0,0,0,.45));
          font-family: var(--paper-font-body1_-_font-family, "Segoe UI", Roboto, sans-serif);
        }
        .hdr { display:flex; align-items:flex-start; justify-content:space-between; }
        .title { font-size:19px; font-weight:700; line-height:1.1; }
        .sub { font-size:13px; color: var(--secondary-text-color,#878d99); margin-top:4px;
               display:flex; align-items:center; gap:7px; }
        .dot { width:8px; height:8px; border-radius:50%; background:#34d399; }
        .dot.off { background:#5a616e; }
        .pwr { width:46px; height:46px; border-radius:50%; border:none; cursor:pointer;
               display:grid; place-items:center; transition:.15s;
               background: var(--c2,#23272f); color: var(--secondary-text-color,#878d99); }
        .pwr.on { background: radial-gradient(circle at 50% 38%, #7cc4ff, var(--acc,#3ea6ff));
                  color:#fff; box-shadow:0 4px 14px rgba(62,166,255,.45); }
        .gauge { position:relative; width:100%; height:236px; margin:6px 0 2px; }
        .gauge svg.ring { position:absolute; inset:0; width:100%; height:100%; }
        .center { position:absolute; inset:0; display:flex; flex-direction:column;
                  align-items:center; justify-content:center; pointer-events:none; }
        .mode-lbl { font-size:12px; font-weight:700; letter-spacing:2.5px;
                    text-transform:uppercase; margin-bottom:2px; }
        .temp { font-size:70px; font-weight:700; line-height:.9; display:flex; align-items:flex-start; }
        .temp .deg { font-size:22px; font-weight:600; color:var(--secondary-text-color,#878d99); margin-top:8px; }
        .cur { font-size:13px; color:var(--secondary-text-color,#878d99); margin-top:10px; }
        .step { position:absolute; top:42%; width:54px; height:54px; border-radius:50%;
                border:1px solid var(--divider-color,rgba(255,255,255,.07)); cursor:pointer;
                background:var(--c2,#23272f); color:inherit; font-size:28px; font-weight:600;
                display:grid; place-items:center; transition:.12s; }
        .step:hover { background:var(--acc,#3ea6ff); color:#fff; border-color:transparent; }
        .step.minus { left:2px; } .step.plus { right:2px; }
        .sec-lbl { font-size:13px; font-weight:600; color:var(--secondary-text-color,#878d99);
                   margin:18px 0 9px; }
        .chips { display:grid; gap:10px; }
        .chips.modes { grid-auto-flow:column; grid-auto-columns:1fr; }
        .chip { background:var(--c2,#23272f); border:1px solid var(--divider-color,rgba(255,255,255,.07));
                border-radius:15px; padding:10px 6px; cursor:pointer; color:var(--secondary-text-color,#878d99);
                display:flex; flex-direction:column; align-items:center; gap:6px; font-size:12px;
                font-weight:600; transition:.12s; }
        .chip .lbl { white-space:nowrap; }
        .chip.active { color:var(--primary-text-color,#f3f5f8);
                       border-color:var(--acc,#3ea6ff); background:var(--acc-bg,rgba(62,166,255,.16)); }
        .seg { display:flex; background:var(--c2,#23272f); border-radius:14px; padding:4px;
               border:1px solid var(--divider-color,rgba(255,255,255,.07)); }
        .seg button { flex:1; border:none; background:none; cursor:pointer; padding:9px 4px;
                      border-radius:11px; font-size:13px; font-weight:600; color:var(--secondary-text-color,#878d99); }
        .seg button.active { background:var(--acc-bg,rgba(62,166,255,.16)); color:var(--primary-text-color,#f3f5f8);
                             box-shadow:inset 0 0 0 1px var(--acc,#3ea6ff); }
        .row2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
        .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
        .pill { display:flex; align-items:center; gap:10px; padding:12px 14px; border-radius:14px;
                cursor:pointer; font-size:12.5px; font-weight:600; transition:.12s;
                background:var(--c2,#23272f); border:1px solid var(--divider-color,rgba(255,255,255,.07));
                color:var(--secondary-text-color,#878d99); }
        .pill .pd { width:10px; height:10px; border-radius:50%; background:#5a616e; flex:none; }
        .pill.active { color:var(--primary-text-color,#f3f5f8);
                       border-color:var(--acc,#3ea6ff); background:var(--acc-bg,rgba(62,166,255,.16)); }
        .pill.active .pd { background:var(--acc,#3ea6ff); }
        select.eco { width:100%; background:var(--c2,#23272f); color:inherit; font-size:13px;
                     border:1px solid var(--divider-color,rgba(255,255,255,.07)); border-radius:14px;
                     padding:12px 14px; font-weight:600; }
        .off-dim { opacity:.55; }
      </style>
      <ha-card class="card">
        <div class="hdr">
          <div>
            <div class="title" id="title"></div>
            <div class="sub"><span class="dot" id="dot"></span><span id="status"></span></div>
          </div>
          <button class="pwr" id="pwr" title="Вкл/Выкл">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
              stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="4" x2="12" y2="12"/>
              <path d="M7.5 7.5a7 7 0 1 0 9 0"/></svg>
          </button>
        </div>

        <div class="gauge" id="gauge">
          <svg class="ring" viewBox="0 0 420 236" preserveAspectRatio="xMidYMid meet">
            <defs><linearGradient id="g" x1="0" y1="1" x2="1" y2="0">
              <stop offset="0" stop-color="var(--acc,#3ea6ff)"/>
              <stop offset="1" stop-color="#7cc4ff"/></linearGradient></defs>
            <path id="track" fill="none" stroke="var(--track,#2c313b)" stroke-width="16" stroke-linecap="round"/>
            <path id="prog"  fill="none" stroke="url(#g)" stroke-width="16" stroke-linecap="round"/>
            <circle id="knob" r="9" fill="#fff" stroke="var(--acc,#3ea6ff)" stroke-width="2"/>
          </svg>
          <button class="step minus" id="minus">−</button>
          <button class="step plus"  id="plus">+</button>
          <div class="center">
            <div class="mode-lbl" id="modeLbl"></div>
            <div class="temp"><span id="target">—</span><span class="deg">°C</span></div>
            <div class="cur" id="cur"></div>
          </div>
        </div>

        <div id="modesWrap"><div class="sec-lbl">Режим</div><div class="chips modes" id="modes"></div></div>
        <div id="fanWrap"><div class="sec-lbl">Скорость вентилятора</div><div class="seg" id="fan"></div></div>
        <div id="swingWrap"><div class="sec-lbl">Жалюзи</div><div class="row2" id="swing"></div></div>
        <div id="ecoWrap"><div class="sec-lbl">Эко-датчик</div><div id="eco"></div></div>
        <div id="featWrap"><div class="sec-lbl">Функции</div><div class="grid3" id="feats"></div></div>
      </ha-card>`;

    // gauge static geometry
    const A0 = 130, SPAN = 280;
    this._gauge = { A0, SPAN, cx: 210, cy: 124, r: 104 };
    const g = this._gauge;
    this._root.getElementById("track").setAttribute("d", arcPath(g.cx, g.cy, g.r, A0, A0 + SPAN));

    // listeners
    this._root.getElementById("pwr").onclick = () => this._togglePower();
    this._root.getElementById("minus").onclick = () => this._bumpTemp(-1);
    this._root.getElementById("plus").onclick = () => this._bumpTemp(+1);
    this._built = true;
  }

  _entity() { return this._st(this._config.entity); }

  _togglePower() {
    const e = this._entity();
    const on = e && e.state !== "off";
    this._call("climate", on ? "turn_off" : "turn_on", { entity_id: this._config.entity });
  }

  _bumpTemp(d) {
    const e = this._entity();
    if (!e) return;
    const a = e.attributes;
    const step = a.target_temp_step || 1;
    let t = (a.temperature ?? a.min_temp ?? 16) + d * step;
    t = Math.min(a.max_temp ?? 30, Math.max(a.min_temp ?? 16, t));
    this._call("climate", "set_temperature", { entity_id: this._config.entity, temperature: t });
  }

  _update() {
    const e = this._entity();
    if (!e) return;
    const a = e.attributes;
    // "unavailable"/"unknown" are NOT powered-on states — only a real HVAC mode is.
    const on = e.state !== "off" && e.state !== "unavailable" && e.state !== "unknown";
    const modeKey = on ? e.state : "off";
    const m = MODE[modeKey] || MODE.off;
    const card = this._root.querySelector(".card");
    card.style.setProperty("--acc", m.accent);
    card.style.setProperty("--acc-bg", this._tint(m.accent));
    card.style.setProperty("--c2", "var(--secondary-background-color,#23272f)");
    card.style.setProperty("--track", "var(--divider-color,#2c313b)");

    // header
    this._root.getElementById("title").textContent =
      this._config.name || a.friendly_name || "Кондиционер";
    this._root.getElementById("status").textContent =
      (this._config.subtitle ? this._config.subtitle + " · " : "") +
      (e.state === "unavailable" ? "недоступно" : "онлайн");
    const dot = this._root.getElementById("dot");
    dot.classList.toggle("off", e.state === "unavailable");
    const pwr = this._root.getElementById("pwr");
    pwr.classList.toggle("on", on);

    // gauge
    const tgt = a.temperature;
    const lo = a.min_temp ?? 16, hi = a.max_temp ?? 30;
    const span = hi - lo;
    const frac = (tgt != null && span > 0) ? Math.min(1, Math.max(0, (tgt - lo) / span)) : 0;
    const g = this._gauge;
    const end = g.A0 + frac * g.SPAN;
    this._root.getElementById("prog").setAttribute("d", arcPath(g.cx, g.cy, g.r, g.A0, on ? end : g.A0 + 0.001));
    const knob = this._root.getElementById("knob");
    const kx = g.cx + g.r * Math.cos((end * Math.PI) / 180);
    const ky = g.cy + g.r * Math.sin((end * Math.PI) / 180);
    knob.setAttribute("cx", kx); knob.setAttribute("cy", ky);
    knob.style.display = on ? "" : "none";
    this._root.getElementById("modeLbl").textContent = m.ru.toUpperCase();
    this._root.getElementById("modeLbl").style.color = m.accent;
    this._root.getElementById("target").textContent = tgt != null ? Math.round(tgt) : "—";
    this._root.getElementById("cur").textContent =
      a.current_temperature != null ? `в комнате ${Math.round(a.current_temperature)}°` : "";
    this._root.getElementById("gauge").classList.toggle("off-dim", !on);

    // modes
    this._renderChips("modes", "modesWrap", (a.hvac_modes || []).filter((x) => x !== "off"),
      (mode) => mode === e.state,
      (mode) => {
        const md = MODE[mode] || { ru: mode, icon: "auto" };
        return `<div class="chip ${mode === e.state ? "active" : ""}" data-mode="${mode}">
          ${svgIcon(md.icon, mode === e.state ? m.accent : "var(--secondary-text-color,#878d99)")}
          <span class="lbl">${md.ru || mode}</span></div>`;
      },
      (el) => { el.querySelectorAll("[data-mode]").forEach((c) =>
        c.onclick = () => this._call("climate", "set_hvac_mode",
          { entity_id: this._config.entity, hvac_mode: c.dataset.mode })); });

    // fan
    this._renderSeg("fan", "fanWrap", a.fan_modes, a.fan_mode,
      (v) => FAN_RU[v] || v,
      (v) => this._call("climate", "set_fan_mode", { entity_id: this._config.entity, fan_mode: v }), on);

    // swing (vertical + horizontal as two cycling pills)
    const swWrap = this._root.getElementById("swingWrap");
    const sw = this._root.getElementById("swing");
    const items = [];
    if (a.swing_modes) items.push(["⇅ Вертикальные", a.swing_mode, a.swing_modes, "set_swing_mode", "swing_mode"]);
    if (a.swing_horizontal_modes) items.push(["⇄ Горизонтальные", a.swing_horizontal_mode, a.swing_horizontal_modes, "set_swing_horizontal_mode", "swing_horizontal_mode"]);
    swWrap.style.display = items.length ? "" : "none";
    sw.innerHTML = items.map((it, i) => {
      const active = it[1] && it[1] !== "off";
      return `<div class="pill ${active ? "active" : ""}" data-sw="${i}">
        <span class="pd"></span><span>${it[0]}: ${SWING_RU[it[1]] || it[1] || "—"}</span></div>`;
    }).join("");
    sw.querySelectorAll("[data-sw]").forEach((el) => {
      const it = items[+el.dataset.sw];
      el.onclick = () => {
        const opts = it[2]; const idx = opts.indexOf(it[1]);
        const next = opts[(idx + 1) % opts.length];
        this._call("climate", it[3], { entity_id: this._config.entity, [it[4]]: next });
      };
    });

    // eco select
    const ecoId = this._config.eco_sensor;
    const ecoSt = this._st(ecoId);
    this._root.getElementById("ecoWrap").style.display = ecoSt ? "" : "none";
    if (ecoSt) {
      const eco = this._root.getElementById("eco");
      eco.innerHTML = `<select class="eco">${(ecoSt.attributes.options || [])
        .map((o) => `<option ${o === ecoSt.state ? "selected" : ""}>${o}</option>`).join("")}</select>`;
      eco.querySelector("select").onchange = (ev) =>
        this._call("select", "select_option", { entity_id: ecoId, option: ev.target.value });
    }

    // feature switches
    const feats = (this._config.features || []).map((id) => this._st(id)).filter(Boolean);
    this._root.getElementById("featWrap").style.display = feats.length ? "" : "none";
    const fc = this._root.getElementById("feats");
    fc.innerHTML = feats.map((st) => {
      const active = st.state === "on";
      return `<div class="pill ${active ? "active" : ""}" data-ent="${st.entity_id}">
        <span class="pd"></span><span>${st.attributes.friendly_name || st.entity_id}</span></div>`;
    }).join("");
    fc.querySelectorAll("[data-ent]").forEach((el) =>
      el.onclick = () => this._call("switch", "toggle", { entity_id: el.dataset.ent }));
  }

  _renderChips(id, wrapId, list, isActive, tpl, bind) {
    const wrap = this._root.getElementById(wrapId);
    wrap.style.display = (list && list.length) ? "" : "none";
    if (!list || !list.length) return;
    const el = this._root.getElementById(id);
    el.innerHTML = list.map(tpl).join("");
    bind(el);
  }

  _renderSeg(id, wrapId, list, current, label, onPick, enabled) {
    const wrap = this._root.getElementById(wrapId);
    wrap.style.display = (list && list.length) ? "" : "none";
    if (!list || !list.length) return;
    const el = this._root.getElementById(id);
    // When disabled (AC off) show it as such instead of silently swallowing taps.
    el.style.opacity = enabled ? "" : "0.45";
    el.style.pointerEvents = enabled ? "" : "none";
    el.innerHTML = list.map((v) =>
      `<button class="${v === current ? "active" : ""}" data-v="${v}">${label(v)}</button>`).join("");
    el.querySelectorAll("button").forEach((b) =>
      b.onclick = () => { if (enabled) onPick(b.dataset.v); });
  }

  _tint(hex) {
    const m = hex.replace("#", "");
    const r = parseInt(m.slice(0, 2), 16), g = parseInt(m.slice(2, 4), 16), b = parseInt(m.slice(4, 6), 16);
    return `rgba(${r},${g},${b},0.16)`;
  }

  static getStubConfig() {
    return { entity: "climate.kondicioner", name: "Кондиционер", subtitle: "Спальня" };
  }
}

customElements.define("haier-ac-card", HaierAcCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "haier-ac-card",
  name: "Haier Evo AC Card",
  description: "Карточка управления кондиционером Haier Evo (тёмная тема).",
  preview: true,
});
console.info("%c HAIER-AC-CARD %c loaded ", "background:#3ea6ff;color:#fff;border-radius:4px 0 0 4px;padding:2px 6px",
  "background:#23272f;color:#3ea6ff;border-radius:0 4px 4px 0;padding:2px 6px");
