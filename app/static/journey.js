"use strict";

(() => {
  const $ = id => document.getElementById(id);
  const canvas = $("story"), ctx = canvas.getContext("2d");
  const C = {bg: "#080e20", panel: "#121e37", line: "#2c3d5d", text: "#f4f7ff",
    muted: "#a9b8d4", blue: "#69c8ff", amber: "#ffd27a", red: "#ff7d8f"};
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const clamp = n => Math.max(0, Math.min(1, n));
  const ease = n => { n = clamp(n); return n * n * (3 - 2 * n); };
  const mix = (a, b, t) => a + (b - a) * t;
  const clock = n => `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
  let data, total = 0, current = 0, playing = false, previousTime, raf;
  const logos = [];

  function box(x, y, w, h, fill = C.panel, radius = 12, stroke) {
    ctx.beginPath(); ctx.roundRect(x, y, w, h, radius);
    ctx.fillStyle = fill; ctx.fill();
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 1.5; ctx.stroke(); }
  }
  function text(value, x, y, size = 16, color = C.text, weight = 400, align = "left") {
    ctx.font = `${weight} ${size}px "Segoe UI", Arial, sans-serif`;
    ctx.textAlign = align; ctx.fillStyle = color; ctx.fillText(value, x, y);
  }
  function lines(value, x, y, width, size = 21, color = C.text, height = 29) {
    ctx.font = `400 ${size}px "Segoe UI", Arial, sans-serif`;
    let row = 0, line = "";
    for (const word of value.split(" ")) {
      const next = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(next).width > width) {
        text(line, x, y + row++ * height, size, color); line = word;
      } else line = next;
    }
    if (line) text(line, x, y + row++ * height, size, color);
    return row;
  }
  function line(x1, y1, x2, y2, color = C.line, dashed = false, width = 2) {
    ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = width;
    ctx.setLineDash(dashed ? [7, 7] : []);
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]);
  }
  function circle(x, y, r, color) {
    ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
  }
  function glow(x, y, r = 6, color = C.blue) {
    circle(x, y, r * 3, `${color}15`); circle(x, y, r * 1.8, `${color}33`); circle(x, y, r, color);
  }
  function badge(label, x, y, color = C.amber, w = 145) {
    box(x, y, w, 30, `${color}14`, 15, `${color}77`);
    text(label, x + w / 2, y + 20, 11, color, 650, "center");
  }
  function packet(x, y, color = C.amber, scale = 1) {
    ctx.save(); ctx.translate(x, y); ctx.scale(scale, scale);
    ctx.shadowColor = color; ctx.shadowBlur = 18;
    box(-18, -23, 36, 46, C.panel, 6, color); ctx.shadowBlur = 0;
    for (let i = 0; i < 3; i++) line(-9, -10 + i * 9, 8, -10 + i * 9, color);
    ctx.restore();
  }
  function person(x, y, color = C.blue, typing = 0) {
    circle(x, y - 51, 15, "#e4ae92");
    box(x - 25, y - 31, 50, 55, color, 16);
    line(x - 12, y + 18, x - 16, y + 51, "#7085a8", false, 9);
    line(x + 12, y + 18, x + 17, y + 51, "#7085a8", false, 9);
    line(x + 17, y - 14, x + 45, y - 8 + Math.sin(typing * 12) * 3, "#e4ae92", false, 7);
    box(x + 22, y - 2, 60, 6, "#516886", 2);
    box(x + 40, y - 39, 43, 36, "#1c304f", 4, C.blue);
  }
  function skyline(t) {
    const base = 555;
    for (let i = 0; i < 27; i++) {
      const h = 30 + ((i * 43) % 70);
      box(i * 51 - 10, base - h, 37, h, "#162540", 2);
      for (let j = 0; j < 3; j++) circle(i * 51 + 1, base - h + 13 + j * 15, 2, "#39587b");
    }
    line(675, base - 34, 1190, base - 34, "#416484", false, 4);
    [795, 1080].forEach(x => {
      line(x, 316, x, base, "#7896b3", false, 6);
      for (let j = -4; j <= 4; j++) line(x, 325, x + j * 33, base - 34, "#416484");
    });
    for (let i = 0; i < 6; i++) glow(700 + ((t * 65 + i * 85) % 470), 527, 3);
    for (let i = 0; i < 5; i++) line(650 + i * 130, 570 + Math.sin(t + i) * 5,
      720 + i * 130, 570 + Math.sin(t + i) * 5, "#29486c");
  }
  function header(scene, index, local) {
    const a = reducedMotion.matches ? 1 : .35 + .65 * ease(local / .65);
    ctx.save(); ctx.globalAlpha = a;
    text(`${String(index + 1).padStart(2, "0")}  /  ${scene.chapter}`, 64, 109, 12, C.blue, 650);
    scene.title.split("\n").forEach((part, i) => text(part, 64, 169 + i * 57, 51, C.text, 700));
    ctx.restore();
  }
  function network(t, loop = false) {
    const xs = [156, 398, 640, 882, 1124], y = 375;
    const labels = ["CUSTOMER RECORDS", "ORDER DESK", "AVAILABLE CAPACITY", "FIELD TEAMS", "PARTNERS"];
    const active = Math.floor(t * .62) % 5;
    for (let i = 0; i < 5; i++) {
      if (i < 4) {
        line(xs[i] + 88, y, xs[i + 1] - 88, y, "#496082", true);
        glow(mix(xs[i] + 93, xs[i + 1] - 93, (t * .6 + i * .2) % 1), y, 4, C.amber);
      }
      box(xs[i] - 87, y - 57, 174, 114, i === active ? "#203650" : C.panel, 15,
        i === active ? C.blue : C.line);
      text(`0${i + 1}`, xs[i] - 66, y - 29, 13, C.blue, 700);
      text(labels[i], xs[i], y + 2, 12, C.text, 650, "center");
      text("SEPARATE RECORDS", xs[i], y + 29, 10, C.muted, 400, "center");
      packet(xs[i], y - 83 - Math.sin(t * 2 + i) * 5, i === active ? C.amber : "#4d6688", .65);
      if (!loop && i < 4) text("PASS IT ON", xs[i] + 121, y + 91, 10, C.amber, 650, "center");
    }
    if (loop) {
      line(1124, 436, 1124, 491, C.red, true);
      line(1124, 491, 398, 491, C.red, true);
      line(398, 491, 398, 436, C.red, true);
      packet(mix(1100, 410, (t * .22) % 1), 491, C.red, .7);
      text("CHECK", 478, 535, 14, C.red, 650);
      text("CORRECT", 710, 535, 14, C.red, 650);
      text("TYPE AGAIN", 945, 535, 14, C.red, 650);
    } else {
      person(141, 497, "#476cbd", t); person(625, 497, "#487e93", t);
      badge("TYPE THE SAME DETAILS AGAIN", 899, 473, C.amber, 272);
    }
  }
  function sceneGraphics(scene, local) {
    const p = clamp(local / scene.duration), t = reducedMotion.matches ? 4 : local;
    switch (scene.id) {
      case "promise": {
        skyline(t);
        text("35", 982, 212, 150, C.amber, 750, "center");
        text("DAYS TO START SERVICE", 982, 247, 17, C.amber, 650, "center");
        text("Case-study figure", 982, 275, 12, C.muted, 400, "center");
        text("A business is ready. Its connection is not.", 65, 280, 20, C.muted);
        const xs = [64, 300, 536];
        data.operators.forEach((op, i) => {
          const x = xs[i], y = 334 + Math.sin(t * .7 + i) * 3;
          box(x, y, 220, 102, "#f5f8ff", 14);
          const image = logos[i];
          if (i === 1) {
            ctx.drawImage(image, x + 35, y + 15, 150, 51);
            text("Türk Telekom", x + 110, y + 86, 13, "#153452", 650, "center");
          } else {
            ctx.drawImage(image, x + 19, y + 23, 48, 48);
            text(i === 0 ? "Turkcell" : "Vodafone", x + 80, y + 51, 20,
              i === 0 ? "#12367b" : "#c60000", 700);
            if (i === 2) text("TÜRKİYE", x + 81, y + 73, 11, "#5b6677", 600);
          }
          text("EXISTING PROVIDER", x + 110, y + 124, 9, C.muted, 500, "center");
        });
        badge("FICTIONAL OPERATOR CASE", 64, 493, C.blue, 329);
        text("Logos identify the market. The challenges are not attributed to these brands.", 65, 549, 12, C.muted);
        break;
      }
      case "handoffs":
        network(t);
        break;
      case "rekey": {
        const wrong = p > .38;
        [100, 565].forEach((x, i) => {
          box(x, 299, 318, 169, C.panel, 16, i === 1 && wrong ? C.red : C.line);
          text(i ? "NEXT TEAM'S COPY" : "ORIGINAL CUSTOMER RECORD", x + 24, 330, 12, C.muted, 650);
          text(i && wrong ? "ORDER 1402" : "ORDER 1042", x + 24, 386, 36,
            i && wrong ? C.red : C.blue, 700);
          for (let j = 0; j < 3; j++) box(x + 24, 409 + j * 13, 245 - j * 42, 4, "#3b5273", 2);
        });
        line(434, 374, 547, 374, C.amber, true);
        packet(mix(444, 535, (t * .42) % 1), 374, C.amber, .7);
        person(191, 535, "#5077bc", t); person(649, 535, "#6886b9", t);
        if (wrong) {
          badge("NUMBERS DO NOT MATCH", 946, 335, C.red, 240);
          text("!", 1065, 463, 100, C.red, 700, "center");
          text("SEND BACK TO CHECK", 1065, 515, 15, C.red, 650, "center");
          line(878, 483, 418, 483, C.red, true);
          glow(mix(860, 430, (t * .3) % 1), 483, 5, C.red);
        }
        break;
      }
      case "partner": {
        const points = [[280, 412], [596, 350], [986, 379]];
        line(280, 412, 596, 350, C.amber, true);
        line(596, 350, 986, 379, C.amber, true);
        ["BALKANS", "TÜRKİYE", "CAUCASUS"].forEach((label, i) => {
          const [x, y] = points[i];
          for (let j = 0; j < 3; j++) {
            ctx.beginPath(); ctx.arc(x, y, 40 + ((t * 16 + j * 22) % 70), 0, Math.PI * 2);
            ctx.strokeStyle = "#69c8ff22"; ctx.lineWidth = 1.5; ctx.stroke();
          }
          glow(x, y, 11);
          text(label, x, y + 104, 17, C.text, 650, "center");
        });
        line(779, 280, 779, 534, C.red, true);
        badge("NO DIRECT ORDER SHARING", 659, 391, C.red, 240);
        packet(mix(618, 736, (t * .35) % 1), 351, C.amber);
        text("STAFF CHASE UPDATES", 429, 549, 13, C.amber, 650);
        text("Simplified locations", 1052, 548, 11, C.muted);
        break;
      }
      case "fallout": {
        text("30%", 1023, 190, 110, C.red, 750, "center");
        text("NEED MORE WORK", 1023, 223, 13, C.red, 650, "center");
        network(t, true);
        break;
      }
      case "waiting": {
        const day = Math.max(1, Math.ceil(ease(p * 1.25) * 35));
        for (let i = 0; i < 35; i++) {
          const x = 70 + (i % 7) * 75, y = 294 + Math.floor(i / 7) * 49;
          box(x, y, 63, 37, i < day ? "#4a3b30" : C.panel, 6, i < day ? "#d29f59" : C.line);
          text(String(i + 1).padStart(2, "0"), x + 31, y + 26, 17, i < day ? C.amber : C.muted, 600, "center");
        }
        text(String(day).padStart(2, "0"), 941, 458, 178, C.amber, 750, "center");
        text("DAYS UNTIL SERVICE", 941, 507, 21, C.text, 650, "center");
        ctx.save(); ctx.translate(1160, 326); ctx.rotate(t * 1.7);
        line(0, 0, 0, -23, C.amber, false, 3); line(0, 0, 16, 0, C.blue, false, 3); ctx.restore();
        text("ONE BUSINESS. FIVE WEEKS.", 70, 574, 16, C.amber, 650);
        break;
      }
      case "visibility": {
        person(142, 452, "#5174bd", t);
        const messages = ["Where is our order?", "Our site needs to open.", "When can we start?"];
        messages.forEach((message, i) => {
          const alpha = .3 + .7 * ease((p - i * .16) * 5);
          ctx.save(); ctx.globalAlpha = alpha;
          const y = 293 + i * 87, x = 291 + i * 30;
          box(x, y, 450, 62, C.panel, 14, i === 2 ? C.amber : C.line);
          text(message, x + 23, y + 40, 23, i === 2 ? C.amber : C.text, 500);
          ctx.restore();
        });
        circle(1041, 388, 78, "#ff7d8f12");
        text("?", 1041, 434 + Math.sin(t * 2) * 5, 127, C.red, 350, "center");
        text("TRUST AT RISK", 1041, 522, 18, C.red, 700, "center");
        for (let i = 0; i < 3; i++) glow(818 + i * 33, 402, 3, (Math.floor(t * 2) % 3) === i ? C.amber : "#344666");
        break;
      }
      case "pressure": {
        text("MORE BUSINESS ORDERS ARRIVING", 71, 286, 12, C.blue, 650);
        for (let i = 0; i < 12; i++) {
          const x = 98 + (i % 6) * 72, y = 331 + Math.floor(i / 6) * 86;
          packet(x + Math.sin(t * 2 + i) * 5, y, C.blue, .7);
        }
        line(526, 376, 683, 376, C.amber, true);
        packet(mix(542, 660, (t * .22) % 1), 376, C.amber, .9);
        box(700, 300, 184, 155, "#302c37", 14, C.amber);
        person(751, 380, "#997956", t);
        text("PROCESSED", 792, 486, 17, C.amber, 700, "center");
        text("BY HAND", 792, 512, 17, C.amber, 700, "center");
        for (let i = 0; i < 6; i++) {
          const h = (35 + i * 24) * (.5 + .5 * ease(p * 2));
          box(953 + i * 39, 453 - h, 24, h, i > 3 ? C.red : "#7685bd", 4);
        }
        text("GROWING PRESSURE", 1069, 486, 15, C.red, 700, "center");
        badge("PARTNER CHOICE", 71, 512, C.red, 222);
        text("Partners expect easier ways to exchange orders.", 316, 533, 18, C.muted);
        break;
      }
      case "summary":
        [["35", "DAYS TO START SERVICE", C.amber], ["30%", "NEED MORE WORK", C.red],
          ["5", "SEPARATE STEPS", C.blue]].forEach(([value, label, color], i) => {
          const x = 64 + i * 397;
          box(x, 292, 368, 168, C.panel, 16, `${color}66`);
          text(value, x + 25, 394, 91, color, 750);
          text(label, x + 28, 434, 13, C.muted, 650);
          glow(x + 323, 329, 10 + Math.sin(t * 2 + i) * 3, color);
        });
        text("CUSTOMER TRUST", 65, 514, 20, C.text, 650);
        text("REVENUE TIMING", 461, 514, 20, C.text, 650);
        text("COMPETITIVENESS", 857, 514, 20, C.text, 650);
        text("The cost of waiting is already a business risk.", 65, 565, 24, C.red, 650);
        break;
      default: throw new Error(`Unknown story scene: ${scene.id}`);
    }
  }
  function sceneAt(time) {
    return data.scenes.findIndex((scene, i) => time < scene.start + scene.duration || i === data.scenes.length - 1);
  }
  function render(time) {
    if (!data) return;
    time = Math.max(0, Math.min(time, total - .001));
    const index = sceneAt(time), scene = data.scenes[index], local = time - scene.start;
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, 1280, 720);
    const gradient = ctx.createRadialGradient(1130, 130, 0, 1130, 130, 950);
    gradient.addColorStop(0, "#1e376555"); gradient.addColorStop(1, "#080e2000");
    ctx.fillStyle = gradient; ctx.fillRect(0, 0, 1280, 720);
    for (let i = 0; i < 35; i++) {
      const t = reducedMotion.matches ? 1 : time;
      circle((i * 137 + t * (3 + i % 4)) % 1280, 90 + (i * 71) % 500, 1, "#8bbfff33");
    }
    text("TürkBizTel / THE WAITING ORDER", 64, 45, 13, C.text, 700);
    if (data.narration.kind === "author-voice-synthetic")
      text("SYNTHETIC AUTHOR-VOICE NARRATION", 740, 45, 9, C.muted, 500, "center");
    else if (data.narration.kind === "local-preset-neural")
      text("COMPUTER-GENERATED VOICE", 740, 45, 9, C.muted, 500, "center");
    text("CURRENT STATE ONLY", 1216, 45, 11, C.blue, 650, "right");
    line(64, 65, 1216, 65);
    header(scene, index, local); sceneGraphics(scene, local);
    text(scene.label, 65, 600, 10, C.muted, 500);
    const beat = scene.beats.find(b => local >= b.start && local < b.start + b.duration);
    if (beat) {
      box(42, 616, 1196, 73, "#040814", 9);
      if (lines(beat.text, 66, 646, 1145, 21, C.text, 28) > 2)
        throw new Error(`Subtitle exceeds safe area: ${scene.id}`);
    }
    for (let i = 0; i < data.scenes.length; i++) {
      const x = 64 + i * (1152 / data.scenes.length), w = 1152 / data.scenes.length - 5;
      box(x, 704, w, 3, "#28354e", 1);
      if (i <= index) box(x, 704, w * (i < index ? 1 : local / scene.duration), 3, C.blue, 1);
    }
    canvas.setAttribute("aria-label", `${scene.chapter}. ${scene.title.replace("\n", " ")}`);
    $("seek").value = time; $("time").textContent = `${clock(time)} / ${clock(total)}`;
  }
  function stop() {
    playing = false; cancelAnimationFrame(raf); previousTime = undefined;
    $("play").textContent = "Play animation";
  }
  function tick(timestamp) {
    if (!playing) return;
    if (previousTime !== undefined) current += (timestamp - previousTime) / 1000;
    previousTime = timestamp;
    if (current >= total) { current = total; stop(); }
    render(current); if (playing) raf = requestAnimationFrame(tick);
  }
  $("play").addEventListener("click", () => {
    if (playing) return stop();
    $("film").pause(); if (current >= total) current = 0;
    playing = true; $("play").textContent = "Pause animation"; raf = requestAnimationFrame(tick);
  });
  $("seek").addEventListener("input", event => { stop(); current = Number(event.target.value); render(current); });
  function step(direction) {
    stop();
    const i = Math.max(0, Math.min(data.scenes.length - 1, sceneAt(current) + direction));
    current = data.scenes[i].start + 1; render(current);
  }
  $("previous").addEventListener("click", () => step(-1));
  $("next").addEventListener("click", () => step(1));
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); });
  reducedMotion.addEventListener("change", () => { if (reducedMotion.matches) stop(); });
  document.querySelector(".storyboard").addEventListener("toggle", event => { if (!event.target.open) stop(); });
  $("film").addEventListener("play", stop);
  $("film").addEventListener("error", () => { $("film-error").hidden = false; });
  $("film").querySelector("source").addEventListener("error", () => { $("film-error").hidden = false; });
  $("film").addEventListener("timeupdate", () => {
    if (!data) return;
    const index = sceneAt($("film").currentTime);
    document.querySelectorAll(".chapter").forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
  });
  async function load() {
    try {
      const response = await fetch("/static/journey-data.json");
      if (!response.ok) throw new Error(`Storyboard returned HTTP ${response.status}`);
      data = await response.json();
      await Promise.all(data.operators.map(async (op, i) => {
        const image = new Image(); image.src = `/static/${op.asset}`;
        await image.decode(); logos[i] = image;
      }));
      await document.fonts.ready;
      total = 0;
      data.scenes.forEach((scene, index) => {
        scene.start = total; total += scene.duration;
        const button = document.createElement("button");
        button.className = "chapter";
        button.textContent = `${String(index + 1).padStart(2, "0")} ${scene.chapter.toLowerCase()}`;
        button.setAttribute("aria-pressed", String(index === 0));
        button.addEventListener("click", () => {
          stop(); $("film").currentTime = scene.start; current = scene.start + 1; render(current);
        });
        $("chapters").append(button);
        const article = document.createElement("article"), label = document.createElement("small");
        label.textContent = `${clock(scene.start)} / ${scene.chapter}`;
        const heading = document.createElement("h2"); heading.textContent = scene.title.replace("\n", " ");
        article.append(label, heading);
        scene.beats.forEach(beat => {
          const paragraph = document.createElement("p"); paragraph.textContent = beat.text; article.append(paragraph);
        });
        $("transcript").append(article);
      });
      $("narration-note").textContent = data.narration.disclosure;
      $("seek").max = total;
      ["play", "previous", "next", "seek"].forEach(id => { $(id).disabled = false; });
      render(1);
      window.amaStory = {render, duration: total, data};
    } catch (error) {
      $("story-error").hidden = false;
      $("story-error").textContent = `Unable to load the storyboard: ${error.message}`;
    }
  }
  load();
})();
