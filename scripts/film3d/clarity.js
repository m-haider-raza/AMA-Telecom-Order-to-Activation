import "./scene.js";

const shared = window.amaFilmTheme, studio = window.ama3DStudio, T = studio.THREE;
const world = new T.Scene(), camera = new T.PerspectiveCamera(36, 1040 / 585, .1, 100);
world.background = new T.Color(0x172b3b);
world.add(new T.HemisphereLight(0xe9f3ff, 0x718493, 2.1));
const key = new T.DirectionalLight(0xfff2df, 2.6);
key.position.set(-5, 12, 8); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, {left: -12, right: 12, top: 12, bottom: -12, far: 45});
key.shadow.normalBias = .025; world.add(key);
const rim = new T.DirectionalLight(0xa7d4ef, 1.1);
rim.position.set(6, 7, -4); world.add(rim);

const {cube, sphere, rod, group, server, desk, moveHuman} = studio;
const floor = cube(world, [13, .25, 9], [0, -.2, 0], 0x486171);
floor.material.roughness = .75;
cube(world, [13, .45, .18], [0, 5.4, -4], 0x607987);
cube(world, [13, .55, .18], [0, .25, -4], 0x607987);
for (let i = 0; i < 5; i++) {
  cube(world, [.1, 4.9, .16], [-6.35 + i * 2.55, 2.8, -3.85], 0x99b9c7);
  const glass = cube(world, [2.43, 4.7, .05], [-5.1 + i * 2.55, 2.8, -3.8], 0x739bae);
  glass.material.transparent = true; glass.material.opacity = .18; glass.material.depthWrite = false;
}
const workstation = group(world, [-.65, 0, .95]);
desk(workstation, [0, 0, 0], "");
const terminal = group(world, [1.55, 0, -.8]);
cube(terminal, [2.8, .15, 1.5], [0, 1.5, 0], 0x83959e);
for (const x of [-1, 1]) rod(terminal, [x, 0, 0], [x, 1.5, 0], .1, 0x596f80);
cube(terminal, [3, 1.8, .2], [0, 2.55, -.1], 0x152735);
const screenCanvas = document.createElement("canvas"); screenCanvas.width = 1200; screenCanvas.height = 720;
const screenContext = screenCanvas.getContext("2d");
const screenTexture = new T.CanvasTexture(screenCanvas); screenTexture.colorSpace = T.SRGBColorSpace;
const screen = new T.Mesh(new T.PlaneGeometry(2.82, 1.63), new T.MeshBasicMaterial({map: screenTexture}));
screen.position.set(0, 2.55, .015); terminal.add(screen);
const rack = server(world, [3.4, 0, -1.4], "", 0x80d9ba);
const boundary = group(world);
server(boundary, [-2.1, 0, -.3], "", 0x8ed6b8);
server(boundary, [.15, 0, -.3], "", 0x8ed6b8);
server(boundary, [2.4, 0, -.3], "", 0x8ed6b8);
const gate = group(world, [.9, 0, 1.8]);
rod(gate, [-1.5, 0, 0], [-1.5, 2, 0], .07, 0xd9a16c);
rod(gate, [1.5, 0, 0], [1.5, 2, 0], .07, 0xd9a16c);
const barrier = cube(gate, [3.1, .14, .1], [0, 1.7, 0], 0xefbd89);
const envelope = cube(world, [.38, .22, .045], [0, 2, 0], 0x91cab3);
const icons = new Map();
let tower, industryLogo;

