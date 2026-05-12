/* ========================================
   MAD Trace Visualizer — Application Logic
   ======================================== */

const AGENT_COLORS = [
    '#8B5CF6','#3B82F6','#10B981','#F59E0B','#EC4899',
    '#06B6D4','#F97316','#6366F1','#14B8A6','#EF4444',
    '#A855F7','#0EA5E9','#22C55E','#FACC15','#F43F5E'
];

let allTraces = [];
let currentTrace = null;
let graphState = { zoom: 1, panX: 0, panY: 0, dragging: false, lastX: 0, lastY: 0, nodes: [], hoveredNode: null };
let seqState = { playing: false, step: 0, timer: null };

// ========== Data Loading ==========
async function loadData() {
    try {
        const resp = await fetch('processed_traces.json');
        allTraces = await resp.json();
        initFilters();
        showQuickStats();
        document.getElementById('loading-overlay').classList.add('hidden');
    } catch (e) {
        console.error('Failed to load data:', e);
        document.querySelector('.loader p').textContent = 'Error loading data. Ensure processed_traces.json exists.';
    }
}

// ========== Filters ==========
function initFilters() {
    const masSet = [...new Set(allTraces.map(t => t.mas_name))].sort();
    const benchSet = [...new Set(allTraces.map(t => t.benchmark_name))].sort();
    const llmSet = [...new Set(allTraces.map(t => t.llm_name))].sort();

    fillSelect('filter-mas', masSet);
    fillSelect('filter-benchmark', benchSet);
    fillSelect('filter-llm', llmSet);

    document.getElementById('filter-mas').addEventListener('change', updateTraceList);
    document.getElementById('filter-benchmark').addEventListener('change', updateTraceList);
    document.getElementById('filter-llm').addEventListener('change', updateTraceList);
    document.getElementById('filter-trace').addEventListener('change', onTraceSelected);
    document.getElementById('trace-search').addEventListener('input', onSearchInput);

    const stats = document.getElementById('header-stats');
    stats.innerHTML = `
        <div class="header-stat"><span class="stat-value">${allTraces.length}</span> traces</div>
        <div class="header-stat"><span class="stat-value">${masSet.length}</span> systems</div>
        <div class="header-stat"><span class="stat-value">${benchSet.length}</span> benchmarks</div>`;

    updateTraceList();
}

function fillSelect(id, values) {
    const sel = document.getElementById(id);
    values.forEach(v => { const o = document.createElement('option'); o.value = v; o.textContent = v; sel.appendChild(o); });
}

function getFiltered() {
    let f = allTraces;
    const mas = document.getElementById('filter-mas').value;
    const bench = document.getElementById('filter-benchmark').value;
    const llm = document.getElementById('filter-llm').value;
    if (mas !== 'all') f = f.filter(t => t.mas_name === mas);
    if (bench !== 'all') f = f.filter(t => t.benchmark_name === bench);
    if (llm !== 'all') f = f.filter(t => t.llm_name === llm);
    return f;
}

function updateTraceList() {
    const filtered = getFiltered();
    const sel = document.getElementById('filter-trace');
    sel.innerHTML = '<option value="">Select a trace (' + filtered.length + ' available)...</option>';
    filtered.forEach(t => {
        const o = document.createElement('option');
        o.value = t.id;
        const agentCount = t.agents.length;
        o.textContent = `#${t.trace_id} — ${t.mas_name} / ${t.benchmark_name} (${agentCount} agents)`;
        sel.appendChild(o);
    });
}

function onSearchInput(e) {
    const q = e.target.value.toLowerCase().trim();
    if (!q) { updateTraceList(); return; }
    const filtered = getFiltered().filter(t =>
        String(t.trace_id).includes(q) || (t.trace_key && t.trace_key.toLowerCase().includes(q)) ||
        t.mas_name.toLowerCase().includes(q) || t.benchmark_name.toLowerCase().includes(q)
    );
    const sel = document.getElementById('filter-trace');
    sel.innerHTML = '<option value="">Search results (' + filtered.length + ')...</option>';
    filtered.forEach(t => {
        const o = document.createElement('option');
        o.value = t.id;
        o.textContent = `#${t.trace_id} — ${t.mas_name} / ${t.benchmark_name}`;
        sel.appendChild(o);
    });
}

