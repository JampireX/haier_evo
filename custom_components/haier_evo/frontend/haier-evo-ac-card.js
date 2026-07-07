/* Haier Evo AC Card — кастомная карточка управления кондиционером haier_evo.
 * Работает только с climate-сущностями интеграции haier_evo (тип AC).
 * Все опции (режимы, вентилятор, свинг, пресеты, свитчи/селекты устройства)
 * определяются динамически из атрибутов сущностей. */

const MODE_META = {
    off: { icon: "mdi:power", label: "Выкл", color: "var(--disabled-text-color, #9e9e9e)" },
    auto: { icon: "mdi:thermostat-auto", label: "Авто", color: "#4caf50" },
    cool: { icon: "mdi:snowflake", label: "Охлаждение", color: "#2196f3" },
    heat: { icon: "mdi:fire", label: "Обогрев", color: "#ff9800" },
    dry: { icon: "mdi:water-percent", label: "Осушение", color: "#00bcd4" },
    fan_only: { icon: "mdi:fan", label: "Вентиляция", color: "#607d8b" },
};

const FAN_LABELS = {
    auto: "Авто", low: "Тихий", medium: "Средний", high: "Быстрый",
    quiet: "Тихий", min: "Мин", max: "Макс",
};

const SWING_LABELS = {
    off: "Выкл", auto: "Авто", both: "Оба",
    vertical: "Вертикально", horizontal: "Горизонтально",
    upper: "Вверх", lower: "Вниз", middle: "Середина",
    position_1: "Позиция 1", position_2: "Позиция 2", position_3: "Позиция 3",
    position_4: "Позиция 4", position_5: "Позиция 5",
};

const PRESET_LABELS = {
    none: "Обычный", eco: "Эко", boost: "Турбо", sleep: "Сон",
    comfort: "Комфорт", away: "Отсутствие", activity: "Активность", home: "Дома",
};

const label = (map, v) => map[v] || (v ? String(v).replace(/_/g, " ") : v);

class HaierEvoAcCard extends HTMLElement {

    static getConfigElement() {
        return document.createElement("haier-evo-ac-card-editor");
    }

    static getStubConfig(hass) {
        return { entity: HaierEvoAcCard.findAcEntities(hass)[0] || "" };
    }

    static findAcEntities(hass) {
        if (!hass) return [];
        return Object.keys(hass.states).filter((id) =>
            id.startsWith("climate.") && hass.entities?.[id]?.platform === "haier_evo");
    }

    setConfig(config) {
        if (!config.entity) {
            throw new Error("Укажите climate-сущность кондиционера Haier Evo");
        }
        this._config = config;
        this._lastRenderKey = null;
    }

    set hass(hass) {
        this._hass = hass;
        this._render();
    }

    getCardSize() {
        return 7;
    }

    /* ---------- data helpers ---------- */

    get _stateObj() {
        return this._hass?.states?.[this._config?.entity];
    }

    _isHaierAc() {
        const id = this._config?.entity || "";
        return id.startsWith("climate.") && this._hass?.entities?.[id]?.platform === "haier_evo";
    }

    _deviceSiblings() {
        const reg = this._hass?.entities || {};
        const deviceId = reg[this._config.entity]?.device_id;
        if (!deviceId) return { switches: [], selects: [], sensors: [] };
        const out = { switches: [], selects: [], sensors: [] };
        for (const [id, entry] of Object.entries(reg)) {
            if (entry.device_id !== deviceId || entry.hidden || entry.disabled_by) continue;
            const st = this._hass.states[id];
            if (!st) continue;
            if (id.startsWith("switch.")) out.switches.push(st);
            else if (id.startsWith("select.")) out.selects.push(st);
            else if (id.startsWith("sensor.") || id.startsWith("binary_sensor.")) out.sensors.push(st);
        }
        return out;
    }

    _call(domain, service, data) {
        this._hass.callService(domain, service, data);
    }

    /* ---------- rendering ---------- */

