"use strict";

/* TürkBizTel · AMA Case Study 30: the guided assignment page.
   One page, six scenes (start, today, architecture, solution, live app demo, live app) and a small hash router, so every scene
   and every slide has a link that can be shared. Page content is built from DOM nodes and textContent only and
   nothing here writes inline styles, so the strict content-security policy holds. */
(() => {
  const SCENES = ["intro", "today", "architecture", "solution", "demo", "app"];
  const NAMES = { today: "Today", architecture: "Architecture", solution: "The solution in action",
    demo: "Live App Demo", app: "Live App" };
  const CHAPTER_KEYS = { 1: "today", 2: "architecture", 3: "solution", 4: "demo", 5: "app" };
  const SLIDES = 16;
  const PUBLIC_PAGES = ["/", "/order", "/track", "/login"];
  const BASE_TITLE = document.title;
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SCROLL = REDUCED ? "auto" : "smooth";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function startPlaying(video) {
    const attempt = video.play();
    if (attempt && attempt.catch) attempt.catch(() => {});
  }

  /* Scroll a row just enough to bring one item into view, centred. Never uses scrollIntoView, so the page itself
     does not jump. */
  function showInRow(row, item) {
    if (row.scrollWidth <= row.clientWidth + 1) return;
    const rowBox = row.getBoundingClientRect();
    const box = item.getBoundingClientRect();
    if (box.left >= rowBox.left && box.right <= rowBox.right) return;
    const left = row.scrollLeft + box.left - rowBox.left - (rowBox.width - box.width) / 2;
    row.scrollTo({ left, behavior: SCROLL });
  }

  function showInColumn(column, item) {
    const columnBox = column.getBoundingClientRect();
    const box = item.getBoundingClientRect();
    if (box.top >= columnBox.top && box.bottom <= columnBox.bottom) return;
    const top = column.scrollTop + box.top - columnBox.top - (columnBox.height - box.height) / 2;
    column.scrollTo({ top, behavior: SCROLL });
  }

  const clock = (seconds) => {
    const whole = Math.round(seconds);
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
  };

  /* ---------- Narration text from a WebVTT file ---------- */

  const CUE_TIMES = /(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})/;
  const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', nbsp: " ", "#39": "'" };

  const toSeconds = (hours, minutes, seconds, millis) =>
    Number(hours || 0) * 3600 + Number(minutes) * 60 + Number(seconds) + Number(millis) / 1000;

  function parseVtt(source) {
    const cues = [];
    for (const block of source.replace(/\r/g, "").split(/\n{2,}/)) {
      const lines = block.split("\n");
      const at = lines.findIndex((line) => CUE_TIMES.test(line));
      if (at < 0) continue;
      const m = CUE_TIMES.exec(lines[at]);
      const text = lines
        .slice(at + 1)
        .map((line) => line.replace(/<[^>]*>/g, "").replace(/&(amp|lt|gt|quot|nbsp|#39);/g, (_, name) => ENTITIES[name]).trim())
        .filter(Boolean)
        .join(" ");
      if (text) cues.push({ start: toSeconds(m[1], m[2], m[3], m[4]), text });
    }
    return cues;
  }

  /* Index of the last start time at or before `time`, or -1. `starts` is in ascending order. */
  function indexAt(starts, time) {
    let found = -1;
    for (let i = 0; i < starts.length && starts[i] <= time + 0.1; i += 1) found = i;
    return found;
  }

  /* ---------- Films ---------- */

  function setupFilm(cinema) {
    const scene = cinema.closest(".scene");
    const video = $("video", cinema);
    const upNext = $(".up-next", cinema);
    const failure = $(".video-fail", scene);
    const captionButton = $("[data-cc]", scene);
    const chipRow = $(".chapters", scene);
    const chips = $$(".chip", scene);
    const chipStarts = chips.map((chip) => Number.parseFloat(chip.dataset.t));
    const transcript = $(".transcript", scene);
    const transcriptBox = $(".transcript-body", transcript);
    const track = video.textTracks.length ? video.textTracks[0] : null;
    let started = false;
    let activeChip = -1;
    let cueStarts = [];
    let cueNodes = [];
    let activeCue = -1;
    let transcriptLoaded = false;

    const prepare = () => {
      if (video.preload === "none") video.preload = "metadata";
    };

    const setChip = (index) => {
      if (index === activeChip) return;
      activeChip = index;
      chips.forEach((chip, i) => {
        if (i === index) chip.setAttribute("aria-current", "true");
        else chip.removeAttribute("aria-current");
      });
      if (index >= 0) showInRow(chipRow, chips[index].parentElement);
    };

    const setCue = (index) => {
      if (!cueNodes.length || index === activeCue) return;
      if (activeCue >= 0) {
        cueNodes[activeCue].classList.remove("is-active");
        cueNodes[activeCue].removeAttribute("aria-current");
      }
      activeCue = index;
      if (index < 0) return;
      cueNodes[index].classList.add("is-active");
      cueNodes[index].setAttribute("aria-current", "true");
      if (transcript.open) showInColumn(transcriptBox, cueNodes[index]);
    };

    const revealFilm = () => {
      const box = cinema.getBoundingClientRect();
      if (box.top < 72 || box.bottom > window.innerHeight) cinema.scrollIntoView({ block: "nearest", behavior: SCROLL });
    };

    const seek = (seconds) => {
      prepare();
      started = true;
      upNext.hidden = true;
      video.currentTime = seconds;
      startPlaying(video);
      setChip(indexAt(chipStarts, seconds));
      revealFilm();
    };

    chips.forEach((chip, i) => chip.addEventListener("click", () => seek(chipStarts[i])));

    ["play", "seeking"].forEach((type) => video.addEventListener(type, () => { started = true; }));
    video.addEventListener("timeupdate", () => {
      if (!started) return;
      setChip(indexAt(chipStarts, video.currentTime));
      setCue(indexAt(cueStarts, video.currentTime));
    });

    /* The film never moves on by itself: at the end it offers the next chapter or a replay. */
    video.addEventListener("ended", () => {
      upNext.hidden = false;
      if (cinema.contains(document.activeElement)) $("a", upNext).focus({ preventScroll: true });
    });
    ["play", "playing", "seeking", "emptied"].forEach((type) => video.addEventListener(type, () => { upNext.hidden = true; }));
    $("[data-replay]", upNext).addEventListener("click", () => {
      started = true;
      upNext.hidden = true;
      video.currentTime = 0;
      video.focus({ preventScroll: true });
      startPlaying(video);
    });

    const showFailure = () => { failure.hidden = false; };
    video.addEventListener("error", showFailure);
    $$("source", video).forEach((source) => source.addEventListener("error", showFailure));
    video.addEventListener("loadeddata", () => { failure.hidden = true; });

    if (track && captionButton) {
      track.mode = "hidden";
      const syncCaptions = () => captionButton.setAttribute("aria-pressed", String(track.mode === "showing"));
      captionButton.addEventListener("click", () => {
        track.mode = track.mode === "showing" ? "hidden" : "showing";
        syncCaptions();
      });
      video.textTracks.addEventListener("change", syncCaptions);
      syncCaptions();
    } else if (captionButton) {
      captionButton.hidden = true;
    }

    const loadTranscript = async () => {
      if (transcriptLoaded) return;
      try {
        const response = await fetch(transcriptBox.dataset.vtt, { credentials: "same-origin" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const cues = parseVtt(await response.text());
        if (!cues.length) throw new Error("No narration found");
        const list = el("ol", "cues");
        cueNodes = cues.map((cue) => {
          const button = el("button", "cue");
          button.type = "button";
          button.append(el("span", "cue-time", clock(cue.start)), el("span", "cue-text", cue.text));
          button.addEventListener("click", () => seek(cue.start));
          const item = el("li");
          item.append(button);
          list.append(item);
          return button;
        });
        cueStarts = cues.map((cue) => cue.start);
        transcriptBox.replaceChildren(list);
        transcriptLoaded = true;
        activeCue = -1;
        if (started) setCue(indexAt(cueStarts, video.currentTime));
      } catch (_) {
        transcriptBox.replaceChildren(el("p", "transcript-wait", "The narration could not be loaded. Turn on captions in the player to follow along."));
      }
    };
    transcript.addEventListener("toggle", () => {
      if (transcript.open) loadTranscript();
    });

    return {
      name: scene.dataset.scene,
      video,
      prepare,
      pause: () => video.pause(),
      playing: () => !video.paused && !video.ended,
    };
  }

  /* ---------- Architecture deck ---------- */

  function setupDeck() {
    const stageBox = $("#deck-stage");
    const image = $("#deck-img");
    const caption = $("#deck-caption");
    const counter = $("#deck-count");
    const previous = $("#deck-prev");
    const next = $("#deck-next");
    const fullButton = $("#deck-full");
    const openLink = $("#deck-open");
    const strip = $("#filmstrip");
    const thumbs = $$("button[data-slide]", strip);
    const acts = $$(".act");
    let current = 0;
    let swapToken = 0;

    thumbs.forEach((thumb) => thumb.setAttribute("aria-label", `Slide ${thumb.dataset.slide}: ${thumb.dataset.title}`));

    const updateEdges = () => {
      const handOff = !document.fullscreenElement;
      const atStart = handOff && current === 1;
      const atEnd = handOff && current === SLIDES;
      previous.classList.toggle("is-edge", atStart);
      next.classList.toggle("is-edge", atEnd);
      previous.setAttribute("aria-label", atStart ? "Back to the film: today" : "Previous slide");
      next.setAttribute("aria-label", atEnd ? "Next chapter: the solution in action" : "Next slide");
    };

    /* Load the new slide before swapping it in, so the stage never flashes empty. */
    const swapImage = (thumb, direction, animate) => {
      swapToken += 1;
      const token = swapToken;
      const probe = new Image();
      let applied = false;
      const apply = () => {
        if (applied || token !== swapToken) return;
        applied = true;
        image.src = thumb.dataset.src;
        image.alt = thumb.dataset.alt;
        if (!animate) return;
        stageBox.dataset.dir = direction < 0 ? "back" : "fwd";
        image.classList.remove("is-new");
        void image.offsetWidth;
        image.classList.add("is-new");
      };
      probe.addEventListener("load", apply);
      probe.addEventListener("error", apply);
      probe.src = thumb.dataset.src;
      if (probe.complete) apply();
    };

    const show = (wanted, options = {}) => {
      const number = Math.min(SLIDES, Math.max(1, Math.trunc(Number(wanted)) || 1));
      if (number === current) return;
      const first = current === 0;
      const direction = options.direction || (number < current ? -1 : 1);
      current = number;
      const thumb = thumbs[number - 1];
      swapImage(thumb, direction, !first && !options.quiet);
      counter.textContent = `${number} / ${SLIDES}`;
      const kicker = el("span", "cap-kicker", `Slide ${number} of ${SLIDES} · ${thumb.dataset.kicker}`);
      caption.replaceChildren(kicker, document.createTextNode(" "), el("strong", "", thumb.dataset.title));
      thumbs.forEach((item, i) => {
        if (i + 1 === number) item.setAttribute("aria-current", "true");
        else item.removeAttribute("aria-current");
      });
      acts.forEach((act) => {
        const inside = number >= Number(act.dataset.slide) && number <= Number(act.dataset.last);
        if (inside) act.setAttribute("aria-current", "true");
        else act.removeAttribute("aria-current");
      });
      openLink.href = thumb.dataset.src;
      showInRow(strip, thumb.parentElement);
      updateEdges();
      if (!first && !options.quiet) {
        [number - 1, number + 1].forEach((neighbour) => {
          if (neighbour >= 1 && neighbour <= SLIDES) new Image().src = thumbs[neighbour - 1].dataset.src;
        });
      }
      if (options.hash) history.replaceState(null, "", `#/architecture/${number}`);
    };

    /* At either end the arrows hand over to the neighbouring chapter, except in full screen or when a key is
       held down, so a long key press never skips a chapter by accident. */
    const step = (direction, repeated) => {
      const target = current + direction;
      if (target < 1 || target > SLIDES) {
        if (repeated || document.fullscreenElement) return;
        window.location.hash = target < 1 ? "#/today" : "#/solution";
        return;
      }
      show(target, { direction, hash: true });
    };

    previous.addEventListener("click", () => step(-1, false));
    next.addEventListener("click", () => step(1, false));
    thumbs.forEach((thumb) => thumb.addEventListener("click", () => show(thumb.dataset.slide, { hash: true })));
    acts.forEach((act) => act.addEventListener("click", () => show(act.dataset.slide, { hash: true })));

    let swipe = null;
    stageBox.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse" || !event.isPrimary || event.target.closest("button")) return;
      swipe = { id: event.pointerId, x: event.clientX, y: event.clientY };
    });
    stageBox.addEventListener("pointerup", (event) => {
      if (!swipe || swipe.id !== event.pointerId) return;
      const dx = event.clientX - swipe.x;
      const dy = event.clientY - swipe.y;
      swipe = null;
      if (Math.abs(dx) > 48 && Math.abs(dx) > 1.4 * Math.abs(dy)) step(dx < 0 ? 1 : -1, false);
    });
    stageBox.addEventListener("pointercancel", () => { swipe = null; });

    const toggleFullscreen = () => {
      if (!document.fullscreenEnabled) return;
      const request = document.fullscreenElement ? document.exitFullscreen() : stageBox.requestFullscreen();
      Promise.resolve(request).catch(() => {});
    };
    if (document.fullscreenEnabled && stageBox.requestFullscreen) {
      fullButton.addEventListener("click", toggleFullscreen);
      document.addEventListener("fullscreenchange", () => {
        const on = document.fullscreenElement === stageBox;
        fullButton.setAttribute("aria-pressed", String(on));
        fullButton.setAttribute("aria-label", on ? "Exit full screen" : "Full screen");
        updateEdges();
      });
    } else {
      fullButton.hidden = true;
    }

    show(1, { quiet: true });
    return { show, step, toggleFullscreen, current: () => current };
  }

  /* ---------- Live app frame ---------- */

  function setupAppFrame() {
    const frame = $("#app-frame");
    const address = $("#app-address");
    const openLink = $("#app-open");
    const segments = $$(".seg");

    /* Pages that need a sign-in open the sign-in page in a new tab: every tab keeps its own session. */
    const reflect = (path) => {
      const url = new URL(path, window.location.origin);
      const fragment = url.pathname === "/login" && url.hash === "#staff" ? "#staff" : "";
      const selected = url.pathname === "/login" && !fragment ? "/" : url.pathname + fragment;
      address.textContent = url.pathname + url.search + fragment;
      openLink.href = PUBLIC_PAGES.includes(url.pathname) ? url.pathname + url.search + fragment : "/";
      segments.forEach((segment) => segment.setAttribute("aria-pressed", String(segment.dataset.frame === selected)));
    };

    segments.forEach((segment) => segment.addEventListener("click", () => {
      frame.src = segment.dataset.frame;
      reflect(segment.dataset.frame);
    }));

    frame.addEventListener("load", () => {
      let where;
      try {
        where = frame.contentWindow.location;
        const sync = () => reflect(where.pathname + where.search + where.hash);
        frame.contentWindow.addEventListener("hashchange", sync);
        frame.contentWindow.addEventListener("turkbiztel:navigate", sync);
      } catch (_) {
        return;
      }
      if (!where || where.href === "about:blank") return;
      reflect(where.pathname + where.search + where.hash);
    });

    return { load: () => { if (!frame.getAttribute("src")) frame.src = frame.dataset.src; } };
  }

  /* ---------- Service-team entry ---------- */

  /* The service team signs in with a Microsoft work account on a single-sign-on deployment and with an access
     token otherwise. The app says which, so the page describes the entry that is really there. */
  function describeStaffEntry() {
    const link = $("#staff-entry");
    const note = $("#staff-entry-note");
    if (!link || !note || !window.fetch) return;
    fetch("/api/auth/me", { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((reply) => (reply.ok ? reply.json() : null))
      .then((me) => {
        if (!me || me.staffAuth !== "sso") return;
        link.textContent = "Sign in with your Microsoft work account";
        note.textContent = ". Your role decides what you can do, and your name is recorded with every change you "
          + "make to an order. Partner sign-in details are shared with the submission, never on this page.";
      })
      .catch(() => {});
  }

  /* ---------- Count-up on the revenue figure ---------- */

  let countRun = 0;
  function countUp() {
    countRun += 1;
    const run = countRun;
    $$(".count[data-count-to]").forEach((node) => {
      const target = Number(node.dataset.countTo);
      if (!Number.isFinite(target)) return;
      if (REDUCED) {
        node.textContent = String(target);
        return;
      }
      const began = performance.now();
      const tick = (now) => {
        if (run !== countRun) return;
        const progress = Math.min(1, (now - began) / 1400);
        node.textContent = String(Math.round(target * (1 - (1 - progress) ** 3)));
        if (progress < 1) window.requestAnimationFrame(tick);
      };
      node.textContent = "0";
      window.requestAnimationFrame(tick);
    });
  }

  /* ---------- Scenes and routing ---------- */

  const page = document.body;
  const stage = $("#stage");
  const scenes = $$(".scene");
  const stops = $$(".stop[data-stop]");
  const films = $$(".cinema").map(setupFilm);
  const deck = setupDeck();
  const appFrame = setupAppFrame();
  describeStaffEntry();
  let scene = "";

  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  function parseHash() {
    const match = /^#\/?([a-z]+)(?:\/(\d+))?$/.exec(window.location.hash);
    if (!match || !SCENES.includes(match[1])) return null;
    return { scene: match[1], slide: match[2] ? Number(match[2]) : 0 };
  }

  function enter(name, initial) {
    const before = SCENES.indexOf(scene);
    const index = SCENES.indexOf(name);
    scene = name;
    films.forEach((film) => film.pause());
    if (document.fullscreenElement) Promise.resolve(document.exitFullscreen()).catch(() => {});
    page.dataset.dir = before > index ? "back" : "fwd";
    page.dataset.scene = name;
    scenes.forEach((node) => { node.hidden = node.dataset.scene !== name; });
    stops.forEach((stop) => {
      const at = SCENES.indexOf(stop.dataset.stop);
      stop.classList.toggle("is-done", at < index);
      stop.classList.toggle("is-current", at === index);
      if (at === index) stop.setAttribute("aria-current", "step");
      else stop.removeAttribute("aria-current");
    });
    document.title = name === "intro" ? BASE_TITLE : `${NAMES[name]} · AMA Case Study 30 | TürkBizTel`;
    if (name === "intro") {
      deck.show(1, { quiet: true });
      countUp();
    }
    if (name === "app") appFrame.load();
    films.filter((film) => film.name === name).forEach((film) => film.prepare());
    if (initial) return;
    window.scrollTo(0, 0);
    const heading = $(`#h-${name}`);
    if (heading) heading.focus({ preventScroll: true });
  }

  function render(initial) {
    const route = parseHash() || (initial ? { scene: "intro", slide: 0 } : null);
    if (!route) return;
    if (route.scene !== scene) enter(route.scene, initial);
    if (route.scene !== "architecture") return;
    deck.show(route.slide || deck.current());
    if (route.slide && route.slide !== deck.current()) history.replaceState(null, "", `#/architecture/${deck.current()}`);
  }

  window.addEventListener("hashchange", () => render(false));

  $(".skip-link").addEventListener("click", (event) => {
    event.preventDefault();
    stage.focus();
  });

  /* ← → move between slides (or chapters), 1–5 jump to a chapter, F toggles full screen on the deck. The keys
     are left alone while a video, a form control or the app frame has focus, and while a film is playing. */
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.target.closest && event.target.closest("video, input, select, textarea, iframe, [contenteditable]")) return;
    const key = event.key;
    if ((key === "ArrowRight" || key === "ArrowLeft") && !event.shiftKey) {
      const direction = key === "ArrowRight" ? 1 : -1;
      if (scene === "architecture") {
        event.preventDefault();
        deck.step(direction, event.repeat);
        return;
      }
      if (event.repeat || films.some((film) => film.playing())) return;
      const target = SCENES[SCENES.indexOf(scene) + direction];
      if (target) {
        event.preventDefault();
        window.location.hash = `#/${target}`;
      }
      return;
    }
    if (CHAPTER_KEYS[key] && !event.shiftKey) {
      window.location.hash = `#/${CHAPTER_KEYS[key]}`;
      return;
    }
    if ((key === "f" || key === "F") && scene === "architecture") {
      event.preventDefault();
      deck.toggleFullscreen();
    }
  });

  render(true);
})();
