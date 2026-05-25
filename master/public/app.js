// State variables
let selectedPoints = [];
let tasks = [];
let currentSelectedTaskId = null;
let monitorIntervalId = null;
let tasksIntervalId = null;


// DOM Elements
const canvas = document.getElementById('tsp-canvas');
const ctx = canvas.getContext('2d');
const txtTaskName = document.getElementById('task-name');
const inputRandomCount = document.getElementById('random-points-count');
const btnGenerateRandom = document.getElementById('btn-generate-random');
const btnClearPoints = document.getElementById('btn-clear-points');
const btnSubmitTask = document.getElementById('btn-submit-task');
const selectedPointsCount = document.getElementById('selected-points-count');
const taskTableBody = document.getElementById('task-table-body');
const canvasModeBadge = document.getElementById('canvas-mode-badge');
const btnResetView = document.getElementById('btn-reset-view');

// Canvas Overlay details
const canvasOverlay = document.getElementById('canvas-overlay-details');
const overlayTaskName = document.getElementById('overlay-task-name');
const overlayDistance = document.getElementById('overlay-distance');
const overlayTime = document.getElementById('overlay-time');

// Initialization
window.addEventListener('DOMContentLoaded', () => {
  // Clear canvas
  drawCanvas();

  // Setup Event Listeners
  canvas.addEventListener('click', handleCanvasClick);
  btnGenerateRandom.addEventListener('click', handleGenerateRandom);
  btnClearPoints.addEventListener('click', handleClearPoints);
  btnSubmitTask.addEventListener('click', handleSubmitTask);
  btnResetView.addEventListener('click', resetToEditMode);
  
  // Setup Comparison Toggle Event Listener
  document.getElementById('comparison-toggle-btn').addEventListener('click', toggleComparisonCollapse);

  // Initial loads and start polling
  fetchTasks();
  fetchMonitor();
  
  tasksIntervalId = setInterval(fetchTasks, 1000);
  monitorIntervalId = setInterval(fetchMonitor, 1000);
});

