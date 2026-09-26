// Junior sidebar panel: statistics computed by `babylog.stats` from the stored
// records (not from sensors). Plain web component + hand-drawn SVG so it works
// offline and needs no build step.

const C = {
  sleep: "#a79ad9",
  night: "#7d6fc4",
  feed: "#f3a987",
  bottle: "#e8895f",
  left: "#f5bd9f",
  right: "#ee9a78",
  breast: "#f3a987",
  wet: "#6cc5ad",
  poopy: "#c99e62",
  note: "#e9cf6f",
};
const FEED_LABEL = { bottle: "Bottle", left: "Left", right: "Right", breast: "Breast" };
const PERIODS = ["day", "week", "month"];

// MARK: formatting

const pad = (n) => String(n).padStart(2, "0");
const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDate = (s) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const hm = (min) => {
  if (min == null) return "–";
  const m = Math.round(min);
  const h = Math.floor(m / 60);
  return h ? `${h}h ${pad(m % 60)}m` : `${m}m`;
};
const clock = (min) => `${pad(Math.floor(min / 60) % 24)}:${pad(Math.floor(min % 60))}`;
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

// MARK: charts

/**
 * Stacked bar chart. buckets: [{label, title, stacks: [{value, color, name}]}]
 */
// Drawn at roughly its on-screen size (half-width card ≈ 520 px) so text stays
// readable; the SVG then scales uniformly.
function barChart({ buckets, fmt = (v) => v, avg = null, width = 520, height = 220, labelEvery = 1 }) {
  const W = width, H = height, L = 46, R = 6, T = 12, B = 24;
  const plotW = W - L - R, plotH = H - T - B;
  const totals = buckets.map((b) => b.stacks.reduce((s, x) => s + x.value, 0));
  const max = niceMax(Math.max(...totals, avg ?? 0));
  const y = (v) => T + plotH - (v / max) * plotH;
  const slot = plotW / Math.max(buckets.length, 1);
  const bw = Math.max(2, Math.min(56, slot * 0.64));
  let svg = "";
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i, yy = y(v);
    svg += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="grid"/>`;
    svg += `<text x="${L - 8}" y="${yy + 4}" class="ylab">${esc(fmt(v))}</text>`;
  }
  buckets.forEach((b, i) => {
    const x = L + slot * i + (slot - bw) / 2;
    let acc = 0;
    const tip = `${b.title ?? b.label}: ` + b.stacks.filter((s) => s.value).map((s) => `${s.name} ${fmt(s.value)}`).join(", ");
    b.stacks.forEach((s, j) => {
      if (!s.value) return;
      const y0 = y(acc), y1 = y(acc + s.value);
      const top = j === b.stacks.length - 1 || b.stacks.slice(j + 1).every((n) => !n.value);
      const r = top ? Math.min(4, bw / 2) : 0;
      const h = y0 - y1;
      svg += top
        ? `<path d="M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + bw - r} Q${x + bw},${y1} ${x + bw},${y1 + r} V${y0} Z" fill="${s.color}"><title>${esc(tip)}</title></path>`
        : `<rect x="${x}" y="${y1}" width="${bw}" height="${h}" fill="${s.color}"><title>${esc(tip)}</title></rect>`;
      acc += s.value;
    });
    if (i % labelEvery === 0) {
      svg += `<text x="${x + bw / 2}" y="${H - 8}" class="xlab">${esc(b.label)}</text>`;
    }
  });
  if (avg) {
    svg += `<line x1="${L}" x2="${W - R}" y1="${y(avg)}" y2="${y(avg)}" class="avg"/>`;
    svg += `<text x="${W - R}" y="${y(avg) - 5}" class="avglab">avg ${esc(fmt(avg))}</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" style="aspect-ratio:${W}/${H}">${svg}</svg>`;
}

