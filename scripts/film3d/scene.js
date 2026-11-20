import * as THREE from "three";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";

const W = 1920, H = 750;
const COLORS = [0x73c9ff, 0xbc99ff, 0x61e5c0, 0xffb06a, 0xf49edc];
const ROOT = "/static/film3d-assets/";
const world = new THREE.Scene();
world.background = new THREE.Color(0x0b1725);
world.fog = new THREE.Fog(0x0b1725, 43, 80);
const camera = new THREE.PerspectiveCamera(31, W / H, .1, 150);
const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
renderer.setSize(W, H);
renderer.setPixelRatio(1);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.2;
world.add(new THREE.HemisphereLight(0xd7edff, 0x456378, 2.8));
const key = new THREE.DirectionalLight(0xffecd7, 3.5);
key.position.set(-9, 18, 15);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
Object.assign(key.shadow.camera, {left: -19, right: 19, top: 19, bottom: -19, far: 70});
key.shadow.normalBias = .025;
world.add(key);
const rim = new THREE.DirectionalLight(0x69bfff, 2);
rim.position.set(12, 12, -12); world.add(rim);

function material(color, glow = 0) {
  return new THREE.MeshStandardMaterial({
    color, roughness: .48, metalness: .18, emissive: color, emissiveIntensity: glow
  });
}
function mesh(parent, geometry, color, position = [0, 0, 0], glow = 0) {
  const object = new THREE.Mesh(geometry, material(color, glow));
  object.position.set(...position); object.castShadow = true; object.receiveShadow = true;
  parent.add(object); return object;
}
function cube(parent, size, position, color, glow = 0) {
  return mesh(parent, new THREE.BoxGeometry(...size), color, position, glow);
}
function sphere(parent, radius, position, color, glow = 0) {
  return mesh(parent, new THREE.SphereGeometry(radius, 24, 16), color, position, glow);
}
function cylinder(parent, top, bottom, height, position, color, glow = 0) {
  return mesh(parent, new THREE.CylinderGeometry(top, bottom, height, 24), color, position, glow);
}
function ring(parent, radius, tube, position, color, glow = 0) {
  return mesh(parent, new THREE.TorusGeometry(radius, tube, 10, 48), color, position, glow);
}
function group(parent, position = [0, 0, 0]) {
  const object = new THREE.Group(); object.position.set(...position); parent.add(object); return object;
}
function rod(parent, a, b, radius, color, glow = 0) {
  const start = new THREE.Vector3(...a), end = new THREE.Vector3(...b);
  const object = cylinder(parent, radius, radius, start.distanceTo(end), [0, 0, 0], color, glow);
  object.position.copy(start.add(end).multiplyScalar(.5));
  object.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0),
    new THREE.Vector3(...b).sub(new THREE.Vector3(...a)).normalize());
  return object;
}
function label(parent, value, position, width = 4, color = "#ddecff", height = .58) {
  const canvas = document.createElement("canvas"); canvas.width = Math.ceil(112 * width / height); canvas.height = 112;
  const w = canvas.width;
  const c = canvas.getContext("2d");
  c.fillStyle = "#0b1d2bea"; c.beginPath(); c.roundRect(1, 1, w - 2, 110, 20); c.fill();
  c.strokeStyle = color; c.lineWidth = 3; c.stroke();
  c.fillStyle = color; c.textAlign = "center"; c.textBaseline = "middle";
  let font = 48;
  do { c.font = `600 ${font--}px "Segoe UI", Arial`; } while (c.measureText(value).width > w - 36);
  c.fillText(value, w / 2, 56);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, depthWrite: false}));
  sprite.userData.textLabel = true;
  sprite.position.set(...position); sprite.scale.set(width, height, 1); parent.add(sprite);
  return sprite;
}
function placard(parent, title, detail, position, color = 0x8dcaff) {
  const station = group(parent, position);
  cube(station, [4.5, .35, 2.8], [0, .18, 0], 0x223d53);
  label(station, title, [0, 3.0, 0], 4.7, `#${color.toString(16).padStart(6, "0")}`);
  label(station, detail, [0, .8, 1.7], 4.9, "#cbd9e8", .52);
  return station;
}
function server(parent, position, title, color = 0x6bc8ff) {
  const rack = group(parent, position);
  cube(rack, [1.6, 2.8, 1.2], [0, 1.4, 0], 0x284256);
  for (let i = 0; i < 5; i++) {
    cube(rack, [1.37, .33, .08], [0, .4 + i * .48, .64], 0x142735);
    cube(rack, [.08, .08, .1], [.48, .4 + i * .48, .7], color, .7);
  }
  if (title) label(rack, title, [0, 3.2, 0], 3.7);
  return rack;
}