// Canvas Drawing Functions
function drawCanvas() {
  const width = canvas.width;
  const height = canvas.height;

  // 1. Clear background
  ctx.fillStyle = '#030712';
  ctx.fillRect(0, 0, width, height);

  // 2. Draw subtle grid lines
  ctx.strokeStyle = '#1e293b';
  ctx.lineWidth = 1;
  const gridSpacing = 40;
  for (let x = gridSpacing; x < width; x += gridSpacing) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = gridSpacing; y < height; y += gridSpacing) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  // 3. Determine what points and path to draw
  let pointsToDraw = [];
  let pathIndices = [];
  let pathStatus = 'edit'; // 'edit', 'queued', 'running', 'completed', 'failed'
  let taskName = '';

  if (currentSelectedTaskId) {
    const selectedTask = tasks.find(t => t.id === currentSelectedTaskId);
    if (selectedTask) {
      pointsToDraw = selectedTask.coords;
      taskName = selectedTask.name;
      pathStatus = selectedTask.status;

      if (selectedTask.status === 'completed' && selectedTask.result && selectedTask.result.tour) {
        pathIndices = selectedTask.result.tour;
      }
    }
  } else {
    pointsToDraw = selectedPoints;
  }

  // 4. Draw Path Connections
  if (pointsToDraw.length >= 2) {
    ctx.beginPath();
    ctx.lineWidth = 2.5;

    if (pathStatus === 'completed' && pathIndices.length > 0) {
      // Draw optimized TSP tour (open path, no wrap-around)
      ctx.strokeStyle = '#10b981'; // Emerald Green
      ctx.shadowColor = 'rgba(16, 185, 129, 0.4)';
      ctx.shadowBlur = 8;
      
      const startPt = pointsToDraw[pathIndices[0]];
      ctx.moveTo(startPt[0], startPt[1]);
      for (let i = 1; i < pathIndices.length; i++) {
        const pt = pointsToDraw[pathIndices[i]];
        ctx.lineTo(pt[0], pt[1]);
      }
      ctx.stroke();
    } else if (pathStatus === 'running') {
      // Draw running style path (yellow dashed connecting inputs)
      ctx.strokeStyle = '#3b82f6'; // Blue
      ctx.shadowColor = 'rgba(59, 130, 246, 0.4)';
      ctx.shadowBlur = 8;
      ctx.setLineDash([6, 4]);

      ctx.moveTo(pointsToDraw[0][0], pointsToDraw[0][1]);
      for (let i = 1; i < pointsToDraw.length; i++) {
        ctx.lineTo(pointsToDraw[i][0], pointsToDraw[i][1]);
      }
      ctx.stroke();
      ctx.setLineDash([]); // Reset
    } else if (pathStatus === 'edit' || pathStatus === 'queued' || pathStatus === 'failed') {
      // Faint lines connecting input points chronologically (open path)
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
      ctx.shadowBlur = 0;
      
      ctx.moveTo(pointsToDraw[0][0], pointsToDraw[0][1]);
      for (let i = 1; i < pointsToDraw.length; i++) {
        ctx.lineTo(pointsToDraw[i][0], pointsToDraw[i][1]);
      }
      ctx.stroke();
    }
    
    // Reset shadow
    ctx.shadowBlur = 0;
  }

  // 5. Draw Points
  pointsToDraw.forEach((pt, index) => {
    const x = pt[0];
    const y = pt[1];

    // Determine point color
    let pointColor = '#3b82f6'; // Blue for Edit
    let haloColor = 'rgba(59, 130, 246, 0.3)';

    if (pathStatus === 'completed') {
      pointColor = '#10b981'; // Emerald
      haloColor = 'rgba(16, 185, 129, 0.3)';
    } else if (pathStatus === 'running') {
      pointColor = '#f59e0b'; // Amber
      haloColor = 'rgba(245, 158, 11, 0.3)';
    }

    // Draw glowing halo
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fillStyle = haloColor;
    ctx.fill();

    // Draw core point
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = pointColor;
    ctx.fill();

    // Highlight start point (index 0 or the first in tour) and end point (last node)
    const isStart = (pathStatus === 'completed' && pathIndices.length > 0) 
      ? (index === pathIndices[0])
      : (index === 0);
    const isEnd = (pathStatus === 'completed' && pathIndices.length > 0) 
      ? (index === pathIndices[pathIndices.length - 1])
      : (index === pointsToDraw.length - 1);

    if (isStart && pointsToDraw.length > 1) {
      ctx.beginPath();
      ctx.arc(x, y, 13, 0, Math.PI * 2);
      ctx.strokeStyle = '#ef4444'; // Red ring for Start node
      ctx.lineWidth = 1.5;
      ctx.stroke();
      
      ctx.fillStyle = '#ef4444';
      ctx.font = 'bold 8px Outfit';
      ctx.fillText('START', x, y + 22);
    } else if (isEnd && pointsToDraw.length > 1) {
      ctx.beginPath();
      ctx.arc(x, y, 13, 0, Math.PI * 2);
      ctx.strokeStyle = '#3b82f6'; // Blue ring for End node
      ctx.lineWidth = 1.5;
      ctx.stroke();
      
      ctx.fillStyle = '#3b82f6';
      ctx.font = 'bold 8px Outfit';
      ctx.fillText('END', x, y + 22);
    }

    // Draw node labels (number)
    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px Outfit';
    ctx.textAlign = 'center';
    ctx.fillText(index + 1, x, y - 14);
  });
}