/** 24-hour timeline, one row per day: sleep and feed bars, diaper/note markers. */
function rhythmChart(data, dayLabel) {
  const days = data.days.length;
  const rowH = days === 1 ? 64 : days <= 7 ? 30 : 16;
  const gap = days === 1 ? 0 : days <= 7 ? 8 : 4;
  const W = 1000, L = days === 1 ? 8 : 62, R = 8, T = 22;
  const H = T + days * (rowH + gap) + 4;
  const x = (min) => L + (Math.min(Math.max(min, 0), 1440) / 1440) * (W - L - R);
  let svg = "";
  for (let h = 0; h <= 24; h += 3) {
    svg += `<line x1="${x(h * 60)}" x2="${x(h * 60)}" y1="${T - 4}" y2="${H}" class="grid"/>`;
    svg += `<text x="${x(h * 60)}" y="${T - 8}" class="xlab">${pad(h % 24)}:00</text>`;
  }
  const [ns, ne] = [data.night.start, data.night.end].map((t) => {
    const [a, b] = t.split(":").map(Number);
    return a * 60 + b;
  });
  data.days.forEach((d, i) => {
    const top = T + i * (rowH + gap);
    svg += `<rect x="${x(0)}" y="${top}" width="${x(ne) - x(0)}" height="${rowH}" class="nightbg"/>`;
    svg += `<rect x="${x(ns)}" y="${top}" width="${x(1440) - x(ns)}" height="${rowH}" class="nightbg"/>`;
    svg += `<rect x="${x(0)}" y="${top}" width="${x(1440) - x(0)}" height="${rowH}" class="rowbg"/>`;
    if (days > 1) svg += `<text x="${L - 8}" y="${top + rowH / 2 + 4}" class="ylab">${esc(dayLabel(d.date))}</text>`;
  });
  for (const s of data.segments) {
    const top = T + s.day * (rowH + gap);
    const sleep = s.kind === "sleep";
    const h = sleep ? rowH : rowH * 0.46;
    const yy = sleep ? top : top + (rowH - h) / 2;
    const w = Math.max(x(s.end) - x(s.start), 2.5);
    const color = sleep ? C.sleep : C[s.type] ?? C.feed;
    const tip = `${sleep ? "Sleep" : FEED_LABEL[s.type] ?? "Feed"} ${clock(s.start)}–${s.active ? "now" : clock(s.end)} (${hm(s.end - s.start)})${s.amount_ml ? `, ${s.amount_ml} mL` : ""}`;
    svg += `<rect x="${x(s.start)}" y="${yy}" width="${w}" height="${h}" rx="${Math.min(4, h / 2)}" fill="${color}" class="${s.active ? "active" : ""}"><title>${esc(tip)}</title></rect>`;
  }
  for (const e of data.events) {
    const top = T + e.day * (rowH + gap);
    const cx = x(e.minute);
    if (e.kind === "diaper") {
      const color = e.pooped ? C.poopy : C.wet;
      const r = Math.max(3, Math.min(6, rowH / 5));
      const tip = `Diaper ${clock(e.minute)}: ${[e.wet && "wet", e.pooped && "poopy"].filter(Boolean).join(" + ")}`;
      svg += `<circle cx="${cx}" cy="${top + rowH - r - 1}" r="${r}" fill="${color}" class="marker"><title>${esc(tip)}</title></circle>`;
    } else if (e.kind === "comment") {
      svg += `<path d="M${cx - 4},${top + 1} h8 l-4,6 z" fill="${C.note}"><title>${esc(`${clock(e.minute)} ${e.comment ?? ""}`)}</title></path>`;
    }
  }
  const nowDay = data.days.findIndex((d) => d.date === isoDate(new Date()));
  if (nowDay >= 0) {
    const now = new Date();
    const nx = x(now.getHours() * 60 + now.getMinutes());
    const top = T + nowDay * (rowH + gap);
    svg += `<line x1="${nx}" x2="${nx}" y1="${top - 3}" y2="${top + rowH + 3}" class="now"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" class="chart" style="aspect-ratio:${W}/${H}">${svg}</svg>`;
}

const legend = (items) =>
  `<div class="legend">${items.map(([name, color]) => `<span><i style="background:${color}"></i>${esc(name)}</span>`).join("")}</div>`;

// MARK: next-16-hours forecast

function eventLabel(e) {
  if (e.type === "feed") return "Feed";
  if (e.type === "wake") return e.kind === "morning" ? "Up for the day" : e.kind === "nap" ? "Wakes from nap" : "Wakes";
  return { nap: "Nap", bedtime: "Bedtime", back_to_sleep: "Back to sleep" }[e.kind] ?? "Sleep";
}

/** Gantt rows: a nap and its wake-up become one row, back-to-back feeds
 *  (e.g. the night feeds) share one row; everything else is a row of its own. */
function forecastRows(f) {
  const rows = [];
  const events = f.events;
  for (let i = 0; i < events.length; i++) {
    const e = events[i];
    if (e.type === "sleep" && e.kind === "nap") {
      const j = events.findIndex((w, k) => k > i && w.type === "wake");
      const wake = j >= 0 && events[j].kind === "nap" ? events[j] : null;
      rows.push({ nap: true, start: e, end: wake });
    } else if (e.type === "wake" && e.kind === "nap") {
      if (!rows.some((r) => r.end === e)) rows.push({ nap: true, start: null, end: e }); // napping now
    } else if (e.type === "feed" && rows.length && rows[rows.length - 1].feeds) {
      rows[rows.length - 1].feeds.push(e);
    } else if (e.type === "feed") {
      rows.push({ feeds: [e] });
    } else {
      rows.push({ event: e });
    }
  }
  return rows;
}

/** Gantt chart of the predicted events: one row each, so windows never
 *  overlap. Pale bar = window, mark = likely time; a nap row spans from the
 *  likely start to the likely wake-up. */
function forecastChart(f, lang) {
  if (!f.events.length) return `<div class="empty">Not enough recent data to predict yet.</div>`;
  const rows = forecastRows(f);
  const W = 1000, L = 150, R = 110, T = 24, rowH = 28, gap = 6;
  const H = T + rows.length * (rowH + gap);
  const t0 = new Date(f.start).getTime(), t1 = new Date(f.end).getTime();
  const x = (ms) => L + ((Math.min(Math.max(ms, t0), t1) - t0) / (t1 - t0)) * (W - L - R);
  const ms = (iso) => new Date(iso).getTime();
  const tm = (iso) => new Date(iso).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
  let svg = "";

  // Night shading from the learned bedtime / morning wake.
  if (f.bedtime && f.morning_wake) {
    const toMin = (v) => Number(v.slice(0, 2)) * 60 + Number(v.slice(3));
    const day0 = new Date(t0);
    day0.setHours(0, 0, 0, 0);
    for (let d = -1; d <= 1; d++) {
      const a = day0.getTime() + (d * 1440 + toMin(f.bedtime)) * 60000;
      const b = day0.getTime() + ((d + 1) * 1440 + toMin(f.morning_wake)) * 60000;
      if (b < t0 || a > t1) continue;
      svg += `<rect x="${x(a)}" y="${T - 6}" width="${x(b) - x(a)}" height="${H - T + 6}" class="nightbg"/>`;
    }
  }
  const first = new Date(t0);
  first.setMinutes(0, 0, 0);
  for (let h = first.getTime() + 3600000; h <= t1; h += 3600000) {
    svg += `<line x1="${x(h)}" x2="${x(h)}" y1="${T - 6}" y2="${H}" class="grid"/>`;
    const hr = new Date(h).getHours();
    if (hr % 2 === 0) svg += `<text x="${x(h)}" y="${T - 10}" class="xlab">${pad(hr)}:00</text>`;
  }
  const windowBar = (e, y, color) => {
    const a = x(ms(e.earliest)), b = x(ms(e.latest));
    return `<rect x="${a}" y="${y + 4}" width="${Math.max(b - a, 4)}" height="${rowH - 8}" rx="${(rowH - 8) / 2}" fill="${color}" fill-opacity=".3"/>`;
  };
  const label = (text, after, y) =>
    `<text x="${Math.min(after + 8, W - R + 6)}" y="${y + rowH / 2 + 4}" class="evlab">${esc(text)}</text>`;
  rows.forEach((row, i) => {
    const y = T + i * (rowH + gap);
    svg += `<rect x="${L}" y="${y}" width="${W - L - R}" height="${rowH}" rx="6" class="rowbg"/>`;
    if (row.nap) {
      const from = row.start ? ms(row.start.likely) : t0;
      const to = row.end ? ms(row.end.likely) : t1;
      svg += `<text x="${L - 12}" y="${y + rowH / 2 + 4}" class="ylab">${row.start ? "Nap" : "Napping"}</text>`;
      if (row.start) svg += windowBar(row.start, y, C.sleep);
      if (row.end) svg += windowBar(row.end, y, C.sleep);
      svg += `<rect x="${x(from)}" y="${y + 8}" width="${Math.max(x(to) - x(from), 4)}" height="${rowH - 16}" rx="${(rowH - 16) / 2}" fill="${C.sleep}"/>`;
      const text = `${row.start ? tm(row.start.likely) : "now"}–${row.end ? tm(row.end.likely) : "…"}`;
      svg += label(text, row.end ? x(ms(row.end.latest)) : x(t1), y);
    } else if (row.feeds) {
      svg += `<text x="${L - 12}" y="${y + rowH / 2 + 4}" class="ylab">${row.feeds.length > 1 ? "Feeds" : "Feed"}</text>`;
      for (const e of row.feeds) svg += windowBar(e, y, C.bottle);
      row.feeds.forEach((e, k) => {
        const m = x(ms(e.likely));
        svg += `<rect x="${m - 2}" y="${y + 2}" width="4" height="${rowH - 4}" rx="2" fill="${C.bottle}"/>`;
        // One feed: time after its window. Several: time beside each mark.
        svg += row.feeds.length === 1
          ? label(tm(e.likely), x(ms(e.latest)), y)
          : `<text x="${m + 6}" y="${y + rowH / 2 + 4}" class="evlab">${esc(tm(e.likely))}</text>`;
      });
    } else {
      const e = row.event;
      const color = e.type === "feed" ? C.bottle : C.sleep;
      const m = x(ms(e.likely));
      svg += `<text x="${L - 12}" y="${y + rowH / 2 + 4}" class="ylab">${esc(eventLabel(e))}</text>`;
      svg += windowBar(e, y, color);
      svg += `<rect x="${m - 2}" y="${y + 2}" width="4" height="${rowH - 4}" rx="2" fill="${color}"/>`;
      svg += label(tm(e.likely), x(ms(e.latest)), y);
    }
  });
  svg += `<line x1="${L}" x2="${L}" y1="${T - 6}" y2="${H}" class="now"/>`;
  return `<svg viewBox="0 0 ${W} ${H + 4}" class="chart" style="aspect-ratio:${W}/${H + 4}">${svg}</svg>`;
}

// MARK: hourly buckets for the day view

function hourly(data) {
  const hours = Array.from({ length: 24 }, (_, h) => ({ h, milk: 0, wet: 0, poopy: 0 }));
  for (const s of data.segments) {
    if (s.amount_ml) hours[Math.min(23, Math.floor(s.start / 60))].milk += s.amount_ml;
  }
  for (const e of data.events) {
    if (e.kind !== "diaper") continue;
    const h = Math.min(23, Math.floor(e.minute / 60));
    if (e.pooped) hours[h].poopy += 1;
    else hours[h].wet += 1;
  }
  return hours;
}

// MARK: panel

class JuniorPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._period = "day"; // opens on today, with the next-16-hours forecast
    this._anchor = isoDate(new Date());
    this._baby = null;
    this._data = null;
    this._error = null;
    this._signature = "";
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
    this.shadowRoot.addEventListener("change", (ev) => {
      if (ev.target.id === "baby") {
        this._baby = ev.target.value;
        this._load();
      }
    });
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.hass = hass;
    // Reload when any babylog entity changed (i.e. the app synced).
    const sig = Object.values(hass.entities ?? {})
      .filter((e) => e.platform === "babylog")
      .map((e) => hass.states[e.entity_id]?.last_updated)
      .join("|");
    if (first) {
      this._signature = sig;
      this._render();
      this._load();
    } else if (sig !== this._signature) {
      this._signature = sig;
      clearTimeout(this._reload);
      this._reload = setTimeout(() => this._load(), 500);
    }
  }

  set narrow(narrow) {
    this._narrow = narrow;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.narrow = narrow;
  }

  _range() {
    const a = parseDate(this._anchor);
    if (this._period === "day") return { start: a, days: 1 };
    if (this._period === "week") {
      const monday = addDays(a, -((a.getDay() + 6) % 7));
      return { start: monday, days: 7 };
    }
    const first = new Date(a.getFullYear(), a.getMonth(), 1);
    return { start: first, days: new Date(a.getFullYear(), a.getMonth() + 1, 0).getDate() };
  }

  _shift(dir) {
    const a = parseDate(this._anchor);
    if (this._period === "day") this._anchor = isoDate(addDays(a, dir));
    else if (this._period === "week") this._anchor = isoDate(addDays(a, 7 * dir));
    else this._anchor = isoDate(new Date(a.getFullYear(), a.getMonth() + dir, 1));
    this._load();
  }

  _onClick(ev) {
    const el = ev.target.closest("[data-action]");
    if (!el) return;
    const { action, value } = el.dataset;
    if (action === "period") {
      this._period = value;
      this._load();
    } else if (action === "prev") this._shift(-1);
    else if (action === "next") this._shift(1);
    else if (action === "today") {
      this._anchor = isoDate(new Date());
      this._load();
    }
  }

  async _load() {
    if (!this._hass) return;
    const { start, days } = this._range();
    const token = (this._token = Symbol());
    this._loading = true;
    this._render();
    try {
      const res = await this._hass.callWS({
        type: "call_service",
        domain: "babylog",
        service: "stats",
        service_data: { start: isoDate(start), days, ...(this._baby ? { baby_id: this._baby } : {}) },
        return_response: true,
      });
      if (token !== this._token) return;
      this._data = res.response;
      this._baby = this._data.baby.id;
      this._error = null;
    } catch (err) {
      if (token !== this._token) return;
      this._error = err.message ?? String(err);
    }
    this._loading = false;
    this._render();
  }

  _lang() {
    return this._hass?.locale?.language ?? navigator.language;
  }

  _periodLabel() {
    const { start, days } = this._range();
    const lang = this._lang();
    if (this._period === "day")
      return start.toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" });
    if (this._period === "month") return start.toLocaleDateString(lang, { month: "long", year: "numeric" });
    const end = addDays(start, days - 1);
    const f = (d) => d.toLocaleDateString(lang, { day: "numeric", month: "short" });
    return `${f(start)} – ${f(end)}`;
  }

  _render() {
    const d = this._data;
    const lang = this._lang();
    const isCurrent = (() => {
      const { start, days } = this._range();
      const today = new Date();
      return today >= start && today < addDays(start, days);
    })();
    const dayLabel = (s) =>
      parseDate(s).toLocaleDateString(lang, this._period === "week" ? { weekday: "short", day: "numeric" } : { day: "numeric" });

    let body = "";
    if (this._error) body = `<ha-card class="msg">Couldn't load statistics: ${esc(this._error)}</ha-card>`;
    else if (!d) body = `<ha-card class="msg">Loading…</ha-card>`;
    else body = this._content(d, dayLabel);

    const babies = d?.babies ?? [];
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="toolbar">
        <ha-menu-button></ha-menu-button>
        <div class="title">Junior${d?.baby?.name && babies.length <= 1 ? ` · ${esc(d.baby.name)}` : ""}</div>
        ${babies.length > 1 ? `<select id="baby">${babies.map((b) => `<option value="${esc(b.id)}" ${b.id === d.baby.id ? "selected" : ""}>${esc(b.name ?? b.id)}</option>`).join("")}</select>` : ""}
      </div>
      <div class="content">
        <div class="periodbar">
          <div class="seg">${PERIODS.map((p) => `<button data-action="period" data-value="${p}" class="${p === this._period ? "on" : ""}">${p[0].toUpperCase() + p.slice(1)}</button>`).join("")}</div>
          <div class="nav">
            <button data-action="prev" class="icon" title="Previous">‹</button>
            <span class="plabel">${esc(this._periodLabel())}</span>
            <button data-action="next" class="icon" title="Next">›</button>
            <button data-action="today" class="today" ${isCurrent ? "disabled" : ""}>Today</button>
          </div>
          ${this._loading ? `<span class="spinner"></span>` : ""}
        </div>
        ${body}
      </div>`;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) {
      menu.hass = this._hass;
      menu.narrow = this._narrow;
    }
  }

  /** The next 16 hours: predicted naps, bedtime, wake-ups and feeds (at night
   *  only feeds). Only on the Day view of today. */
  _nextCard(d) {
    const f = d.forecast;
    if (!f || d.days.length !== 1 || d.days[0].date !== isoDate(new Date())) return "";
    return `<ha-card class="wide next">
      <div class="head"><h2>Next 16 hours</h2>${legend([["Sleep", C.sleep], ["Feed", C.bottle]])}</div>
      ${forecastChart(f, this._lang())}
    </ha-card>`;
  }

  _content(d, dayLabel) {
    const s = d.summary;
    const single = d.days.length === 1;
    const tiles = `
      <div class="tiles">
        ${tile("Milk", `${s.milk_ml} mL`, single ? `${s.bottles} bottles` : `${s.milk_ml_per_day} mL / day`, C.bottle, single ? s.milk_pct : null)}
        ${tile("Feeds", single ? `${s.feeds}` : `${s.feeds_per_day} / day`, s.avg_feed_interval_min ? `every ${hm(s.avg_feed_interval_min)}` : "–", C.feed)}
        ${tile("Sleep", `${hm(s.sleep_min_per_day)}${single ? "" : " / day"}`, `longest ${hm(s.longest_sleep_min)}`, C.sleep, single ? s.sleep_pct : null)}
        ${tile("Diapers", single ? `${s.diapers}` : `${s.diapers_per_day} / day`, `${s.wet} wet · ${s.poopy} poopy`, C.wet)}
      </div>`;

    const rhythm = `${this._nextCard(d)}
      <ha-card class="wide">
        <div class="head"><h2>Rhythm</h2>${legend([["Sleep", C.sleep], ["Bottle", C.bottle], ["Breast", C.feed], ["Wet", C.wet], ["Poopy", C.poopy], ["Night", "var(--junior-night-bg)"]])}</div>
        ${rhythmChart(d, dayLabel)}
      </ha-card>`;

    let milk, diapers, sleep = null;
    if (single) {
      const hrs = hourly(d);
      const hl = (h) => (h % 3 === 0 ? pad(h) : "");
      milk = barChart({ buckets: hrs.map((h) => ({ label: hl(h.h), title: `${pad(h.h)}:00`, stacks: [{ value: h.milk, color: C.bottle, name: "Milk" }] })), fmt: (v) => `${Math.round(v)}` });
      diapers = barChart({ buckets: hrs.map((h) => ({ label: hl(h.h), title: `${pad(h.h)}:00`, stacks: [{ value: h.wet, color: C.wet, name: "Wet" }, { value: h.poopy, color: C.poopy, name: "Poopy" }] })), fmt: (v) => `${Math.round(v * 10) / 10}` });
    } else {
      const every = d.days.length > 14 ? 5 : 1;
      const days = d.days;
      const elapsed = days.filter((x) => x.elapsed);
      const avg = (k) => (elapsed.length ? elapsed.reduce((t, x) => t + x[k], 0) / elapsed.length : null);
      milk = barChart({ buckets: days.map((x) => ({ label: dayLabel(x.date), stacks: [{ value: x.milk_ml, color: C.bottle, name: "Milk" }] })), fmt: (v) => `${Math.round(v)}`, avg: avg("milk_ml"), labelEvery: every });
      sleep = barChart({ buckets: days.map((x) => ({ label: dayLabel(x.date), stacks: [{ value: x.night_sleep_min, color: C.night, name: "Night" }, { value: x.day_sleep_min, color: C.sleep, name: "Naps" }] })), fmt: (v) => hm(v).replace(" 00m", "h"), avg: avg("sleep_min"), labelEvery: every });
      diapers = barChart({ buckets: days.map((x) => ({ label: dayLabel(x.date), stacks: [{ value: x.diapers - x.poopy, color: C.wet, name: "Wet" }, { value: x.poopy, color: C.poopy, name: "Poopy" }] })), fmt: (v) => `${Math.round(v * 10) / 10}`, avg: avg("diapers"), labelEvery: every });
    }

    return `${tiles}${rhythm}
      <div class="grid">
        <ha-card><div class="head"><h2>Milk</h2><span class="unit">mL</span></div>${milk}</ha-card>
        ${sleep ? `<ha-card><div class="head"><h2>Sleep</h2>${legend([["Night", C.night], ["Naps", C.sleep]])}</div>${sleep}</ha-card>` : ""}
        <ha-card><div class="head"><h2>Diapers</h2>${legend([["Wet", C.wet], ["Poopy", C.poopy]])}</div>${diapers}</ha-card>
      </div>`;
  }
}

/** Summary tile; with `pct` (Day view) it fills up from the bottom to that %
 *  of a usual day (the average over the week before), e.g. milk so far today. */
function tile(label, value, sub, color, pct = null) {
  const fill = pct == null ? "" : `<div class="fill" style="height:${Math.min(pct, 100)}%;background:${color}"></div>`;
  const badge =
    pct == null
      ? ""
      : `<span class="pct" title="Compared with the average day in the week before">${pct}%${pct > 100 ? " ↑" : ""} of usual</span>`;
  return `<ha-card class="tile">${fill}<div class="tin"><div class="tlabel"><i style="background:${color}"></i>${esc(label)}${badge}</div><div class="tvalue">${esc(value)}</div><div class="tsub">${esc(sub)}</div></div></ha-card>`;
}

const STYLE = `
  :host { display:block; min-height:100vh; background:var(--primary-background-color); color:var(--primary-text-color);
    --junior-night-bg: color-mix(in srgb, ${C.night} 10%, transparent); font-family: var(--paper-font-body1_-_font-family, inherit); }
  .toolbar { display:flex; align-items:center; gap:8px; height:var(--header-height,56px); padding:0 12px; box-sizing:border-box;
    background:var(--app-header-background-color); color:var(--app-header-text-color, white); border-bottom:var(--app-header-border-bottom, none); }
  .title { font-size:20px; flex:1; margin-left:4px; }
  select { font:inherit; font-size:14px; padding:6px 8px; border-radius:8px; border:1px solid var(--divider-color); background:var(--card-background-color); color:var(--primary-text-color); }
  .content { max-width:1280px; margin:0 auto; padding:16px; display:flex; flex-direction:column; gap:16px; }
  .periodbar { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }
  .seg { display:inline-flex; background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:20px; padding:3px; }
  .seg button { border:0; background:none; color:var(--primary-text-color); font:inherit; font-size:14px; padding:6px 14px; border-radius:16px; cursor:pointer; }
  .seg button.on { background:var(--primary-color); color:var(--text-primary-color, white); }
  .nav { display:flex; align-items:center; gap:6px; flex:1; }
  .nav .icon { width:34px; height:34px; border-radius:50%; border:0; background:none; color:var(--primary-text-color); font-size:24px; line-height:1; cursor:pointer; }
  .nav .icon:hover, .today:hover:not([disabled]) { background:var(--secondary-background-color); }
  .plabel { font-size:16px; font-weight:500; min-width:150px; text-align:center; }
  .today { border:1px solid var(--divider-color); background:none; color:var(--primary-color); font:inherit; font-size:14px; border-radius:16px; padding:5px 12px; cursor:pointer; }
  .today[disabled] { color:var(--disabled-text-color); cursor:default; }
  .spinner { width:16px; height:16px; border:2px solid var(--divider-color); border-top-color:var(--primary-color); border-radius:50%; animation:spin .8s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  ha-card { display:block; padding:16px; box-sizing:border-box; }
  .msg { color:var(--secondary-text-color); }
  .tiles { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:16px; }
  .tile { padding:14px 16px; }
  .tlabel { display:flex; align-items:center; gap:8px; color:var(--secondary-text-color); font-size:14px; }
  .tlabel i, .legend i { width:10px; height:10px; border-radius:3px; display:inline-block; flex:none; }
  .tvalue { font-size:26px; font-weight:500; margin-top:6px; }
  .tsub { color:var(--secondary-text-color); font-size:13px; margin-top:2px; }
  .grid { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:16px; }
  .head { display:flex; align-items:center; flex-wrap:wrap; gap:8px 16px; margin-bottom:10px; }
  h2 { font-size:16px; font-weight:500; margin:0; flex:1; }
  .unit { color:var(--secondary-text-color); font-size:13px; }
  .legend { display:flex; flex-wrap:wrap; gap:4px 12px; font-size:12px; color:var(--secondary-text-color); }
  .legend span { display:inline-flex; align-items:center; gap:5px; }
  .chart { width:100%; height:auto; display:block; overflow:visible; }
  .grid line, line.grid { stroke:var(--divider-color); stroke-width:1; }
  .ylab { fill:var(--secondary-text-color); font-size:12px; text-anchor:end; }
  .xlab { fill:var(--secondary-text-color); font-size:12px; text-anchor:middle; }
  .avg { stroke:var(--primary-text-color); stroke-dasharray:5 5; opacity:.45; }
  .avglab { fill:var(--secondary-text-color); font-size:12px; text-anchor:end; }
  .rowbg { fill:var(--secondary-background-color); opacity:.35; }
  .nightbg { fill:var(--junior-night-bg); }
  .now { stroke:var(--error-color, #db4437); stroke-width:2; }
  rect.active { opacity:.8; }
  .marker { stroke:var(--card-background-color); stroke-width:1.5; }
  .next .head { margin-bottom:4px; }
  .evlab { fill:var(--primary-text-color); font-size:12px; font-weight:500; }
  .empty { color:var(--secondary-text-color); font-size:14px; }
  .tile { position:relative; overflow:hidden; }
  .tile .fill { position:absolute; left:0; right:0; bottom:0; opacity:.18; transition:height .4s; }
  .tile .tin { position:relative; }
  .pct { margin-left:auto; font-size:12px; color:var(--secondary-text-color); }
  @media (max-width: 870px) { .grid { grid-template-columns:1fr; } .tiles { grid-template-columns:repeat(2, minmax(0,1fr)); } }
  @media (max-width: 480px) { .content { padding:10px; gap:10px; } .tvalue { font-size:21px; } .plabel { min-width:0; flex:1; } }
`;

if (!customElements.get("junior-panel")) customElements.define("junior-panel", JuniorPanel);