    _render() {
        if (!this._hass || !this._config) return;
        if (!this.shadowRoot) this.attachShadow({ mode: "open" });

        const st = this._stateObj;
        if (!st || !this._isHaierAc()) {
            this.shadowRoot.innerHTML = `<ha-card><div style="padding:16px;color:var(--error-color)">
                Карточка Haier Evo AC работает только с кондиционерами интеграции haier_evo.<br>
                Сущность: <code>${this._config.entity || "не задана"}</code></div></ha-card>`;
            return;
        }

        const sib = this._deviceSiblings();
        // Перерисовываем только при реальном изменении задействованных состояний
        const renderKey = JSON.stringify([
            st.state, st.attributes,
            sib.switches.map((s) => [s.entity_id, s.state]),
            sib.selects.map((s) => [s.entity_id, s.state]),
            sib.sensors.map((s) => [s.entity_id, s.state]),
        ]);
        if (renderKey === this._lastRenderKey) return;
        this._lastRenderKey = renderKey;

        const a = st.attributes;
        const isOn = st.state !== "off" && st.state !== "unavailable";
        const unavailable = st.state === "unavailable";
        const name = this._config.name || a.friendly_name || st.entity_id;
        const target = a.temperature;
        const current = a.current_temperature;

        this.shadowRoot.innerHTML = `
            <style>${HaierEvoAcCard.styles}</style>
            <ha-card>
                <div class="card ${unavailable ? "unavailable" : ""}">
                    <div class="header">
                        <div class="title">
                            <div class="name">${name}</div>
                            <div class="sub">${unavailable ? "недоступен" :
                                `в комнате ${current != null ? current + " °C" : "—"}`}</div>
                        </div>
                        <button class="power ${isOn ? "on" : ""}" id="power" title="Вкл/выкл">
                            <ha-icon icon="mdi:power"></ha-icon>
                        </button>
                    </div>

                    <div class="temp-row">
                        <button class="step" id="temp-down"><ha-icon icon="mdi:minus"></ha-icon></button>
                        <div class="temp">
                            <span class="value">${target != null ? target : "—"}</span><span class="unit">°C</span>
                        </div>
                        <button class="step" id="temp-up"><ha-icon icon="mdi:plus"></ha-icon></button>
                    </div>

                    ${this._sectionModes(st)}
                    ${this._sectionChips("Вентилятор", "fan", a.fan_modes, a.fan_mode, FAN_LABELS)}
                    ${this._sectionChips("Свинг (вертикальный)", "swing", a.swing_modes, a.swing_mode, SWING_LABELS)}
                    ${this._sectionChips("Свинг (горизонтальный)", "swingh", a.swing_horizontal_modes, a.swing_horizontal_mode, SWING_LABELS)}
                    ${this._sectionChips("Пресет", "preset", a.preset_modes, a.preset_mode, PRESET_LABELS)}
                    ${this._sectionSwitches(sib.switches)}
                    ${this._sectionSelects(sib.selects)}
                    ${this._sectionSensors(sib.sensors)}
                </div>
            </ha-card>`;

        this._bind(st, sib);
    }

    _sectionModes(st) {
        const modes = st.attributes.hvac_modes || [];
        if (!modes.length) return "";
        const btns = modes.map((m) => {
            const meta = MODE_META[m] || { icon: "mdi:circle", label: m, color: "var(--primary-color)" };
            const active = st.state === m;
            return `<button class="mode ${active ? "active" : ""}" data-mode="${m}"
                        style="${active ? `--mode-color:${meta.color}` : ""}" title="${meta.label}">
                        <ha-icon icon="${meta.icon}"></ha-icon><span>${meta.label}</span>
                    </button>`;
        }).join("");
        return `<div class="section"><div class="label">Режим</div><div class="modes">${btns}</div></div>`;
    }

    _sectionChips(title, kind, options, current, labels) {
        if (!options || !options.length) return "";
        const chips = options.map((o) =>
            `<button class="chip ${o === current ? "active" : ""}" data-kind="${kind}" data-value="${o}">
                ${label(labels, o)}</button>`).join("");
        return `<div class="section"><div class="label">${title}</div><div class="chips">${chips}</div></div>`;
    }