// Handle clicking on the canvas to add custom points
function handleCanvasClick(e) {
  if (currentSelectedTaskId) {
    // If viewing a task, clicking the canvas resets to edit mode with the current task's coordinates
    const selectedTask = tasks.find(t => t.id === currentSelectedTaskId);
    if (selectedTask) {
      selectedPoints = [...selectedTask.coords];
      selectedPointsCount.textContent = selectedPoints.length;
    }
    resetToEditMode();
    return;
  }

  const rect = canvas.getBoundingClientRect();
  
  // Scale mouse coordinates to match canvas internal resolution
  const x = Math.round((e.clientX - rect.left) * (canvas.width / rect.width));
  const y = Math.round((e.clientY - rect.top) * (canvas.height / rect.height));

  // Limit max points
  if (selectedPoints.length >= 15) {
    alert('最多只能新增 15 個點位！');
    return;
  }

  selectedPoints.push([x, y]);
  selectedPointsCount.textContent = selectedPoints.length;
  drawCanvas();
}

// Generate random coordinate points
function handleGenerateRandom() {
  if (currentSelectedTaskId) {
    resetToEditMode();
  }

  const count = parseInt(inputRandomCount.value) || 10;
  if (count < 3 || count > 15) {
    alert('隨機點位數量必須在 3 至 15 之間！');
    return;
  }

  selectedPoints = [];
  const padding = 40;
  for (let i = 0; i < count; i++) {
    const x = Math.floor(Math.random() * (canvas.width - padding * 2)) + padding;
    const y = Math.floor(Math.random() * (canvas.height - padding * 2)) + padding;
    selectedPoints.push([x, y]);
  }

  selectedPointsCount.textContent = selectedPoints.length;
  drawCanvas();
}

// Clear selected points
function handleClearPoints() {
  selectedPoints = [];
  selectedPointsCount.textContent = 0;
  resetToEditMode();
}

// Submit a new TSP task to Master API
function handleSubmitTask() {
  if (selectedPoints.length < 2) {
    alert('請至少新增 2 個座標點位才能提交任務！');
    return;
  }

  const name = txtTaskName.value.trim() || `TSP Task #${tasks.length + 1}`;
  
  btnSubmitTask.disabled = true;
  btnSubmitTask.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 提交中...';

  fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, coords: selectedPoints })
  })
  .then(res => {
    if (!res.ok) throw new Error('提交任務失敗');
    return res.json();
  })
  .then(task => {
    txtTaskName.value = '';
    selectedPoints = [];
    selectedPointsCount.textContent = 0;
    
    // Automatically select the newly created task
    currentSelectedTaskId = task.id;
    
    fetchTasks();
  })
  .catch(err => {
    alert('伺服器錯誤：' + err.message);
  })
  .finally(() => {
    btnSubmitTask.disabled = false;
    btnSubmitTask.innerHTML = '<i class="fa-solid fa-paper-plane"></i> 提交任務';
  });
}

// Reset canvas to edit mode
function resetToEditMode() {
  currentSelectedTaskId = null;
  canvasModeBadge.textContent = '編輯模式';
  canvasModeBadge.parentNode.parentNode.classList.remove('viewing');
  canvasOverlay.classList.add('hidden');
  
  const panel = document.getElementById('dp-player-panel');
  if (panel) panel.classList.add('hidden');
  
  const compBody = document.getElementById('comparison-table-body');
  if (compBody) {
    compBody.innerHTML = `
      <tr>
        <td colspan="6" class="empty-message">載入中...</td>
      </tr>
    `;
  }
  
  drawCanvas();
  
  // Remove highlighted row selection in table
  const rows = document.querySelectorAll('#task-table-body tr');
  rows.forEach(r => r.classList.remove('selected'));
}

// Fetch tasks list from Master
function fetchTasks() {
  fetch('/api/tasks')
    .then(res => res.json())
    .then(data => {
      tasks = data;
      renderTaskTable();
      
      // Update canvas overlay details if currently viewing a task
      if (currentSelectedTaskId) {
        const task = tasks.find(t => t.id === currentSelectedTaskId);
        if (task) {
          updateCanvasOverlay(task);
          const panel = document.getElementById('dp-player-panel');
          if (task.status === 'completed' && panel && panel.classList.contains('hidden')) {
            setupComparisonPanel(task);
          }
        } else {
          resetToEditMode();
        }
      }
      
      drawCanvas();
    })
    .catch(err => console.error('Error fetching tasks:', err));
}

