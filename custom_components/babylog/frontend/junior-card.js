// `custom:junior-card`: the baby at a glance, from the babylog entities.
// Loaded on every page by the integration (no Lovelace resource needed).
//
//   type: custom:junior-card
//   baby: junior          # optional: entity id prefix, e.g. sensor.junior_feeding
//   title: Junior         # optional: defaults to the baby's device name

const CC = { feed: "#f3a987", bottle: "#e8895f", sleep: "#a79ad9", wet: "#6cc5ad", poopy: "#c99e62" };
const FEEDING = { bottle: "Bottle", left: "Left side", right: "Right side", breast: "Breast" };

const ago = (iso) => {
  if (!iso) return null;
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  const h = Math.floor(min / 60);
  return h ? `${h}h ${String(min % 60).padStart(2, "0")}m` : `${min}m`;
};
const escHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class JuniorCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = config ?? {};
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", () => {
        history.pushState(null, "", "/junior");
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      });
    }
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    // "since" durations tick even when no state changes.
    this._timer = setInterval(() => this._render(), 30000);
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 3;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 3 };
  }

  /** Entity id prefix for the configured (or first) baby. */
  _prefix() {
    if (this._config?.baby) return this._config.baby;
    const feeding = Object.values(this._hass?.entities ?? {}).find(
      (e) => e.platform === "babylog" && e.entity_id.startsWith("sensor.") && e.entity_id.endsWith("_feeding")
    );
    return feeding ? feeding.entity_id.slice("sensor.".length, -"_feeding".length) : null;
  }

  _render() {
    if (!this.shadowRoot || !this._hass) return;
    const hass = this._hass;
    const prefix = this._prefix();
    const st = (id) => hass.states[id];
    const feeding = st(`sensor.${prefix}_feeding`);
    const sleeping = st(`binary_sensor.${prefix}_sleeping`);
    const diaper = st(`sensor.${prefix}_diaper`);

    if (!prefix || !feeding) {
      this.shadowRoot.innerHTML = `<style>${CARD_STYLE}</style><ha-card><div class="empty">No Junior baby found yet. Sync from the app, or set <code>baby:</code> to the entity prefix.</div></ha-card>`;
      return;
    }

    const device = hass.devices?.[hass.entities?.[feeding.entity_id]?.device_id];
    const title = this._config.title ?? device?.name_by_user ?? device?.name ?? "Junior";

    const feedNow = feeding.state !== "none" && feeding.state !== "unknown";
    const feedTile = feedNow
      ? tile(CC.feed, "Feeding", FEEDING[feeding.state] ?? feeding.state, `for ${ago(feeding.attributes.since)}`, true)
      : tile(
          CC.feed,
          "Last feed",
          feeding.attributes.since ? `${ago(feeding.attributes.since)} ago` : "–",
          [FEEDING[feeding.attributes.last_feed], feeding.attributes.last_amount_ml && `${feeding.attributes.last_amount_ml} mL`]
            .filter(Boolean)
            .join(" · ")
        );
    const asleep = sleeping?.state === "on";
    const sleepTile = tile(
      CC.sleep,
      asleep ? "Sleeping" : "Awake",
      sleeping?.attributes.since ? ago(sleeping.attributes.since) : "–",
      sleeping?.attributes.last_sleep_minutes != null && !asleep ? `last nap ${fmtMin(sleeping.attributes.last_sleep_minutes)}` : asleep ? "asleep" : "",
      asleep
    );
    const diaperKnown = diaper && diaper.state !== "unknown";
    const diaperTile = tile(
      diaper?.state === "wet" ? CC.wet : CC.poopy,
      "Diaper",
      diaperKnown ? `${ago(diaper.attributes.since)} ago` : "–",
      diaperKnown ? { wet: "Wet", poopy: "Poopy", both: "Wet + poopy" }[diaper.state] : ""
    );
    const fa = feeding.attributes, sa = sleeping?.attributes ?? {};
    const bar = (label, value, pct, color) => `
      <div class="bar" title="${pct == null ? "" : `${pct}% of a usual day (last 7 days)`}">
        <div class="barlab"><span>${escHtml(label)}</span><span>${escHtml(value)}${pct == null ? "" : ` · ${pct}%`}</span></div>
        <div class="track"><div style="width:${Math.min(pct ?? 0, 100)}%;background:${color}"></div></div>
      </div>`;
    const todayTile = `<div class="tile" style="--c:${CC.bottle}">
      <div class="label"><i></i>Today</div>
      ${bar("Milk", `${fa.milk_today_ml ?? 0} mL`, fa.milk_today_pct, CC.bottle)}
      ${bar("Sleep", fmtMin(Number(sa.sleep_today_min ?? 0)), sa.sleep_today_pct, CC.sleep)}
    </div>`;

    this.shadowRoot.innerHTML = `<style>${CARD_STYLE}</style>
      <ha-card>
        <div class="header"><span>${escHtml(title)}</span><ha-icon icon="mdi:chart-timeline-variant"></ha-icon></div>
        <div class="tiles">${feedTile}${sleepTile}${diaperTile}${todayTile}</div>
        ${this._next(sleeping, feeding)}
      </ha-card>`;
  }

  /** "Next: nap 14:05–15:40 · feed 15:10–16:30" from the prediction attributes. */
  _next(sleeping, feeding) {
    const lang = this._hass.locale?.language ?? navigator.language;
    const t = (iso) => new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
    const part = (label, a, prefix) => {
      if (!a?.[`${prefix}_earliest`]) return null;
      const late = Date.now() > new Date(a[`${prefix}_latest`]).getTime();
      return `<span${late ? ' class="late"' : ""}><b>${escHtml(label)}</b> ${t(a[`${prefix}_earliest`])}–${t(a[`${prefix}_latest`])}${late ? " · overdue" : ""}</span>`;
    };
    const sa = sleeping?.attributes;
    const asleep = sleeping?.state === "on";
    const kind = asleep ? sa?.wake_kind : sa?.next_sleep_kind;
    const sleepLabel = asleep
      ? "wakes"
      : { nap: "nap", bedtime: "bedtime", back_to_sleep: "back to sleep" }[kind] ?? "sleep";
    const parts = [
      part(sleepLabel, sa, asleep ? "wake" : "next_sleep"),
      part("feed", feeding?.attributes, "next_feed"),
    ].filter(Boolean);
    return parts.length ? `<div class="next">Next: ${parts.join(" · ")}</div>` : "";
  }
}

