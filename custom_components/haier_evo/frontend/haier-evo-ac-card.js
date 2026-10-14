/* Haier Evo AC Card — минималистичная и гибко настраиваемая карточка кондиционера.
 *
 * Работает с любой climate-сущностью (редактор по умолчанию предлагает кондиционеры
 * haier_evo). Режимы, вентилятор, свинг и пресеты берутся из атрибутов сущности,
 * переключатели, селекты и датчики — с того же устройства. Подписи значений переводит
 * сам Home Assistant (formatEntityState / formatEntityAttributeValue), поэтому они
 * совпадают с языком интерфейса; свои подписи, иконки и цвета задаются в YAML.
 *
 * Все параметры, кроме entity, необязательны:
 *   name, show_name, show_current, show_power, show_labels, compact,
 *   hide_when_off (true | [секции]), control_style ("pills" | "chips"),
 *   sections ([temperature, modes, fan, swing, swing_horizontal, preset,
 *             switches, selects, sensors] — состав и порядок),
 *   modes / fan_modes / swing_modes / swing_horizontal_modes / preset_modes
 *     (какие значения показывать и в каком порядке),
 *   switches / selects / sensors (списки сущностей; по умолчанию — всё с устройства),
 *   exclude (сущности, которые не показывать), temperature_step,
 *   debounce (мс; нажатия −/+ собираются в одну команду),
 *   labels ({mode|fan|swing|swing_horizontal|preset: {значение: подпись}}),
 *   icons ({значение|секция|entity_id: "mdi:…"}), colors ({режим: цвет}),
 *   accent_color, style (дополнительный CSS).
 */