// Render Task Table
function renderTaskTable() {
  if (tasks.length === 0) {
    taskTableBody.innerHTML = `
      <tr>
        <td colspan="6" class="empty-message">目前沒有任務，請在上方建立或隨機產生點位！</td>
      </tr>
    `;
    return;
  }

  let html = '';
  tasks.forEach(task => {
    let statusBadge = '';
    switch (task.status) {
      case 'queued':
        statusBadge = '<span class="badge badge-queued"><i class="fa-solid fa-clock"></i> 排隊中</span>';
        break;
      case 'running':
        statusBadge = `<span class="badge badge-running"><i class="fa-solid fa-rotate fa-spin"></i> 執行中</span>`;
        break;
      case 'completed':
        statusBadge = '<span class="badge badge-completed"><i class="fa-solid fa-circle-check"></i> 已完成</span>';
        break;
      case 'failed':
        statusBadge = '<span class="badge badge-failed"><i class="fa-solid fa-circle-xmark"></i> 失敗</span>';
        break;
    }

    const workerDisplay = task.workerId ? `<span class="worker-name">${task.workerId}</span>` : '-';
    
    let resultDisplay = '-';
    if (task.status === 'completed' && task.result) {
      resultDisplay = `<span style="font-weight:600;color:#10b981">${task.result.distance}</span> <span style="color:#94a3b8;font-size:0.8rem">(${task.result.time}ms)</span>`;
    } else if (task.status === 'failed') {
      resultDisplay = `<span style="color:#ef4444" title="${task.error || ''}">運算失敗</span>`;
    }

    const isSelected = task.id === currentSelectedTaskId ? 'class="selected"' : '';

    html += `
      <tr ${isSelected} onclick="selectTask('${task.id}')">
        <td style="font-weight: 500; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${task.name}</td>
        <td style="font-family: monospace;">${task.coords.length}</td>
        <td>${workerDisplay}</td>
        <td>${statusBadge}</td>
        <td>${resultDisplay}</td>
        <td>
          <button class="btn-small" style="color:#fca5a5;" onclick="deleteTask(event, '${task.id}')">
            <i class="fa-solid fa-trash-can"></i>
          </button>
        </td>
      </tr>
    `;
  });

  taskTableBody.innerHTML = html;
}

// Select a task to view on Canvas
function selectTask(taskId) {
  currentSelectedTaskId = taskId;
  const task = tasks.find(t => t.id === taskId);
  if (task) {
    canvasModeBadge.textContent = `檢視模式: ${task.name}`;
    updateCanvasOverlay(task);
    setupComparisonPanel(task);
  }
  drawCanvas();
  renderTaskTable();
}

// Update the overlay details on the Canvas
function updateCanvasOverlay(task) {
  overlayTaskName.textContent = task.name;
  canvasOverlay.classList.remove('hidden');

  if (task.status === 'completed' && task.result) {
    overlayDistance.textContent = task.result.distance;
    overlayDistance.style.color = '#10b981';
    overlayTime.textContent = `${task.result.time} ms`;
  } else if (task.status === 'running') {
    overlayDistance.textContent = '計算中...';
    overlayDistance.style.color = '#3b82f6';
    overlayTime.textContent = '進行中';
  } else if (task.status === 'queued') {
    overlayDistance.textContent = '排隊中...';
    overlayDistance.style.color = '#f59e0b';
    overlayTime.textContent = '等待指派';
  } else {
    overlayDistance.textContent = '失敗';
    overlayDistance.style.color = '#ef4444';
    overlayTime.textContent = '無結果';
  }
}