function hideSprites(root) {
  root.traverse(object => { if (object.isSprite) object.visible = false; });
}
async function initialize() {
  studio.renderer.setSize(1040, 585);
  for (const person of [studio.customer, studio.manager]) {
    world.add(person.wrapper); hideSprites(person.wrapper);
  }
  for (const avatar of studio.avatars) {
    world.add(avatar.bot); hideSprites(avatar.bot);
    avatar.bot.scale.copy(avatar.restScale).multiplyScalar(1.5);
  }
  tower = studio.tower; world.add(tower); hideSprites(tower);
  tower.position.set(3.3, .1, -8.5); tower.scale.setScalar(.53);
  hideSprites(world);
  for (const [name, file] of [
    ["Azure Local", "azure-local.svg"], ["Azure Arc", "azure-arc.svg"],
    ["Azure Container Registry", "container-registry.svg"], ["Azure Monitor", "azure-monitor.svg"]
  ]) {
    const image = await new Promise((resolve, reject) => {
      const source = new Image();
      source.onload = () => resolve(source);
      source.onerror = () => reject(new Error(`Required official icon failed: ${file}`));
      source.src = "/static/film3d-assets/" + file;
    });
    icons.set(name, image);
  }
  industryLogo = await new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Required Turkish industry-context logo failed"));
    image.src = "/static/solution-turk-telekom.svg";
  });
}