function onTraceSelected() {
    const id = document.getElementById('filter-trace').value;
    if (!id) { hideVis(); return; }
    currentTrace = allTraces[parseInt(id)];
    showVis();
}

function hideVis() {
    currentTrace = null;
    document.getElementById('main-content').classList.add('hidden');
    document.getElementById('trace-info').classList.add('hidden');
    document.getElementById('empty-state').classList.remove('hidden');
    stopSeqAnimation();
}

function showVis() {
    document.getElementById('empty-state').classList.add('hidden');
    document.getElementById('main-content').classList.remove('hidden');
    document.getElementById('trace-info').classList.remove('hidden');

    const t = currentTrace;
    document.getElementById('info-mas').textContent = '⚙ ' + t.mas_name;
    document.getElementById('info-benchmark').textContent = '📊 ' + t.benchmark_name;
    document.getElementById('info-llm').textContent = '🤖 ' + t.llm_name;
    document.getElementById('info-agents').textContent = '👥 ' + t.agents.length + ' agents';
    document.getElementById('info-edges').textContent = '🔗 ' + t.edges.length + ' edges';
    document.getElementById('info-steps').textContent = '📝 ' + t.sequence.length + ' steps';

    renderGraph();
    buildSequenceDiagram();
}

function showQuickStats() {
    const masGroups = {};
    allTraces.forEach(t => { masGroups[t.mas_name] = (masGroups[t.mas_name] || 0) + 1; });
    const container = document.getElementById('quick-stats');
    container.innerHTML = Object.entries(masGroups).sort((a,b) => b[1]-a[1]).map(([name, count]) =>
        `<div class="quick-stat"><div class="quick-stat-value">${count}</div><div class="quick-stat-label">${name}</div></div>`
    ).join('');
}

// ========== Graph Rendering ==========
function renderGraph() {
    const canvas = document.getElementById('graph-canvas');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const t = currentTrace;
    const agentNames = t.agents;
    if (agentNames.length === 0) { ctx.fillStyle='#606078'; ctx.font='14px Inter'; ctx.textAlign='center'; ctx.fillText('No agents found in this trace', rect.width/2, rect.height/2); return; }

    // Layout nodes in a circle
    const cx = rect.width / 2, cy = rect.height / 2;
    const radius = Math.min(cx, cy) * 0.55;
    const colorMap = {};
    graphState.nodes = agentNames.map((name, i) => {
        const angle = (i / agentNames.length) * Math.PI * 2 - Math.PI / 2;
        const color = AGENT_COLORS[i % AGENT_COLORS.length];
        colorMap[name] = color;
        const nodeR = 26;
        return { name, x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius, r: nodeR, color };
    });

    // Reset pan/zoom
    graphState.zoom = 1; graphState.panX = 0; graphState.panY = 0;
    drawGraph(ctx, rect.width, rect.height);
    buildLegend(colorMap);
}