    _sectionSwitches(switches) {
        if (!switches.length) return "";
        const rows = switches.map((s) => `
            <div class="toggle-row">
                <span>${s.attributes.friendly_name || s.entity_id}</span>
                <ha-switch data-entity="${s.entity_id}" ${s.state === "on" ? "checked" : ""}></ha-switch>
            </div>`).join("");
        return `<div class="section"><div class="label">Функции</div>${rows}</div>`;
    }

    _sectionSelects(selects) {
        if (!selects.length) return "";
        const rows = selects.map((s) => {
            const opts = (s.attributes.options || []).map((o) =>
                `<option value="${o}" ${o === s.state ? "selected" : ""}>${o}</option>`).join("");
            return `<div class="select-row">
                <span>${s.attributes.friendly_name || s.entity_id}</span>
                <select data-entity="${s.entity_id}">${opts}</select>
            </div>`;
        }).join("");
        return `<div class="section"><div class="label">Настройки</div>${rows}</div>`;
    }

    _sectionSensors(sensors) {
        if (!sensors.length) return "";
        const items = sensors.map((s) => {
            const unit = s.attributes.unit_of_measurement || "";
            return `<div class="sensor"><span class="sname">${s.attributes.friendly_name || s.entity_id}</span>
                <span class="svalue">${s.state}${unit ? " " + unit : ""}</span></div>`;
        }).join("");
        return `<div class="section"><div class="label">Датчики</div><div class="sensors">${items}</div></div>`;
    }

    /* ---------- events ---------- */

    _bind(st, sib) {
        const root = this.shadowRoot;
        const entity_id = st.entity_id;
        const a = st.attributes;

        root.getElementById("power")?.addEventListener("click", () => {
            this._call("climate", st.state === "off" ? "turn_on" : "turn_off", { entity_id });
        });

        const step = a.target_temp_step || 1;
        const clamp = (t) => Math.min(a.max_temp ?? 30, Math.max(a.min_temp ?? 16, t));
        root.getElementById("temp-up")?.addEventListener("click", () => {
            if (a.temperature == null) return;
            this._call("climate", "set_temperature", { entity_id, temperature: clamp(a.temperature + step) });
        });
        root.getElementById("temp-down")?.addEventListener("click", () => {
            if (a.temperature == null) return;
            this._call("climate", "set_temperature", { entity_id, temperature: clamp(a.temperature - step) });
        });

        root.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => {
            this._call("climate", "set_hvac_mode", { entity_id, hvac_mode: b.dataset.mode });
        }));

        const chipService = {
            fan: ["set_fan_mode", "fan_mode"],
            swing: ["set_swing_mode", "swing_mode"],
            swingh: ["set_swing_horizontal_mode", "swing_horizontal_mode"],
            preset: ["set_preset_mode", "preset_mode"],
        };
        root.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
            const [service, field] = chipService[b.dataset.kind];
            this._call("climate", service, { entity_id, [field]: b.dataset.value });
        }));

        root.querySelectorAll("ha-switch[data-entity]").forEach((sw) => sw.addEventListener("change", () => {
            this._call("switch", sw.checked ? "turn_on" : "turn_off", { entity_id: sw.dataset.entity });
        }));

        root.querySelectorAll("select[data-entity]").forEach((sel) => sel.addEventListener("change", () => {
            this._call("select", "select_option", { entity_id: sel.dataset.entity, option: sel.value });
        }));
    }

    static styles = `
        .card { padding: 16px; }
        .card.unavailable { opacity: .5; pointer-events: none; }
        .header { display: flex; align-items: center; justify-content: space-between; }
        .name { font-size: 1.15em; font-weight: 500; }
        .sub { color: var(--secondary-text-color); font-size: .9em; margin-top: 2px; }
        .power { width: 44px; height: 44px; border-radius: 50%; border: none; cursor: pointer;
                 background: var(--secondary-background-color); color: var(--secondary-text-color);
                 display: flex; align-items: center; justify-content: center; transition: all .2s; }
        .power.on { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .temp-row { display: flex; align-items: center; justify-content: center; gap: 24px; margin: 18px 0 6px; }
        .temp .value { font-size: 2.6em; font-weight: 300; }
        .temp .unit { font-size: 1.2em; color: var(--secondary-text-color); margin-left: 2px; }
        .step { width: 40px; height: 40px; border-radius: 50%; border: none; cursor: pointer;
                background: var(--secondary-background-color); color: var(--primary-text-color);
                display: flex; align-items: center; justify-content: center; }
        .step:active { background: var(--primary-color); color: #fff; }
        .section { margin-top: 14px; }
        .label { font-size: .8em; text-transform: uppercase; letter-spacing: .5px;
                 color: var(--secondary-text-color); margin-bottom: 6px; }
        .modes { display: flex; gap: 8px; flex-wrap: wrap; }
        .mode { flex: 1 1 0; min-width: 64px; padding: 8px 4px; border-radius: 12px; border: none; cursor: pointer;
                background: var(--secondary-background-color); color: var(--secondary-text-color);
                display: flex; flex-direction: column; align-items: center; gap: 4px; font-size: .75em; }
        .mode.active { background: var(--mode-color, var(--primary-color)); color: #fff; }
        .chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .chip { padding: 6px 14px; border-radius: 16px; border: none; cursor: pointer; font-size: .85em;
                background: var(--secondary-background-color); color: var(--primary-text-color); }
        .chip.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
        .toggle-row, .select-row { display: flex; align-items: center; justify-content: space-between;
                padding: 6px 0; font-size: .95em; }
        .select-row select { background: var(--secondary-background-color); color: var(--primary-text-color);
                border: none; border-radius: 8px; padding: 6px 10px; font-size: .95em; }
        .sensors { display: flex; gap: 16px; flex-wrap: wrap; }
        .sensor { display: flex; flex-direction: column; }
        .sname { font-size: .75em; color: var(--secondary-text-color); }
        .svalue { font-size: .95em; }
    `;
}