function fmtMin(min) {
  const m = Math.round(min);
  const h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${m}m`;
}

function tile(color, label, value, sub, live = false) {
  return `<div class="tile ${live ? "live" : ""}" style="--c:${color}">
    <div class="label"><i></i>${escHtml(label)}</div>
    <div class="value">${escHtml(value)}</div>
    <div class="sub">${escHtml(sub ?? "")}</div>
  </div>`;
}

const CARD_STYLE = `
  ha-card { padding:12px 14px 14px; cursor:pointer; height:100%; box-sizing:border-box; }
  .header { display:flex; align-items:center; justify-content:space-between; font-size:16px; font-weight:500; margin-bottom:10px; color:var(--primary-text-color); }
  .header ha-icon { color:var(--secondary-text-color); --mdc-icon-size:20px; }
  .tiles { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:8px; }
  .tile { border-radius:12px; padding:10px 12px; background:color-mix(in srgb, var(--c) 14%, transparent); }
  .tile.live { background:color-mix(in srgb, var(--c) 32%, transparent); }
  .label { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--secondary-text-color); }
  .label i { width:8px; height:8px; border-radius:50%; background:var(--c); }
  .tile.live .label i { animation:pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity:.3; } }
  .value { font-size:20px; font-weight:500; margin-top:4px; color:var(--primary-text-color); }
  .sub { font-size:12px; color:var(--secondary-text-color); min-height:1em; margin-top:1px; }
  .empty { color:var(--secondary-text-color); font-size:14px; }
  .bar { margin-top:5px; }
  .barlab { display:flex; justify-content:space-between; font-size:12px; color:var(--primary-text-color); }
  .barlab span:last-child { color:var(--secondary-text-color); }
  .track { height:6px; border-radius:3px; background:color-mix(in srgb, var(--primary-text-color) 10%, transparent); overflow:hidden; margin-top:2px; }
  .track div { height:100%; border-radius:3px; }
  .next { margin-top:10px; font-size:13px; color:var(--secondary-text-color); }
  .next b { font-weight:500; color:var(--primary-text-color); }
  .next .late { color:var(--warning-color, #ffa600); }
`;

if (!customElements.get("junior-card")) customElements.define("junior-card", JuniorCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "junior-card")) window.customCards.push({
  type: "junior-card",
  name: "Junior",
  description: "Feeding, sleep, diaper and today's totals for a Junior baby.",
  preview: true,
});