function drawGraph(ctx, w, h) {
    const t = currentTrace;
    const nodes = graphState.nodes;
    const nodeMap = {};
    nodes.forEach(n => nodeMap[n.name] = n);

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(graphState.panX, graphState.panY);
    ctx.scale(graphState.zoom, graphState.zoom);

    // Draw edges
    const drawnEdges = new Set();
    t.edges.forEach(e => {
        const from = nodeMap[e.from], to = nodeMap[e.to];
        if (!from || !to) return;
        const key = [e.from, e.to].sort().join('→');
        const isBidirectional = drawnEdges.has(key);
        if (!isBidirectional) drawnEdges.add(key);

        const dx = to.x - from.x, dy = to.y - from.y;
        const dist = Math.sqrt(dx*dx + dy*dy);
        const ux = dx/dist, uy = dy/dist;
        const startX = from.x + ux * from.r, startY = from.y + uy * from.r;
        const endX = to.x - ux * to.r, endY = to.y - uy * to.r;

        // Offset for bidirectional
        const offset = isBidirectional ? 4 : 0;
        const nx = -uy * offset, ny = ux * offset;

        ctx.beginPath();
        ctx.moveTo(startX + nx, startY + ny);
        ctx.lineTo(endX + nx, endY + ny);
        ctx.strokeStyle = from.color + '40';
        ctx.lineWidth = 1.8;
        ctx.stroke();

        // Arrowhead
        const aLen = 8, aW = 4;
        const ax = endX + nx, ay = endY + ny;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(ax - ux * aLen + uy * aW, ay - uy * aLen - ux * aW);
        ctx.lineTo(ax - ux * aLen - uy * aW, ay - uy * aLen + ux * aW);
        ctx.closePath();
        ctx.fillStyle = from.color + '60';
        ctx.fill();
    });

    // Draw nodes
    nodes.forEach(n => {
        const isHovered = graphState.hoveredNode === n.name;
        const r = isHovered ? n.r + 4 : n.r;

        // Glow
        if (isHovered) {
            ctx.beginPath(); ctx.arc(n.x, n.y, r + 8, 0, Math.PI * 2);
            const glow = ctx.createRadialGradient(n.x, n.y, r, n.x, n.y, r + 8);
            glow.addColorStop(0, n.color + '30'); glow.addColorStop(1, 'transparent');
            ctx.fillStyle = glow; ctx.fill();
        }

        // Body
        ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        const grad = ctx.createRadialGradient(n.x - r*0.3, n.y - r*0.3, 0, n.x, n.y, r);
        grad.addColorStop(0, n.color + 'CC'); grad.addColorStop(1, n.color + '80');
        ctx.fillStyle = grad; ctx.fill();
        ctx.strokeStyle = n.color; ctx.lineWidth = 2; ctx.stroke();

        // Label
        ctx.fillStyle = '#fff'; ctx.font = `${isHovered ? '600' : '500'} ${Math.max(9, 12 - Math.floor(n.name.length / 8))}px Inter`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        const displayName = n.name.length > 18 ? n.name.slice(0, 16) + '…' : n.name;
        ctx.fillText(displayName, n.x, n.y);
    });

    ctx.restore();
}

function buildLegend(colorMap) {
    const legend = document.getElementById('graph-legend');
    legend.innerHTML = Object.entries(colorMap).map(([name, color]) => {
        return `<div class="legend-item"><div class="legend-dot" style="background:${color}"></div><span class="legend-name">${name}</span></div>`;
    }).join('');
}

// ========== Graph Interactions ==========
function setupGraphInteractions() {
    const canvas = document.getElementById('graph-canvas');

    canvas.addEventListener('mousedown', e => {
        graphState.dragging = true; graphState.lastX = e.clientX; graphState.lastY = e.clientY;
    });
    canvas.addEventListener('mousemove', e => {
        if (graphState.dragging) {
            graphState.panX += e.clientX - graphState.lastX;
            graphState.panY += e.clientY - graphState.lastY;
            graphState.lastX = e.clientX; graphState.lastY = e.clientY;
            redrawGraph();
        }
        handleHover(e);
    });
    canvas.addEventListener('mouseup', () => { graphState.dragging = false; });
    canvas.addEventListener('mouseleave', () => { graphState.dragging = false; graphState.hoveredNode = null; redrawGraph(); hideTooltip(); });
    canvas.addEventListener('wheel', e => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        graphState.zoom = Math.max(0.3, Math.min(3, graphState.zoom * delta));
        redrawGraph();
    }, { passive: false });

    document.getElementById('btn-zoom-in').addEventListener('click', () => { graphState.zoom = Math.min(3, graphState.zoom * 1.2); redrawGraph(); });
    document.getElementById('btn-zoom-out').addEventListener('click', () => { graphState.zoom = Math.max(0.3, graphState.zoom / 1.2); redrawGraph(); });
    document.getElementById('btn-reset').addEventListener('click', () => { graphState.zoom = 1; graphState.panX = 0; graphState.panY = 0; redrawGraph(); });
}