const ground = cube(world, [28, .36, 20], [0, -.25, 0], 0x152d40);
ground.receiveShadow = true;
const stageRing = ring(world, 10.8, .045, [0, -.035, 0], 0x349ccb, .45);
stageRing.rotation.x = -Math.PI / 2;
const skyline = group(world, [0, 0, -24]);
for (let i = 0; i < 25; i++) {
  const height = 1.2 + (i * 17 % 11) * .35, x = (i - 12) * 1.9;
  cube(skyline, [1.2, height, 1.3], [x, height / 2, 0], 0x17334b);
  for (let j = 0; j < height - .3; j += .55)
    cube(skyline, [.52, .13, .03], [x, .45 + j, .67], 0x558eb0, .25);
}
const istanbul = group(world, [-8, 0, -13]);
cylinder(istanbul, 1, 1.3, 1.3, [0, .65, 0], 0x365c76);
mesh(istanbul, new THREE.SphereGeometry(1.15, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
  0x608aa0, [0, 1.3, 0]);
for (const x of [-1.7, 1.7]) {
  cylinder(istanbul, .12, .18, 3.4, [x, 1.7, 0], 0x52798e);
  mesh(istanbul, new THREE.ConeGeometry(.22, .7, 16), 0xabc7d0, [x, 3.75, 0]);
}

const tower = group(world, [9.6, 0, -5.2]);
cube(tower, [3.6, .55, 3.2], [0, .28, 0], 0x426982);
cube(tower, [2.05, 6.6, 1.9], [0, 3.75, 0], 0x244e68);
for (let floor = 0; floor < 8; floor++)
  for (const x of [-.5, .5])
    cube(tower, [.49, .24, .04], [x, 1 + floor * .67, .98], 0x65cafa, .6);
cube(tower, [4.15, 1.85, 3.4], [0, 7.55, 0], 0x376b89);
cube(tower, [3.6, 1.1, .08], [0, 7.6, 1.75], 0x081c2c);
const towerEyes = [-.87, .87].map(x => sphere(tower, .24, [x, 7.65, 1.85], 0x96efff, 1.2));
const roof = mesh(tower, new THREE.ConeGeometry(3, .9, 4), 0x5083a0, [0, 8.85, 0]);
roof.rotation.y = Math.PI / 4;
rod(tower, [0, 9.1, 0], [0, 10, 0], .065, 0x99c3d4);
const beacon = sphere(tower, .19, [0, 10.1, 0], 0x8decc8, 1.5);
const towerHalo = ring(tower, 2.5, .035, [0, 7.65, 0], 0x6cd0ff, 1);
towerHalo.rotation.x = Math.PI / 2;
label(tower, "ORCHESTRATION TOWER", [0, 10.9, 0], 5.3, "#a5e5ff", .6);
label(tower, "Guarded deterministic supervisor", [0, .95, 2.05], 5.1, "#c6d7e4", .5);

const avatars = [];
const names = ["Inspector", "Architect", "Cable crew", "Rescue mechanic", "Messenger"];
for (let i = 0; i < 5; i++) {
  const base = [-10 + i * 4.75, 0, 3.25], podium = group(world, base);
  cylinder(podium, 1.3, 1.4, .22, [0, .11, 0], 0x253f52);
  const halo = ring(podium, 1.3, .055, [0, .24, 0], COLORS[i], .45);
  halo.rotation.x = Math.PI / 2;
  label(podium, names[i], [0, -.05, 1.45], 3.65, `#${COLORS[i].toString(16)}`, .55);
  const bot = group(world, base);
  if (i === 3) sphere(bot, .62, [0, 1.1, 0], COLORS[i]);
  else mesh(bot, new THREE.CapsuleGeometry(.43, .47, 6, 16), COLORS[i], [0, 1.04, 0]);
  const head = i === 1 ? cube(bot, [.95, .75, .75], [0, 1.87, 0], COLORS[i]) :
    sphere(bot, i === 2 ? .4 : .48, [0, 1.87, 0], COLORS[i]);
  cube(bot, [.69, .29, .13], [0, 1.92, .43], 0x0b2433);
  const eyes = [];
  for (const x of i === 2 ? [0] : [-.17, .17])
    eyes.push(sphere(bot, i === 2 ? .115 : .08, [x, 1.95, .52], 0xdaf8ff, .4));
  for (const x of [-.26, .26]) {
    rod(bot, [x, .68, 0], [x, .29, .02], .12, 0x587b90);
    cube(bot, [.3, .15, .46], [x, .25, .13], 0x162c3e);
  }
  const arms = [-1, 1].map(side => {
    const arm = group(bot, [side * .48, 1.28, 0]);
    rod(arm, [0, 0, 0], [side * .2, -.48, .16], .105, COLORS[i]);
    sphere(arm, .14, [side * .2, -.48, .16], 0xcee2ec);
    return arm;
  });
  let prop;
  if (i === 0) {
    for (const x of [-.32, .32])
      mesh(bot, new THREE.ConeGeometry(.17, .4, 4), COLORS[i], [x, 2.38, 0]);
    cube(bot, [.48, .65, .075], [-.72, 1.13, .39], 0xe3edf2);
    for (let y = .96; y < 1.36; y += .14)
      cube(bot, [.27, .035, .085], [-.7, y, .45], 0x249c83);
    prop = ring(bot, .25, .05, [.83, 1.32, .4], 0xf4d49c);
    rod(bot, [.68, 1.1, .4], [.51, .89, .4], .06, 0xf4d49c);
  } else if (i === 1) {
    mesh(bot, new THREE.SphereGeometry(.58, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2),
      0xffd06b, [0, 2.17, 0]);
    cylinder(bot, .65, .65, .1, [0, 2.16, 0], 0xffd06b);
    prop = group(bot, [0, 2.65, 0]);
    for (let j = 0; j < 3; j++)
      cube(prop, [.25, .25, .25], [Math.cos(j * 2.1) * .8, 0, Math.sin(j * 2.1) * .8], 0x8de4ff, .2);
    cube(bot, [.65, .38, .1], [-.6, 1.08, .35], 0x77bced);
  } else if (i === 2) {
    bot.scale.y = 1.18;
    rod(bot, [.7, .94, .2], [.7, 1.5, .2], .07, 0xc7dae2);
    prop = ring(bot, .16, .07, [.7, 1.57, .2], 0xc7dae2);
    cube(bot, [.2, .3, .2], [-.74, .9, .28], 0x163143);
    for (let j = 0; j < 3; j++)
      rod(bot, [-.83 + .08 * j, 1.0, .3], [-.83 + .08 * j, 1.17, .3], .021, 0xffce79);
  } else if (i === 3) {
    prop = ring(bot, .61, .16, [0, 1.05, .32], 0xffac62);
    for (const angle of [0, Math.PI / 2, Math.PI, Math.PI * 1.5]) {
      const band = cube(prop, [.25, .2, .16], [Math.cos(angle) * .62, Math.sin(angle) * .62, .05], 0xfff0d7);
      band.rotation.z = angle;
    }
    cube(bot, [.6, .32, .38], [.74, .7, .2], 0x597287);
    bot.userData.retryLabel = label(bot, "ONE RETRY", [0, 2.68, 0], 2.4, "#ffca96", .38);
  } else {
    const headset = ring(bot, .55, .07, [0, 1.96, 0], 0x122d45);
    headset.scale.y = .95;
    for (const x of [-.52, .52]) cube(bot, [.18, .3, .27], [x, 1.96, 0], 0x243c56);
    rod(bot, [.5, 1.85, .1], [.3, 1.66, .55], .035, 0x153047);
    sphere(bot, .06, [.3, 1.66, .55], 0xe9d9ea);
    const shape = new THREE.Shape();
    shape.moveTo(-.48, -.18); shape.lineTo(.5, 0); shape.lineTo(-.48, .2); shape.lineTo(-.2, 0);
    shape.closePath();
    prop = mesh(bot, new THREE.ExtrudeGeometry(shape, {depth: .025, bevelEnabled: false}),
      0xf3edf5, [.74, 1.1, .3]);
  }
  avatars.push({bot, base, arms, eyes, head, prop, halo, restScale: bot.scale.clone()});
}

const stages = new Map();
const animations = [];
function chapter(id) { const scene = group(world); stages.set(id, scene); return scene; }
function animate(id, callback) { animations.push({id, callback}); }
function orderBlock(parent, position, text = "ORDER A", color = 0x8ddbc9) {
  const object = group(parent, position);
  cube(object, [1.65, 1.22, .72], [0, 0, 0], color);
  label(object, text, [0, .95, 0], 2.7);
  return object;
}
function desk(parent, position, text) {
  const object = group(parent, position);
  cube(object, [3, .16, 1.6], [0, 1.5, 0], 0x6c8392);
  for (const x of [-1.15, 1.15]) rod(object, [x, .1, 0], [x, 1.5, 0], .095, 0x657d8c);
  const laptop = group(object, [-.6, 1.61, 0]);
  cube(laptop, [1.15, .055, .78], [0, 0, .23], 0x21364b);
  const screen = cube(laptop, [1.15, .72, .055], [0, .38, -.12], 0x2ba8d8, .25);
  screen.rotation.x = -.15;
  label(object, text, [0, .55, 1.3], 4.4);
  return object;
}
for (const id of ["opening", "intake"]) {
  const set = chapter(id);
  desk(set, [-7, 0, -1], "Connectivity / one click");
  server(set, [-1.5, 0, -2.4], "DURABLE ORDER + EVENT");
  const order = orderBlock(set, [-4, 2.2, .2]);
  const duplicate = orderBlock(set, [-2.3, 2.2, .2], "SAME KEY", 0x5a7689);
  set.userData.overviewLabel = label(set,
    id === "intake" ? "Persist once. Dispatch in sequence." : "ISTANBUL / CONNECTIVITY STARTS HERE",
    [-2, 6.2, -2], 9, "#aeead8", .75);
  animate(id, f => {
    order.position.set(-6.1 + f.progress * 4.6, 2 + Math.sin(f.progress * Math.PI) * 1.2, -.1);
    order.rotation.y = Math.sin(f.local) * .12;
    duplicate.visible = id === "intake" && f.beat === 0;
    duplicate.scale.setScalar(.8 + Math.sin(f.local * 2) * .025);
  });
}
{
  const set = chapter("validation");
  const block = orderBlock(set, [-.6, 2.3, -.6], "VALIDATE INTENT");
  const scanner = ring(set, 1.35, .09, [-.6, 2.3, -.6], 0x88daff, .8);
  label(set, "Coverage / eligibility / customer details", [-1, 4.7, -1], 9, "#b5e7ff");
  label(set, "order.validated", [2, 1, 1], 4.4, "#b8f0d8");
  animate("validation", f => {
    scanner.rotation.y = f.local * .8; scanner.rotation.x = Math.sin(f.local) * .3;
    scanner.position.y = 2.3 + Math.sin(f.local * 2) * .3;
    block.rotation.y = Math.sin(f.local * .8) * .16;
  });
}
{
  const set = chapter("decomposition");
  const order = orderBlock(set, [-3.5, 2.8, -2], "PRODUCT");
  const services = ["ACCESS", "NETWORK", "CPE"].map((name, i) =>
    orderBlock(set, [-3.5 + i * 2.55, 1.55, .2], name, 0xb69ae8));
  services.forEach((service, i) => rod(set, [-3.5, 2.8, -2], [-3.5 + i * 2.55, 1.55, .2],
    .035, 0xa786e1, .5));
  label(set, "Product intent becomes a dependency graph", [-1.5, 4.7, -1], 9.5, "#d5bdff");
  animate("decomposition", f => {
    order.rotation.y = f.local * .13;
    services.forEach((s, i) => { s.position.y = 1.6 + Math.sin(f.local * 2 - i) * .12; });
  });
}
{
  const set = chapter("provisioning");
  server(set, [-2, 0, -1.5], "CAPACITY / ALLOCATE ONCE", 0x67e6bc);
  const router = cube(set, [2.5, .8, 1.4], [2, 1.7, 0], 0x398875);
  for (let i = 0; i < 6; i++) cube(set, [.15, .12, .07], [1.1 + i * .35, 1.7, .74], 0x8affd4, .8);
  rod(set, [-2, 2, -.8], [2, 1.7, 0], .055, 0x7bffd0, .5);
  label(set, "Simulated adapter / no live network changes", [0, 4.6, -1], 9.6, "#a8efdb");
  animate("provisioning", f => { router.position.y = 1.7 + Math.sin(f.local * 2.8) * .05; });
}
{
  const set = chapter("recovery");
  server(set, [-2, 0, -1.3], "TIMEOUT / BOUNDED RECOVERY", 0xffb679);
  const hazard = mesh(set, new THREE.OctahedronGeometry(.8), 0xff896e, [1.6, 2.6, 0], .3);
  const retry = ring(set, 1.2, .09, [1.6, 2.4, 0], 0xffc386, .5);
  label(set, "Allowlisted timeout: one retry, not a loop", [0, 4.7, -1], 9.3, "#ffcb9d");
  label(set, "Capacity is not allocated twice", [1.4, .9, 1.2], 5.5, "#b4eddb");
  animate("recovery", f => {
    hazard.rotation.set(f.local * .4, f.local * .7, 0); hazard.visible = f.beat === 0;
    retry.rotation.z = -f.local * 1.6;
  });
}
{
  const set = chapter("human");
  desk(set, [-3.8, 0, -.9], "SERVICE MANAGER / REVIEW");
  server(set, [1.5, 0, -.8], "NO ALLOCATION WHILE HELD", 0xffaa8e);
  const gate = group(set, [-.9, 0, .7]);
  for (const x of [-1.2, 1.2]) rod(gate, [x, 0, 0], [x, 2.4, 0], .1, 0xff9c85);
  const barrier = cube(gate, [2.5, .26, .17], [0, 1.7, 0], 0xffa985);
  set.userData.overviewLabel = label(set, "Hold before capacity. Human approval is bounded.",
    [-2, 6.3, -1], 10, "#ffc4aa", .75);
  animate("human", f => { barrier.rotation.z = f.beat === 1 ? -f.progress * .95 : 0; });
}
{
  const set = chapter("communication");
  desk(set, [-6.6, 0, -1], "CUSTOMER PORTAL");
  const phone = cube(set, [1.3, 2.2, .22], [-.4, 2.1, -.4], 0x385a77);
  cube(set, [1.12, 1.85, .04], [-.4, 2.17, -.26], 0x7ed6bf, .2);
  label(set, "Siparisiniz aktif / Your service is active", [-.8, 4.5, -1], 9.3, "#f4c5e9");
  label(set, "Persisted TR / EN portal updates / not sent SMS", [-.5, .75, 1.1], 9.4);
  animate("communication", f => { phone.rotation.y = Math.sin(f.local) * .12; });
}
{
  const set = chapter("partners");
  const left = placard(set, "PARTNER / MCP", "Authenticated tool call", [-4, 0, -1]);
  const right = placard(set, "GUARDED ORDER INTAKE", "No approval or provisioning authority", [2, 0, -1]);
  mesh(left, new THREE.IcosahedronGeometry(.85), 0x8ecbff, [0, 1.6, 0]);
  server(right, [0, 0, 0], "", 0x71dbc8);
  rod(set, [-3, 2.1, -1], [1, 2.1, -1], .04, 0x9dd5ff, .5);
  label(set, "CAPACITY CHECK + ORDER SUBMISSION", [-1.2, 4.6, -1], 9, "#b7dcff");
}
{
  const set = chapter("standards");
  ["TMF622", "TMF641", "MCP"].forEach((name, i) => {
    const stand = placard(set, name, ["Product order", "Service order", "Tool transport"][i],
      [-5 + i * 4, 0, -.9], 0x9bc9ff);
    const shield = mesh(stand, new THREE.DodecahedronGeometry(.83), 0x497697, [0, 1.65, 0]);
    animate("standards", f => { shield.rotation.y = f.local * .35 + i; });
  });
  label(set, "TMF-inspired subsets / NOT full certification", [-1, 4.5, -1], 10, "#ffcf97");
}

const productBadges = [];
async function svgTexture(url, whiteBackground = false) {
  const image = await new Promise((resolve, reject) => {
    const source = new Image();
    source.onload = () => resolve(source);
    source.onerror = () => reject(new Error(`Required SVG image failed: ${url}`));
    source.src = url;
  });
  const aspect = image.naturalWidth / image.naturalHeight;
  const canvas = document.createElement("canvas");
  canvas.width = 768; canvas.height = Math.round(768 / aspect);
  const c = canvas.getContext("2d");
  if (whiteBackground) { c.fillStyle = "#ffffff"; c.fillRect(0, 0, canvas.width, canvas.height); }
  c.drawImage(image, 0, 0, canvas.width, canvas.height);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  return {texture, aspect};
}
async function product(parent, name, file, position, width = 1.35) {
  const {texture, aspect} = await svgTexture(ROOT + file);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, depthWrite: false}));
  sprite.position.set(...position); sprite.scale.set(width, width / aspect, 1);
  parent.add(sprite); productBadges.push({name, file, sprite, aspect});
  label(parent, name, [position[0], position[1] - width / aspect / 2 - .4, position[2]], 4.5, "#ecf4ff", .7);
}
function localDatacenter(set) {
  const enclosure = cube(set, [10.2, .3, 6.5], [-3.8, .05, -.8], 0x23544e);
  const boundary = ring(set, 5.15, .055, [-3.8, .22, -.8], 0x8be9ca, .9);
  boundary.rotation.x = -Math.PI / 2; boundary.scale.y = .72;
  server(set, [-6.5, 0, -1.2], "CUSTOMER DATA", 0x80e1b9);
  server(set, [-3.6, 0, -1.2], "LOCAL WORKFLOW", 0x80e1b9);
  server(set, [-.7, 0, -1.2], "INFERENCE / KEYS", 0x80e1b9);
  label(set, "TURKIYE / OPERATOR DATACENTER", [-3.8, .65, 3.1], 10, "#b6f7df");
  label(set, "Payloads / prompts / raw logs / backups STAY LOCAL", [-3.8, 7.2, -.8], 10, "#b6f7df", .7);
  return enclosure;
}
const badgeTasks = [];
{
  const set = chapter("residency"); localDatacenter(set);
  badgeTasks.push(product(set, "Azure Local", "azure-local.svg", [-3.7, 5.6, -1.1], 1.8));
  label(set, "Proposed sovereign workload / not a deployed site", [1.4, 1.6, 2.9], 8, "#ffce9b");
}
for (const id of ["hybrid", "strict"]) {
  const set = chapter(id); localDatacenter(set);
  badgeTasks.push(product(set, "Azure Local", "azure-local.svg", [-3.7, 5.6, -1.1], 1.8));
  const external = group(set, [3.7, 0, -4.4]);
  const cloud = group(external, [0, 3.1, 0]);
  for (let i = 0; i < 4; i++) sphere(cloud, .8, [-1.15 + i * .65, Math.sin(i) * .2, 0], 0x446e8c);
  label(external, "OUTSIDE TURKIYE", [0, 5.1, 0], 5.2, "#b6d9f2");
  if (id === "hybrid") {
    badgeTasks.push(product(external, "Azure Arc", "azure-arc.svg", [0, 4.15, 1.4], 1.0));
    badgeTasks.push(product(external, "Azure Container Registry", "container-registry.svg", [0, 2.65, 1.4], 1.0));
    badgeTasks.push(product(external, "Azure Monitor", "azure-monitor.svg", [0, 1.15, 1.4], 1.0));
    const cable = rod(set, [.3, 2.5, -1.2], [3.4, 3.1, -4.2], .035, 0xffc688, .5);
    label(set, "APPROVED ONLY: images / Arc metadata / safe aggregates", [-1.8, 8.6, -2], 11, "#ffce9b", .7);
    const bead = sphere(set, .15, [.3, 2.5, -1.2], 0xffc688, .8);
    animate(id, f => {
      const t = f.local % 2 / 2;
      bead.position.lerpVectors(new THREE.Vector3(.3, 2.5, -1.2), new THREE.Vector3(3.4, 3.1, -4.2), t);
      cable.material.emissiveIntensity = .3 + Math.sin(f.local * 2) * .1;
    });
  } else {
    const lock = group(set, [.65, 2.7, -1.3]);
    cube(lock, [1.0, .75, .6], [0, -.2, 0], 0xffca85);
    ring(lock, .32, .09, [0, .28, 0], 0xffe0aa);
    label(set, "No live cloud link / disconnected eligibility required", [-1.4, 8.6, -2], 11, "#ffce9b", .7);
    label(external, "DISCONNECTED", [0, 2.1, 1.2], 4.6, "#ffcf9e");
  }
}
{
  const set = chapter("impact");
  const metrics = [
    ["LEAD TIME", "35 days -> target <7 days", 3.0],
    ["FALLOUT", "30% -> target <8%", 1.9]
  ];
  metrics.forEach(([title, detail, height], i) => {
    const stand = placard(set, title, detail, [-3.5 + i * 5, 0, -.8], 0x94e5c9);
    const bar = cube(stand, [.9, height, .9], [-.55, height / 2 + .3, 0], 0x486c87);
    cube(stand, [.9, .65, .9], [.75, .625, 0], 0x65d9b0);
    animate("impact", f => { bar.scale.y = .95 + .05 * Math.sin(f.local); });
  });
  label(set, "Assignment ambitions / validate in a measured pilot", [-1, 4.7, -1], 10.5, "#ffe0a4");
}

