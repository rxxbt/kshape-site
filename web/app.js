(() => {
  const $ = (id) => document.getElementById(id);
  const SOLSCAN = "https://solscan.io/tx/";
  const WINDOW_HOURS = { h24: 24, d3: 72, d7: 168 };
  let snap = null;

  // ---------------------------------------------------------------- formatting
  const int = (n) => Math.round(n).toLocaleString("en-US");
  const usd = (n) => "$" + (n >= 100 ? int(n) : n.toFixed(2));
  const pct = (n) => int(n) + "%";
  const compact = (n) => n >= 1e9 ? (n / 1e9).toFixed(1) + "B"
    : n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "K" : int(n);
  const sup = (s) => s.replace(/[0-9-]/g, (c) => "⁰¹²³⁴⁵⁶⁷⁸⁹"["0123456789".indexOf(c)] ?? "⁻");
  function big(n) {                         // the Board abbreviates with K / M / B percent
    if (n === "inf" || n === Infinity) return "∞";
    if (n < 1e3) return n.toFixed(0) + "%";
    if (n < 1e6) return (n / 1e3).toFixed(1) + "K%";
    if (n < 1e9) return (n / 1e6).toFixed(1) + "M%";
    if (n < 1e12) return (n / 1e9).toFixed(1) + "B%";
    const e = Math.floor(Math.log10(n));
    return (n / 10 ** e).toFixed(1) + " × 10" + sup(String(e)) + "%";
  }
  const utc = (iso) => {
    const d = new Date(iso);
    return d.toLocaleString("en-GB", { timeZone: "UTC", day: "numeric", month: "short",
      hour: "2-digit", minute: "2-digit" }) + " UTC";
  };
  function ago(iso) {
    const m = Math.round((Date.now() - new Date(iso)) / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + " min ago";
    const h = Math.round(m / 60);
    return h + (h === 1 ? " hour ago" : " hours ago");
  }

  // ------------------------------------------------------------------ numbers
  function render(s) {
    const p = s.payouts, q = s.quote, t = s.token;
    $("paid").textContent = p.paid.toFixed(8);
    $("paid-shares").textContent = p.shares_paid ? `≈ ${p.shares_paid.toFixed(4)} shares of NVIDIA` : "";
    $("paid-sub").textContent = `≈ ${usd(p.paid_usd)} at today's NVDA price · ` +
      `+${p.pending.toFixed(4)} pending · ${int(p.payout_count)} payouts to ${int(p.wallets_paid)} wallets`;

    $("apr-since").textContent = pct(s.apr.since_launch);
    $("apy-since").textContent = "APY " + big(s.apy.since_launch) +
      ` · first ${Math.floor(s.hours_since_launch)} hours`;
    const launch = new Date(s.launch_at);
    for (const [k, hrs] of Object.entries(WINDOW_HOURS)) {
      const el = $("apr-" + k), v = s.apr[k];
      el.classList.toggle("na", v == null);
      if (v == null) {
        const ready = new Date(Math.ceil((launch.getTime() + hrs * 3600e3) / 3600e3) * 3600e3);
        const left = Math.max(1, Math.ceil((ready - Date.now()) / 3600e3));
        el.textContent = "n/a";
        el.title = `Available once ${hrs} complete hours of data exist, in about ${left} h`;
        setSub(el, left < 48 ? `in ${left} h` : `in ${Math.ceil(left / 24)} days`);
      } else {
        el.textContent = pct(v);
        setSub(el, "APY " + big(s.apy[k]));
      }
    }
    $("apr-real").textContent = pct(s.apr.realized_since_launch);
    setSub($("apr-real"), "NVDAX paid + pending");

    const f = s.fees, h = s.holders;
    $("g-assessed").textContent = int(f.assessed) + " KSHAPE";
    $("g-assessed-sub").textContent = `${f.pct_of_supply.toFixed(2)}% of supply`;
    $("g-elig").textContent = `${int(h.eligible_wallets)} of ${int(h.owners)}`;
    $("g-elig-sub").textContent = `${usd(h.eligible_usd)} held · $${int(h.min_usd)} floor`;
    $("g-tax").textContent = (t.transfer_fee_bps / 100).toFixed(t.transfer_fee_bps % 100 ? 1 : 0) + "%";
    $("g-ded").textContent = (s.deduction.measured * 100).toFixed(2) + "%";
    $("g-px").textContent = "$" + t.price_usd.toPrecision(3);
    $("g-mcap").textContent = `${usd(t.market_cap_usd)} market cap`;
    $("g-nv").textContent = usd(q.price_usd);
    $("g-share").textContent = p.token_per_share ? compact(p.token_per_share) + " KSHAPE" : "—";

    $("chart-total").textContent = `${f.quote_bought.toFixed(4)} NVDAX total`;
    $("updated").textContent = `Updated ${ago(s.generated_at)} · ${utc(s.generated_at)}`;
    chart(s);
  }
  function setSub(el, text) {
    let sub = el.nextElementSibling;
    if (!sub || !sub.classList.contains("sub")) {
      sub = document.createElement("p"); sub.className = "sub"; el.after(sub);
    }
    sub.textContent = text;
  }

  // -------------------------------------------------------------------- chart
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };
  let pts = [], geom = null, kdata = null;

  function chart(s) {
    const box = $("chart"), W = box.clientWidth || 600;
    const H = Math.max(200, Math.min(320, Math.round(W * 0.38)));
    const m = { l: 44, r: 14, t: 12, b: 26 };
    const t0 = new Date(s.launch_at).getTime(), t1 = new Date(s.generated_at).getTime();
    pts = s.series.bought_cumulative.map(([t, v, sig]) => ({ t: new Date(t).getTime(), v, sig, iso: t }));
    const vmax = Math.max(1, ...pts.map((p) => p.v)) * 1.12;
    const x = (t) => m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r);
    const y = (v) => H - m.b - v / vmax * (H - m.t - m.b);
    geom = { x, y, W, H, m };

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
    const step = niceStep(vmax / 4);
    for (let v = 0; v <= vmax; v += step) {
      svg.append(el("line", { class: "grid-line", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      const lab = el("text", { class: "axis", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" });
      lab.textContent = +v.toFixed(2); svg.append(lab);
    }
    const spanH = (t1 - t0) / 3600e3;
    const [every, unit] = spanH <= 12 ? [2, "h"] : spanH <= 40 ? [4, "h"] : spanH <= 240 ? [24, "d"] : [168, "w"];
    for (let hh = 0; hh <= spanH; hh += every) {
      const lab = el("text", { class: "axis", x: x(t0 + hh * 3600e3), y: H - 6, "text-anchor": "middle" });
      lab.textContent = unit === "h" ? `${hh}h` : unit === "d" ? `day ${hh / 24}` : `wk ${hh / 168}`;
      svg.append(lab);
    }
    // step-after line from launch (0) to now (last value)
    let d = `M${x(t0)},${y(0)}`, prev = 0;
    for (const p of pts) { d += `H${x(p.t)}V${y(p.v)}`; prev = p.v; }
    d += `H${x(t1)}`;
    svg.append(el("path", { class: "area", d: d + `V${y(0)}H${x(t0)}Z` }));
    svg.append(el("path", { class: "line", d }));
    for (const p of pts) svg.append(el("circle", { class: "dot", cx: x(p.t), cy: y(p.v), r: 3.5 }));
    const cursor = el("line", { class: "cursor", y1: m.t, y2: H - m.b, visibility: "hidden" });
    svg.append(cursor);
    const hit = el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
    svg.append(hit);
    hit.addEventListener("pointermove", (e) => hover(e, svg, cursor));
    hit.addEventListener("pointerdown", (e) => hover(e, svg, cursor));
    box.replaceChildren(svg);
  }
  function niceStep(raw) {
    const e = 10 ** Math.floor(Math.log10(raw)), f = raw / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
  }
  function hover(e, svg, cursor) {
    if (!pts.length) return;
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) * (geom.W / r.width);
    let best = pts[0];
    for (const p of pts) if (Math.abs(geom.x(p.t) - px) < Math.abs(geom.x(best.t) - px)) best = p;
    const i = pts.indexOf(best), add = best.v - (i ? pts[i - 1].v : 0);
    cursor.setAttribute("x1", geom.x(best.t)); cursor.setAttribute("x2", geom.x(best.t));
    cursor.setAttribute("visibility", "visible");
    const tip = $("tip");
    tip.innerHTML = `${utc(best.iso)}<br><b>+${add.toFixed(4)}</b> NVDAX · total ${best.v.toFixed(4)}<br>` +
      `<a href="${SOLSCAN}${best.sig}" target="_blank" rel="noopener">view transaction ↗</a>`;
    tip.hidden = false;
    const wrap = tip.parentElement.getBoundingClientRect();
    const cx = r.left - wrap.left + geom.x(best.t) * (r.width / geom.W);
    const cy = r.top - wrap.top + geom.y(best.v) * (r.height / geom.H);
    const left = Math.min(Math.max(8, cx - tip.offsetWidth / 2), wrap.width - tip.offsetWidth - 8);
    tip.style.left = left + "px";
    tip.style.top = Math.max(8, cy - tip.offsetHeight - 14) + "px";
  }
  document.querySelector(".chart-wrap").addEventListener("pointerleave", () => {
    $("tip").hidden = true;
    const c = document.querySelector(".chart .cursor"); if (c) c.setAttribute("visibility", "hidden");
  });


  // ------------------------------------------------------------- the K chart
  function kchart(k) {
    const box = $("kchart"), W = box.clientWidth || 600;
    const H = Math.max(220, Math.min(340, Math.round(W * 0.42)));
    const m = { l: 52, r: W < 520 ? 44 : 56, t: 14, b: 26 };
    const nv = k.series.nvda.map(([d, v]) => [Date.parse(d), v]);
    const wg = k.series.wages.map(([d, v]) => [Date.parse(d), v]);
    const t0 = Math.min(nv[0][0], wg[0][0]), t1 = Math.max(nv.at(-1)[0], wg.at(-1)[0]);
    const vmax = Math.max(...nv.map((p) => p[1])) * 1.08;
    const x = (t) => m.l + (t - t0) / (t1 - t0) * (W - m.l - m.r);
    const y = (v) => H - m.b - v / vmax * (H - m.t - m.b);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H });
    const step = niceStep(vmax / 4);
    for (let v = 0; v <= vmax; v += step) {
      svg.append(el("line", { class: "grid-line", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) }));
      const lab = el("text", { class: "axis", x: m.l - 8, y: y(v) + 4, "text-anchor": "end" });
      lab.textContent = int(v); svg.append(lab);
    }
    for (let yr = new Date(t0).getUTCFullYear() + 1; yr <= new Date(t1).getUTCFullYear(); yr++) {
      const tx = Date.UTC(yr, 0, 1);
      if (tx < t0 || tx > t1) continue;
      const lab = el("text", { class: "axis", x: x(tx), y: H - 6, "text-anchor": "middle" });
      lab.textContent = yr; svg.append(lab);
    }
    const path = (pts) => "M" + pts.map((p) => `${x(p[0])},${y(p[1])}`).join("L");
    svg.append(el("path", { class: "wage", d: path(wg) }));
    svg.append(el("path", { class: "nvda", d: path(nv) }));
    for (const [pts, cls, name] of [[nv, "nvda", "NVDA"], [wg, "wage", "wages"]]) {
      const last = pts.at(-1);
      const lab = el("text", { class: "endlab", x: x(last[0]) + 8, y: y(last[1]) + 4, fill: "" });
      lab.setAttribute("fill", cls === "nvda" ? "var(--green)" : "var(--red)");
      lab.textContent = `${(last[1] / 100).toFixed(last[1] > 200 ? 0 : 2)}×`;
      svg.append(lab);
    }
    box.replaceChildren(svg);
    const n = nv.at(-1)[1], w = wg.at(-1)[1];
    $("k-sub").innerHTML =
      `<b style="color:var(--green)">NVDA +${int(n - 100)}%</b>` +
      `<span style="color:var(--dim)"> &nbsp;·&nbsp; </span>` +
      `<b style="color:var(--red)">real wages +${(w - 100).toFixed(1)}%</b>`;
    $("k-source").textContent = `Both indexed to January 2020 = 100. ${k.sources.nvda}. ${k.sources.wages}. ` +
      `Updated ${utc(k.generated_at)}.`;
  }

  // -------------------------------------------------------------------- wiring
  $("copy").addEventListener("click", async () => {
    const ca = $("ca").textContent.trim();
    try { await navigator.clipboard.writeText(ca); }
    catch { const r = document.createRange(); r.selectNodeContents($("ca"));
      getSelection().removeAllRanges(); getSelection().addRange(r); document.execCommand("copy"); }
    $("copy").textContent = "copied"; setTimeout(() => ($("copy").textContent = "copy"), 1600);
  });
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { if (snap) chart(snap); if (kdata) kchart(kdata); }, 150); });

  async function load() {
    try {
      const r = await fetch("data/latest.json?t=" + Date.now(), { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      snap = await r.json(); render(snap);
      try {
        const kr = await fetch("data/k.json?t=" + Date.now(), { cache: "no-store" });
        if (kr.ok) { kdata = await kr.json(); kchart(kdata); }
      } catch { document.querySelector(".kcard").hidden = true; }
    } catch (e) {
      $("updated").textContent = "Couldn't load live data. Try again in a minute.";
    }
  }
  load();
  setInterval(() => document.visibilityState === "visible" && load(), 5 * 60e3);
})();