function handleHover(e) {
    const canvas = document.getElementById('graph-canvas');
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - graphState.panX) / graphState.zoom;
    const my = (e.clientY - rect.top - graphState.panY) / graphState.zoom;

    let found = null;
    for (const n of graphState.nodes) {
        const dx = mx - n.x, dy = my - n.y;
        if (dx*dx + dy*dy < n.r * n.r) { found = n; break; }
    }

    if (found) {
        graphState.hoveredNode = found.name;
        showTooltip(e.clientX, e.clientY, found);
    } else {
        graphState.hoveredNode = null;
        hideTooltip();
    }
    redrawGraph();
}

function showTooltip(x, y, node) {
    const tip = document.getElementById('tooltip');
    tip.classList.remove('hidden');
    tip.innerHTML = `<div class="tooltip-title" style="color:${node.color}">${node.name}</div>`;
    tip.style.left = (x + 12) + 'px'; tip.style.top = (y - 10) + 'px';
}

function hideTooltip() { document.getElementById('tooltip').classList.add('hidden'); }

function redrawGraph() {
    if (!currentTrace) return;
    const canvas = document.getElementById('graph-canvas');
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    drawGraph(ctx, rect.width, rect.height);
}

// ========== Sequence Diagram ==========
function buildSequenceDiagram() {
    const container = document.getElementById('sequence-diagram');
    const t = currentTrace;
    const seq = t.sequence;

    document.getElementById('seq-total').textContent = seq.length;
    document.getElementById('seq-current').textContent = '0';

    if (seq.length === 0) {
        container.innerHTML = '<div style="text-align:center;color:#606078;padding:3rem;font-size:0.9rem;">No sequence data available for this trace.</div>';
        return;
    }

    // Collect agents involved in sequence
    const seqAgents = [];
    const seqAgentSet = new Set();
    seq.forEach(s => {
        if (!seqAgentSet.has(s.from)) { seqAgentSet.add(s.from); seqAgents.push(s.from); }
        if (!seqAgentSet.has(s.to)) { seqAgentSet.add(s.to); seqAgents.push(s.to); }
    });

    const colorMap = {};
    seqAgents.forEach((a, i) => colorMap[a] = AGENT_COLORS[i % AGENT_COLORS.length]);
    const colWidth = Math.max(100, Math.min(160, (container.clientWidth - 40) / seqAgents.length));

    // Header
    let html = '<div class="seq-header">';
    html += '<div style="width:28px;flex-shrink:0"></div>';
    seqAgents.forEach(a => {
        const c = colorMap[a];
        const short = a.length > 16 ? a.slice(0, 14) + '…' : a;
        html += `<div class="seq-agent-col" style="width:${colWidth}px;min-width:${colWidth}px;max-width:${colWidth}px"><div class="seq-agent-name" style="background:${c}90" title="${a}">${short}</div></div>`;
    });
    html += '</div>';

    // Rows
    html += '<div class="seq-rows">';
    seq.forEach((s, i) => {
        const fromIdx = seqAgents.indexOf(s.from);
        const toIdx = seqAgents.indexOf(s.to);
        if (fromIdx < 0 || toIdx < 0) return;
        const color = colorMap[s.from];
        const label = s.label || s.type || '';
        const shortLabel = label.length > 24 ? label.slice(0, 22) + '…' : label;

        html += `<div class="seq-row" data-step="${i}">`;
        html += `<div class="seq-step-number">${i + 1}</div>`;

        // Cells with lifelines
        seqAgents.forEach((a, j) => {
            html += `<div class="seq-row-cell" style="width:${colWidth}px;min-width:${colWidth}px;max-width:${colWidth}px"></div>`;
        });

        // Arrow overlay
        const minIdx = Math.min(fromIdx, toIdx);
        const maxIdx = Math.max(fromIdx, toIdx);
        const leftPx = 28 + minIdx * colWidth + colWidth / 2;
        const rightPx = 28 + maxIdx * colWidth + colWidth / 2;
        const arrowWidth = rightPx - leftPx;
        const isRight = toIdx > fromIdx;

        if (fromIdx !== toIdx) {
            html += `<div class="seq-arrow" style="left:${leftPx}px;width:${arrowWidth}px">`;
            html += `<div class="seq-arrow-line" style="background:${color}"></div>`;
            if (isRight) {
                html += `<div class="seq-arrow-head" style="border-left:7px solid ${color}"></div>`;
            } else {
                html += `<div class="seq-arrow-head" style="border-right:7px solid ${color};order:-1"></div>`;
            }
            html += `<div class="seq-arrow-label" style="left:50%;transform:translateX(-50%);color:${color}">${shortLabel}</div>`;
            html += '</div>';
        } else {
            // Self-loop
            const selfPx = 28 + fromIdx * colWidth + colWidth / 2;
            html += `<div style="position:absolute;left:${selfPx + 10}px;top:2px;border:2px solid ${color};border-left:none;width:20px;height:28px;border-radius:0 8px 8px 0"></div>`;
            html += `<div class="seq-arrow-label" style="position:absolute;left:${selfPx + 35}px;top:6px;color:${color}">${shortLabel}</div>`;
        }

        html += '</div>';
    });
    html += '</div>';

    container.innerHTML = html;
    seqState.step = 0;
    seqState.playing = false;

    // Initially show all rows
    container.querySelectorAll('.seq-row').forEach(r => r.classList.add('visible'));
}

