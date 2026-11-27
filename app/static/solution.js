"use strict";

(async () => {
  const $ = id => document.getElementById(id);
  const canvas = $("story"), ctx = canvas.getContext("2d");
  const C = {bg: "#0b1725", panel: "#142a3b", line: "#355267", white: "#edf5fa",
    muted: "#a7bdcd", mint: "#8ee4c7", amber: "#f4c27a", red: "#fc9293", blue: "#8dbfff"};
  const clamp = n => Math.max(0, Math.min(1, n));
  const ease = n => { n = clamp(n); return n * n * (3 - 2 * n); };
  const clock = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
  let logo;
  let playing = false, position = 1.2, previous;
  let motionTime = 0, nodeIndex = 0;

  function box(x, y, w, h, fill = C.panel, stroke = C.line, radius = 12) {
    ctx.beginPath(); ctx.roundRect(x, y, w, h, radius);
    ctx.fillStyle = fill; ctx.fill(); ctx.strokeStyle = stroke; ctx.lineWidth = 1.5; ctx.stroke();
  }
  function text(value, x, y, size = 20, color = C.white, weight = 400, align = "left") {
    ctx.font = `${weight} ${size}px "Segoe UI", Arial, sans-serif`;
    ctx.fillStyle = color; ctx.textAlign = align; ctx.fillText(value, x, y);
  }
  function wrap(value, x, y, width, size = 18, color = C.white, height = 25) {
    ctx.font = `400 ${size}px "Segoe UI", Arial, sans-serif`;
    let line = "", row = 0;
    for (const word of value.split(" ")) {
      const next = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(next).width > width) {
        text(line, x, y + row++ * height, size, color); line = word;
      } else line = next;
    }
    if (line) text(line, x, y + row++ * height, size, color);
    return row;
  }
  function line(points, color = C.line, dashed = false) {
    ctx.beginPath(); ctx.moveTo(...points[0]); points.slice(1).forEach(p => ctx.lineTo(...p));
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash(dashed ? [8, 7] : []);
    ctx.lineDashOffset = dashed ? -motionTime * 16 : 0; ctx.stroke();
    ctx.setLineDash([]); ctx.lineDashOffset = 0;
  }
  function arrow(points, color = C.mint, dashed = false) {
    line(points, color, dashed);
    const a = points[points.length - 2], b = points[points.length - 1];
    const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    ctx.beginPath(); ctx.moveTo(...b);
    ctx.lineTo(b[0] - 11 * Math.cos(angle - .45), b[1] - 11 * Math.sin(angle - .45));
    ctx.lineTo(b[0] - 11 * Math.cos(angle + .45), b[1] - 11 * Math.sin(angle + .45));
    ctx.closePath(); ctx.fillStyle = color; ctx.fill();
  }
  function dot(x, y, radius, color) {
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  }
  function packet(points, progress, label, color = C.amber, width = 112) {
    progress = ease(progress);
    const lengths = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
    let distance = progress * lengths.reduce((a, b) => a + b, 0), index = 0;
    while (index < lengths.length - 1 && distance > lengths[index]) distance -= lengths[index++];
    const t = lengths[index] ? distance / lengths[index] : 0;
    const a = points[index], b = points[index + 1];
    const x = a[0] + (b[0] - a[0]) * t, y = a[1] + (b[1] - a[1]) * t;
    const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
    ctx.save();
    for (let trail = 1; trail <= 5; trail++) {
      ctx.globalAlpha = .45 * (1 - trail / 6);
      dot(x - Math.cos(angle) * (width / 2 + trail * 10),
        y - Math.sin(angle) * (width / 2 + trail * 10), 4 - trail * .4, color);
    }
    ctx.globalAlpha = 1; ctx.shadowColor = color; ctx.shadowBlur = 12;
    box(x - width / 2, y - 20, width, 40, "#142d3e", color, 7);
    ctx.restore();
    text(label, x, y + 6, 14, color, 600, "center");
    return {x, y};
  }
  function node(x, y, width, title, detail, color = C.mint, height = 90) {
    const arrival = ease((motionTime - nodeIndex++ * .1) / .55);
    ctx.save(); ctx.globalAlpha = arrival; ctx.translate(0, 15 * (1 - arrival));
    box(x, y, width, height, C.panel, color);
    text(title, x + 17, y + 31, 20, color, 600);
    wrap(detail, x + 17, y + 57, width - 34, 15, C.muted, 21);
    ctx.fillStyle = color; ctx.fillRect(x + 17, y + height - 7, (width - 34) * arrival, 2);
    ctx.restore();
  }
  function person(x, y, name, role, time, color = C.mint) {
    ctx.save(); ctx.translate(0, Math.sin(time * 2) * 2);
    ctx.globalAlpha = .28; ctx.beginPath(); ctx.ellipse(x, y + 103, 44, 6, 0, 0, Math.PI * 2);
    ctx.fillStyle = C.blue; ctx.fill(); ctx.globalAlpha = 1;
    const skin = "#e9b698", hair = "#243440";
    dot(x, y - 32, 22, hair); dot(x, y - 27, 19, skin);
    ctx.beginPath(); ctx.arc(x - 3, y - 37, 19, Math.PI, Math.PI * 2);
    ctx.fillStyle = hair; ctx.fill();
    const blink = time % 4.2 > 4 ? .8 : 2;
    ctx.fillStyle = C.bg; ctx.fillRect(x - 8, y - 30, 3, blink);
    ctx.fillRect(x + 6, y - 30, 3, blink);
    ctx.beginPath(); ctx.arc(x, y - 25, 7, .25, Math.PI - .25); ctx.strokeStyle = hair;
    ctx.lineWidth = 1.5; ctx.stroke();
    box(x - 7, y - 9, 14, 14, skin, skin, 3);
    box(x - 28, y - 1, 56, 64, color, color, 13);
    line([[x - 13, y + 62], [x - 19, y + 95]], "#7195b4");
    line([[x + 13, y + 62], [x + 21, y + 95]], "#7195b4");
    box(x - 30, y + 91, 24, 10, hair, hair, 4);
    box(x + 10, y + 91, 24, 10, hair, hair, 4);
    const hand = Math.sin(time * 3.2) * 7;
    ctx.lineWidth = 5;
    line([[x + 23, y + 16], [x + 57, y + 35 + hand], [x + 83, y + 18 + hand]], color);
    line([[x - 23, y + 16], [x - 40, y + 45]], color);
    dot(x + 83, y + 18 + hand, 5, skin); dot(x - 40, y + 45, 5, skin);
    ctx.restore();
    text(name, x, y + 126, 19, C.white, 600, "center");
    text(role, x, y + 150, 13, C.muted, 400, "center");
  }
  function laptop(x, y, time, label = "PLACE ORDER") {
    box(x, y, 150, 86, "#102334", C.blue, 6);
    text(label, x + 75, y + 48, 14, C.blue, 600, "center");
    box(x - 10, y + 88, 170, 9, C.blue, C.blue, 3);
    dot(x + 132, y + 15, 3 + Math.sin(time * 3), C.mint);
    const click = (time % 2.8) / 2.8;
    ctx.save(); ctx.globalAlpha = (1 - click) * .5;
    ctx.beginPath(); ctx.arc(x + 75, y + 48, 11 + click * 28, 0, Math.PI * 2);
    ctx.strokeStyle = C.mint; ctx.lineWidth = 2; ctx.stroke(); ctx.restore();
  }
  function building(x, y, label, color = C.blue) {
    box(x, y, 125, 115, C.panel, color, 5);
    for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) box(x + 15 + c * 25, y + 15 + r * 25, 12, 12, color, color, 2);
    text(label, x + 62, y + 143, 17, C.white, 600, "center");
  }
  function tick(x, y, label, complete) {
    dot(x, y - 5, 12, complete ? C.mint : C.line);
    if (complete) text("✓", x, y + 1, 16, C.bg, 700, "center");
    text(label, x + 25, y + 1, 19, complete ? C.white : C.muted);
  }
  function banner(value, color = C.amber) {
    box(40, 464, 1200, 37, "#112536", C.line, 7);
    text(value, 60, 488, 16, color, 500);
  }
  function progress(f) { return clamp((f.progress - .08) / .82); }

  function atmosphere(f) {
    ctx.save();
    ctx.globalAlpha = .08;
    for (let i = 0; i < 38; i++) {
      const x = (i * 197 + f.time * (7 + i % 4)) % 1280;
      const y = 180 + (i * 83) % 260;
      dot(x, y, 1 + i % 3, i % 2 ? C.mint : C.blue);
      if (i % 3 === 0) line([[x, y], [x + 38, y - 12]], C.blue);
    }
    ctx.restore();
    if ((f.scene.id === "communication" || f.scene.id === "impact") && f.beat > 0) {
      ctx.save(); ctx.globalAlpha = .5;
      for (let i = 0; i < 25; i++) {
        const x = 70 + (i * 173) % 1140 + Math.sin(f.local * 2 + i) * 10;
        const y = 175 + (i * 47 + f.local * 30) % 265;
        ctx.save(); ctx.translate(x, y); ctx.rotate(f.local + i);
        ctx.fillStyle = [C.mint, C.blue, C.amber][i % 3]; ctx.fillRect(-2, -3, 4, 6);
        ctx.restore();
      }
      ctx.restore();
    }
  }

  function drawScene(f) {
    const p = progress(f), t = f.local, second = f.beat > 0;
    switch (f.scene.id) {
      case "opening":
        person(140, 257, "CUSTOMER", f.scene.customerLabel || "Demo Marmara Logistics", t);
        laptop(240, 278, t);
        building(1035, 227, "TürkBizTel");
        arrow([[410, 320], [1000, 320]], C.blue);
        ["DEDICATED INTERNET", "MANAGED SD-WAN", "CLOUD CONNECT"].forEach((label, i) => {
          box(458 + i * 172, 210, 157, 57, C.panel, C.line, 8);
          text(label, 536 + i * 172, 243, 12, C.white, 600, "center");
        });
        packet([[420, 320], [950, 320]], p, "ORDER A");
        box(500, 372, 213, 72, "#ffffff", "#ffffff", 8);
        ctx.drawImage(logo, 518, 382, 176, 60);
        wrap(f.scene.industryContextLabel || "Turkish industry context only. No affiliation or live integration.",
          741, 395, 400, 16, C.muted, 23);
        banner(f.scene.banner || "Follow A / timeout recovery     |     B / human-review branch     |     C / partner intake");
        break;
      case "intake":
        person(110, 251, "CUSTOMER", "Istanbul / 500 Mbps", t);
        laptop(207, 265, t, second ? "ACCEPTED" : "SUBMIT");
        node(492, 235, 280, "OPERATOR API", "TMF622-inspired productOrder", C.blue);
        node(938, 235, 280, "DURABLE STATE", "Order + event / one atomic save");
        arrow([[375, 285], [481, 285]], C.blue);
        arrow([[783, 285], [926, 285]]);
        packet(second ? [[787, 285], [918, 285]] : [[375, 285], [480, 285]], p,
          second ? "received" : "ORDER A");
        if (second) {
          arrow([[1075, 338], [1075, 405], [635, 405]]);
          packet([[1075, 350], [1075, 405], [635, 405]], p, "QUEUED");
          text("Supervisor releases one persisted event per step", 510, 443, 18, C.muted);
        }
        banner("Same idempotency key + same request = same order / never a second accepted order");
        break;
      case "validation":
        node(65, 231, 270, "RECEIVED EVENT", "Order A / durable queue", C.blue);
        node(490, 207, 300, "VALIDATION AGENT", second ? "Valid order / checks passed" : "Address + feasibility + inventory");
        arrow([[350, 275], [474, 275]]);
        packet([[350, 275], [474, 275]], p, "received");
        ["Istanbul / IST-001", "Catalogue product", "Available capacity"].forEach((label, i) =>
          tick(535, 330 + i * 36, label, second || p > (i + 1) / 4));
        node(945, 224, 270, second ? "NEXT: DECOMPOSE" : "INVALID SITE?", second ? "order.validated event" : "Pause / ask for correction", second ? C.mint : C.amber);
        arrow([[807, 269], [926, 269]], second ? C.mint : C.amber, !second);
        packet([[807, 269], [926, 269]], p, second ? "validated" : "CHECK", second ? C.mint : C.amber);
        banner(second ? "Validation DONE -> decomposition QUEUED; provisioning still WAITING" :
          "Invalid site -> awaitingCustomer -> confirmation -> same order resumes");
        break;
      case "decomposition":
        node(50, 263, 260, "PRODUCT / TMF622", "A / Dedicated Internet / 500 Mbps", C.blue);
        node(472, 175, 300, "SERVICE / TMF641", "Access circuit");
        node(472, 345, 300, "SERVICE / TMF641", "Internet service");
        node(954, 175, 270, "RESOURCE", "Fibre access port", C.amber);
        node(954, 345, 270, "RESOURCE", "IPv4 /29 block", C.amber);
        [[[320, 291], [453, 220]], [[320, 320], [453, 390]]].forEach((path, i) => {
          arrow(path); packet(path, clamp(p * 1.5 - i * .12), `A-S${i + 1}`);
        });
        [[[788, 220], [936, 220]], [[788, 390], [936, 390]]].forEach((path, i) => {
          arrow(path, C.amber); packet(path, second ? p : clamp((p - .5) * 2), `A-R${i + 1}`);
        });
        banner("Linked product -> service -> resource identifiers; simplified TMF-inspired subsets, not certification");
        break;
      case "provisioning":
        node(60, 212, 280, "PROVISIONING AGENT", "Policy before capacity allocation");
        node(493, 212, 280, "WHOLESALE PARTNER", second ? "Timeout / no commitment" : "Simulated reserve request", second ? C.red : C.blue);
        node(938, 212, 280, "NETWORK ADAPTER", "Synthetic network / no live changes", C.blue);
        arrow([[354, 255], [478, 255]], second ? C.red : C.mint);
        packet([[354, 255], [478, 255]], p, second ? "TIMEOUT" : "RESERVE", second ? C.red : C.amber);
        arrow([[788, 255], [922, 255]], C.line, true);
        ["Capacity", "Risk approval", "Change window"].forEach((label, i) => tick(112 + i * 330, 374, label, !second && p > i / 3));
        if (second) {
          arrow([[630, 317], [630, 419], [325, 419]], C.red);
          packet([[630, 317], [630, 419], [325, 419]], p, "FALLOUT", C.red);
        }
        banner(second ? "fallout.detected -> recovery QUEUED; no premature activation confirmation" :
          "Provisioning is the only active execution agent for this step");
        break;
      case "recovery":
        node(59, 234, 280, "RECOVERY AGENT", "Allowlist / retry once / no escalation", C.amber);
        node(496, 234, 280, "ALTERNATE ROUTE", "Fictional wholesale partner", C.blue);
        node(936, 234, 280, "PROVISIONING", second ? "Reserve once / complete linked work" : "Held until recovery", second ? C.mint : C.line);
        arrow([[353, 279], [480, 279]], C.amber);
        packet([[353, 279], [480, 279]], p, "RETRY 1/1", C.amber);
        arrow([[790, 279], [920, 279]], second ? C.mint : C.line);
        if (second) packet([[790, 279], [920, 279]], p, "recovered", C.mint);
        text(second ? "Recovery hands execution back to the guarded supervisor" : "On the happy path, this agent remains on standby", 640, 397, 23, C.white, 500, "center");
        banner("A / One retry is a bounded playbook, not unrestricted autonomous access");
        break;
      case "human":
        node(55, 235, 285, "ORDER B / ROUTE RISK", "Capacity not allocated", C.red);
        person(620, 250, "SERVICE MANAGER", second ? "Explicit audited approval" : "Review / approve or reject", t, C.amber);
        node(940, 235, 280, "PROVISIONING", second ? "Resume within the same policies" : "HELD / no autonomous bypass", second ? C.mint : C.red);
        arrow([[356, 280], [500, 280]], C.amber);
        packet([[356, 280], [500, 280]], p, "REVIEW", C.amber);
        arrow([[740, 280], [922, 280]], second ? C.mint : C.line, !second);
        if (second) packet([[740, 280], [922, 280]], p, "APPROVED", C.mint);
        box(64, 385, 340, 48, "#322332", C.red, 8);
        text("Partner approval -> HTTP 403", 84, 416, 19, C.red, 600);
        banner(f.scene.banner || "Closed window -> audited simulated release / missing capacity cannot be invented by approval");
        break;
      case "communication":
        node(58, 226, 290, "SERVICES ACTIVATED", "Provisioning complete");
        node(492, 226, 300, "CUSTOMER UPDATE", "Persist messages / complete order");
        person(1090, 243, "CUSTOMER", second ? "Service active / portal confirmation" : "Waiting for a truthful status", t);
        arrow([[363, 273], [477, 273]]);
        packet([[363, 273], [477, 273]], p, "activated", C.mint);
        arrow([[808, 273], [985, 273]], C.blue);
        packet([[808, 273], [985, 273]], p, second ? "TR / EN" : "UPDATE", C.blue);
        box(480, 352, 505, 86, C.panel, C.mint);
        text("EN  Your synthetic service is active.", 502, 385, 20);
        text("TR  Sentetik hizmetiniz etkinleştirildi.", 502, 416, 20, C.mint);
        banner("Portal simulation only / no external SMS or email / hash-linked audit, not WORM evidence");
        break;
      case "partners":
        person(112, 248, "WHOLESALE PARTNER", "Order C / synthetic organization", t, C.blue);
        laptop(206, 262, t, second ? "SUBMIT C" : "CHECK CAPACITY");
        node(507, 220, 290, "MCP / HTTP JSON-RPC", second ? "submit_partner_order" : "initialize -> tools/list", C.blue);
        node(943, 220, 275, "SAME ORDER FLOW", "Durable acceptance + supervisor");
        arrow([[380, 267], [493, 267]], C.blue);
        packet([[380, 267], [493, 267]], p, second ? "ORDER C" : "DISCOVER", C.blue);
        arrow([[811, 267], [927, 267]]);
        packet([[811, 267], [927, 267]], p, second ? "received" : "CAPACITY");
        node(492, 357, 530, "TOOLS / CHECK + SUBMIT", "check_partner_capacity / submit_partner_order", C.mint, 86);
        banner("MCP is a tool protocol, not a telecom order schema; partner tools cannot approve or provision");
        break;
      case "standards":
        [["TMF622", "Product order", 60], ["TMF641", "Service orders", 471], ["RESOURCES", "Simplified demo read interface", 882]].forEach(v =>
          node(v[2], 205, 338, v[0], v[1], v[0] === "RESOURCES" ? C.amber : C.mint));
        arrow([[410, 250], [456, 250]]); arrow([[821, 250], [867, 250]], C.amber);
        packet([[250, 330], [1030, 330]], p, "LINKED IDS", C.blue, 130);
        box(60, 370, 1160, 71, C.panel, second ? C.amber : C.blue);
        text(second ? "PRODUCTION GATES: full schemas + conformance; TMF652 / TMF638 / TMF639 as applicable" :
          "MCP: initialize / discover tools / authenticated capacity and order calls", 83, 412, 21, second ? C.amber : C.blue, 500);
        banner("Implemented: TMF-inspired subset + MCP wrapper / NOT a certified TM Forum implementation");
        break;
      case "residency":
        box(42, 171, 878, 281, "#102e37", C.mint, 15);
        text("TÜRKIYE / OPERATOR DATACENTER", 65, 202, 17, C.mint, 600);
        node(66, 222, 247, "AZURE LOCAL", "Approved hardware / VMs / containers", C.mint, 100);
        node(351, 222, 253, "LOCAL EXECUTION", "Portal / agents / OSS-BSS adapters", C.mint, 100);
        node(644, 222, 250, "LOCAL INFERENCE", "Approved model / no foreign API", C.mint, 100);
        arrow([[320, 270], [342, 270]]); arrow([[612, 270], [635, 270]]);
        ["Orders + events", "Logs + keys", "Backups + DR"].forEach((label, i) => {
          box(70 + i * 279, 355, 251, 57, "#152b3c", C.line, 7);
          text(label, 195 + i * 279, 390, 19, C.white, 500, "center");
        });
        packet([[125, 337], [817, 337]], p, second ? "STAYS LOCAL" : "ORDER DATA", C.mint, 134);
        node(962, 210, 272, "PUBLIC AZURE", "No Türkiye public region listed", C.blue, 105);
        line([[942, 180], [942, 445]], C.red, true);
        text("LOCAL DATA", 983, 371, 21, C.red, 600); text("DOES NOT CROSS", 983, 402, 19, C.red, 600);
        banner("PROPOSED Azure Local target; repository currently has local files / synthetic West Europe demo", C.amber);
        break;
      case "hybrid":
        box(42, 171, 550, 281, "#102e37", C.mint, 15);
        text("IN TÜRKIYE / CUSTOMER DATA PLANE", 64, 202, 17, C.mint, 600);
        node(65, 228, 504, "AZURE LOCAL", "Customer orders / events / local model / logs / backups", C.mint, 95);
        node(65, 346, 504, "EGRESS POLICY", "No customer payloads, prompts, raw logs or backups", C.red, 82);
        box(833, 171, 405, 281, "#17263c", C.blue, 15);
        text("OUTSIDE TÜRKIYE / CONDITIONAL", 851, 202, 16, C.blue, 600);
        ["Azure Container Registry / images", "Azure Arc / platform metadata", "Azure Monitor / aggregate metrics"].forEach((label, i) =>
          text(label, 852, 253 + i * 66, 17, C.white, 500));
        line([[713, 175], [713, 447]], C.amber, true);
        arrow([[823, 245], [603, 245]], C.blue);
        packet([[823, 245], [603, 245]], p, "IMAGES IN", C.blue, 124);
        arrow([[603, 310], [823, 310]], C.amber, true);
        packet([[603, 310], [823, 310]], p, "REVIEWED", C.amber, 124);
        arrow([[603, 377], [823, 377]], C.amber, true);
        packet([[603, 377], [823, 377]], p, "OPT-IN", C.amber, 100);
        if (second) {
          packet([[603, 421], [662, 421]], p, "PII", C.red, 70);
          dot(713, 421, 18, C.red); text("×", 713, 428, 26, C.bg, 700, "center");
          text("BLOCK", 750, 429, 16, C.red, 600);
        }
        banner("Conditional connected design: approve each external service + metadata flow; no default log export");
        break;
      case "strict":
        box(42, 171, 746, 281, "#102e37", C.mint, 15);
        text("TÜRKIYE / STRICT POLICY OPTION", 65, 202, 17, C.mint, 600);
        node(65, 226, 337, second ? "LOCAL CONTROL PLANE" : "CONNECTED ARC", second ?
          "Disconnected operations / approved deployment" : "Sends system metadata to Azure", second ? C.mint : C.amber, 100);
        node(433, 226, 330, "LOCAL WORKLOADS", "Customer data + inference remain local", C.mint, 100);
        text(second ? "No live external cloud services" : "Filtering app data does not filter agent metadata", 70, 386, 23, C.white, 500);
        node(951, 226, 280, "OUTSIDE AZURE", second ? "No connected management link" : "Receives platform metadata", C.blue, 100);
        arrow([[798, 274], [937, 274]], second ? C.red : C.amber, second);
        if (!second) packet([[798, 274], [937, 274]], p, "METADATA", C.amber, 125);
        else {
          dot(867, 274, 27, C.red); text("×", 867, 286, 41, C.bg, 700, "center");
          text("DISCONNECTED", 822, 365, 16, C.red, 600);
        }
        banner("Requires Microsoft eligibility + supported hardware + local control-plane capacity + operational approval");
        break;
      case "impact":
        [["ACTIVATION LEAD TIME", "35 days", "< 7 days", 59], ["MANUAL FALLOUT", "30%", "< 8%", 669]].forEach(v => {
          box(v[3], 187, 550, 177, C.panel, C.line);
          text(v[0], v[3] + 24, 222, 17, C.muted, 600);
          text(v[1], v[3] + 24, 289, 43, C.white, 600);
          arrow([[v[3] + 211, 274], [v[3] + 322, 274]], C.mint);
          packet([[v[3] + 228, 274], [v[3] + 306, 274]], p, "TARGET", C.mint, 90);
          text(v[2], v[3] + 342, 289, 43, C.mint, 600);
        });
        text(second ? "Measure elapsed days / fallout / recovery success / customer outcomes" :
          "Earlier checks -> durable handoffs -> bounded recovery -> visible status", 640, 407, 23, C.white, 500, "center");
        banner("Assignment ambitions, NOT achieved results / TRY 900m + TRY 150m are hypotheses, not realized savings");
        break;
    }
  }

  try {
    const response = await fetch(canvas.dataset.story || "/static/solution-data.json");
    if (!response.ok) throw new Error(`Story data HTTP ${response.status}`);
    const data = await response.json();
    $("voice-disclosure").textContent = data.narration?.disclosure || "Synthetic system narration.";
    logo = await new Promise((resolve, reject) => {
      const image = new Image(); image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("Unable to load Turkish telecom context logo"));
      image.src = `/static/${data.branding.asset}`;
    });
    if (window.amaFilmTheme?.ready) await window.amaFilmTheme.ready;
    let total = 0;
    data.scenes.forEach(scene => { scene.start = total; total += scene.duration || 20; });

    function frameAt(time) {
      time = Math.max(0, Math.min(total - .001, time));
      const index = data.scenes.findIndex(s => time < s.start + (s.duration || 20));
      const scene = data.scenes[index], local = time - scene.start;
      let beat = 0;
      scene.beats.forEach((b, i) => {
        if (local >= (b.start ?? i * (scene.duration || 20) / scene.beats.length)) beat = i;
      });
      const current = scene.beats[beat];
      const start = current.start ?? beat * (scene.duration || 20) / scene.beats.length;
      const duration = current.duration || (scene.duration || 20) / scene.beats.length;
      return {time, index, scene, local, beat, progress: clamp((local - start) / duration),
        agentStates: scene.agentStates[beat], event: scene.events[beat]};
    }
    function render(time) {
      const f = frameAt(time);
      motionTime = f.local; nodeIndex = 0;
      if (window.amaFilmTheme) {
        return window.amaFilmTheme.render({f, data, total, ctx, drawScene, C, text, wrap,
          box, line, arrow, dot, clock, ease});
      }
      ctx.fillStyle = C.bg; ctx.fillRect(0, 0, 1280, 720);
      const gradient = ctx.createRadialGradient(700, 300, 10, 700, 300, 700);
      gradient.addColorStop(0, "#20476666"); gradient.addColorStop(1, "#0b172500");
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, 1280, 720);
      atmosphere(f);
      text("TürkBizTel / THE JOURNEY OF AN ORDER", 40, 31, 13, C.mint, 600);
      text(f.scene.mode === "proposed" ? "PROPOSED PRODUCTION DESIGN / NOT DEPLOYED" :
        "ANIMATED EXPLANATION / SYNTHETIC SCENARIO", 800, 31, 12,
      f.scene.mode === "proposed" ? C.amber : C.muted, 600);
      text(`${String(f.index + 1).padStart(2, "0")} / ${f.scene.chapter.toUpperCase()}`, 40, 74, 13, C.amber, 600);
      text(f.scene.title, 40, 119, 34, C.white, 600);
      text((f.scene.order ? `ORDER ${f.scene.order}  /  ` : "") + f.event, 42, 149, 16, C.blue, 500);
      ctx.save();
      const arrival = ease(f.local / .45);
      ctx.globalAlpha = arrival; ctx.translate(16 * (1 - arrival), 0);
      drawScene(f);
      ctx.restore();
      data.agents.forEach((name, i) => {
        const status = f.agentStates[i];
        const color = ({active: C.mint, done: C.blue, held: C.red, queued: C.amber, local: C.mint})[status] || C.line;
        const x = 40 + i * 244;
        box(x, 516, 224, 66, status === "active" ? "#1b453f" : C.panel, color, 9);
        if (i < data.agents.length - 1) {
          arrow([[x + 228, 549], [x + 240, 549]], status === "done" ? C.blue : C.line);
        }
        if (status === "active") {
          ctx.beginPath(); ctx.arc(x + 22, 545, 17, f.local * 2, f.local * 2 + Math.PI * 1.5);
          ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
        }
        box(x + 10, 534, 24, 22, "#0b1725", color, 6);
        ctx.fillStyle = color; ctx.fillRect(x + 15, 542, 4, 3); ctx.fillRect(x + 25, 542, 4, 3);
        line([[x + 22, 534], [x + 22, 529]], color); dot(x + 22, 528, 2, color);
        text(`${i + 1}  ${name}`, x + 45, 543, 14, C.white, 600);
        text(status.toUpperCase(), x + 45, 566, 12, color === C.line ? C.muted : color, 600);
      });
      const b = f.scene.beats[f.beat];
      if (f.local >= (b.start || 0) && f.local < (b.start || 0) + (b.duration || 20)) {
        box(34, 595, 1212, 88, "#07121ee8", C.line, 9);
        wrap(b.text, 53, 622, 1172, 19, C.white, 26);
      }
      text("EVENT SEQUENCE + PACING ILLUSTRATIVE / NOT MEASURED PRODUCTION TIME", 40, 706, 10, C.muted, 500);
      if (data.narration?.kind === "author-voice-synthetic") {
        text("AUTHOR-SAMPLE VOICE / LOCALLY SYNTHESIZED", 740, 706, 10, C.muted, 500);
      }
      text(`${clock(f.time)} / ${clock(total)}`, 1116, 706, 12, C.mint, 500);
      ctx.fillStyle = C.mint; ctx.fillRect(0, 716, 1280 * f.time / total, 4);
      return {scene: f.scene.id, beat: f.beat, event: f.event, agentStates: f.agentStates, progress: f.progress};
    }
    window.amaStory = {duration: total, render, data, frameAt};
    render(position); $("scrub").max = total;
    $("scrub").addEventListener("input", e => { position = Number(e.target.value); render(position); });
    $("runtime").textContent = `${clock(total)} / Animated film / English narration / HD`;
    $("animation-play").addEventListener("click", () => {
      playing = !playing; previous = undefined;
      $("animation-play").textContent = playing ? "Pause animation" : "Play animation";
      if (playing && position >= total - .1) position = 0;
      if (playing) requestAnimationFrame(animate);
    });
    function animate(now) {
      if (!playing) return;
      if (previous !== undefined) position = Math.min(total, position + (now - previous) / 1000);
      previous = now; render(position); $("scrub").value = position;
      if (position >= total || document.hidden || !$("animation-panel").open) {
        playing = false; $("animation-play").textContent = "Play animation";
      } else requestAnimationFrame(animate);
    }
    data.scenes.forEach(scene => {
      const button = document.createElement("button");
      button.textContent = `${clock(scene.start)}  ${scene.chapter}`;
      button.addEventListener("click", () => { $("film").currentTime = scene.start; $("film").focus(); });
      $("chapters").append(button);
      const heading = document.createElement("h2"); heading.textContent = `${clock(scene.start)} / ${scene.chapter}`;
      $("transcript").append(heading);
      scene.beats.forEach(beat => { const p = document.createElement("p"); p.textContent = beat.text; $("transcript").append(p); });
    });
    data.references.forEach(ref => {
      const p = document.createElement("p"), a = document.createElement("a");
      a.href = ref.url; a.textContent = ref.name; a.target = "_blank"; a.rel = "noreferrer";
      p.append(a); $("references").append(p);
    });
    $("film").addEventListener("timeupdate", () => {
      const time = $("film").currentTime;
      [...$("chapters").children].forEach((button, i) => button.setAttribute("aria-current",
        String(time >= data.scenes[i].start && time < data.scenes[i].start + data.scenes[i].duration)));
    });
    $("load-status").textContent = data.loadStatusLabel || "Play the animated film or select a chapter. The agent activation rail follows the current order; proposed Azure Local architecture is labelled separately.";
  } catch (error) {
    $("load-status").textContent = `Unable to load the animation: ${error.message}. Video downloads remain available.`;
    console.error(error);
  }
})();