// Delete or cancel a task
function deleteTask(event, taskId) {
  event.stopPropagation(); // Prevent row click selection trigger

  if (!confirm('確定要取消並刪除此任務嗎？')) return;

  fetch(`/api/tasks/${taskId}`, { method: 'DELETE' })
    .then(res => {
      if (!res.ok) throw new Error('刪除失敗');
      return res.json();
    })
    .then(() => {
      if (currentSelectedTaskId === taskId) {
        resetToEditMode();
      }
      fetchTasks();
    })
    .catch(err => alert(err.message));
}

// Fetch resources monitor metrics from Master
function fetchMonitor() {
  fetch('/api/monitor')
    .then(res => res.json())
    .then(data => {
      updateMonitorUI(data);
    })
    .catch(err => console.error('Error fetching monitor telemetry:', err));
}

// Update Monitor Telemetry Cards
function updateMonitorUI(data) {
  const { workers, resources } = data;

  // 1. Update Master Card
  const masterStats = resources['Master'] || { cpu: 0, memory: 0 };
  updateNodeStats('Master', 'online', masterStats.cpu, masterStats.memory);

  // 2. Update Worker Cards
  const workerIds = ['Worker A', 'Worker B', 'Worker C'];
  workerIds.forEach(id => {
    // Standardize IDs for HTML selectors (replace space with dash)
    const htmlId = id.replace(' ', '-');
    const workerInfo = workers.find(w => w.id === id);
    const workerStats = resources[id] || { cpu: 0, memory: 0 };

    if (workerInfo) {
      updateNodeStats(htmlId, workerInfo.status, workerStats.cpu, workerStats.memory, workerInfo.currentTaskId);
    } else {
      updateNodeStats(htmlId, 'offline', 0, 0);
    }
  });
}

// Helper to update a specific node card in UI
function updateNodeStats(nodeHtmlId, status, cpu, memory, currentTaskId = null) {
  const card = document.getElementById(`node-${nodeHtmlId}`);
  if (!card) return;

  const dot = card.querySelector('.node-dot');
  const footer = card.querySelector('.node-footer');
  
  // Update offline opacity class
  if (status === 'offline') {
    card.classList.add('offline-node');
  } else {
    card.classList.remove('offline-node');
  }

  // Update Status LED Dot
  dot.className = 'node-dot'; // Reset
  dot.classList.add(status);

  // Update Text Metrics
  const cpuText = document.getElementById(`cpu-${nodeHtmlId}`);
  const memText = document.getElementById(`mem-${nodeHtmlId}`);
  if (cpuText) cpuText.textContent = `${cpu}%`;
  if (memText) memText.textContent = `${memory}%`;

  // Update Progress Bars
  const cpuBar = document.getElementById(`cpu-bar-${nodeHtmlId}`);
  const memBar = document.getElementById(`mem-bar-${nodeHtmlId}`);

  if (cpuBar) {
    cpuBar.style.width = `${cpu}%`;
    cpuBar.className = 'progress-bar-fill ' + getMetricFillClass(cpu);
  }
  if (memBar) {
    memBar.style.width = `${memory}%`;
    memBar.className = 'progress-bar-fill ' + getMetricFillClass(memory);
  }

  // Update Footer (Only for Workers)
  if (footer) {
    if (status === 'offline') {
      footer.innerHTML = '<span class="status-msg"><i class="fa-solid fa-triangle-exclamation"></i> 已斷線</span>';
    } else if (status === 'busy' && currentTaskId) {
      footer.innerHTML = `<span style="color:#3b82f6;font-weight:500;"><i class="fa-solid fa-spinner fa-spin"></i> 計算中: ${currentTaskId.substring(5, 12)}</span>`;
    } else {
      footer.innerHTML = '<span style="color:#10b981;font-weight:500;"><i class="fa-solid fa-check-double"></i> 待命空閒</span>';
    }
  }
}

// Determine progress bar color theme based on percentage
function getMetricFillClass(val) {
  if (val < 50) return 'fill-low';     // Green
  if (val < 80) return 'fill-medium';  // Orange
  return 'fill-high';                  // Red
}