/* ---------- editor ---------- */

class HaierEvoAcCardEditor extends HTMLElement {

    setConfig(config) {
        this._config = { ...config };
        this._render();
    }

    set hass(hass) {
        this._hass = hass;
        this._render();
    }

    _render() {
        if (!this._hass || !this._config) return;
        const acs = HaierEvoAcCard.findAcEntities(this._hass);
        const options = acs.map((id) => {
            const nm = this._hass.states[id]?.attributes?.friendly_name || id;
            return `<option value="${id}" ${id === this._config.entity ? "selected" : ""}>${nm} (${id})</option>`;
        }).join("");
        this.innerHTML = `
            <div style="padding: 8px 0;">
                <label style="display:block; font-size:.9em; margin-bottom:4px;">Кондиционер Haier Evo</label>
                <select id="entity" style="width:100%; padding:8px; border-radius:8px;
                        background: var(--secondary-background-color); color: var(--primary-text-color); border:none;">
                    <option value="">— выберите кондиционер —</option>${options}
                </select>
                ${acs.length ? "" : `<div style="color:var(--error-color); margin-top:8px;">
                    Кондиционеры haier_evo не найдены</div>`}
            </div>`;
        this.querySelector("#entity").addEventListener("change", (e) => {
            this._config = { ...this._config, entity: e.target.value };
            this.dispatchEvent(new CustomEvent("config-changed", {
                detail: { config: this._config }, bubbles: true, composed: true,
            }));
        });
    }
}

customElements.define("haier-evo-ac-card", HaierEvoAcCard);
customElements.define("haier-evo-ac-card-editor", HaierEvoAcCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
    type: "haier-evo-ac-card",
    name: "Haier Evo AC Card",
    description: "Карточка управления кондиционером Haier Evo (все режимы и функции)",
    preview: true,
});
