/* Haier Evo AC Card — минималистичная и гибко настраиваемая карточка кондиционера.
 *
 * Работает с любой climate-сущностью (редактор по умолчанию предлагает кондиционеры
 * haier_evo). Режимы, вентилятор, свинг и пресеты берутся из атрибутов сущности,
 * переключатели, селекты и датчики — с того же устройства. Подписи значений переводит
 * сам Home Assistant (formatEntityState / formatEntityAttributeValue), поэтому они
 * совпадают с языком интерфейса; свои подписи, иконки и цвета задаются в YAML.
 *
 * Все параметры, кроме entity, необязательны (пустое значение — значение по умолчанию):
 *   name, show_name, show_current, show_power, show_labels, compact,
 *   hide_when_off (true | [секции]), control_style ("pills" | "chips"),
 *   sections ([temperature, modes, fan, swing, swing_horizontal, preset,
 *             switches, selects, sensors] — состав и порядок),
 *   modes / fan_modes / swing_modes / swing_horizontal_modes / preset_modes
 *     (какие значения показывать и в каком порядке; без modes режим «Выкл»
 *      не показывается, если есть кнопка питания),
 *   switches / selects / sensors (списки сущностей; по умолчанию — всё с устройства;
 *     в selects подходят и select, и input_select),
 *   exclude (сущности, которые не показывать), temperature_step,
 *   debounce (мс; нажатия −/+ собираются в одну команду),
 *   labels ({mode|fan|swing|swing_horizontal|preset: {значение: подпись}}),
 *   icons ({режим | fan | swing | swing_horizontal | preset | power | increase | decrease |
 *           current_temperature | entity_id: "mdi:…"}), colors ({режим: цвет}),
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

// Keys that open a focused native <select> (or move through its open list).
const SELECT_OPEN_KEYS = [" ", "Enter", "ArrowUp", "ArrowDown", "F4"];
// A picker dismissed without a choice does not always report it: stop holding renders after this.
const SELECT_HOLD_MS = 30000;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class HaierEvoAcCard extends HTMLElement {

    static async getConfigElement() {
        await loadHaForm();
        return document.createElement("haier-evo-ac-card-editor");
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
        // Another entity: a −/+ change still waiting for the debounce belongs to the previous one —
        // send it there now (as on disconnect) and do not let its value leak into the new entity.
        if (this._config && this._config.entity !== config.entity) this._clearPending(true);
        // Empty values (null, or a field cleared in the visual editor) fall back to the defaults.
        const own = Object.fromEntries(Object.entries(config).filter(([, value]) => value != null));
        this._config = { ...DEFAULTS, ...own };
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
        const sections = this._sections(this._isOff(st));
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

    _isUnavailable(st) {
        return st.state === "unavailable" || st.state === "unknown";
    }

    // An unavailable / unknown AC is not "on": no active power button, hide_when_off applies.
    _isOff(st) {
        return st.state === "off" || this._isUnavailable(st);
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

    // Resolves to false when the call fails (Home Assistant shows the error to the user itself).
    _call(domain, service, data) {
        try {
            return Promise.resolve(this._hass.callService(domain, service, data)).then(() => true, () => false);
        } catch (e) {
            return Promise.resolve(false);
        }
    }

    // select.* and input_select.* both have a select_option service with the same data.
    _selectOption(entity_id, option) {
        return this._call(entity_id.split(".")[0], "select_option", { entity_id, option });
    }

    /* ---------- rendering ---------- */

    _render() {
        if (!this._hass || !this._config) return;
        if (!this.shadowRoot) {
            this.attachShadow({ mode: "open" });
            const root = this.shadowRoot;
            root.addEventListener("click", (ev) => this._onClick(ev));
            root.addEventListener("change", (ev) => this._onChange(ev));
            root.addEventListener("pointerdown", (ev) => this._onPress(ev));
            root.addEventListener("keydown", (ev) => this._onPress(ev));
            root.addEventListener("focusout", (ev) => this._onFocusOut(ev));
        }
        const cfg = this._config;
        const st = this._stateObj;
        if (!st) {
            const missing = JSON.stringify(["missing", cfg.entity, this._lang]);
            if (missing !== this._key) {
                this._key = missing;
                this._setContent(`<ha-card><div class="warn">${esc(this._t("missing"))}: `
                    + `${esc(cfg.entity)}</div></ha-card>`);
            }
            return;
        }
        // The command is applied optimistically; once the entity reports it, stop showing the
        // pending value.
        if (this._pending != null && !this._timer && Number(st.attributes.temperature) === this._pending) {
            this._pending = null;
        }

        const unavailable = this._isUnavailable(st);
        const off = this._isOff(st);
        const sections = this._sections(off);
        const switches = sections.includes("switches") ? this._entities("switches") : [];
        const selects = sections.includes("selects") ? this._entities("selects") : [];
        const sensors = sections.includes("sensors") ? this._entities("sensors") : [];
        const key = JSON.stringify([
            st.state, st.attributes, this._pending, this._hass.language, this._hass.locale,
            [...switches, ...selects, ...sensors].map((s) => [s.entity_id, s.state, s.attributes]),
        ]);
        // Translations (the formatters) and the entity registry may arrive after the first render;
        // Home Assistant replaces them when they change, so they are compared by identity.
        const refs = [
            this._hass.localize, this._hass.formatEntityState, this._hass.formatEntityAttributeValue,
            this._hass.formatEntityAttributeName, this._hass.entities,
        ];
        if (key === this._key && refs.every((ref, i) => ref === this._refs?.[i])) return;
        // A render would replace an open native <select> and close its picker: wait for the choice.
        if (this._selectOpen()) {
            this._deferred = true;
            return;
        }
        this._key = key;
        this._refs = refs;

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
        this._setContent(`
            <ha-card class="${cfg.compact ? "compact" : ""} ${off ? "off" : ""} ${unavailable ? "unavailable" : ""}"
                     style="${esc(vars.join(";"))}">
                ${this._renderHeader(st, off, unavailable)}
                ${body.join("")}
            </ha-card>`);
    }

    // Replaces the card content, keeping keyboard focus on the same control. The user's CSS is set
    // as text, so it never goes through the HTML parser (nothing in it can close the <style>).
    _setContent(html) {
        const root = this.shadowRoot;
        const focused = this._controlId(root.activeElement);
        const userCss = this._config.style ? `<style class="user"></style>` : "";
        root.innerHTML = `<style>${HaierEvoAcCard.styles}</style>${userCss}${html}`;
        if (userCss) root.querySelector("style.user").textContent = String(this._config.style);
        this._deferred = false;
        if (!focused) return;
        [...root.querySelectorAll("[data-act]")].find((el) => this._controlId(el) === focused)
            ?.focus({ preventScroll: true });
    }

    // A control's identity across renders: tag, action and the control / entity / value it is for.
    _controlId(el) {
        const data = el?.dataset;
        return data?.act ? [el.tagName, data.act, data.k, data.e, data.v].join("|") : null;
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
        const action = esc(this._t(off ? "turn_on" : "turn_off"));
        const power = cfg.show_power ? `
            <button class="ic pwr ${off ? "" : "on"}" data-act="power" title="${action}"
                    aria-label="${action}" aria-pressed="${!off}"${unavailable ? " disabled" : ""}>
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
        // An empty list means "not listed", the same as for every other value list.
        const listed = Array.isArray(this._config.modes) && this._config.modes.length > 0;
        let modes = this._values("mode", st.attributes.hvac_modes || []);
        // "off" is covered by the power button unless the modes are listed explicitly.
        if (!listed && this._config.show_power) modes = modes.filter((m) => m !== "off");
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
        const entity_id = this._pendingEntity;
        const temperature = this._pending;
        const current = this._hass.states[entity_id]?.attributes.temperature;
        if (current != null && Number(current) === temperature) {
            // −/+ ended where it started (or at a limit): nothing to send, every command beeps.
            this._pending = null;
            this._key = null;
            this._render();
            return;
        }
        this._call("climate", "set_temperature", { entity_id, temperature }).then((ok) => {
            if (ok || this._timer || this._pending !== temperature || this._pendingEntity !== entity_id) return;
            // Rejected (Home Assistant shows the error): show the real target now, not after the timeout.
            clearTimeout(this._resetTimer);
            this._pending = null;
            this._key = null;
            this._render();
        });
        // If the entity never reports the value (e.g. the command failed), show the real one again.
        this._resetTimer = setTimeout(() => {
            this._pending = null;
            this._key = null;
            this._render();
        }, 5000);
    }

    // Forgets the optimistic target; with flush, a change still waiting for the debounce is sent first.
    _clearPending(flush) {
        clearTimeout(this._timer);
        if (flush && this._timer) this._sendTemperature();
        clearTimeout(this._resetTimer);
        this._timer = this._resetTimer = null;
        this._pending = this._pendingEntity = null;
    }

    _onClick(ev) {
        const el = ev.target?.closest?.("[data-act]");
        if (!el || el.tagName === "SELECT" || el.disabled || !this._stateObj) return;
        const st = this._stateObj;
        const entity_id = st.entity_id;
        switch (el.dataset.act) {
            case "power":
                // Nothing to switch while the AC is unavailable (the button is disabled then).
                if (!this._isUnavailable(st)) {
                    this._call("climate", st.state === "off" ? "turn_on" : "turn_off", { entity_id });
                }
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
                this._selectOption(el.dataset.e, el.dataset.v);
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
        if (el?.tagName !== "SELECT" || !this._stateObj) return;
        let call;
        if (el.value && el.dataset.act === "ctl") {
            const c = CONTROLS[el.dataset.k];
            call = this._call("climate", c.service, { entity_id: this._stateObj.entity_id, [c.attr]: el.value });
        } else if (el.value && el.dataset.act === "select") {
            call = this._selectOption(el.dataset.e, el.value);
        }
        // The choice closed the picker: renders held back meanwhile can run now.
        this._releaseSelect();
        // Once the call is done, show the entity state again: a failed (or ignored) choice must not
        // stay selected, or picking it once more would not fire "change".
        call?.then(() => {
            this._key = null;
            this._render();
        });
    }

    // A native <select> counts as open from the press that opens its picker until a choice
    // ("change") or until it loses focus; meanwhile renders are held back (see _render).
    _onPress(ev) {
        const el = ev.target;
        const opens = el?.tagName === "SELECT" && (ev.type === "pointerdown" || SELECT_OPEN_KEYS.includes(ev.key));
        if (opens) {
            this._openSelect = el;
            this._openSince = Date.now();
        } else if (ev.type === "pointerdown" || el === this._openSelect) {
            this._releaseSelect();
        }
    }

    _onFocusOut(ev) {
        if (ev.target === this._openSelect) this._releaseSelect();
    }

    _releaseSelect() {
        if (!this._openSelect) return;
        this._openSelect = null;
        if (this._deferred) this._render();
    }

    _selectOpen() {
        const el = this._openSelect;
        if (el && el.isConnected && this.shadowRoot.activeElement === el
            && Date.now() - this._openSince < SELECT_HOLD_MS) {
            return true;
        }
        this._openSelect = null;
        return false;
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
        button:disabled { cursor: default; }
        button:focus-visible, .pill:focus-within { outline: 2px solid var(--hac-accent); outline-offset: 2px; }
        ha-icon { --mdc-icon-size: 22px; }
        .ic { width: var(--hac-btn); height: var(--hac-btn); border-radius: 50%; flex: none;
              display: inline-flex; align-items: center; justify-content: center;
              color: var(--secondary-text-color); transition: background-color .15s, color .15s; }
        .ic:not(:disabled):hover, .mode:hover, .tg:hover, .chip:hover { background: var(--secondary-background-color); }
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

/* ---------- visual editor (ha-form; everything else is available in YAML) ---------- */

const EDITOR_TEXT = {
    ru: {
        entity: "Кондиционер", name: "Название", sections: "Секции (состав и порядок)",
        control_style: "Вид списков", show_name: "Название", show_current: "Температура в комнате",
        show_power: "Кнопка питания", show_labels: "Подписи", compact: "Компактно",
        hide_when_off: "Скрывать лишнее, когда выключен", advanced: "Дополнительно",
        modes: "Режимы (состав и порядок)", switches: "Переключатели", selects: "Списки", sensors: "Датчики",
        exclude: "Не показывать", temperature_step: "Шаг температуры", debounce: "Задержка отправки температуры, мс",
        accent_color: "Цвет акцента (CSS)", pills: "Компактные списки", chips: "Кнопки",
        temperature: "Температура", o_modes: "Режимы", fan: "Вентилятор", swing: "Вертикальный поток",
        swing_horizontal: "Горизонтальный поток", preset: "Пресет",
        off: "Выкл", auto: "Авто", cool: "Охлаждение", heat: "Обогрев", heat_cool: "Обогрев/охлаждение",
        dry: "Осушение", fan_only: "Вентиляция",
        h_modes: "Пусто — все режимы (без «Выкл», если есть кнопка питания)",
        h_switches: "Пусто — все переключатели устройства", h_selects: "Пусто — все списки устройства",
        h_sensors: "Пусто — все датчики устройства", h_temperature_step: "Пусто — шаг сущности",
        yaml: "задаётся в YAML",
    },
    en: {
        entity: "Air conditioner", name: "Name", sections: "Sections (which and in what order)",
        control_style: "Choice style", show_name: "Name", show_current: "Room temperature",
        show_power: "Power button", show_labels: "Labels", compact: "Compact",
        hide_when_off: "Hide extras when off", advanced: "Advanced",
        modes: "Modes (which and in what order)", switches: "Switches", selects: "Selects", sensors: "Sensors",
        exclude: "Hide entities", temperature_step: "Temperature step", debounce: "Temperature send delay, ms",
        accent_color: "Accent colour (CSS)", pills: "Compact pills", chips: "Chips",
        temperature: "Temperature", o_modes: "Modes", fan: "Fan", swing: "Vertical swing",
        swing_horizontal: "Horizontal swing", preset: "Preset",
        off: "Off", auto: "Auto", cool: "Cool", heat: "Heat", heat_cool: "Heat/cool", dry: "Dry", fan_only: "Fan only",
        h_modes: "Empty — all modes (without Off when there is a power button)",
        h_switches: "Empty — all switches of the device", h_selects: "Empty — all selects of the device",
        h_sensors: "Empty — all sensors of the device", h_temperature_step: "Empty — the entity's step",
        yaml: "set in YAML",
    },
};

const editorText = (hass) => EDITOR_TEXT[String(hass?.locale?.language || hass?.language
    || globalThis.document?.documentElement?.lang || "ru").startsWith("ru") ? "ru" : "en"];

// The form shows the effective values: defaults are visible (and editable), not empty fields.
function editorData(config) {
    const data = { ...config };
    for (const key of ["show_name", "show_current", "show_power", "show_labels", "compact",
        "hide_when_off", "control_style", "debounce"]) {
        if (data[key] == null) data[key] = DEFAULTS[key];
    }
    if (!Array.isArray(data.sections) || !data.sections.length) data.sections = [...DEFAULTS.sections];
    return data;
}

// Back to the card config: cleared fields are dropped (the card falls back to its defaults), and a
// default the form only displayed is not written unless the config had that key already.
function editorConfig(config, data) {
    const result = {};
    for (const [key, value] of Object.entries(data)) {
        const empty = value == null || value === "" || (Array.isArray(value) && !value.length);
        const shownDefault = !(key in config) && key in DEFAULTS
            && JSON.stringify(value) === JSON.stringify(DEFAULTS[key]);
        if (!empty && !shownDefault) result[key] = value;
    }
    return result;
}

function editorSchema(config, T) {
    const option = (v) => ({ value: v, label: T[`o_${v}`] || T[v] || v });
    const hide = config.hide_when_off;
    return [
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
                // A list of sections is YAML-only: shown read-only, so the switch cannot replace it.
                Array.isArray(hide)
                    ? { name: "hide_when_off", type: "constant", value: `${hide.join(", ")} (${T.yaml})` }
                    : { name: "hide_when_off", selector: { boolean: {} } },
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
    ];
}

// ha-form is loaded lazily by Home Assistant; asking a built-in card for its editor pulls it in.
async function loadHaForm() {
    for (const card of ["hui-tile-card", "hui-button-card"]) {
        if (customElements.get("ha-form")) return;
        try {
            await customElements.get(card)?.getConfigElement?.();
        } catch (e) {
            // Best effort: an <ha-form> created before the definition is upgraded once it exists.
        }
    }
}

class HaierEvoAcCardEditor extends HTMLElement {

    setConfig(config) {
        this._config = config || {};
        this._update();
    }

    set hass(hass) {
        this._hass = hass;
        if (this._form) this._form.hass = hass;
        else this._update();
    }

    _update() {
        if (!this._config || !this._hass) return;
        if (!this._form) {
            this._form = document.createElement("ha-form");
            this._form.addEventListener("value-changed", (ev) => this._valueChanged(ev));
            this.appendChild(this._form);
        }
        const T = editorText(this._hass);
        const hide = this._config.hide_when_off;
        // Rebuilt only when it really changes (language, a YAML-only hide_when_off list).
        const schemaKey = JSON.stringify([T === EDITOR_TEXT.ru, Array.isArray(hide) ? hide : null]);
        if (schemaKey !== this._schemaKey) {
            this._schemaKey = schemaKey;
            this._form.schema = editorSchema(this._config, T);
            this._form.computeLabel = (schema) => T[schema.name];
            this._form.computeHelper = (schema) => T[`h_${schema.name}`];
        }
        this._form.hass = this._hass;
        this._form.data = editorData(this._config);
    }

    _valueChanged(ev) {
        ev.stopPropagation();
        this._config = editorConfig(this._config, ev.detail.value);
        this.dispatchEvent(new CustomEvent("config-changed", {
            detail: { config: this._config }, bubbles: true, composed: true,
        }));
    }
}

if (!customElements.get("haier-evo-ac-card-editor")) {
    customElements.define("haier-evo-ac-card-editor", HaierEvoAcCardEditor);
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