// Wrapped in a function scope: loading the file twice (e.g. also added as a Lovelace resource)
// must not clash on top-level declarations.
(() => {
"use strict";

const SECTIONS = [
    "temperature", "modes", "fan", "swing", "swing_horizontal", "preset",
    "switches", "selects", "sensors",
];

const DEFAULTS = {
    show_name: true,
    show_current: true,
    show_power: true,
    show_labels: false,
    compact: false,
    hide_when_off: false,
    control_style: "pills",
    sections: ["temperature", "modes", "fan", "swing", "swing_horizontal", "preset", "switches"],
    debounce: 700,
};

// Climate-backed choice controls: options attribute, current-value attribute, service.
const CONTROLS = {
    fan: { options: "fan_modes", attr: "fan_mode", service: "set_fan_mode", icon: "mdi:fan" },
    swing: { options: "swing_modes", attr: "swing_mode", service: "set_swing_mode", icon: "mdi:arrow-up-down" },
    swing_horizontal: {
        options: "swing_horizontal_modes", attr: "swing_horizontal_mode",
        service: "set_swing_horizontal_mode", icon: "mdi:arrow-left-right",
    },
    preset: { options: "preset_modes", attr: "preset_mode", service: "set_preset_mode", icon: "mdi:star-four-points-outline" },
};

const MODE_ICONS = {
    off: "mdi:power", auto: "mdi:thermostat-auto", cool: "mdi:snowflake", heat: "mdi:fire",
    heat_cool: "mdi:sun-snowflake-variant", dry: "mdi:water-percent", fan_only: "mdi:fan",
};

// Theme-aware (HA state colours), overridable per mode via `colors`.
const MODE_COLORS = {
    off: "var(--state-climate-off-color, var(--disabled-text-color, #9e9e9e))",
    auto: "var(--state-climate-auto-color, #4caf50)",
    cool: "var(--state-climate-cool-color, #2196f3)",
    heat: "var(--state-climate-heat-color, #ff8100)",
    heat_cool: "var(--state-climate-heat_cool-color, #ffa000)",
    dry: "var(--state-climate-dry-color, #00bcd4)",
    fan_only: "var(--state-climate-fan_only-color, #8a8a8a)",
};

// Icons for the haier_evo switches, by their translation key.
const SWITCH_ICONS = {
    ac_light: "mdi:lightbulb-outline", ac_sound: "mdi:volume-high", ac_quiet: "mdi:weather-night",
    ac_turbo: "mdi:rocket-launch-outline", ac_health: "mdi:leaf", ac_comfort: "mdi:sleep",
    ac_cleaning: "mdi:broom", ac_antifreeze: "mdi:snowflake-thermometer",
    ac_autohumidity: "mdi:water-percent",
};

const TEXT = {
    ru: {
        missing: "Сущность не найдена", turn_on: "Включить", turn_off: "Выключить",
        decrease: "Уменьшить", increase: "Увеличить",
        fan: "Вентилятор", swing: "Вертикальный поток", swing_horizontal: "Горизонтальный поток",
        preset: "Пресет",
    },
    en: {
        missing: "Entity not found", turn_on: "Turn on", turn_off: "Turn off",
        decrease: "Decrease", increase: "Increase",
        fan: "Fan", swing: "Vertical swing", swing_horizontal: "Horizontal swing", preset: "Preset",
    },
};

// Used only when Home Assistant cannot translate a value itself.
const FALLBACK_LABELS = {
    mode: {
        off: "Выкл", auto: "Авто", cool: "Охлаждение", heat: "Обогрев",
        heat_cool: "Обогрев/охлаждение", dry: "Осушение", fan_only: "Вентиляция",
    },
    fan: { auto: "Авто", low: "Низкая", medium: "Средняя", high: "Высокая" },
    swing: { off: "Выкл", on: "Вкл", auto: "Авто" },
    preset: { none: "Нет", sleep: "Сон", boost: "Турбо", eco: "Эко", comfort: "Комфорт" },
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class HaierEvoAcCard extends HTMLElement {

    static getConfigForm() {
        return haierEvoAcCardForm();
    }

    static getStubConfig(hass) {
        const climates = Object.keys(hass?.states || {}).filter((id) => id.startsWith("climate."));
        const haier = climates.filter((id) => hass.entities?.[id]?.platform === "haier_evo");
        return { entity: haier[0] || climates[0] || "" };
    }

    setConfig(config) {
        if (!config || !String(config.entity || "").startsWith("climate.")) {
            throw new Error("entity: укажите climate-сущность (climate.…)");
        }
        this._config = { ...DEFAULTS, ...config };
        this._key = null;
        this._render();
    }

    set hass(hass) {
        this._hass = hass;
        this._render();
    }

    get hass() {
        return this._hass;
    }

    connectedCallback() {
        this._render();
    }

    disconnectedCallback() {
        // Do not lose a temperature change that is still waiting for the debounce.
        if (this._timer) {
            clearTimeout(this._timer);
            this._sendTemperature();
        }
    }

    getCardSize() {
        const st = this._stateObj;
        if (!st || !this._config) return 3;
        const rows = { temperature: 2, modes: 1, switches: 1, selects: 1, sensors: 1 };
        const sections = this._sections(st.state === "off");
        let size = 1 + sections.reduce((n, s) => n + (rows[s] ?? 0), 0);
        const choices = sections.filter((s) => CONTROLS[s]).length;
        size += this._config.control_style === "chips" ? choices : Math.min(choices, 1);
        return Math.max(size, 3);
    }

    getGridOptions() {
        return { columns: 6, min_columns: 4 };
    }

    /* ---------- data helpers ---------- */

    get _stateObj() {
        return this._hass?.states?.[this._config?.entity];
    }

    get _lang() {
        return String(this._hass?.locale?.language || this._hass?.language || "ru").startsWith("ru") ? "ru" : "en";
    }

    _t(key) {
        return TEXT[this._lang][key];
    }

    _sections(off) {
        const cfg = this._config;
        const hidden = cfg.hide_when_off === true
            ? SECTIONS.filter((s) => s !== "modes")
            : (Array.isArray(cfg.hide_when_off) ? cfg.hide_when_off : []);
        const wanted = Array.isArray(cfg.sections) && cfg.sections.length ? cfg.sections : DEFAULTS.sections;
        return wanted.filter((s, i) => SECTIONS.includes(s) && wanted.indexOf(s) === i && !(off && hidden.includes(s)));
    }

    // Values to show for a choice control: configured subset/order, else what the entity offers.
    _values(kind, available) {
        const key = kind === "mode" ? "modes" : `${kind}_modes`;
        const wanted = this._config[key];
        if (!Array.isArray(wanted) || !wanted.length) return available;
        return wanted.filter((v) => available.includes(v));
    }

    _label(kind, value, stateObj = this._stateObj) {
        const labels = this._config.labels || {};
        const custom = labels[kind]?.[value] ?? (typeof labels[value] === "string" ? labels[value] : undefined);
        if (custom != null) return String(custom);
        if (value == null || value === "") return "—";
        let translated;
        try {
            translated = kind === "mode"
                ? this._hass.formatEntityState?.(stateObj, value)
                : this._hass.formatEntityAttributeValue?.(stateObj, CONTROLS[kind].attr, value);
        } catch (e) {
            translated = undefined;
        }
        if (translated && translated !== value) return translated;
        const group = kind === "swing_horizontal" ? "swing" : kind;
        return FALLBACK_LABELS[group]?.[value] ?? String(value).replace(/_/g, " ");
    }

    _controlTitle(kind) {
        let title;
        try {
            title = this._hass.formatEntityAttributeName?.(this._stateObj, CONTROLS[kind].attr);
        } catch (e) {
            title = undefined;
        }
        return title && title !== CONTROLS[kind].attr ? title : this._t(kind);
    }

    _icon(key, fallback) {
        return this._config.icons?.[key] || fallback;
    }

    _modeColor(mode) {
        return this._config.colors?.[mode] || MODE_COLORS[mode] || "var(--hac-accent)";
    }

    _num(value, step) {
        const n = Number(value);
        if (!Number.isFinite(n)) return "—";
        const digits = (step && step < 1) || !Number.isInteger(n) ? 1 : 0;
        return n.toLocaleString(this._hass?.locale?.language || this._hass?.language || undefined,
            { minimumFractionDigits: digits, maximumFractionDigits: digits });
    }

    get _unit() {
        return this._hass?.config?.unit_system?.temperature || "°C";
    }

    // Entities of the same device (switches / selects / sensors), unless listed explicitly.
    _entities(kind) {
        const listed = this._config[kind];
        const exclude = this._config.exclude || [];
        let ids;
        if (Array.isArray(listed) && listed.length) {
            ids = listed;
        } else {
            const domains = { switches: ["switch"], selects: ["select"], sensors: ["sensor", "binary_sensor"] }[kind];
            const reg = this._hass.entities || {};
            const device = reg[this._config.entity]?.device_id;
            ids = !device ? [] : Object.keys(reg).filter((id) => {
                const entry = reg[id];
                return entry.device_id === device && !entry.hidden && !entry.disabled_by
                    && domains.includes(id.split(".")[0]);
            });
        }
        return ids.filter((id) => !exclude.includes(id) && this._hass.states[id]).map((id) => this._hass.states[id]);
    }

    // "Кондиционер Подсветка" -> "Подсветка" (the device name is redundant inside the card).
    _shortName(stateObj) {
        const full = stateObj.attributes.friendly_name || stateObj.entity_id;
        const device = this._stateObj?.attributes.friendly_name;
        return device && full.startsWith(`${device} `) ? full.slice(device.length + 1) : full;
    }

    _entityIcon(stateObj, fallback) {
        const entry = this._hass.entities?.[stateObj.entity_id];
        const generic = ["mdi:toggle-switch", "mdi:format-list-bulleted"];
        const own = stateObj.attributes.icon && !generic.includes(stateObj.attributes.icon)
            ? stateObj.attributes.icon : null;
        return this._icon(stateObj.entity_id, entry?.icon || own || SWITCH_ICONS[entry?.translation_key] || fallback);
    }

    _call(domain, service, data) {
        this._hass.callService(domain, service, data);
    }

    /* ---------- rendering ---------- */

    _render() {
        if (!this._hass || !this._config) return;
        if (!this.shadowRoot) {
            this.attachShadow({ mode: "open" });
            this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
            this.shadowRoot.addEventListener("change", (ev) => this._onChange(ev));
        }
        const cfg = this._config;
        const st = this._stateObj;
        const css = `<style>${HaierEvoAcCard.styles}${cfg.style ? `\n${cfg.style}` : ""}</style>`;
        if (!st) {
            this._key = null;
            this.shadowRoot.innerHTML = `${css}<ha-card><div class="warn">${esc(this._t("missing"))}: `
                + `${esc(cfg.entity)}</div></ha-card>`;
            return;
        }
        // The command is applied optimistically; once the entity reports it, stop showing the
        // pending value.
        if (this._pending != null && !this._timer && Number(st.attributes.temperature) === this._pending) {
            this._pending = null;
        }

        const off = st.state === "off";
        const sections = this._sections(off);
        const switches = sections.includes("switches") ? this._entities("switches") : [];
        const selects = sections.includes("selects") ? this._entities("selects") : [];
        const sensors = sections.includes("sensors") ? this._entities("sensors") : [];
        const key = JSON.stringify([
            st.state, st.attributes, this._pending, this._hass.language, this._hass.locale,
            [...switches, ...selects, ...sensors].map((s) => [s.entity_id, s.state, s.attributes.options]),
        ]);
        if (key === this._key) return;
        this._key = key;

        const unavailable = st.state === "unavailable" || st.state === "unknown";
        const body = [];
        let pills = [];
        const flushPills = () => {
            if (pills.length) body.push(`<div class="pills">${pills.join("")}</div>`);
            pills = [];
        };
        for (const section of sections) {
            if (CONTROLS[section]) {
                const html = this._renderControl(section, st);
                if (!html) continue;
                if (cfg.control_style === "chips") {
                    flushPills();
                    body.push(html);
                } else {
                    pills.push(html);
                }
                continue;
            }
            if (section === "selects" && cfg.control_style !== "chips") {
                pills.push(...selects.map((s) => this._renderSelect(s)));
                continue;
            }
            flushPills();
            if (section === "temperature") body.push(this._renderTemperature(st));
            else if (section === "modes") body.push(this._renderModes(st));
            else if (section === "switches") body.push(this._renderSwitches(switches));
            else if (section === "selects") body.push(selects.map((s) => this._renderSelect(s)).join(""));
            else if (section === "sensors") body.push(this._renderSensors(sensors));
        }
        flushPills();

        const vars = [`--hac-mode:${this._modeColor(off ? "off" : st.state)}`];
        if (cfg.accent_color) vars.push(`--hac-accent:${cfg.accent_color}`);
        this.shadowRoot.innerHTML = `${css}
            <ha-card class="${cfg.compact ? "compact" : ""} ${off ? "off" : ""} ${unavailable ? "unavailable" : ""}"
                     style="${esc(vars.join(";"))}">
                ${this._renderHeader(st, off, unavailable)}
                ${body.join("")}
            </ha-card>`;
    }

    _renderHeader(st, off, unavailable) {
        const cfg = this._config;
        if (!cfg.show_name && !cfg.show_current && !cfg.show_power && !unavailable) return "";
        const name = cfg.name ?? st.attributes.friendly_name ?? st.entity_id;
        const current = st.attributes.current_temperature;
        let sub = "";
        if (unavailable) {
            sub = `<span class="sub">${esc(this._hass.formatEntityState?.(st) ?? st.state)}</span>`;
        } else if (cfg.show_current && current != null) {
            sub = `<span class="sub"><ha-icon icon="${esc(this._icon("current_temperature", "mdi:thermometer"))}">`
                + `</ha-icon>${esc(this._num(current))} ${esc(this._unit)}</span>`;
        }
        const power = cfg.show_power ? `
            <button class="ic pwr ${off ? "" : "on"}" data-act="power" title="${esc(this._t(off ? "turn_on" : "turn_off"))}"
                    aria-label="${esc(this._t(off ? "turn_on" : "turn_off"))}" aria-pressed="${!off}">
                <ha-icon icon="${esc(this._icon("power", "mdi:power"))}"></ha-icon>
            </button>` : "";
        return `<div class="hdr">
                <button class="info" data-act="more-info">
                    ${cfg.show_name ? `<span class="name">${esc(name)}</span>` : ""}${sub}
                </button>${power}
            </div>`;
    }

    _renderTemperature(st) {
        const target = this._pending ?? st.attributes.temperature;
        if (target == null) return "";
        const step = this._step(st);
        return `<div class="temp ${this._pending != null ? "pending" : ""}">
                <button class="ic" data-act="dec" title="${esc(this._t("decrease"))}" aria-label="${esc(this._t("decrease"))}">
                    <ha-icon icon="${esc(this._icon("decrease", "mdi:minus"))}"></ha-icon></button>
                <div class="t">${esc(this._num(target, step))}<span class="u">${esc(this._unit)}</span></div>
                <button class="ic" data-act="inc" title="${esc(this._t("increase"))}" aria-label="${esc(this._t("increase"))}">
                    <ha-icon icon="${esc(this._icon("increase", "mdi:plus"))}"></ha-icon></button>
            </div>`;
    }

    _renderModes(st) {
        let modes = this._values("mode", st.attributes.hvac_modes || []);
        // "off" is covered by the power button unless the modes are listed explicitly.
        if (!Array.isArray(this._config.modes) && this._config.show_power) modes = modes.filter((m) => m !== "off");
        if (!modes.length) return "";
        const buttons = modes.map((mode) => {
            const label = this._label("mode", mode, st);
            const active = st.state === mode;
            return `<button class="mode ${active ? "on" : ""}" data-act="mode" data-v="${esc(mode)}"
                        style="--c:${esc(this._modeColor(mode))}" title="${esc(label)}" aria-label="${esc(label)}"
                        aria-pressed="${active}">
                    <ha-icon icon="${esc(this._icon(mode, MODE_ICONS[mode] || "mdi:thermostat"))}"></ha-icon>
                    ${this._config.show_labels ? `<span>${esc(label)}</span>` : ""}
                </button>`;
        }).join("");
        return `<div class="modes">${buttons}</div>`;
    }

    _renderControl(kind, st) {
        const c = CONTROLS[kind];
        const values = this._values(kind, st.attributes[c.options] || []);
        if (!values.length) return "";
        return this._renderChoice({
            icon: this._icon(kind, c.icon),
            title: this._controlTitle(kind),
            values,
            current: st.attributes[c.attr],
            label: (v) => this._label(kind, v, st),
            data: `data-act="ctl" data-k="${esc(kind)}"`,
        });
    }

    _renderSelect(s) {
        const values = s.attributes.options || [];
        if (!values.length) return "";
        return this._renderChoice({
            icon: this._entityIcon(s, "mdi:form-dropdown"),
            title: this._shortName(s),
            values,
            current: s.state,
            label: (v) => this._hass.formatEntityState?.(s, v) ?? v,
            data: `data-act="select" data-e="${esc(s.entity_id)}"`,
        });
    }

    // A choice is either a compact pill (current value; a transparent native <select> on top
    // opens the picker) or a row of chips, depending on control_style.
    _renderChoice({ icon, title, values, current, label, data }) {
        const known = values.includes(current);
        if (this._config.control_style === "chips") {
            const chips = values.map((v) => `<button class="chip ${v === current ? "on" : ""}" ${data}
                    data-v="${esc(v)}" aria-pressed="${v === current}">${esc(label(v))}</button>`).join("");
            return `<div class="row"><ha-icon class="lead" icon="${esc(icon)}" title="${esc(title)}"></ha-icon>
                    <div class="chips">${chips}</div></div>`;
        }
        const text = known ? label(current) : "—";
        const options = (known ? "" : `<option value="" selected disabled hidden>—</option>`)
            + values.map((v) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(label(v))}</option>`).join("");
        return `<label class="pill" title="${esc(title)}">
                <ha-icon icon="${esc(icon)}"></ha-icon>
                <span>${esc(this._config.show_labels ? `${title}: ${text}` : text)}</span>
                <select ${data} aria-label="${esc(title)}">${options}</select>
            </label>`;
    }

    _renderSwitches(switches) {
        if (!switches.length) return "";
        const labels = this._config.show_labels;
        const buttons = switches.map((s) => {
            const name = this._shortName(s);
            const on = s.state === "on";
            return `<button class="tg ${on ? "on" : ""} ${labels ? "wide" : ""}" data-act="toggle"
                        data-e="${esc(s.entity_id)}" title="${esc(name)}" aria-label="${esc(name)}" aria-pressed="${on}">
                    <ha-icon icon="${esc(this._entityIcon(s, "mdi:toggle-switch-outline"))}"></ha-icon>
                    ${labels ? `<span>${esc(name)}</span>` : ""}
                </button>`;
        }).join("");
        return `<div class="toggles">${buttons}</div>`;
    }

    _renderSensors(sensors) {
        if (!sensors.length) return "";
        const items = sensors.map((s) => {
            const value = this._hass.formatEntityState?.(s)
                ?? `${s.state}${s.attributes.unit_of_measurement ? ` ${s.attributes.unit_of_measurement}` : ""}`;
            return `<span class="sn"><ha-icon icon="${esc(this._entityIcon(s, "mdi:eye-outline"))}"></ha-icon>`
                + `${esc(this._shortName(s))} <b>${esc(value)}</b></span>`;
        }).join("");
        return `<div class="sensors">${items}</div>`;
    }

    /* ---------- events ---------- */

    _step(st) {
        return Number(this._config.temperature_step) || Number(st.attributes.target_temp_step) || 1;
    }

    _changeTemperature(direction) {
        const st = this._stateObj;
        const a = st.attributes;
        const base = this._pending ?? a.temperature;
        if (base == null) return;
        const step = this._step(st);
        const min = Number(a.min_temp ?? 7);
        const max = Number(a.max_temp ?? 35);
        const next = Math.round((Number(base) + direction * step) / step) * step;
        this._pending = Number(Math.min(max, Math.max(min, next)).toFixed(2));
        this._pendingEntity = st.entity_id;
        clearTimeout(this._timer);
        clearTimeout(this._resetTimer);
        const delay = Number(this._config.debounce) || 0;
        if (delay > 0) {
            this._timer = setTimeout(() => this._sendTemperature(), delay);
        } else {
            this._sendTemperature();
        }
        this._key = null;
        this._render();
    }

    _sendTemperature() {
        this._timer = null;
        if (this._pending == null || !this._hass) return;
        this._call("climate", "set_temperature", { entity_id: this._pendingEntity, temperature: this._pending });
        // If the entity never reports the value (e.g. the command failed), show the real one again.
        this._resetTimer = setTimeout(() => {
            this._pending = null;
            this._key = null;
            this._render();
        }, 5000);
    }

    _onClick(ev) {
        const el = ev.target?.closest?.("[data-act]");
        if (!el || el.tagName === "SELECT" || !this._stateObj) return;
        const st = this._stateObj;
        const entity_id = st.entity_id;
        switch (el.dataset.act) {
            case "power":
                this._call("climate", st.state === "off" ? "turn_on" : "turn_off", { entity_id });
                break;
            case "inc":
                this._changeTemperature(1);
                break;
            case "dec":
                this._changeTemperature(-1);
                break;
            case "mode":
                this._call("climate", "set_hvac_mode", { entity_id, hvac_mode: el.dataset.v });
                break;
            case "ctl": {
                const c = CONTROLS[el.dataset.k];
                this._call("climate", c.service, { entity_id, [c.attr]: el.dataset.v });
                break;
            }
            case "select":
                this._call("select", "select_option", { entity_id: el.dataset.e, option: el.dataset.v });
                break;
            case "toggle": {
                const target = this._hass.states[el.dataset.e];
                this._call("homeassistant", target?.state === "on" ? "turn_off" : "turn_on", { entity_id: el.dataset.e });
                break;
            }
            case "more-info":
                this.dispatchEvent(new CustomEvent("hass-more-info", {
                    detail: { entityId: entity_id }, bubbles: true, composed: true,
                }));
                break;
            default:
                break;
        }
    }

    _onChange(ev) {
        const el = ev.target;
        if (el?.tagName !== "SELECT" || !el.value || !this._stateObj) return;
        if (el.dataset.act === "ctl") {
            const c = CONTROLS[el.dataset.k];
            this._call("climate", c.service, { entity_id: this._stateObj.entity_id, [c.attr]: el.value });
        } else if (el.dataset.act === "select") {
            this._call("select", "select_option", { entity_id: el.dataset.e, option: el.value });
        }
    }

    /* Theming: override these CSS variables from a theme or the `style` option. */
    static styles = `
        :host {
            --hac-accent: var(--primary-color);
            --hac-btn: 40px;
            --hac-temp-size: 3rem;
            --hac-gap: 12px;
            --hac-radius: 12px;
        }
        ha-card { padding: 16px; display: flex; flex-direction: column; gap: var(--hac-gap); }
        ha-card.compact { padding: 12px; --hac-btn: 34px; --hac-temp-size: 2.25rem; --hac-gap: 8px; }
        ha-card.unavailable { opacity: .55; }
        ha-card.unavailable > :not(.hdr) { pointer-events: none; }
        .warn { color: var(--error-color); }
        button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0;
                 cursor: pointer; -webkit-tap-highlight-color: transparent; }
        button:focus-visible, .pill:focus-within { outline: 2px solid var(--hac-accent); outline-offset: 2px; }
        ha-icon { --mdc-icon-size: 22px; }
        .ic { width: var(--hac-btn); height: var(--hac-btn); border-radius: 50%; flex: none;
              display: inline-flex; align-items: center; justify-content: center;
              color: var(--secondary-text-color); transition: background-color .15s, color .15s; }
        .ic:hover, .mode:hover, .tg:hover, .chip:hover { background: var(--secondary-background-color); }
        .hdr { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
        .info { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; text-align: left; }
        .name { font-size: 1rem; font-weight: 500; color: var(--primary-text-color);
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
        .sub { display: inline-flex; align-items: center; gap: 2px; font-size: .875rem;
               color: var(--secondary-text-color); }
        .sub ha-icon { --mdc-icon-size: 16px; }
        .pwr.on { color: var(--hac-mode); }
        .temp { display: flex; align-items: center; justify-content: center; gap: 16px; }
        .t { font-size: var(--hac-temp-size); font-weight: 300; line-height: 1; min-width: 3ch; text-align: center;
             font-variant-numeric: tabular-nums; color: var(--primary-text-color); transition: opacity .15s; }
        .t .u { font-size: .35em; vertical-align: top; margin-left: 2px; color: var(--secondary-text-color); }
        .temp.pending .t { opacity: .6; }
        ha-card.off .t { color: var(--disabled-text-color, var(--secondary-text-color)); }
        .modes { display: flex; gap: 4px; }
        .mode { flex: 1 1 0; min-width: 0; min-height: var(--hac-btn); border-radius: var(--hac-radius);
                display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
                color: var(--secondary-text-color); transition: background-color .15s, color .15s; }
        .mode span { font-size: .6875rem; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .mode.on { color: var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); }
        .pills { display: flex; flex-wrap: wrap; gap: 8px; }
        .pill { position: relative; display: inline-flex; align-items: center; gap: 6px; height: 32px;
                padding: 0 12px; border-radius: 16px; border: 1px solid var(--divider-color);
                font-size: .8125rem; color: var(--primary-text-color); cursor: pointer; max-width: 100%; }
        .pill span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .pill ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
        .pill select { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer;
                       font-size: 16px; }
        .row { display: flex; align-items: center; gap: 8px; }
        .row .lead { --mdc-icon-size: 18px; color: var(--secondary-text-color); flex: none; }
        .chips { display: flex; flex-wrap: wrap; gap: 6px; }
        .chip { padding: 6px 12px; border-radius: 16px; font-size: .8125rem; border: 1px solid var(--divider-color);
                color: var(--primary-text-color); }
        .chip.on { border-color: transparent; color: var(--hac-accent);
                   background: color-mix(in srgb, var(--hac-accent) 14%, transparent); }
        .toggles { display: flex; flex-wrap: wrap; gap: 4px; }
        .tg { width: var(--hac-btn); height: var(--hac-btn); border-radius: 50%; display: inline-flex;
              align-items: center; justify-content: center; gap: 6px; color: var(--secondary-text-color);
              transition: background-color .15s, color .15s; }
        .tg.wide { width: auto; padding: 0 12px; border-radius: var(--hac-radius); font-size: .8125rem; }
        .tg.on { color: var(--hac-accent); background: color-mix(in srgb, var(--hac-accent) 14%, transparent); }
        .sensors { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .8125rem; color: var(--secondary-text-color); }
        .sn { display: inline-flex; align-items: center; gap: 4px; }
        .sn ha-icon { --mdc-icon-size: 16px; }
        .sn b { font-weight: 500; color: var(--primary-text-color); }
    `;
}

/* ---------- visual editor (ha-form schema; everything else is available in YAML) ---------- */

// Built when the editor opens: by then HA has set <html lang> to the user's language.
function haierEvoAcCardForm() {
    const ru = String(globalThis.document?.documentElement?.lang || "ru").startsWith("ru");
    const T = ru ? {
        entity: "Кондиционер", name: "Название", sections: "Секции (состав и порядок)",
        control_style: "Вид списков", show_name: "Название", show_current: "Температура в комнате",
        show_power: "Кнопка питания", show_labels: "Подписи", compact: "Компактно",
        hide_when_off: "Скрывать лишнее, когда выключен", advanced: "Дополнительно",
        modes: "Режимы (состав и порядок)", switches: "Переключатели", selects: "Списки", sensors: "Датчики",
        exclude: "Не показывать", temperature_step: "Шаг температуры", debounce: "Задержка отправки температуры, мс",
        accent_color: "Цвет акцента (CSS)", pills: "Компактные списки", chips: "Кнопки",
        temperature: "Температура", fan: "Вентилятор", swing: "Вертикальный поток",
        swing_horizontal: "Горизонтальный поток", preset: "Пресет",
        off: "Выкл", auto: "Авто", cool: "Охлаждение", heat: "Обогрев", heat_cool: "Обогрев/охлаждение",
        dry: "Осушение", fan_only: "Вентиляция",
        h_switches: "Пусто — все переключатели устройства", h_selects: "Пусто — все списки устройства",
        h_sensors: "Пусто — все датчики устройства",
    } : {
        entity: "Air conditioner", name: "Name", sections: "Sections (which and in what order)",
        control_style: "Choice style", show_name: "Name", show_current: "Room temperature",
        show_power: "Power button", show_labels: "Labels", compact: "Compact",
        hide_when_off: "Hide extras when off", advanced: "Advanced",
        modes: "Modes (which and in what order)", switches: "Switches", selects: "Selects", sensors: "Sensors",
        exclude: "Hide entities", temperature_step: "Temperature step", debounce: "Temperature send delay, ms",
        accent_color: "Accent colour (CSS)", pills: "Compact pills", chips: "Chips",
        temperature: "Temperature", fan: "Fan", swing: "Vertical swing", swing_horizontal: "Horizontal swing",
        preset: "Preset", off: "Off", auto: "Auto", cool: "Cool", heat: "Heat", heat_cool: "Heat/cool",
        dry: "Dry", fan_only: "Fan only",
        h_switches: "Empty — all switches of the device", h_selects: "Empty — all selects of the device",
        h_sensors: "Empty — all sensors of the device",
    };
    const option = (v) => ({ value: v, label: T[v] || v });
    return {
        schema: [
            { name: "entity", required: true, selector: { entity: { domain: "climate" } } },
            { name: "name", selector: { text: {} } },
            {
                name: "sections",
                selector: { select: { multiple: true, reorder: true, options: SECTIONS.map(option) } },
            },
            {
                name: "control_style",
                selector: { select: { mode: "dropdown", options: ["pills", "chips"].map(option) } },
            },
            {
                type: "grid", name: "", schema: [
                    { name: "show_name", selector: { boolean: {} } },
                    { name: "show_current", selector: { boolean: {} } },
                    { name: "show_power", selector: { boolean: {} } },
                    { name: "show_labels", selector: { boolean: {} } },
                    { name: "compact", selector: { boolean: {} } },
                    { name: "hide_when_off", selector: { boolean: {} } },
                ],
            },
            {
                type: "expandable", name: "", title: T.advanced, schema: [
                    {
                        name: "modes",
                        selector: { select: { multiple: true, reorder: true, options: Object.keys(MODE_ICONS).map(option) } },
                    },
                    { name: "switches", selector: { entity: { multiple: true } } },
                    { name: "selects", selector: { entity: { multiple: true, domain: ["select", "input_select"] } } },
                    { name: "sensors", selector: { entity: { multiple: true, domain: ["sensor", "binary_sensor"] } } },
                    { name: "exclude", selector: { entity: { multiple: true } } },
                    { name: "temperature_step", selector: { number: { min: 0.1, max: 5, step: 0.1, mode: "box" } } },
                    { name: "debounce", selector: { number: { min: 0, max: 5000, step: 100, mode: "box" } } },
                    { name: "accent_color", selector: { text: {} } },
                ],
            },
        ],
        computeLabel: (schema) => T[schema.name],
        computeHelper: (schema) => T[`h_${schema.name}`],
    };
}

if (!customElements.get("haier-evo-ac-card")) {
    customElements.define("haier-evo-ac-card", HaierEvoAcCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
        type: "haier-evo-ac-card",
        name: "Haier Evo AC Card",
        description: "Минималистичная карточка кондиционера с гибкой настройкой",
        preview: true,
        documentationURL: "https://github.com/JampireX/haier_evo",
    });
}
})();
