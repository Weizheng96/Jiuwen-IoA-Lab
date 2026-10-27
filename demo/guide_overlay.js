/* Editorial explanation only. Never writes platform state, event counters or signatures. */
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const root = document.createElement("div");
  root.id = "ioa-editorial-guide";
  root.dataset.editorialOnly = "true";
  root.style.cssText =
    "position:fixed;inset:0;pointer-events:none;z-index:9000;font-family:Microsoft YaHei,sans-serif";
  const svg = document.createElementNS(NS, "svg");
  svg.style.cssText =
    "position:absolute;width:100%;height:100%;overflow:visible";
  const legend = document.createElement("div");
  legend.textContent = "讲解动画 · 业务状态以平台记录为准";
  legend.style.cssText =
    "position:absolute;left:24px;bottom:118px;background:#07151feb;border:1px dashed #62e4d0;border-radius:8px;padding:9px 12px;color:#a7e8df;font-size:13px";
  const panel = document.createElement("div");
  panel.style.cssText =
    "position:absolute;display:none;background:#071b2af5;border:1px solid #60dccc;border-radius:22px;padding:35px 40px;box-shadow:0 20px 70px #0008;color:#eefcff;box-sizing:border-box;text-align:left";
  root.append(svg, legend, panel);
  document.body.append(root);
  let shot = { targets: [], links: [] },
    refs = {},
    start = performance.now(),
    frame;
  const make = (tag, attributes) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attributes)) n.setAttribute(k, v);
    return n;
  };
  const resolve = (key) =>
    document.querySelector(refs[key] || "#__no_guide_target");
  const rect = (key) => resolve(key)?.getBoundingClientRect();
  function paint(now) {
    svg.replaceChildren();
    const defs = make("defs", {}),
      marker = make("marker", {
        id: "guide-arrow",
        viewBox: "0 0 10 10",
        refX: 8,
        refY: 5,
        markerWidth: 6,
        markerHeight: 6,
        orient: "auto-start-reverse",
      });
    marker.append(make("path", { d: "M0 0 L10 5 L0 10 Z", fill: "#7cf0d6" }));
    defs.append(marker);
    svg.append(defs);
    for (const key of shot.targets) {
      const r = rect(key);
      if (!r || r.width < 1) continue;
      svg.append(
        make("rect", {
          x: r.x - 5,
          y: r.y - 5,
          width: r.width + 10,
          height: r.height + 10,
          rx: 15,
          fill: "#56ead407",
          stroke: "#79ebd8",
          "stroke-width": 2.5,
          "stroke-dasharray": "9 6",
          opacity: 0.7 + 0.25 * Math.sin(now / 500),
        }),
      );
    }
    shot.links.forEach(([a, b, label], i) => {
      const r = rect(a),
        s = rect(b);
      if (!r || !s) return;
      const vertical = Math.abs(r.x - s.x) < 130;
      let x0 = r.x + r.width / 2,
        y0 = r.y + r.height / 2,
        x1 = s.x + s.width / 2,
        y1 = s.y + s.height / 2;
      if (vertical) {
        y0 = r.y + (y1 > y0 ? r.height + 8 : -8);
        y1 = s.y + (y1 > y0 ? -8 : s.height + 8);
      } else {
        x0 = r.x + (x1 > x0 ? r.width + 8 : -8);
        x1 = s.x + (x1 > x0 ? -8 : s.width + 8);
      }
      const mx = (x0 + x1) / 2,
        my = (y0 + y1) / 2 - (vertical ? 0 : 45 + i * 12);
      const path = make("path", {
        d: `M${x0},${y0} Q${mx},${my} ${x1},${y1}`,
        fill: "none",
        stroke: "#7cf0d6",
        "stroke-width": 3,
        "stroke-dasharray": "8 6",
        "marker-end": "url(#guide-arrow)",
      });
      svg.append(path);
      const t = ((now - start) / 2800 + i * 0.35) % 1,
        p = path.getPointAtLength(path.getTotalLength() * t);
      svg.append(make("circle", { cx: p.x, cy: p.y, r: 6, fill: "#c1ffed" }));
      if (Math.abs(x1 - x0) > 180) {
        const text = make("text", {
          x: mx,
          y: my - 10,
          fill: "#ddfff6",
          "font-size": 20,
          "font-weight": 700,
          "text-anchor": "middle",
          "paint-order": "stroke",
          stroke: "#07141f",
          "stroke-width": 7,
        });
        text.textContent = label;
        svg.append(text);
      }
    });
    if (shot.panel) {
      const r = rect("market");
      if (r) {
        panel.style.left = r.x + 20 + "px";
        panel.style.top = r.y + Math.min(r.height * 0.25, 120) + "px";
        panel.style.width = r.width - 40 + "px";
      }
    }
    frame = requestAnimationFrame(paint);
  }
  window.IoaDemoGuide = {
    show(next, selectors) {
      shot = next;
      refs = selectors;
      start = performance.now();
      panel.replaceChildren();
      panel.style.display = next.panel ? "block" : "none";
      (next.panel || []).forEach((line, i) => {
        const p = document.createElement("div");
        p.textContent = line;
        p.style.cssText =
          i === 0
            ? "font-size:34px;font-weight:700;color:#8df2dc;margin-bottom:20px"
            : `font-size:${i === 1 ? 23 : 20}px;line-height:1.9;color:#d2e4ef`;
        panel.append(p);
      });
    },
    dispose() {
      cancelAnimationFrame(frame);
      root.remove();
    },
  };
  frame = requestAnimationFrame(paint);
})();