// ========== Sequence Animation ==========
function setupSequenceControls() {
    document.getElementById('seq-play').addEventListener('click', startSeqAnimation);
    document.getElementById('seq-pause').addEventListener('click', pauseSeqAnimation);
    document.getElementById('seq-reset').addEventListener('click', resetSeqAnimation);
}

function startSeqAnimation() {
    if (!currentTrace || currentTrace.sequence.length === 0) return;
    const rows = document.querySelectorAll('.seq-row');
    if (rows.length === 0) return;

    // If at end, reset first
    if (seqState.step >= rows.length) resetSeqAnimation();

    // Hide future rows
    rows.forEach((r, i) => { if (i >= seqState.step) r.classList.remove('visible'); });

    seqState.playing = true;
    document.getElementById('seq-play').classList.add('hidden');
    document.getElementById('seq-pause').classList.remove('hidden');

    const speed = 11 - parseInt(document.getElementById('seq-speed-slider').value);
    const interval = speed * 150;

    seqState.timer = setInterval(() => {
        if (seqState.step < rows.length) {
            rows[seqState.step].classList.add('visible');
            rows[seqState.step].scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            seqState.step++;
            document.getElementById('seq-current').textContent = seqState.step;
        } else {
            stopSeqAnimation();
        }
    }, interval);
}

function pauseSeqAnimation() {
    clearInterval(seqState.timer);
    seqState.playing = false;
    document.getElementById('seq-play').classList.remove('hidden');
    document.getElementById('seq-pause').classList.add('hidden');
}

function stopSeqAnimation() {
    clearInterval(seqState.timer);
    seqState.playing = false;
    document.getElementById('seq-play').classList.remove('hidden');
    document.getElementById('seq-pause').classList.add('hidden');
}

function resetSeqAnimation() {
    stopSeqAnimation();
    seqState.step = 0;
    document.getElementById('seq-current').textContent = '0';
    const rows = document.querySelectorAll('.seq-row');
    rows.forEach(r => r.classList.remove('visible'));
    // Show all immediately
    setTimeout(() => rows.forEach(r => r.classList.add('visible')), 50);
}

// ========== Tabs ==========
function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(btn.dataset.tab).classList.add('active');
        });
    });
}

// ========== Window Resize ==========
function setupResize() {
    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => { if (currentTrace) renderGraph(); }, 200);
    });
}

// ========== Init ==========
document.addEventListener('DOMContentLoaded', () => {
    setupTabs();
    setupGraphInteractions();
    setupSequenceControls();
    setupResize();
    loadData();
});
