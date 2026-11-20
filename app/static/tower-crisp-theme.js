"use strict";

(() => {
  const inherited = window.amaFilmTheme;
  const icons = new Map();
  const ready = Promise.all(["azure-local.svg", "azure-arc.svg", "container-registry.svg", "azure-monitor.svg"]
    .map(file => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => { icons.set(file, image); resolve(); };
      image.onerror = () => reject(new Error(`Official product icon failed: ${file}`));
      image.src = "/static/film3d-assets/" + file;
    })));

  window.amaFilmTheme = {
    ready,
    render(args) {
      const {f, ctx, text, box, arrow, C} = args;
      const cards = f.scene.cards;
      const sceneEvidence = {cardBounds: [], officialIcons: [], humanException: Boolean(f.scene.exception)};
      const drawScene = cards ? () => {
        const shown = cards.map(card => ({...card}));
        if (f.beat === 1 && f.scene.exception) shown[2] = {...f.scene.exception, exception: true};
        const progress = f.progress;
        shown.forEach((card, i) => {
          const x = 58 + i * 416, color = card.exception ? C.amber : f.scene.mode === "proposed" ? C.blue : C.mint;
          box(x, 191, 320, 176, "#142d43", color, 14);
          const headingSize = 21;
          text(card.title, x + 20, 225, headingSize, color, 700);
          const lines = card.body.split("\n");
          lines.forEach((line, n) => text(line, x + 20, 274 + n * 34, 22, C.white, 500));
          ctx.fillStyle = "#29465b"; ctx.fillRect(x + 20, 348, 280, 6);
          ctx.fillStyle = color; ctx.fillRect(x + 20, 348, 280 * (.1 + .9 * progress), 6);
          const productIcons = card.icons || (card.icon ? [card.icon] : []);
          productIcons.forEach((file, n) => {
            const image = icons.get(file), ratio = image.naturalWidth / image.naturalHeight;
            ctx.drawImage(image, x + 257 - (productIcons.length - 1 - n) * 44, 310, 36, 36 / ratio);
            sceneEvidence.officialIcons.push(file);
          });
          ctx.font = `700 ${headingSize}px "Segoe UI", Arial, sans-serif`;
          const headingWidth = ctx.measureText(card.title).width;
          ctx.font = '500 22px "Segoe UI", Arial, sans-serif';
          const bodyWidth = Math.max(...lines.map(line => ctx.measureText(line).width));
          if (headingWidth > 280 || bodyWidth > 280) throw new Error(`Crisp card overflows: ${f.scene.id}`);
          sceneEvidence.cardBounds.push({x, y:191, width:320, height:176, headingWidth, bodyWidth, fontSize:22});
          if (i < shown.length - 1 && !f.scene.parallelLanes) {
            const start = x + 332, end = x + 402;
            arrow([[start, 278], [end, 278]], color);
            box(start + (end - start) * progress - 10, 268, 20, 20, color, color, 5);
          }
        });
        const owner = f.scene.ownerLabel || (f.scene.mode === "proposed" ? "PROPOSED DESIGN / NOT DEPLOYED" :
          f.scene.id === "impact" ? "ASSIGNMENT TARGETS / NOT MEASURED RESULTS" :
            f.scene.id === "recovery" && f.beat === 1 ? "UNKNOWN-FAULT HUMAN TRIAGE / PRODUCTION GATE" :
              f.scene.id === "decomposition" && f.beat === 1 ? "NEW CATALOGUE APPROVAL / PRODUCTION GATE" :
                f.scene.id === "communication" && f.beat === 1 ? "EXTERNAL DELIVERY INCIDENTS / PRODUCTION GATE" :
                  f.scene.id === "standards" ? "SIMPLIFIED SUBSETS / NOT CERTIFIED" : "IMPLEMENTED SIMULATOR / NO LIVE NETWORK CHANGES");
        text(owner, 640, 417, 18, C.amber, 600, "center");
      } : args.drawScene;
      let captionLines = 0;
      const wrap = (...values) => {
        captionLines = args.wrap(...values);
        if (captionLines > 2) throw new Error(`Crisp captions overflow: ${f.scene.id}`);
        return captionLines;
      };
      const evidence = inherited.render({...args, drawScene, wrap});
      return {...evidence, ...sceneEvidence, captionLines, presetNarrator:true, distinctVoice:true};
    }
  };
})();