const packet = sphere(world, .18, [0, 0, 0], 0xb0ffe1, 1.5);
const trails = Array.from({length: 10}, (_, i) => sphere(world, .09 - i * .004, [0, 0, 0], 0x7edbcd, .8));
const dispatchRoutes = avatars.map(avatar => {
  const curve = new THREE.CatmullRomCurve3([
    new THREE.Vector3(9.6, 7.2, -3.4),
    new THREE.Vector3(5, 6.2, 0),
    new THREE.Vector3(avatar.base[0], 2.5, 5.9)
  ]);
  const object = mesh(world, new THREE.TubeGeometry(curve, 40, .022, 5, false), 0x365f76, [0, 0, 0], .15);
  return {object, curve};
});
const confetti = Array.from({length: 70}, (_, i) => {
  const piece = cube(world, [.09, .18, .025], [0, 0, 0], COLORS[i % 5]);
  piece.visible = false; return piece;
});

let customer, manager, brand;
const humanEvidence = [];
function pointBone(bone, child, target) {
  bone.updateWorldMatrix(true, true);
  const direction = child.getWorldPosition(new THREE.Vector3()).sub(bone.getWorldPosition(new THREE.Vector3())).normalize();
  const delta = new THREE.Quaternion().setFromUnitVectors(direction, new THREE.Vector3(...target).normalize());
  const rotation = delta.multiply(bone.getWorldQuaternion(new THREE.Quaternion()));
  bone.quaternion.copy(bone.parent.getWorldQuaternion(new THREE.Quaternion()).invert().multiply(rotation));
}
async function human(role, file) {
  const loading = new THREE.LoadingManager();
  // Original FBX texture names are mapped to their losslessly re-encoded PNGs.
  loading.addHandler(/\.tga$/i, new THREE.TextureLoader(loading));
  loading.setURLModifier(url => /\.tga$/i.test(url) ?
    ROOT + role + "/" + url.split(/[\\/]/).pop().replace(/\.tga$/i, ".png") : url);
  let textureFailure;
  loading.onError = url => { textureFailure = url; };
  const allLoaded = new Promise(resolve => { loading.onLoad = resolve; });
  const model = await new FBXLoader(loading).loadAsync(ROOT + role + "/" + file);
  await allLoaded;
  if (textureFailure) throw new Error(`Required human texture failed: ${textureFailure}`);
  const wrapper = group(world);
  wrapper.add(model);
  const meshes = [], bones = [];
  let texturedMeshes = 0, skinnedMeshes = 0;
  model.traverse(object => {
    if (object.isBone) bones.push(object);
    if (!object.isMesh) return;
    meshes.push(object.name);
    if (object.isSkinnedMesh) skinnedMeshes++;
    if (/midpoly|lowpoly|ultralowpoly/i.test(object.name)) object.visible = false;
    object.castShadow = true; object.receiveShadow = true;
    const convert = source => {
      if (source.map) source.map.colorSpace = THREE.SRGBColorSpace;
      return new THREE.MeshStandardMaterial({
        color: source.map ? 0xffffff : source.color, map: source.map, normalMap: source.normalMap,
        alphaMap: source.alphaMap, transparent: source.transparent, opacity: source.opacity,
        alphaTest: source.alphaMap ? .35 : 0, side: THREE.DoubleSide,
        metalness: 0, roughness: .78
      });
    };
    object.material = Array.isArray(object.material) ? object.material.map(convert) : convert(object.material);
    if ([object.material].flat().some(m => m.map)) texturedMeshes++;
  });
  const bounds = new THREE.Box3().setFromObject(model);
  model.scale.setScalar(3.8 / bounds.getSize(new THREE.Vector3()).y);
  model.position.y = -bounds.min.y * model.scale.y;
  world.updateMatrixWorld(true);
  for (const side of ["L", "R"]) {
    const upper = bones.find(b => new RegExp(`${side}[_ ]?UpperArm`, "i").test(b.name));
    const forearm = bones.find(b => new RegExp(`${side}[_ ]?Forearm`, "i").test(b.name));
    const hand = bones.find(b => new RegExp(`${side}[_ ]?Hand$`, "i").test(b.name));
    if (upper && forearm) pointBone(upper, forearm, [side === "L" ? .18 : -.18, -.85, .28]);
    if (forearm && hand) pointBone(forearm, hand, [side === "L" ? .1 : -.1, -.8, .4]);
  }
  const rig = bones.map(bone => ({bone, rest: bone.quaternion.clone()}));
  label(wrapper, role === "customer" ? "CUSTOMER / BUSINESS SUIT" : "HUMAN SERVICE MANAGER",
    [0, 4.3, 0], 4.0, "#f2dcc1", .55);
  humanEvidence.push({role, file, meshes, boneCount: bones.length, meshHeight: 3.8,
    texturedMeshes, skinnedMeshes, boneNames: bones.map(b => b.name)});
  return {wrapper, model, rig};
}

