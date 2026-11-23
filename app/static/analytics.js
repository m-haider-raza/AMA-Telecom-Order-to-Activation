"use strict";
/* Analytics tab of the console: order to delivery, time per agent, fallout and the data behind the figures.
   It runs after app.js and borrows its helpers (api, toast, requireToken, token); every name of its own stays private.
   The strict CSP forbids style attributes, so the charts are plain DOM whose sizes and positions are set through
   the CSSOM. Everything that comes from the server is written with textContent. */
(() => {
  const FAST_MS = 2000, SLOW_MS = 6000, SOURCE_MS = 30000, DAY = 86400;
  const AXIS_LOW = 10, AXIS_HIGH = 100 * DAY;
  const lockedText = () => session.staffAuth === "sso" ? "Sign in with Microsoft to see the analytics."
    : "Sign in as staff, or load the manager token from Demo access, to see the analytics.";
  const refusedText = () => session.staff ? `Your role (${session.staff.roleLabel}) cannot open the analytics.`
    : "This token can't open the analytics. Use the manager token, or sign in as staff.";
  const SCENARIOS = {
    happy: "Straight-through activation", "auto-recovery": "Partner timeout, fixed by an agent",
    "human-exception": "Route conflict, manager approval", "change-window": "Closed change window, policy hold",
    "address-correction": "Invalid site, customer correction",
  };
  const BRIEF = {
    happy: "straight-through", "auto-recovery": "partner timeout", "human-exception": "route conflict",
    "change-window": "closed change window", "address-correction": "invalid site",
  };
  const OUTCOMES = [
    ["straight-through", "Straight through", "straight", "ok"], ["auto-recovered", "Fixed by an agent", "recovered", "info"],
    ["needs-people", "Needed a person", "people", "warn"], ["failed", "Failed", "failed", "bad"],
    ["in-flight", "Still in progress", "flight", "mute"],
  ];
  const VERDICT = {met: ["Target met", "ok"], "not-met": ["Not met yet", "bad"], "no-data": ["No data yet", "mute"]};

  let report = null, source = null, scope = "auto", signature = "", sourceSignature = "", lastStatus = "";
  let lastLoad = 0, lastSource = 0, inFlight = null, starting = false;

  function make(tag, props, ...children) {
    const element = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === false) return;
      if (key === "text") element.textContent = value;
      else if (key === "class") element.className = value;
      else element.setAttribute(key, value === true ? "" : value);
    });
    element.append(...children.flat(Infinity).filter(child => child !== null && child !== undefined && child !== false));
    return element;
  }
  function place(element, styles) { Object.assign(element.style, styles); return element; }
  const chip = (text, tone) => make("span", {class: tone ? `chip ${tone}` : "chip", text});
  const heads = titles => make("thead", null, make("tr", null, titles.map(title => make("th", {text: title}))));
  const number = (value, digits = 1) => new Intl.NumberFormat("en-GB", {maximumFractionDigits: digits}).format(value);
  const percent = value => value === null || value === undefined ? "–" : `${number(value)}%`;
  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "–";
    if (seconds < 1) return `${number(seconds * 1000, 0)} ms`;
    if (seconds < 60) return `${number(seconds, seconds < 10 ? 1 : 0)} s`;
    if (seconds < 3600) return `${number(seconds / 60, seconds < 600 ? 1 : 0)} min`;
    if (seconds < DAY) return `${number(seconds / 3600, seconds < 36000 ? 1 : 0)} h`;
    return `${number(seconds / DAY, seconds < 10 * DAY ? 1 : 0)} d`;
  }
  function stamp(iso) {
    const when = new Date(iso);
    return iso && !Number.isNaN(when.getTime()) ? when.toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "–";
  }
  const utc = value => !value || /(?:Z|[+-]\d\d:?\d\d)$/i.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const mixText = mix => Object.entries(mix || {}).filter(([, count]) => count).map(([name, count]) => `${count} ${BRIEF[name] || name}`).join(", ");

  /* Charts */
  /* A horizontal scale: coloured zones, reference lines, labelled ticks and one pin. Every position is a percentage. */
  function gauge({label, zones, lines, ticks, pin}) {
    const track = make("div", {class: "an-track"});
    zones.forEach(([from, to, tone]) => track.append(place(make("span", {class: `an-zone an-c-${tone}`}), {left: `${from}%`, width: `${to - from}%`})));
    lines.forEach(([position, tone]) => track.append(place(make("span", {class: `an-line ${tone}`}), {left: `${position}%`})));
    const lane = make("div", {class: "an-lane"});
    if (pin) {
      const at = Math.min(Math.max(pin.position, 2), 98), edge = at < 12 ? " start" : at > 88 ? " end" : "";
      lane.append(place(make("span", {class: `an-pin ${pin.tone}${edge}`}, make("span", {class: "an-pin-label", text: pin.text})), {left: `${at}%`}));
    }
    const scale = make("div", {class: "an-scale"});
    ticks.forEach(([position, text, note, tone]) => scale.append(
      place(make("span", {class: tone ? `an-tick ${tone}` : "an-tick"}, make("b", {text}), note ? make("i", {text: note}) : null), {left: `${position}%`})));
    return make("div", {class: "an-gauge", role: "img", "aria-label": label}, lane, track, scale);
  }
  const along = seconds => 100 * Math.log10(Math.min(Math.max(seconds, AXIS_LOW), AXIS_HIGH) / AXIS_LOW) / Math.log10(AXIS_HIGH / AXIS_LOW);
  function cycleGauge(cycle) {
    const target = along(cycle.targetDays * DAY), today = along(cycle.baselineDays * DAY);
    return gauge({
      label: `Average order to delivery ${duration(cycle.avg)}. Target ${cycle.targetDays} days. Today ${cycle.baselineDays} days. Logarithmic scale.`,
      zones: [[0, target, "good"], [target, today, "gap"], [today, 100, "bad"]],
      lines: [[target, "target"], [today, "today"]],
      ticks: [[along(60), "1 min"], [along(3600), "1 h"], [along(DAY), "1 d"],
        [target, `${cycle.targetDays} d`, "target", "target"], [today, `${cycle.baselineDays} d`, "today", "today"]],
      pin: cycle.avg === null ? null : {position: along(cycle.avg), text: duration(cycle.avg), tone: VERDICT[cycle.verdict][1]},
    });
  }
  function falloutGauge(fallout) {
    const top = Math.max(40, Math.ceil((fallout.falloutPercent || 0) * 1.15 / 5) * 5);
    const at = value => 100 * Math.min(value, top) / top, target = at(fallout.targetPercent), today = at(fallout.baselinePercent);
    return gauge({
      label: `Fallout rate ${percent(fallout.falloutPercent)}. Target under ${fallout.targetPercent}%. Today ${fallout.baselinePercent}%.`,
      zones: [[0, target, "good"], [target, today, "gap"], [today, 100, "bad"]],
      lines: [[target, "target"], [today, "today"]],
      ticks: [[0, "0%"], [target, `${fallout.targetPercent}%`, "target", "target"], [today, `${fallout.baselinePercent}%`, "today", "today"], [100, `${top}%`]],
      pin: fallout.falloutPercent === null ? null : {position: at(fallout.falloutPercent), text: percent(fallout.falloutPercent), tone: VERDICT[fallout.verdict][1]},
    });
  }
  /* One bar split into proportional pieces; pieces with no value are left out. */
  function stack(parts, label) {
    const bar = make("div", {class: "an-stack", role: "img", "aria-label": label});
    const shown = parts.filter(part => part.value > 0), total = shown.reduce((sum, part) => sum + part.value, 0);
    shown.forEach(part => {
      const piece = make("span", {class: `an-c-${part.tone}`, title: part.title});
      // Shares add up to 100 so the pieces always fill the bar, even when the values are fractions of a second.
      piece.style.flexGrow = String(100 * part.value / total); bar.append(piece);
    });
    return bar;
  }
  const legend = items => make("div", {class: "an-legend"},
    items.map(([tone, text, value]) => make("span", {class: "an-key"}, make("i", {class: `an-dot an-c-${tone}`}), text, value === undefined ? null : make("b", {text: value}))));
  const kpi = (label, value, feet, word, tone) => make("div", {class: "metric an-kpi"},
    make("div", {class: "metric-label", text: label}), make("div", {class: "metric-value", text: value}),
    feet.map(line => make("div", {class: "metric-foot", text: line})), word ? chip(word, tone) : null);

  /* Painting */
  function paintRun() {
    const bench = report && report.scopes.benchmark, running = Boolean(bench && bench.status === "running");
    const busy = starting || running, size = bench ? bench.run.size : 0, button = $("#an-run-button");
    const reason = can("operate") ? "" : `Your role (${session.staff.roleLabel}) cannot run benchmarks.`;
    button.disabled = busy || Boolean(reason); button.title = reason; $("#an-size").disabled = busy || Boolean(reason);
    button.textContent = busy ? "Running…" : bench ? "Run again" : "Run benchmark";
    const bar = $("#an-progress");
    bar.classList.toggle("hidden", !running);
    if (running) {
      const share = size ? Math.round(100 * bench.completed / size) : 0;
      bar.setAttribute("aria-valuenow", String(share)); $("#an-progress-fill").style.width = `${share}%`;
    }
    let text = "No run yet. A run sends a fixed mix of synthetic orders through the five agents and measures them end to end.";
    if (running) text = `Running: ${number(bench.completed, 0)} of ${number(size, 0)} orders delivered so far.`;
    else if (starting) text = "Starting the run…";
    else if (bench && bench.status === "failed") text = `The last run did not finish: ${bench.run.error || "unknown error"}`;
    else if (bench) text = `Last run ${bench.run.id}: ${number(size, 0)} orders, finished ${stamp(bench.run.completedAt)}. Mix: ${mixText(bench.run.mix)}. A person takes ${bench.run.humanDelaySeconds} s to decide.`;
    $("#an-run-status").textContent = text;
    if (session.isolatedArchitecture) paintIsolatedReviews();
  }
  let loadingReviews = false;
  async function paintIsolatedReviews() {
    if (loadingReviews) return;
    loadingReviews = true;
    let host = $("#an-isolated-reviews");
    if (!host) {
      host = make("div", {id: "an-isolated-reviews"});
      $("#an-run-status").after(host);
    }
    try {
      const {orders} = await api("/api/isolated/benchmark/holds");
      host.replaceChildren();
      if (orders.length) host.append(make("p", {text: "Isolated benchmark: a service manager must explicitly decide these synthetic holds. No agent or supervisor can approve them."}));
      orders.forEach(order => {
        const row = make("div", {class: "an-actions"}, make("span", {text: `${order.id}: ${order.exception?.type || order.state} `}));
        const decisions = order.state === "awaitingCustomer" ? [["correct-site", "Confirm synthetic site"]] : [["approve", "Approve simulated change"], ["reject", "Reject"]];
        decisions.forEach(([decision, label]) => {
          const button = make("button", {type: "button", text: label, disabled: !can("manage")});
          button.addEventListener("click", async () => {
            button.disabled = true;
            try {
              const body = {decision};
              if (decision === "correct-site") body.siteReference = order.exception.expectedSiteReference;
              await api(`/api/isolated/benchmark/orders/${encodeURIComponent(order.id)}/decision`, {method: "POST", body: JSON.stringify(body)});
              loadingReviews = false;
              paintIsolatedReviews();
            } catch (error) { toast(error.message); button.disabled = false; }
          });
          row.append(button);
        });
        host.append(row);
      });
    } catch (error) { host.textContent = `Benchmark review unavailable: ${error.message}`; }
    finally { loadingReviews = false; }
  }
  function paintVerdict() {
    const {cycle, fallout, sample} = report, met = [cycle.verdict, fallout.verdict].filter(item => item === "met").length;
    const empty = cycle.verdict === "no-data" && fallout.verdict === "no-data", thin = !empty && !sample.sufficient;
    const tone = empty ? "mute" : thin ? "warn" : ["bad", "warn", "ok"][met];
    const outcome = ["neither target is met yet", "one of the two targets is met", "both targets are met"][met];
    const title = empty ? "Nothing to measure yet" : thin ? `Indicative: ${outcome}` : outcome.charAt(0).toUpperCase() + outcome.slice(1);
    const lines = empty ? ["Run a benchmark, or create orders, and the results appear here."] : [
      ...thin ? [`Only ${number(sample.decided, 0)} orders have a final outcome; ${number(sample.minimum, 0)} are needed for a reliable measurement. Run a benchmark for a firm answer.`] : [],
      cycle.avg === null ? "No order has reached activation yet."
        : `Orders reached activation in ${duration(cycle.avg)} on average. Target: under ${cycle.targetDays} days. Today: ${cycle.baselineDays} days.`,
      fallout.falloutPercent === null ? "No order has a final outcome yet."
        : `${percent(fallout.falloutPercent)} of orders needed a person or failed. Target: under ${fallout.targetPercent}%. Today: ${fallout.baselinePercent}%.`,
    ];
    $("#an-verdict").replaceChildren(make("div", {class: `an-verdict ${tone}`}, make("strong", {text: title}), lines.map(line => make("span", {text: line}))));
  }
  function paintScope() {
    const {scopes} = report, bench = scopes.benchmark, parts = [];
    if (["benchmark", "all"].includes(report.scope) && bench) {
      parts.push(`the ${bench.status === "running" ? "running" : "latest"} benchmark (${number(bench.run.size, 0)} synthetic orders)`);
    }
    if (["live", "all"].includes(report.scope)) parts.push(`${number(scopes.live.orders, 0)} live demo orders`);
    let text = parts.length ? `Showing ${parts.join(" and ")}.` : "There is no benchmark run yet. Press Run benchmark to measure a full batch of synthetic orders.";
    $("#an-scope").textContent = text;
  }
  function paintKpis() {
    const {cycle, fallout, sample} = report, [cycleWord, cycleTone] = VERDICT[cycle.verdict], [falloutWord, falloutTone] = VERDICT[fallout.verdict];
    $("#an-kpis").replaceChildren(
      kpi("Average order to delivery", duration(cycle.avg),
        [`Target under ${cycle.targetDays} days (today ${cycle.baselineDays})`, `Median ${duration(cycle.median)} · slowest 10% ${duration(cycle.p90)}`], cycleWord, cycleTone),
      kpi("Fallout rate", percent(fallout.falloutPercent),
        [`Target under ${fallout.targetPercent}% (today ${fallout.baselinePercent}%)`, `${number(fallout.falloutOrders, 0)} of ${number(fallout.decided, 0)} orders needed a person or failed`], falloutWord, falloutTone),
      kpi("Incidents fixed by agents", percent(fallout.agentResolvedPercent),
        [fallout.incidentOrders ? `${number(fallout.outcomes["auto-recovered"], 0)} of ${number(fallout.incidentOrders, 0)} incidents needed no person` : "No incidents in this selection"]),
      kpi("Orders measured", number(sample.orders, 0), [`${number(sample.completed, 0)} delivered · ${number(sample.inFlight, 0)} in progress`],
        sample.sufficient ? "Reliable sample" : `Indicative: under ${sample.minimum} finished`, sample.sufficient ? "ok" : "warn"));
  }
  function paintCycle() {
    const cycle = report.cycle;
    let caption = "No order has been delivered in this selection yet.";
    if (cycle.avg !== null) {
      const used = cycle.budgetUsedPercent < 0.1 ? "less than 0.1%" : `${number(cycle.budgetUsedPercent)}%`;
      caption = `The average order uses ${used} of the ${cycle.targetDays}-day budget. ${percent(cycle.withinSlaPercent)} of delivered orders arrived within ${cycle.targetDays} days. Slowest 10%: ${duration(cycle.p90)}.`;
    }
    $("#an-cycle").replaceChildren(
      cycleGauge(cycle), make("p", {class: "muted an-caption", text: caption}),
      legend([["good", `Inside the ${cycle.targetDays}-day target`], ["gap", `Between target and today (${cycle.baselineDays} days)`], ["bad", "Slower than today"]]),
      make("p", {class: "muted small", text: "Logarithmic scale: each step to the right is ten times longer."}));
  }
  function paintFallout() {
    const fallout = report.fallout, counts = fallout.outcomes, fixed = counts["auto-recovered"] || 0;
    const bar = stack(OUTCOMES.map(([key, name, tone]) => ({value: counts[key] || 0, tone, title: `${name}: ${counts[key] || 0}`})),
      `How the orders ended: ${OUTCOMES.map(([key, name]) => `${name} ${counts[key] || 0}`).join(", ")}`);
    const caption = fallout.decided
      ? `${number(fallout.falloutOrders, 0)} of ${number(fallout.decided, 0)} orders needed a person or failed. ${number(fixed, 0)} more had an incident that an agent playbook fixed on its own; those are not counted as fallout.`
      : "No order has a final outcome yet.";
    $("#an-fallout").replaceChildren(
      falloutGauge(fallout), make("p", {class: "muted an-caption", text: caption}),
      make("div", {class: "an-sub", text: "How the orders ended"}), bar,
      legend(OUTCOMES.map(([key, name, tone]) => [tone, name, number(counts[key] || 0, 0)])));
  }
  function agentRow(row, index, peak) {
    const average = row.total.avg, queue = row.queue.avg || 0, work = row.process.avg || 0;
    const bar = stack([{value: queue, tone: "queue", title: `Waiting for the agent: ${duration(queue)}`}, {value: work, tone: "work", title: `Agent working: ${duration(work)}`}],
      `${row.agent}: ${duration(average)} on average, of which ${duration(queue)} waiting and ${duration(work)} working`);
    place(bar, {width: average ? `${Math.max(100 * average / peak, 1.5)}%` : "0"});
    return make("div", {class: "an-agent-row"},
      make("div", {class: "an-agent-name"}, make("b", {text: String(index + 1)}), make("span", {text: row.agent})),
      make("div", {class: "an-bar-cell"}, bar),
      make("div", {class: "an-agent-value"}, make("strong", {text: duration(average)}),
        make("small", {text: row.total.count ? `${number(row.total.count, 0)} steps · slowest 10% ${duration(row.total.p90)}` : "No steps yet"})));
  }
  function paintAgents() {
    const rows = report.agents, peak = Math.max(0, ...rows.map(row => row.total.avg || 0));
    const host = $("#an-agents");
    if (!peak) { host.replaceChildren(make("div", {class: "empty", text: "No agent step has been recorded for this selection yet."})); }
    else {
      host.replaceChildren(make("div", {class: "an-agent-rows"}, rows.map((row, index) => agentRow(row, index, peak)),
        legend([["queue", "Waiting for the agent to pick the work up"], ["work", "Agent working"]])));
    }
    const {cycle, lifecycle} = report;
    if (cycle.avg === null) { $("#an-lifecycle").replaceChildren(); return; }
    const agents = lifecycle.agentSeconds || 0, people = lifecycle.peopleSeconds || 0, rest = Math.max(cycle.avg - agents - people, 0);
    const parts = [["agents", "Agents", agents], ["wait", "Waiting for a person", people], ["other", "Other time between steps", rest]];
    $("#an-lifecycle").replaceChildren(
      make("div", {class: "an-sub", text: "Where a delivered order's time goes, on average"}),
      stack(parts.map(([tone, name, value]) => ({value, tone, title: `${name}: ${duration(value)}`})),
        `Average delivered order, ${duration(cycle.avg)}: ${parts.map(([, name, value]) => `${name} ${duration(value)}`).join(", ")}`),
      legend(parts.map(([tone, name, value]) => [tone, name, duration(value)])));
  }
  function paintScenarios() {
    const host = $("#an-scenarios");
    if (!report.scenarios.length) { host.replaceChildren(make("div", {class: "empty", text: "No orders in this selection yet."})); return; }
    const rows = report.scenarios.map(row => {
      const outcomes = OUTCOMES.filter(([key]) => row.outcomes[key])
        .map(([key, name, , tone]) => [chip(`${row.outcomes[key]} ${name.toLowerCase()}`, tone), " "]);
      return make("tr", null,
        make("td", null, make("span", {class: "order-name", text: SCENARIOS[row.scenario] || row.scenario})),
        make("td", {text: number(row.orders, 0)}), make("td", {text: number(row.completed, 0)}),
        make("td", {text: duration(row.avgSeconds)}), make("td", null, outcomes));
    });
    host.replaceChildren(make("div", {class: "an-table"},
      make("table", null, heads(["Scenario", "Orders", "Delivered", "Average", "Outcomes"]), make("tbody", null, rows))));
  }
  function paintNotes() {
    $("#an-notes").replaceChildren(make("ul", {class: "an-notes"}, report.notes.map(text => make("li", {text}))));
  }
  const fact = (term, value) => [make("dt", {text: term}), make("dd", {text: value})];
  function paintSource() {
    const host = $("#an-source");
    if (!source) { host.replaceChildren(make("div", {class: "empty", text: "Loading…"})); return; }
    if (source.kind !== "azure-sql") {
      host.replaceChildren(make("p", null, chip(source.kind === "azure-blob" ? "Azure Blob snapshot" : "Local file", "warn")), make("p", {class: "muted", text: source.note}));
      return;
    }
    if (source.error) { host.replaceChildren(make("p", null, chip("Azure SQL", "info")), make("p", {class: "form-error", text: source.error})); return; }
    const behind = source.version - source.projectedVersion;
    host.replaceChildren(...[
      make("p", null, chip("Azure SQL", "info"), " ", behind === 0 ? chip("Tables in sync", "ok") : chip(`Tables ${number(behind, 0)} behind`, "warn")),
      make("dl", {class: "an-facts"}, fact("Server", source.server), fact("Database", source.database),
        fact("Order book", `version ${number(source.version, 0)} · ${number(source.documentBytes / 1024, 0)} KB · written ${stamp(utc(source.updatedAt))}`),
        fact("Tables updated to", `version ${number(source.projectedVersion, 0)}`)),
      source.projectionError ? make("p", {class: "form-error", text: `The last table update failed: ${source.projectionError}`}) : null,
      make("div", {class: "an-sub", text: "Rows per table"}),
      make("div", {class: "an-table"}, make("table", {class: "an-mini"}, heads(["Table", "Rows", "Matches order book"]),
        make("tbody", null, source.tables.map(row => make("tr", null, make("td", {text: row.name}), make("td", {text: number(row.rows, 0)}),
          make("td", null, row.match === null ? "–" : chip(row.match ? "Yes" : "No", row.match ? "ok" : "bad"))))))),
      make("div", {class: "an-sub", text: "The same figures, straight from SQL"}),
      make("div", {class: "an-table"}, make("table", {class: "an-mini"}, heads(["Scope", "Orders", "Decided", "Delivered", "Average", "Fallout"]),
        make("tbody", null, (source.views.kpiSummary || []).map(row => make("tr", null, make("td", {text: row.scope}), make("td", {text: number(row.orders, 0)}),
          make("td", {text: number(row.decided, 0)}), make("td", {text: number(row.completed, 0)}),
          make("td", {text: duration(row.avgElapsedSeconds)}), make("td", {text: percent(row.falloutPercent)}))))))].filter(Boolean));
  }

  /* Loading */
  function lock(message) {
    report = null; signature = `locked:${message}`;
    $("#an-content").classList.add("hidden");
    const box = $("#an-locked"); box.textContent = message; box.classList.remove("hidden");
  }
  function adopt(next) {
    $("#an-locked").classList.add("hidden"); $("#an-content").classList.remove("hidden"); $("#an-stale").classList.add("hidden");
    const state = next.scopes.benchmark ? next.scopes.benchmark.status : "";
    if (state !== lastStatus) { lastStatus = state; lastSource = 0; }
    const mark = JSON.stringify(next);
    if (mark === signature) return;
    signature = mark; report = next;
    paintRun(); paintVerdict(); paintScope(); paintKpis(); paintCycle(); paintFallout(); paintAgents(); paintScenarios(); paintNotes();
  }
  function problem(error) {
    if (error.status === 401) lock(lockedText());
    else if (error.status === 403) lock(refusedText());
    else if (!report) lock(`The analytics could not be loaded: ${error.message}`);
    else {
      const note = $("#an-stale");
      note.textContent = `The report could not be refreshed (${error.message}). Showing the last figures.`; note.classList.remove("hidden");
    }
  }
  async function loadSource() {
    lastSource = Date.now();
    try {
      const next = await api("/api/analytics/source"), mark = JSON.stringify(next);
      if (mark === sourceSignature) return;
      sourceSignature = mark; source = next; paintSource();
    } catch (error) {
      if (source) return;
      sourceSignature = "";
      $("#an-source").replaceChildren(make("p", {class: "form-error", text: `The data source could not be read: ${error.message}`}));
    }
  }
  async function refresh() {
    lastLoad = Date.now();
    const wanted = scope;
    try {
      const next = await api(`/api/analytics?scope=${wanted}`);
      if (wanted !== scope) return;
      adopt(next);
    } catch (error) { problem(error); return; }
    if (!source || Date.now() - lastSource >= SOURCE_MS) await loadSource();
  }
  const visible = () => !$("#analytics-page").classList.contains("hidden") && !document.hidden && !document.body.classList.contains("booting");
  function poll(force = false) {
    if (!force && !visible()) return Promise.resolve();
    if (inFlight) return force ? inFlight.then(() => poll(true)) : inFlight;
    inFlight = refresh().finally(() => { inFlight = null; });
    return inFlight;
  }
  function tick() {
    if (!visible() || inFlight) return;
    const running = report && report.scopes.benchmark && report.scopes.benchmark.status === "running";
    if (Date.now() - lastLoad >= (running ? FAST_MS : SLOW_MS) - 100) poll();
  }
  async function runBenchmark() {
    if (starting || !requireToken()) return;
    starting = true; paintRun();
    try {
      await api("/api/analytics/benchmark", {method: "POST", body: JSON.stringify({size: Number($("#an-size").value)})});
      toast("Benchmark started. The figures fill in as orders are delivered.");
    } catch (error) {
      toast(error.status === 409 ? "A benchmark is already running. Wait for it to finish." : error.message, true);
    } finally { starting = false; }
    await poll(true);
    paintRun();
  }

  document.querySelectorAll("#analytics-page [data-scope]").forEach(button => button.addEventListener("click", () => {
    scope = button.dataset.scope; signature = "";
    document.querySelectorAll("#analytics-page [data-scope]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    poll(true);
  }));
  $("#an-run-button").addEventListener("click", runBenchmark);
  document.querySelectorAll('[data-page="analytics"]').forEach(button => button.addEventListener("click", () => poll(true)));
  $("#access-form").addEventListener("submit", () => setTimeout(() => poll(true), 0));
  document.addEventListener("visibilitychange", () => poll());
  setInterval(tick, 1000);
})();