const LAYOUT = {
  world: {x: 32, y: 194, width: 1040, height: 645},
  responsibilities: {x: 1100, y: 194, width: 788, height: 645},
  handoffs: {x: 32, y: 847, width: 1856, height: 78},
  captions: {x: 32, y: 938, width: 1856, height: 96}
};
function wrapLines(ctx, value, width, size) {
  ctx.font = `500 ${size}px "Segoe UI", Arial`;
  const lines = []; let line = "";
  for (const word of value.split(" ")) {
    const next = line ? line + " " + word : word;
    if (line && ctx.measureText(next).width > width) { lines.push(line); line = word; }
    else line = next;
  }
  if (line) lines.push(line);
  return lines;
}
function terminalScreen(f) {
  const c = screenContext, error = f.beat === 1 && ["validation", "provisioning", "recovery", "human"].includes(f.scene.id);
  c.fillStyle = "#edf3f5"; c.fillRect(0, 0, 1200, 720);
  c.fillStyle = "#254b65"; c.fillRect(0, 0, 1200, 100);
  c.font = "600 42px Segoe UI"; c.fillStyle = "#ffffff"; c.fillText("TürkBizTel / SYNTHETIC ORDER A", 45, 65);
  c.fillStyle = "#20384b"; c.font = "600 48px Segoe UI";
  c.fillText(f.scene.id === "decomposition" ? "PRODUCT -> SERVICE -> RESOURCE" :
    f.scene.id === "human" ? "SERVICE MANAGER REVIEW" : "ORDER WORKSPACE", 45, 175);
  c.font = "36px Segoe UI";
  const lines = f.scene.id === "decomposition" ? [
    "Dedicated Internet Access / approved catalogue", "Access circuit + Internet service",
    "Fibre access port + IPv4 /29 block", "Linked to the same product order"
  ] : [
    "Demo Marmara Logistics / Istanbul", "Site reference: IST-001",
    "Owner: " + f.scene.role.replace(/^[0-9] \/ /, ""),
    error ? "HOLD / EXPLICIT REVIEW REQUIRED" : "PERSISTED STATE / GUARDED TRANSITION"
  ];
  lines.forEach((line, i) => c.fillText(line, 45, 260 + i * 92));
  c.fillStyle = error ? "#b87731" : "#258770";
  c.fillRect(45, 648, 1095 * (.15 + .8 * f.progress), 20);
  screenTexture.needsUpdate = true;
}
function render({f, data, total, ctx, text, box, clock}) {
  const scene = f.scene, id = scene.id, t = f.local;
  const customer = scene.actor === "customer", manager = scene.actor === "manager", datacenter = scene.actor === "datacenter";
  moveHuman(studio.customer, [-1.65, 0, -.35], customer, f, false);
  moveHuman(studio.manager, [-1.65, 0, -.35], manager, f, manager);
  for (const person of [studio.customer, studio.manager]) hideSprites(person.wrapper);
  studio.avatars.forEach((avatar, i) => {
    avatar.bot.visible = scene.actor === "agent" && i === scene.agent;
    avatar.bot.position.set(-1.9, .06, -.15);
    avatar.bot.rotation.y = .18 + Math.sin(t * .7) * .025;
    avatar.arms.forEach((arm, side) => { arm.rotation.z = (side ? 1 : -1) * (.15 + Math.sin(t * 1.3) * .08); });
    if (avatar.prop && i === 1) avatar.prop.rotation.y = t * .18;
  });
  boundary.visible = datacenter;
  workstation.visible = !datacenter;
  terminal.visible = !datacenter;
  rack.visible = !datacenter && !["opening", "intake"].includes(id);
  tower.visible = ["opening", "intake", "impact"].includes(id);
  gate.visible = id === "human" || id === "provisioning" && f.beat === 1;
  barrier.rotation.z = id === "human" && f.beat === 1 ? -.55 * f.progress : 0;
  envelope.visible = !datacenter && id !== "human";
  envelope.position.set(-1 + f.progress * 3.7, 1.8, .8);
  envelope.material.color.setHex(f.beat === 1 && ["validation", "provisioning", "recovery"].includes(id) ?
    0xe6a06d : 0x83c6aa);
  terminalScreen(f);
  camera.position.set(4.4 + Math.sin(t * .23) * .2, 4.3, 8.8 + Math.cos(t * .25) * .2);
  camera.lookAt(.1, 2.0, -.25);
  world.updateMatrixWorld(true); studio.renderer.render(world, camera);

  ctx.fillStyle = "#0b1725"; ctx.fillRect(0, 0, 1920, 1080);
  text("TürkBizTel / ORDER TO ACTIVATION / CLEAR OWNERSHIP EDITION", 36, 34, 17, "#9cdecc", 600);
  text("SIMULATED WORKFLOW / AMBER PATHS ARE PRODUCTION GATES", 1138, 34, 15, "#e8c493", 600);
  text(scene.chapter.toUpperCase(), 36, 75, 19, "#ecc99f", 600);
  text(scene.title, 36, 127, 40, "#eef3f8", 600);
  text(scene.event, 38, 166, 22, "#a5c7e8", 500);
  box(1668, 51, 214, 87, "#ffffff", "#ffffff", 5);
  const logoHeight = 198 * industryLogo.naturalHeight / industryLogo.naturalWidth;
  ctx.drawImage(industryLogo, 1676, 57 + (75 - logoHeight) / 2, 198, logoHeight);
  text("INDUSTRY CONTEXT / NO ENDORSEMENT", 1647, 163, 12, "#acbfd0", 500);
  box(32, 194, 1040, 645, "#172b3b", "#466275", 12);
  ctx.drawImage(studio.renderer.domElement, 32, 204);
  text(customer ? "CUSTOMER / SUITED HUMAN" : manager ? "HUMAN SERVICE MANAGER" :
    datacenter ? "PROPOSED OPERATOR DATACENTER / TURKIYE" :
      "FOCAL AGENT / " + data.avatars[scene.agent].character.toUpperCase(),
  54, 821, 22, "#e2ebf1", 600);
  box(1100, 194, 788, 645, "#122738", "#466275", 12);
  text(scene.role, 1124, 235, 28, "#eaf3f7", 600);
  text(scene.actor === "agent" ? "ONE SPECIALIST. ONE DEFINED RESPONSIBILITY." : "ACCOUNTABLE OWNER AND EXPLICIT CONTROL",
    1124, 263, 15, "#94b8d0", 500);
  const bounds = [];
  scene.panels.forEach((panel, i) => {
    const y = 294 + i * 104, emphasis = f.beat === 0 ? i <= 1 : /EXCEPTION|HUMAN|LIMIT|DECISION/.test(panel.label);
    const color = panel.proposed || scene.mode === "proposed" ? "#edc493" :
      /EXCEPTION|LIMIT/.test(panel.label) ? "#edb896" : "#b2e1d1";
    if (emphasis) { ctx.fillStyle = "#213e50"; ctx.fillRect(1115, y - 18, 757, 97); }
    text(panel.label, 1124, y, 17, color, 600);
    const lines = wrapLines(ctx, panel.text, 710, 26);
    if (lines.length > 2) throw new Error(`Responsibility panel overflows: ${id} / ${panel.label}`);
    lines.forEach((line, n) => text(line, 1124, y + 34 + n * 31, 26, "#e4edf3", 500));
    bounds.push({label: panel.label, x:1124, y:y-18, width:735, height:97, fontSize:26,
      lines:lines.length, measuredWidth:Math.max(...lines.map(line=>ctx.measureText(line).width))});
  });
  if (id === "impact") {
    for (const [x, label, value] of [[1124, "LEAD-TIME TARGET", "35 days -> under 7"],
      [1501, "FALLOUT TARGET", "30% -> under 8%"]]) {
      box(x, 723, 360, 93, "#213e50", "#d5b78b", 8);
      text(label, x + 15, 748, 16, "#edc493", 600);
      text(value, x + 15, 791, 31, "#eef4f8", 600);
    }
  }
  if (datacenter) {
    let x = 1140;
    for (const [name, image] of icons) {
      const size = name === "Azure Local" ? 48 : 40, ratio = image.naturalWidth / image.naturalHeight;
      ctx.drawImage(image, x, 751, size, size / ratio);
      if (name === "Azure Container Registry") {
        text("Azure Container", x - 12, 802, 16, "#d1e6f5", 500);
        text("Registry", x - 12, 822, 16, "#d1e6f5", 500);
      } else text(name, x - 12, 821, 16, "#d1e6f5", 500);
      x += name === "Azure Local" ? 160 : 184;
    }
  }
  text("MAIN: VALIDATE > PLAN > PROVISION > UPDATE    |    TIMEOUT ONLY: RECOVER > PROVISION", 38, 862, 18, "#acbfd0", 600);
  const labels = ["1 / Validate", "2 / Plan", "3 / Provision", "4 / Recover", "5 / Update"];
  labels.forEach((label, i) => {
    const x = 32 + i * 374, active = i === scene.agent;
    box(x, 874, 360, 50, active ? "#244c46" : "#142c3e", active ? "#9cdecc" : "#466275", 7);
    text(label, x + 15, 905, 22, active ? "#c5f1df" : "#aec3d4", 600);
    text(active ? "OWNER IN FOCUS" : i === 3 ? "EXCEPTION ONLY" : "BOUNDED ROLE",
      x + 177, 905, 14, active ? "#a6e7ce" : "#86a8bf", 500);
  });
  const beat = scene.beats[f.beat], captionLines = wrapLines(ctx, beat.text, 1700, 28);
  if (captionLines.length > 2) throw new Error(`Narration captions overflow: ${id}`);
  box(32, 938, 1856, 96, "#071a28", "#466275", 10);
  if (f.local >= beat.start && f.local < beat.start + beat.duration)
    captionLines.forEach((line, i) => text(line, 54, 974 + i * 35, 28, "#eff5f8", 500));
  text("RENDERED HUMANS / SYNTHETIC AUTHOR VOICE / TARGETS REQUIRE PILOT EVIDENCE", 38, 1064, 14, "#9cb8ce", 500);
  text(`${clock(f.time)} / ${clock(total)}`, 1735, 1064, 18, "#b3e6d1", 600);
  ctx.fillStyle = "#94dfc5"; ctx.fillRect(0, 1076, 1920 * f.time / total, 4);
  return {
    scene:id, agent:scene.agent, owner:scene.role, activeAgent:scene.agent,
    layout:LAYOUT, panelBounds:bounds, captionFontSize:28, captionLines:captionLines.length,
    visibleAgents:studio.avatars.filter(a=>a.bot.visible).length,
    visibleHumans:[customer?"customer":null,manager?"manager":null].filter(Boolean),
    perspectiveCamera:camera.isPerspectiveCamera, geometryTriangles:studio.renderer.info.render.triangles,
    shadows:studio.renderer.shadowMap.enabled, officialIcons:datacenter?[...icons.keys()]:[],
    industryContextLogo: Boolean(industryLogo), noEndorsementLabel:true,
    camera:camera.position.toArray()
  };
}
window.amaFilmTheme = {ready:shared.ready.then(initialize), render};