function moveHuman(person, position, visible, f, reviewing) {
  person.wrapper.visible = visible; person.wrapper.position.set(...position);
  person.wrapper.rotation.y = reviewing ? .23 : -.1;
  person.model.rotation.y = Math.sin(f.local * .7) * .025;
  for (const joint of person.rig) {
    joint.bone.quaternion.copy(joint.rest);
    if (/Spine1$/i.test(joint.bone.name)) joint.bone.rotateX(Math.sin(f.local * 1.7) * .012);
    if (/Head$/i.test(joint.bone.name)) {
      joint.bone.rotateX(Math.sin(f.local * 1.3) * .04 + (reviewing ? .12 : .04));
      joint.bone.rotateY(Math.sin(f.local * .7) * .075);
    }
    if (visible && (reviewing || ["opening", "intake", "communication"].includes(f.scene.id))) {
      const upper = person.rig.find(j => /R[_ ]?UpperArm/i.test(j.bone.name))?.bone;
      const forearm = person.rig.find(j => /R[_ ]?Forearm/i.test(j.bone.name))?.bone;
      const hand = person.rig.find(j => /R[_ ]?Hand$/i.test(j.bone.name))?.bone;
      if (upper && forearm) pointBone(upper, forearm, [-.15, -.78, .6]);
      if (forearm && hand) pointBone(forearm, hand, [0, -.22, .98]);
    }
    if (/R[_ ]?UpperArm/i.test(joint.bone.name))
      joint.bone.rotateX(Math.sin(f.local * 2.2) * .065 + (reviewing ? -.13 : -.05));
    if (/R[_ ]?Hand$/i.test(joint.bone.name))
      joint.bone.rotateZ(Math.sin(f.local * 3.1) * .1);
  }
}
function renderFrame({f, data, total, ctx, text, wrap, box, clock}) {
  const id = f.scene.id, t = f.local, current = f.agentStates.findIndex(s => s === "active");
  world.traverse(object => { if (object.userData.textLabel) object.visible = true; });
  stages.forEach((scene, key) => { scene.visible = key === id; });
  animations.forEach(animation => { if (animation.id === id) animation.callback(f); });
  const customerVisible = ["opening", "intake", "human", "communication", "impact"].includes(id);
  moveHuman(customer, id === "human" ? [-9.2, 0, -2.4] : id === "impact" ? [-10.7, 0, -2.4] :
    [id === "communication" ? -7.2 : -7.65, 0, -2.25], customerVisible, f, false);
  moveHuman(manager, id === "human" ? [-4.1, 0, -2.05] : [-6.3, 0, -2.3],
    id === "human" || id === "impact", f, id === "human");
  brand.visible = ["opening", "intake", "impact"].includes(id);
  const held = f.agentStates.includes("held");
  avatars.forEach((avatar, i) => {
    const active = i === current, state = f.agentStates[i];
    const stride = active ? .3 : .08;
    avatar.bot.position.set(avatar.base[0], .16 + Math.sin(t * (active ? 3.3 : 1.7) + i) * stride, avatar.base[2]);
    avatar.bot.rotation.y = Math.sin(t * .9 + i) * (active ? .14 : .05);
    avatar.bot.scale.copy(avatar.restScale).multiplyScalar(active ? 1.15 : 1);
    avatar.arms.forEach((arm, side) => {
      arm.rotation.z = (side ? 1 : -1) * (active ? .35 + Math.sin(t * 3.2) * .3 : .08);
    });
    avatar.eyes.forEach(eye => { eye.scale.y = t % (4.2 + i * .2) > 4 ? .22 : 1; });
    if (avatar.prop) {
      if (i === 1) avatar.prop.rotation.y = t * 1.2;
      if (i === 3) avatar.prop.rotation.z = t * .7;
      if (i === 4) { avatar.prop.position.y = 1.1 + Math.sin(t * 2) * .16; avatar.prop.rotation.z = Math.sin(t) * .18; }
    }
    if (avatar.bot.userData.retryLabel) avatar.bot.userData.retryLabel.visible = id === "recovery";
    avatar.halo.material.emissiveIntensity = active ? .8 + Math.sin(t * 4) * .3 : state === "done" ? .6 : .12;
    dispatchRoutes[i].object.visible = active;
  });
  beacon.material.color.setHex(held ? 0xff897a : 0x8decc8);
  beacon.material.emissive.setHex(held ? 0xff897a : 0x8decc8);
  beacon.scale.setScalar(1 + Math.sin(t * 4) * .17);
  towerHalo.rotation.z = t * .2;
  towerEyes.forEach(eye => { eye.scale.y = t % 6 > 5.75 ? .16 : 1; });
  const target = current >= 0 ? current : f.agentStates.findIndex(s => s === "queued");
  const route = target >= 0 ? dispatchRoutes[target].curve : new THREE.CatmullRomCurve3([
    new THREE.Vector3(-8, 2.2, -.7), new THREE.Vector3(-3, 4, -.4), new THREE.Vector3(9.6, 7.2, -3.4)
  ]);
  packet.visible = id !== "strict";
  packet.position.copy(route.getPoint(t % 3 / 3));
  trails.forEach((piece, i) => {
    piece.visible = packet.visible;
    piece.position.copy(route.getPoint(((t / 3 - (i + 1) * .024) % 1 + 1) % 1));
  });
  confetti.forEach((piece, i) => {
    piece.visible = id === "impact" && f.beat === 1;
    piece.position.set(-9 + (i * 31 % 190) / 10, 8 - ((t * 1.35 + i * .41) % 7.6), -1 + (i * 17 % 60) / 10);
    piece.rotation.set(t + i, t * .6, t * 2 + i);
  });
  const front = ["opening", "intake", "human", "communication"].includes(id);
  camera.position.set((front ? 4 : 8) + Math.sin(t * .32) * 1.2, front ? 9.2 : 10.5, 24 + Math.cos(t * .25) * .8);
  const aim = new THREE.Vector3(front ? -.7 : 0, 4.7, 0);
  const focus = id === "opening" || id === "human" ?
    1 - THREE.MathUtils.smoothstep(t, f.scene.beats[1].start - .6, f.scene.beats[1].start + 1.8) : 0;
  if (focus) {
    camera.position.lerp(new THREE.Vector3(id === "opening" ? -4.5 : -1.5, 6.5, 11.5), focus);
    aim.lerp(new THREE.Vector3(id === "opening" ? -6.9 : -5.4, 2.5, -1), focus);
  }
  for (const set of stages.values())
    if (set.userData.overviewLabel) set.userData.overviewLabel.visible = focus < .3;
  brand.visible = brand.visible && focus < .3;
  camera.lookAt(aim);
  world.updateMatrixWorld(true);
  camera.updateMatrixWorld(true);
  // Cinematic close-ups may crop geometry, but never leave half a word at the frame edge.
  world.traverse(object => {
    if (!object.userData.textLabel || !object.visible) return;
    const point = object.getWorldPosition(new THREE.Vector3()).applyMatrix4(camera.matrixWorldInverse);
    const scale = object.getWorldScale(new THREE.Vector3()), distance = -point.z;
    const halfW = camera.projectionMatrix.elements[0] * scale.x / (2 * distance);
    const halfH = camera.projectionMatrix.elements[5] * scale.y / (2 * distance);
    point.applyMatrix4(camera.projectionMatrix);
    object.visible = distance > 0 && point.x - halfW > -1 && point.x + halfW < 1 &&
      point.y - halfH > -1 && point.y + halfH < 1;
  });
  renderer.render(world, camera);
  ctx.fillStyle = "#0b1725"; ctx.fillRect(0, 0, 1920, 1080);
  text("TürkBizTel / MISSION: ACTIVATE / 3D EDITION", 38, 35, 17, "#9ae7cb", 600);
  text(f.scene.mode === "proposed" ? "PROPOSED AZURE LOCAL DESIGN / NOT DEPLOYED" :
    "SYNTHETIC DEMONSTRATION / NO LIVE NETWORK CHANGES", 1040, 35, 16,
  f.scene.mode === "proposed" ? "#ffcd97" : "#abc7dd", 600);
  text(`${String(f.index + 1).padStart(2, "0")} / ${f.scene.chapter.toUpperCase()}`, 38, 77, 19, "#ffce97", 600);
  text(f.scene.title, 38, 132, 43, "#eff5ff", 600);
  text((f.scene.order ? `ORDER ${f.scene.order} / ` : "") + f.event, 40, 168, 24, "#acd1ff", 500);
  ctx.drawImage(renderer.domElement, 0, 188);
  data.agents.forEach((role, i) => {
    const status = f.agentStates[i], color = `#${COLORS[i].toString(16)}`;
    const x = 36 + i * 379;
    box(x, 892, 360, 40, "#11283dee", status === "active" ? color : "#365067", 8);
    text(`${role} / ${status.toUpperCase()}`, x + 13, 918, 19,
      status === "held" ? "#ffa28e" : color, 600);
  });
  const beat = f.scene.beats[f.beat];
  if (f.local >= beat.start && f.local < beat.start + beat.duration) {
    box(32, 945, 1856, 96, "#061521ef", "#365269", 10);
    wrap(beat.text, 54, 982, 1805, 28, "#f0f6ff", 35);
  }
  text("RENDERED HUMAN CHARACTERS / SYNTHETIC AUTHOR VOICE / ILLUSTRATIVE EVENT PACING", 38, 1065, 14, "#a9c3d6", 500);
  text(`${clock(f.time)} / ${clock(total)}`, 1730, 1065, 18, "#b0ecd6", 600);
  ctx.fillStyle = "#8de7c9"; ctx.fillRect(0, 1076, 1920 * f.time / total, 4);
  const gl = renderer.getContext(), debug = gl.getExtension("WEBGL_debug_renderer_info");
  return {
    scene: id, beat: f.beat, activeAgent: current, agentStates: f.agentStates,
    renderer: "Three.js / real WebGL mesh rendering", perspectiveCamera: camera.isPerspectiveCamera,
    camera: camera.position.toArray(), shadows: renderer.shadowMap.enabled,
    visibleHumans: [customerVisible ? "customer" : null, manager.wrapper.visible ? "manager" : null].filter(Boolean),
    humans: humanEvidence, towerHeight: 10.3, avatarCount: avatars.length,
    geometryTriangles: renderer.info.render.triangles,
    officialIcons: productBadges.filter(b => {
      for (let parent = b.sprite; parent; parent = parent.parent) if (!parent.visible) return false;
      return true;
    }).map(b => ({name: b.name, file: b.file, aspect: b.aspect})),
    gpu: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)
  };
}
async function initialize() {
  [customer, manager] = await Promise.all([
    human("customer", "Business_Male_01.fbx"),
    human("manager", "Business_Female_02.fbx")
  ]);
  await Promise.all(badgeTasks);
  const {texture, aspect} = await svgTexture("/static/solution-turk-telekom.svg", true);
  brand = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, depthWrite: false}));
  brand.position.set(-10.2, 6.1, -4.3);
  brand.scale.set(3.2, 3.2 / aspect, 1);
  world.add(brand);
}
window.ama3DStudio = {
  THREE, renderer, tower, avatars, cube, sphere, cylinder, ring, rod, group, server, desk,
  material, svgTexture, moveHuman,
  get customer() { return customer; },
  get manager() { return manager; }
};
window.amaFilmTheme = {ready: initialize(), render: renderFrame};
