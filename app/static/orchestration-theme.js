"use strict";

window.amaFilmTheme = {
  render({f, data, total, ctx, drawScene, C, text, wrap, box, line, arrow, dot, clock, ease}) {
    const colors = ["#80d5ff", "#c5a1ff", "#6de3cf", "#ffba76", "#ffa6d5"];
    const active = f.agentStates.indexOf("active");
    const next = f.agentStates.indexOf("queued");
    const held = f.agentStates.indexOf("held");
    const target = active >= 0 ? active : held >= 0 ? held : next;
    const proposed = f.scene.mode === "proposed";
    const t = f.time;
    const quips = {
      opening: "ONE CLICK. FIVE SPECIALISTS. LET'S CONNECT.",
      intake: "SAVE ONCE. ROUTE SAFELY. NO DOUBLE ORDERS.",
      validation: "CHECK THE SITE. SAVE THE MISSION.",
      decomposition: "ONE PRODUCT. A CREW OF LINKED TASKS.",
      provisioning: "POLICY FIRST. CABLE SECOND.",
      recovery: "ONE RETRY. NOT INFINITY.",
      human: "A HUMAN DECISION. NO ROBOT SHORTCUTS.",
      communication: "MERHABA! THE CUSTOMER GETS THE GOOD NEWS.",
      partners: "A NEW ENTRANCE. THE SAME RULES.",
      standards: "SHARED CONTRACTS. NO CERTIFICATION SHORTCUT.",
      residency: "BRING THE COMPUTE HOME. KEEP CUSTOMER DATA LOCAL.",
      hybrid: "IMAGES IN. ONLY APPROVED METADATA OUT.",
      strict: "NO METADATA EXPORT? EVALUATE DISCONNECTED OPERATIONS.",
      impact: "LESS CHASING. MORE CONNECTING. NOW PROVE THE TARGETS."
    };

    function ellipse(x, y, rx, ry, color) {
      ctx.beginPath(); ctx.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
    }
    function polygon(points, color, stroke = color) {
      ctx.beginPath(); ctx.moveTo(...points[0]); points.slice(1).forEach(p => ctx.lineTo(...p));
      ctx.closePath(); ctx.fillStyle = color; ctx.fill();
      ctx.strokeStyle = stroke; ctx.lineWidth = 2; ctx.stroke();
    }
    function ring(x, y, radius, color, start = 0, length = Math.PI * 2) {
      ctx.beginPath(); ctx.arc(x, y, radius, start, start + length);
      ctx.strokeStyle = color; ctx.lineWidth = 4; ctx.stroke();
    }
    function eyes(y, color, wide = false) {
      const blink = t % 5 > 4.8 ? 2 : 8;
      box(-24, y - 10, 48, 29, "#0b1725", color, 11);
      ellipse(-10, y + 3, 4, blink / 2, color);
      ellipse(10, y + 3, wide ? 6 : 4, blink / 2, color);
      ctx.beginPath(); ctx.arc(0, y + 5, 11, .4, Math.PI - .4);
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
    }
    function avatar(index, x, status) {
      const color = colors[index], live = status === "active";
      const bob = Math.sin(t * (live ? 6 : 2) + index) * (live ? 5 : 1.8);
      const hand = Math.sin(t * 4 + index) * (live ? 11 : 3);
      ctx.save(); ctx.translate(x, 833 + bob);
      ellipse(0, 56 - bob, 68, 9, "#050f1b");
      if (live) {
        ctx.save(); ctx.globalAlpha = .25;
        ellipse(0, -22, 104, 87, color); ctx.restore();
        ring(0, -28, 83, color, t * 1.8, Math.PI * 1.3);
      }
      switch (index) {
        case 0:
          polygon([[-42, -62], [-30, -101], [-9, -73]], color);
          polygon([[9, -73], [30, -101], [42, -62]], color);
          box(-41, -80, 82, 61, color, color, 24);
          eyes(-57, "#e5f7ff");
          box(-32, -15, 64, 59, "#27546c", color, 14);
          line([[-17, 44], [-23, 58]], color); line([[17, 44], [23, 58]], color);
          box(-56, -1, 31, 39, "#eef4e5", color, 4);
          for (let r = 0; r < 3; r++) {
            text("✓", -52, 11 + r * 10, 11, "#19625c", 700);
            line([[-40, 7 + r * 10], [-31, 7 + r * 10]], "#19625c");
          }
          line([[31, 2], [57, -1 + hand]], color);
          ring(65, -17 + hand, 20, color);
          line([[79, -2 + hand], [94, 15 + hand]], color);
          ctx.save(); ctx.globalAlpha = .22; dot(65, -17 + hand, 17, C.blue); ctx.restore();
          break;
        case 1:
          box(-33, -78, 66, 58, "#443462", color, 8);
          eyes(-57, color);
          ctx.beginPath(); ctx.arc(0, -80, 42, Math.PI, 0);
          ctx.fillStyle = "#efca76"; ctx.fill();
          box(-48, -83, 96, 9, "#efca76", "#efca76", 3);
          polygon([[-31, -13], [31, -13], [43, 40], [-43, 40]], "#443462", color);
          box(-25, -5, 50, 37, "#1c2d4e", color, 3);
          line([[-17, 11], [17, 11]], color); line([[0, -1], [0, 25]], color);
          for (let b = 0; b < 3; b++) {
            const a = t * (live ? 1.9 : .4) + b * Math.PI * 2 / 3;
            const bx = Math.cos(a) * 65, by = -28 + Math.sin(a) * 23;
            box(bx - 10, by - 10, 20, 20, "#443462", color, 4);
          }
          line([[-21, 41], [-27, 58]], color); line([[21, 41], [27, 58]], color);
          break;
        case 2:
          box(-30, -94, 60, 80, "#245b5b", color, 20);
          box(-25, -66, 50, 20, "#0b1725", color, 8);
          dot(Math.sin(t * 2) * 12, -56, 6, color);
          line([[0, -94], [0, -112]], color); dot(0, -115, 6, color);
          box(-26, -5, 52, 49, "#245b5b", color, 8);
          box(-15, 4, 30, 20, "#0b1725", color, 4);
          for (let n = 0; n < 3; n++) dot(-8 + n * 8, 13, 2, color);
          line([[-27, 4], [-54, -13 + hand], [-67, -38 + hand]], color);
          line([[27, 4], [53, -3 - hand], [70, -35 - hand]], color);
          ring(-68, -46 + hand, 12, color, .3, Math.PI * 1.55);
          box(61, -53 - hand, 22, 19, color, color, 3);
          for (let n = 0; n < 3; n++) line([[65 + n * 7, -53 - hand], [65 + n * 7, -61 - hand]], color);
          line([[-15, 44], [-23, 59]], color); line([[15, 44], [23, 59]], color);
          break;
        case 3:
          dot(0, -56, 37, "#694537"); eyes(-61, color, true);
          box(-46, -94, 92, 15, color, color, 7);
          box(-13, -105, 26, 22, "#f7e3ad", "#f7e3ad", 4);
          dot(0, 4, 45, "#694537");
          ring(0, 4, 37, color);
          ctx.save(); ctx.translate(0, 4); ctx.rotate(live ? t * 2 : -.3);
          for (let n = 0; n < 4; n++) {
            ctx.rotate(Math.PI / 2); box(-8, -43, 16, 16, "#fff0ce", "#fff0ce", 3);
          }
          ctx.restore();
          text(live || status === "done" ? "1/1" : "RETRY", 0, 11, live ? 21 : 13, color, 700, "center");
          line([[-32, 32], [-40, 57]], color); line([[32, 32], [40, 57]], color);
          line([[40, -7], [65, -17 + hand]], color);
          box(53, -17 + hand, 35, 29, "#694537", color, 5);
          break;
        case 4:
          box(-43, -83, 86, 65, "#65415f", color, 28);
          eyes(-57, color);
          ring(0, -53, 49, color, Math.PI, Math.PI);
          box(-55, -65, 15, 31, color, color, 6);
          box(40, -65, 15, 31, color, color, 6);
          line([[49, -32], [33, -18], [15, -18]], color); dot(12, -18, 4, color);
          polygon([[-28, -10], [28, -10], [37, 45], [-37, 45]], "#65415f", color);
          box(-20, 0, 40, 25, "#0b1725", color, 7);
          text("TR/EN", 0, 18, 11, color, 700, "center");
          line([[-26, 44], [-31, 58]], color); line([[26, 44], [31, 58]], color);
          ctx.save(); ctx.translate(70, -25 + hand); ctx.rotate(-.25);
          polygon([[-20, 0], [20, -12], [0, 17], [-3, 3]], color);
          ctx.restore();
          if (live || status === "done") {
            box(-95, -110, 63, 31, "#65415f", color, 9);
            text(Math.floor(t) % 2 ? "Merhaba!" : "Hello!", -64, -89, 12, color, 600, "center");
          }
          break;
      }
      ctx.restore();
    }
    function tower() {
      const color = proposed ? C.amber : C.mint;
      const x = 1645;
      ellipse(x, 887, 213, 23, "#050f1b");
      polygon([[1527, 421], [1763, 421], [1810, 872], [1480, 872]], "#162e46", "#49728e");
      for (let floor = 0; floor < 6; floor++) {
        const y = 475 + floor * 57;
        box(1538 - floor * 5, y, 215 + floor * 10, 37, "#0d2034", "#345773", 5);
        for (let column = 0; column < 5; column++) {
          ctx.save(); ctx.globalAlpha = .3 + .3 * Math.sin(t * 1.4 + floor + column) ** 2;
          box(1550 - floor * 5 + column * (38 + floor * 2), y + 9, 22, 18, color, color, 2);
          ctx.restore();
        }
      }
      box(1449, 278, 393, 159, "#244660", color, 26);
      polygon([[1430, 281], [1501, 233], [1793, 233], [1863, 281]], "#315d79", color);
      box(1470, 305, 351, 78, "#081c2e", color, 18);
      for (let eye = 0; eye < 2; eye++) {
        const ex = 1568 + eye * 150;
        ellipse(ex + Math.sin(t * .6) * 5, 339, 25, t % 6 > 5.8 ? 2 : 13, color);
      }
      text("ORCHESTRATION AGENT", x, 414, 23, C.white, 700, "center");
      line([[x, 233], [x, 185]], color); dot(x, 180, 8, color);
      ring(x, 180, 26, color, t, Math.PI * .8);
      ring(x, 180, 43, color, t + Math.PI, Math.PI * .6);
      box(1472, 898, 349, 43, "#10283b", color, 9);
      text(proposed ? (data.localSupervisorLabel || "PROPOSED LOCAL SUPERVISOR") : "GUARDED SUPERVISOR", x, 926, 18, color, 700, "center");
      const status = proposed ? (data.architectureStatusLabel || "TARGET ARCHITECTURE") : active >= 0 ? `DISPATCH: ${data.agents[active].toUpperCase()}` :
        held >= 0 ? "HOLD: POLICY / HUMAN REVIEW" : next >= 0 ? "NEXT EVENT QUEUED" :
          f.agentStates.every(s => s === "done") ? "MISSION COMPLETE" : "AWAITING THE ORDER";
      text(status, x, 975, 17, color, 600, "center");
      text("One persisted handoff at a time.", x, 1008, 17, C.muted, 400, "center");
      text(data.authorityLabel || "No unlimited AI execution authority.", x, 1034, 15, C.muted, 400, "center");
    }

    ctx.fillStyle = "#081322"; ctx.fillRect(0, 0, 1920, 1080);
    const light = ctx.createRadialGradient(1610, 390, 40, 1250, 450, 1200);
    light.addColorStop(0, "#294d6b"); light.addColorStop(1, "#081322");
    ctx.fillStyle = light; ctx.fillRect(0, 0, 1920, 1080);
    ctx.save(); ctx.globalAlpha = .12;
    for (let i = 0; i < 50; i++) {
      const x = (i * 181 + t * (7 + i % 4)) % 1920;
      const y = 210 + (i * 67) % 690;
      dot(x, y, i % 3 + 1, C.blue);
    }
    for (let i = 0; i < 18; i++) {
      const x = i * 120, h = 70 + (i * 71) % 180;
      box(x, 1040 - h, 80, h, "#385775", "#385775", 3);
    }
    ctx.restore();
    text("TürkBizTel / MISSION: ACTIVATE", 52, 43, 18, C.mint, 700);
    text(data.editionLabel || "THE CONTROL TOWER EDITION", 1465, 43, 17, C.blue, 600);
    text(`${String(f.index + 1).padStart(2, "0")} / ${f.scene.chapter.toUpperCase()}`, 52, 91, 18, C.amber, 600);
    text(f.scene.title, 52, 153, 48, C.white, 700);
    text((f.scene.order ? `ORDER ${f.scene.order} / ` : "") + f.event, 54, 194, 22, C.blue, 500);
    box(35, 225, 1345, 388, "#0d2133", "#34536d", 20);
    ctx.save(); ctx.translate(12, 65); ctx.scale(1.05, 1.05);
    const arrival = ease(f.local / .42);
    ctx.globalAlpha = arrival; ctx.translate(18 * (1 - arrival), 0);
    drawScene(f); ctx.restore();
    box(58, 630, 1298, 43, "#243b52", "#49728e", 13);
    text(f.scene.takeaway || quips[f.scene.id], 707, 658, 19, C.white, 700, "center");

    line([[167, 690], [1645, 690], [1645, 439]], "#42617e", true);
    data.agents.forEach((name, i) => {
      const x = 167 + i * 278, status = f.agentStates[i];
      const selected = i === target && !proposed;
      const color = status === "held" ? C.red : colors[i];
      const path = [[1645, 439], [1645, 690], [x, 690], [x, 711]];
      if (selected) {
        ctx.save(); ctx.globalAlpha = .8; line(path, color); ctx.restore();
        const progress = (t * .45) % 1;
        const px = 1645 + (x - 1645) * progress;
        ctx.save(); ctx.shadowColor = color; ctx.shadowBlur = 18;
        dot(px, 690, 7, color); ctx.restore();
      }
      arrow([[x, 690], [x, 708]], selected ? color : "#42617e");
      box(x - 131, 710, 262, 239, selected ? "#18344b" : "#102237", selected ? color : "#35506b", 18);
      text(String(i + 1).padStart(2, "0"), x - 112, 736, 17, color, 700);
      avatar(i, x, status);
      text(name, x, 919, 23, C.white, 600, "center");
      text(status.toUpperCase(), x, 942, 15, status === "held" ? C.red : color, 700, "center");
    });
    tower();
    if (f.scene.id === "communication" && f.beat > 0 || f.scene.id === "impact") {
      ctx.save(); ctx.globalAlpha = .7;
      for (let i = 0; i < 42; i++) {
        const x = 65 + i * 31 + Math.sin(t * 2 + i) * 12;
        const y = 229 + (i * 47 + t * 45) % 380;
        ctx.save(); ctx.translate(x, y); ctx.rotate(t * 2 + i);
        ctx.fillStyle = colors[i % 5]; ctx.fillRect(-2, -4, 4, 8); ctx.restore();
      }
      ctx.restore();
    }
    const beat = f.scene.beats[f.beat];
    if (f.local >= beat.start && f.local < beat.start + beat.duration) {
      box(36, 965, 1340, 78, "#06111ef2", "#42617e", 12);
      wrap(beat.text, 58, 996, 1292, 27, C.white, 34);
    }
    text(data.footerLabel || (proposed ? "PROPOSED PRODUCTION DESIGN / NOT DEPLOYED" : "SYNTHETIC SCENARIO / NO LIVE NETWORK CHANGES"),
      42, 1068, 13, proposed ? C.amber : C.muted, 600);
    text(data.voiceLabel || "AUTHOR-SAMPLE VOICE / LOCALLY SYNTHESIZED", 762, 1068, 12, C.muted, 500);
    text(`${clock(f.time)} / ${clock(total)}`, 1780, 1068, 17, C.mint, 600);
    ctx.fillStyle = C.mint; ctx.fillRect(0, 1076, 1920 * f.time / total, 4);
    return {scene: f.scene.id, beat: f.beat, event: f.event, agentStates: f.agentStates,
      progress: f.progress, activeAgent: active, tower: {x: 1430, y: 180, width: 433, height: 761},
      avatars: data.avatars};
  }
};
