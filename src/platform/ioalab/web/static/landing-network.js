
(() => {
  const canvas = document.getElementById('networkCanvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d', { alpha: true });
  const visual = canvas.parentElement;
  const host = document.querySelector('.hero');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const annotations = [...host.querySelectorAll('.network-annotation')];
  let width = 0, height = 0, dpr = 1, nodes = [], edges = [], adjacency = [], packets = [];
  let pointerX = 0, pointerY = 0, targetX = 0, targetY = 0;
  let cameraYaw = -.47, cameraPitch = .96, last = 0, raf = 0;
  const baseYaw = -.47, basePitch = .96;
  const palette = {
    line: [48, 77, 55],
    node: '#426d4a',
    nodeSoft: '#9bae8e',
    packet: '#548d5d',
    warm: '#c27b58'
  };
  const rand = (a, b) => a + Math.random() * (b - a);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const edgeKey = (a, b) => a < b ? `${a}:${b}` : `${b}:${a}`;

  function resize() {
    const rect = visual.getBoundingClientRect();
    width = Math.max(1, rect.width);
    height = Math.max(1, rect.height);
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    createNetwork();
  }

  // A broad, overlapping density field: local communities emerge, but no isolated left/right camps.
  function densityAt(x, y) {
    const fields = [
      [-.92, -.38, .48, .52, .62], [-.66, .16, .44, .48, .78],
      [-.28, -.34, .46, .43, .70], [.02, .22, .48, .52, .84],
      [.39, -.37, .48, .46, .74], [.73, .22, .46, .48, .82],
      [.99, -.26, .36, .56, .52], [-.12, .72, .43, .32, .48],
      [.47, .70, .48, .32, .43]
    ];
    let sum = 0;
    for (const [cx, cy, sx, sy, weight] of fields) {
      const dx = (x - cx) / sx, dy = (y - cy) / sy;
      sum += weight * Math.exp(-.5 * (dx * dx + dy * dy));
    }
    return clamp(.30 + sum * .36, .30, .98);
  }

  function createNetwork() {
    nodes = []; edges = []; adjacency = []; packets = [];
    const count = clamp(Math.round(width / 4.8), 118, 330);
    let attempts = 0;
    while (nodes.length < count && attempts < count * 100) {
      attempts++;
      const x = rand(-1.22, 1.22), y = rand(-.98, .98);
      if (Math.random() > densityAt(x, y)) continue;
      nodes.push({
        x, y, z: rand(-.62, .62),
        phase: rand(0, Math.PI * 2),
        drift: rand(.0015, .006),
        radius: rand(.9, 2.0),
        degree: 0,
        hub: false
      });
    }
    // Keep a few evenly distributed peripheral nodes so the fabric reaches the edges.
    while (nodes.length < count) {
      nodes.push({ x: rand(-1.22, 1.22), y: rand(-.98, .98), z: rand(-.62, .62), phase: rand(0, Math.PI * 2), drift: rand(.0015, .006), radius: rand(.9, 1.8), degree: 0, hub: false });
    }

    const edgeMap = new Map();
    const addEdge = (a, b, bridge = false) => {
      if (a === b) return;
      const key = edgeKey(a, b);
      if (edgeMap.has(key)) return;
      const na = nodes[a], nb = nodes[b];
      const d = Math.hypot(na.x - nb.x, (na.y - nb.y) * .92, (na.z - nb.z) * .42);
      edgeMap.set(key, { a: Math.min(a, b), b: Math.max(a, b), d, bridge });
    };

    // Local k-nearest links create irregular meshes and short chains with varied density.
    for (let i = 0; i < nodes.length; i++) {
      const near = [];
      for (let j = 0; j < nodes.length; j++) {
        if (i === j) continue;
        const a = nodes[i], b = nodes[j];
        const d = Math.hypot(a.x - b.x, (a.y - b.y) * .92, (a.z - b.z) * .42);
        near.push({ j, d });
      }
      near.sort((a, b) => a.d - b.d);
      const keep = 2 + (Math.random() < .90 ? 1 : 0) + (Math.random() < .45 ? 1 : 0) + (Math.random() < .18 ? 1 : 0);
      for (let k = 0; k < Math.min(keep, near.length); k++) addEdge(i, near[k].j, false);
      // A few nearby cross-links make dense pockets without turning every pocket into a clique.
      for (let k = keep; k < Math.min(near.length, keep + 7); k++) {
        if (near[k].d < .36 && Math.random() < .30) addEdge(i, near[k].j, false);
      }
    }

    // Long-haul links are sparse and distributed; they connect the fabric without a central hub.
    const bridgeTarget = Math.round(nodes.length * .18);
    let bridgeAttempts = 0, bridgeAdded = 0;
    while (bridgeAdded < bridgeTarget && bridgeAttempts < bridgeTarget * 40) {
      bridgeAttempts++;
      const a = Math.floor(rand(0, nodes.length));
      const b = Math.floor(rand(0, nodes.length));
      if (a === b) continue;
      const na = nodes[a], nb = nodes[b];
      const d = Math.hypot(na.x - nb.x, (na.y - nb.y) * .92, (na.z - nb.z) * .42);
      if (d < .48 || d > 1.55) continue;
      const before = edgeMap.size;
      addEdge(a, b, true);
      if (edgeMap.size > before) bridgeAdded++;
    }

    edges = [...edgeMap.values()];
    adjacency = nodes.map(() => []);
    for (let ei = 0; ei < edges.length; ei++) {
      const e = edges[ei];
      e.index = ei;
      nodes[e.a].degree++;
      nodes[e.b].degree++;
      adjacency[e.a].push(e.b);
      adjacency[e.b].push(e.a);
    }
    const degrees = nodes.map(n => n.degree).sort((a, b) => b - a);
    const hubCutoff = degrees[Math.floor(degrees.length * .11)] || 6;
    nodes.forEach(n => { n.hub = n.degree >= hubCutoff; });

    // Packets move through successive links, like work being handed from agent to agent.
    const packetCount = clamp(Math.round(width / 34), 18, 44);
    for (let i = 0; i < packetCount; i++) {
      let from = Math.floor(rand(0, nodes.length));
      let tries = 0;
      while (adjacency[from].length < 2 && tries++ < 30) from = Math.floor(rand(0, nodes.length));
      const choices = adjacency[from];
      const to = choices[Math.floor(rand(0, choices.length))] ?? ((from + 1) % nodes.length);
      packets.push({ from, to, t: Math.random(), speed: rand(.11, .28), size: rand(.9, 1.7), warm: Math.random() < .17 });
    }
  }

  function project(n, yaw, pitch, time) {
    const wobble = reduceMotion ? 0 : time * .00016;
    const x = (n.x + Math.sin(wobble + n.phase) * n.drift) * width * .52;
    const y = (n.y + Math.cos(wobble * .83 + n.phase * 1.3) * n.drift * .75) * height * .61;
    const z = n.z * Math.min(width, height) * .47;
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x1 = x * cy + z * sy;
    const z1 = -x * sy + z * cy;
    const y1 = y * cp - z1 * sp;
    const depth = y * sp + z1 * cp;
    const perspective = 1320 / (1320 + depth);
    return { x: width * .54 + x1 * perspective, y: height * .53 + y1 * perspective, scale: perspective, depth };
  }

  function advancePackets(dt) {
    for (const p of packets) {
      p.t += p.speed * dt;
      if (p.t < 1) continue;
      p.t -= 1;
      const previous = p.from;
      p.from = p.to;
      const options = adjacency[p.from] || [];
      let nextOptions = options.filter(n => n !== previous);
      if (!nextOptions.length) nextOptions = options;
      if (!nextOptions.length) continue;
      // Prefer onward routes, but allow occasional backtracking to avoid mechanical loops.
      p.to = nextOptions[Math.floor(rand(0, nextOptions.length))];
    }
  }

  function draw(time) {
    if (!width || !height) return;
    const dt = last ? Math.min((time - last) / 1000, .04) : 1 / 60;
    last = time;
    pointerX += (targetX - pointerX) * .065;
    pointerY += (targetY - pointerY) * .065;
    cameraYaw += (baseYaw + pointerX * .34 - cameraYaw) * .045;
    cameraPitch += (basePitch - pointerY * .20 - cameraPitch) * .045;

    annotations.forEach((el, i) => {
      el.style.setProperty('--ann-x', (pointerX * (4 + i)) + 'px');
      el.style.setProperty('--ann-y', (pointerY * (3 + i * .4)) + 'px');
    });

    ctx.clearRect(0, 0, width, height);
    const centerX = width * .55, centerY = height * .54;
    const haze = ctx.createRadialGradient(centerX, centerY, 12, centerX, centerY, Math.max(width, height) * .66);
    haze.addColorStop(0, 'rgba(160,190,145,.16)');
    haze.addColorStop(.52, 'rgba(184,202,165,.075)');
    haze.addColorStop(1, 'rgba(245,243,236,0)');
    ctx.fillStyle = haze;
    ctx.fillRect(0, 0, width, height);

    if (!reduceMotion) advancePackets(dt);
    const projected = nodes.map(n => project(n, cameraYaw, cameraPitch, time));

    // Draw long-range paths first, then local links; opacity follows distance and depth.
    const sortedEdges = edges.slice().sort((a, b) => (projected[a.a].depth + projected[a.b].depth) - (projected[b.a].depth + projected[b.b].depth));
    for (const e of sortedEdges) {
      const a = projected[e.a], b = projected[e.b];
      const depthFactor = clamp(1.12 - (a.depth + b.depth) / (Math.min(width, height) * 1.5), .58, 1.28);
      const alpha = e.bridge ? .045 * depthFactor : clamp(.145 - e.d * .18, .045, .12) * depthFactor;
      ctx.strokeStyle = `rgba(${palette.line[0]},${palette.line[1]},${palette.line[2]},${alpha})`;
      ctx.lineWidth = e.bridge ? .72 : (.48 + (1 - clamp(e.d / .5, 0, 1)) * .42);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }

    // A few faint directional traces reinforce that this is a working network, not a static diagram.
    for (const p of packets) {
      const a = projected[p.from], b = projected[p.to];
      if (!a || !b) continue;
      const x = a.x + (b.x - a.x) * p.t;
      const y = a.y + (b.y - a.y) * p.t;
      const tx = a.x + (b.x - a.x) * Math.max(0, p.t - .075);
      const ty = a.y + (b.y - a.y) * Math.max(0, p.t - .075);
      const color = p.warm ? palette.warm : palette.packet;
      ctx.beginPath();
      ctx.strokeStyle = p.warm ? 'rgba(194,123,88,.30)' : 'rgba(84,141,93,.32)';
      ctx.lineWidth = 1.1;
      ctx.moveTo(tx, ty);
      ctx.lineTo(x, y);
      ctx.stroke();
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.arc(x, y, p.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.fillStyle = p.warm ? 'rgba(194,123,88,.12)' : 'rgba(84,141,93,.12)';
      ctx.arc(x, y, p.size * 3.2, 0, Math.PI * 2);
      ctx.fill();
    }

    const order = nodes.map((n, i) => ({ n, p: projected[i] })).sort((a, b) => a.p.depth - b.p.depth);
    for (const { n, p } of order) {
      const pulse = .5 + .5 * Math.sin(time * .0011 + n.phase);
      const r = n.radius * p.scale * (n.hub ? 1.22 : 1);
      if (n.hub) {
        ctx.beginPath();
        ctx.fillStyle = `rgba(73,117,75,${.045 + pulse * .055})`;
        ctx.arc(p.x, p.y, (6 + pulse * 4) * p.scale, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.beginPath();
      ctx.fillStyle = n.hub ? palette.node : palette.nodeSoft;
      ctx.arc(p.x, p.y, r + (n.hub ? pulse * .35 : 0), 0, Math.PI * 2);
      ctx.fill();
      if (n.hub) {
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(56,96,62,.25)';
        ctx.lineWidth = .65;
        ctx.arc(p.x, p.y, r + 2.7 * p.scale, 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    if (!reduceMotion) raf = requestAnimationFrame(draw);
  }

  function setPointer(e) {
    const rect = host.getBoundingClientRect();
    targetX = clamp(((e.clientX - rect.left) / rect.width - .5) * 2, -1, 1);
    targetY = clamp(((e.clientY - rect.top) / rect.height - .5) * 2, -1, 1);
    if (reduceMotion) draw(performance.now());
  }
  host.addEventListener('pointermove', e => { if (e.pointerType === 'mouse') setPointer(e); });
  host.addEventListener('pointerleave', () => { targetX = 0; targetY = 0; if (reduceMotion) draw(performance.now()); });

  const observer = new ResizeObserver(() => { resize(); if (reduceMotion) draw(0); });
  observer.observe(visual);
  resize();
  if (reduceMotion) draw(0); else raf = requestAnimationFrame(draw);

  const toggle = document.getElementById('menuToggle');
  const mobile = document.getElementById('mobileNav');
  toggle.addEventListener('click', () => {
    const open = mobile.classList.toggle('open');
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? '×' : '☰';
  });
  mobile.querySelectorAll('a').forEach(a => a.addEventListener('click', () => {
    mobile.classList.remove('open');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.textContent = '☰';
  }));

  const sections = ['network', 'protocol', 'ecosystem', 'about'].map(id => document.getElementById(id));
  const links = [...document.querySelectorAll('.side-nav a[href^="#"]')];
  const io = new IntersectionObserver(entries => entries.forEach(entry => {
    if (entry.isIntersecting) links.forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + entry.target.id));
  }), { rootMargin: '-35% 0px -55% 0px' });
  sections.forEach(section => io.observe(section));
})();