// Comparison Panel Control Functions
function setupComparisonPanel(task) {
  const panel = document.getElementById('dp-player-panel');
  if (!panel) return;

  if (task.status === 'completed' && task.subtasks && task.subtasks.length > 0) {
    updateComparisonTable(task);
    panel.classList.remove('hidden');
    return;
  }
  
  panel.classList.add('hidden');
}

function toggleComparisonCollapse() {
  const container = document.getElementById('comparison-scroll-container');
  const toggleText = document.getElementById('comparison-toggle-text');
  if (!container || !toggleText) return;
  
  if (container.classList.contains('collapsed')) {
    container.classList.remove('collapsed');
    toggleText.innerHTML = '<i class="fa-solid fa-chevron-up"></i> 收合';
  } else {
    container.classList.add('collapsed');
    toggleText.innerHTML = '<i class="fa-solid fa-chevron-down"></i> 展開';
  }
}

function updateComparisonTable(task) {
  const tbody = document.getElementById('comparison-table-body');
  if (!tbody) return;
  
  if (task.status !== 'completed' || !task.subtasks || task.subtasks.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="empty-message">無子任務資料</td>
      </tr>
    `;
    return;
  }
  
  let optimalSubtaskId = null;
  const completedSubs = task.subtasks.filter(sub => sub.status === 'completed' && sub.result);
  if (completedSubs.length > 0) {
    const bestSub = completedSubs.reduce((min, sub) => sub.result.distance < min.result.distance ? sub : min, completedSubs[0]);
    if (bestSub) {
      optimalSubtaskId = bestSub.id;
    }
  }
  
  let html = '';
  task.subtasks.forEach(sub => {
    let routeStartText = '';
    if (sub.first_step !== null && sub.first_step !== undefined) {
      routeStartText = `節點 1 ➔ 節點 ${sub.first_step + 1}`;
    } else {
      routeStartText = '完整路徑';
    }
    
    const workerText = sub.workerId || '未指派';
    
    let tourText = '-';
    let distText = '-';
    let timeText = '-';
    let badgeHtml = '';
    let rowClass = '';
    
    if (sub.status === 'completed' && sub.result) {
      const tour1based = sub.result.tour.map(idx => idx + 1);
      tourText = tour1based.join(' ➔ ');
      distText = `${sub.result.distance}`;
      timeText = `${sub.result.time} ms`;
      
      const isOptimal = sub.id === optimalSubtaskId;
      if (isOptimal) {
        rowClass = 'class="highlight-optimal-row"';
        badgeHtml = '<span class="badge-optimal badge-optimal"><i class="fa-solid fa-crown"></i> 最佳 (Optimal)</span>';
      } else {
        badgeHtml = '<span class="badge-sub-complete">已完成</span>';
      }
    } else if (sub.status === 'running') {
      badgeHtml = '<span class="badge badge-running" style="font-size:0.7rem;padding:0.1rem 0.4rem;"><i class="fa-solid fa-spinner fa-spin"></i> 執行中</span>';
    } else if (sub.status === 'failed') {
      badgeHtml = '<span class="badge badge-failed" style="font-size:0.7rem;padding:0.1rem 0.4rem;">失敗</span>';
    } else {
      badgeHtml = '<span class="badge badge-queued" style="font-size:0.7rem;padding:0.1rem 0.4rem;">等待中</span>';
    }
    
    html += `
      <tr ${rowClass}>
        <td style="font-weight: 500;">${routeStartText}</td>
        <td style="font-family: monospace;">${workerText}</td>
        <td style="font-size: 0.75rem; color: var(--text-muted); font-family: monospace; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${tourText}">${tourText}</td>
        <td style="font-weight: 600; font-family: monospace;">${distText}</td>
        <td style="font-family: monospace;">${timeText}</td>
        <td>${badgeHtml}</td>
      </tr>
    `;
  });
  
  tbody.innerHTML = html;
}

