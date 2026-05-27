// 狀態變數
let selectedPoints = [];
let tasks = [];
let currentSelectedTaskId = null;
let monitorIntervalId = null;
let tasksIntervalId = null;
let transitioningWorkers = new Set();

// 模擬功能狀態變數
let simState = 'idle'; // 'idle', 'running', 'paused'
let simProgress = 0.0; // 0.0 到 (tour.length - 1)
let pausedPosition = null; // [x, y] 暫停時的座標
let dynamicNewNodes = []; // 暫停時使用者新增的點位
let animationFrameId = null;
let lastFrameTime = 0;

// 時間格式化輔助函式（將毫秒轉換為幾分幾秒，小於一分則顯示秒數）
function formatTime(ms) {
  if (ms === null || ms === undefined || isNaN(ms)) return '-';
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) {
    return `${totalSeconds.toFixed(2)} 秒`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = (totalSeconds % 60).toFixed(2);
  return `${minutes} 分 ${seconds} 秒`;
}


// DOM 元素參照
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

// 模擬控制面板 DOM 元素參照
const simulationPanel = document.getElementById('simulation-panel');
const btnSimToggle = document.getElementById('btn-sim-toggle');
const btnSimPause = document.getElementById('btn-sim-pause');
const btnSimReplan = document.getElementById('btn-sim-replan');
const btnSimReset = document.getElementById('btn-sim-reset');
const simStatusText = document.getElementById('sim-status-text');


// 頁面初始化
window.addEventListener('DOMContentLoaded', () => {
  // 清除畫布並繪製初始狀態
  drawCanvas();

  // 設定事件監聽器
  canvas.addEventListener('click', handleCanvasClick);
  btnGenerateRandom.addEventListener('click', handleGenerateRandom);
  btnClearPoints.addEventListener('click', handleClearPoints);
  btnSubmitTask.addEventListener('click', handleSubmitTask);
  btnResetView.addEventListener('click', resetToEditMode);
  
  // 模擬控制面板事件監聽 (防禦性設計：避免舊 HTML 快取導致 TypeError)
  if (btnSimToggle) btnSimToggle.addEventListener('click', handleSimToggle);
  if (btnSimPause) btnSimPause.addEventListener('click', handleSimPause);
  if (btnSimReplan) btnSimReplan.addEventListener('click', handleSimReplan);
  if (btnSimReset) btnSimReset.addEventListener('click', handleSimReset);
  
  // 設定子任務比較表的收合切換事件
  document.getElementById('comparison-toggle-btn').addEventListener('click', toggleComparisonCollapse);

  // 初始載入任務與監控資料，並開始定時輪詢
  fetchTasks();
  fetchMonitor();
  
  tasksIntervalId = setInterval(fetchTasks, 1000);   // 每 1 秒更新任務狀態
  monitorIntervalId = setInterval(fetchMonitor, 1000); // 每 1 秒更新系統監控
});

// 畫布繪製函式
function drawCanvas() {
  const width = canvas.width;
  const height = canvas.height;

  // 1. 清除背景
  ctx.fillStyle = '#030712';
  ctx.fillRect(0, 0, width, height);

  // 2. 繪製淡色網格線
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

  // 3. 決定要繪製的點位和路徑
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

  // 4. 繪製路徑連接線
  if (pointsToDraw.length >= 2) {
    ctx.lineWidth = 2.5;

    const drawStaticPath = () => {
      if (!pathIndices || pathIndices.length === 0) return;
      ctx.beginPath();
      ctx.strokeStyle = '#10b981'; // 綠色
      ctx.shadowColor = 'rgba(16, 185, 129, 0.4)';
      ctx.shadowBlur = 8;
      
      const startPt = pointsToDraw[pathIndices[0]];
      if (startPt) {
        ctx.moveTo(startPt[0], startPt[1]);
        for (let i = 1; i < pathIndices.length; i++) {
          const pt = pointsToDraw[pathIndices[i]];
          if (pt) ctx.lineTo(pt[0], pt[1]);
        }
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
    };

    if (pathStatus === 'completed' && pathIndices.length > 0) {
      if (simState !== 'idle') {
        try {
          // --- 模擬中的動態路徑繪製 (均勻像素速度) ---
          // 1. 計算所有段的長度與累計距離
          const segmentLengths = [];
          const cumulativeDistances = [0];
          for (let i = 0; i < pathIndices.length - 1; i++) {
            const pt1 = pointsToDraw[pathIndices[i]];
            const pt2 = pointsToDraw[pathIndices[i+1]];
            const len = (pt1 && pt2) ? Math.sqrt((pt2[0] - pt1[0])**2 + (pt2[1] - pt1[1])**2) : 0;
            segmentLengths.push(len);
            cumulativeDistances.push(cumulativeDistances[i] + len);
          }
          const totalDist = cumulativeDistances[cumulativeDistances.length - 1];

          // 2. 依據已走過之像素距離 simProgress，找到當前位於哪個線段 (currSegment) 和比例 t
          let currSegment = 0;
          let t = 0.0;
          
          if (simProgress >= totalDist) {
            currSegment = pathIndices.length - 1;
            t = 0.0;
          } else {
            for (let i = 0; i < segmentLengths.length; i++) {
              if (simProgress >= cumulativeDistances[i] && simProgress < cumulativeDistances[i+1]) {
                currSegment = i;
                const segLen = segmentLengths[i];
                t = segLen > 0 ? (simProgress - cumulativeDistances[i]) / segLen : 0;
                break;
              }
            }
          }

          let targetX = 0, targetY = 0;
          if (currSegment >= pathIndices.length - 1) {
            const pt = pointsToDraw[pathIndices[pathIndices.length - 1]];
            if (pt) {
              targetX = pt[0];
              targetY = pt[1];
            }
          } else {
            const pt1 = pointsToDraw[pathIndices[currSegment]];
            const pt2 = pointsToDraw[pathIndices[currSegment + 1]];
            if (pt1 && pt2) {
              targetX = pt1[0] + (pt2[0] - pt1[0]) * t;
              targetY = pt1[1] + (pt2[1] - pt1[1]) * t;
            } else if (pt1) {
              targetX = pt1[0];
              targetY = pt1[1];
            }
          }

          // A. 繪製「已走過」的實線路徑 (淡綠色)
          ctx.beginPath();
          ctx.strokeStyle = '#10b981';
          ctx.shadowColor = 'rgba(16, 185, 129, 0.3)';
          ctx.shadowBlur = 6;
          
          const startPt = pointsToDraw[pathIndices[0]];
          if (startPt) {
            ctx.moveTo(startPt[0], startPt[1]);
            for (let i = 1; i <= currSegment; i++) {
              const pt = pointsToDraw[pathIndices[i]];
              if (pt) ctx.lineTo(pt[0], pt[1]);
            }
            // 連接到當前移動點
            ctx.lineTo(targetX, targetY);
          }
          ctx.stroke();
          ctx.shadowBlur = 0; // 重置陰影

          // B. 繪製「未走過」的虛線路徑 (藍色虛線)
          if (currSegment < pathIndices.length - 1) {
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(59, 130, 246, 0.7)'; // 藍色半透明
            ctx.shadowColor = 'rgba(59, 130, 246, 0.2)';
            ctx.shadowBlur = 4;
            ctx.setLineDash([6, 4]);
            
            ctx.moveTo(targetX, targetY);
            for (let i = currSegment + 1; i < pathIndices.length; i++) {
              const pt = pointsToDraw[pathIndices[i]];
              if (pt) ctx.lineTo(pt[0], pt[1]);
            }
            ctx.stroke();
            ctx.setLineDash([]); // 重置虛線
            ctx.shadowBlur = 0;
          }

          // C. 繪製移動目標的呼吸紅點
          ctx.beginPath();
          const pulseRadius = 12 + Math.sin(Date.now() / 150) * 3;
          ctx.arc(targetX, targetY, pulseRadius, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(239, 68, 68, 0.25)'; // 發光紅色圈圈
          ctx.fill();

          ctx.beginPath();
          ctx.arc(targetX, targetY, 6, 0, Math.PI * 2);
          ctx.fillStyle = '#ef4444'; // 紅色核心
          ctx.fill();

          ctx.fillStyle = '#ffffff';
          ctx.font = '8px Arial';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText('🚚', targetX, targetY);
        } catch (e) {
          console.error("Error drawing dynamic path simulation, falling back to static path:", e);
          drawStaticPath();
        }
      } else {
        // --- 標準已完成路徑繪製 (靜態綠色實線) ---
        drawStaticPath();
      }
    } else if (pathStatus === 'running') {
      // 繪製執行中的路徑（藍色虛線）
      ctx.beginPath();
      ctx.strokeStyle = '#3b82f6';
      ctx.shadowColor = 'rgba(59, 130, 246, 0.4)';
      ctx.shadowBlur = 8;
      ctx.setLineDash([6, 4]);

      if (pointsToDraw[0]) {
        ctx.moveTo(pointsToDraw[0][0], pointsToDraw[0][1]);
        for (let i = 1; i < pointsToDraw.length; i++) {
          const pt = pointsToDraw[i];
          if (pt) ctx.lineTo(pt[0], pt[1]);
        }
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.shadowBlur = 0;
    } else if (pathStatus === 'edit' || pathStatus === 'queued' || pathStatus === 'failed') {
      // 繪製編輯/等待/失敗狀態的淡色連接線
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
      ctx.shadowBlur = 0;
      
      if (pointsToDraw[0]) {
        ctx.moveTo(pointsToDraw[0][0], pointsToDraw[0][1]);
        for (let i = 1; i < pointsToDraw.length; i++) {
          const pt = pointsToDraw[i];
          if (pt) ctx.lineTo(pt[0], pt[1]);
        }
      }
      ctx.stroke();
    }
  }

  // 5. 繪製節點點位
  pointsToDraw.forEach((pt, index) => {
    if (!pt || pt.length < 2) return;
    const x = pt[0];
    const y = pt[1];

    // 決定點位顏色
    let pointColor = '#3b82f6'; // 藍色：編輯模式
    let haloColor = 'rgba(59, 130, 246, 0.3)';

    if (pathStatus === 'completed') {
      pointColor = '#10b981'; // 綠色：已完成
      haloColor = 'rgba(16, 185, 129, 0.3)';
    } else if (pathStatus === 'running') {
      pointColor = '#f59e0b'; // 琥珀色：執行中
      haloColor = 'rgba(245, 158, 11, 0.3)';
    }

    // 繪製發光光暈效果
    ctx.beginPath();
    ctx.arc(x, y, 9, 0, Math.PI * 2);
    ctx.fillStyle = haloColor;
    ctx.fill();

    // 繪製核心點位
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = pointColor;
    ctx.fill();

    // 標記起點和終點
    const isStart = (pathStatus === 'completed' && pathIndices.length > 0) 
      ? (index === pathIndices[0])
      : (index === 0);
    const isEnd = (pathStatus === 'completed' && pathIndices.length > 0) 
      ? (index === pathIndices[pathIndices.length - 1])
      : (index === pointsToDraw.length - 1);

    if (isStart && pointsToDraw.length > 1) {
      ctx.beginPath();
      ctx.arc(x, y, 13, 0, Math.PI * 2);
      ctx.strokeStyle = '#ef4444'; // 紅色圖圈：起點
      ctx.lineWidth = 1.5;
      ctx.stroke();
      
      ctx.fillStyle = '#ef4444';
      ctx.font = 'bold 8px Outfit';
      ctx.fillText('START', x, y + 22);
    } else if (isEnd && pointsToDraw.length > 1) {
      ctx.beginPath();
      ctx.arc(x, y, 13, 0, Math.PI * 2);
      ctx.strokeStyle = '#3b82f6'; // 藍色圖圈：終點
      ctx.lineWidth = 1.5;
      ctx.stroke();
      
      ctx.fillStyle = '#3b82f6';
      ctx.font = 'bold 8px Outfit';
      ctx.fillText('END', x, y + 22);
    }

    // 繪製節點編號標籤
    ctx.fillStyle = '#94a3b8';
    ctx.font = '10px Outfit';
    ctx.textAlign = 'center';
    ctx.fillText(index + 1, x, y - 14);
  });

  // 6. 繪製中途新增的臨時點位
  if (simState === 'paused' && dynamicNewNodes.length > 0) {
    dynamicNewNodes.forEach((node, idx) => {
      if (!node || node.length < 2) return;
      const nx = node[0];
      const ny = node[1];
      
      // 紫色光暈
      ctx.beginPath();
      ctx.arc(nx, ny, 10, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(168, 85, 247, 0.25)';
      ctx.fill();
      
      // 紫色核心
      ctx.beginPath();
      ctx.arc(nx, ny, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#a855f7';
      ctx.fill();
      
      ctx.fillStyle = '#f3e8ff';
      ctx.font = 'bold 8px Outfit';
      ctx.textAlign = 'center';
      ctx.fillText(`NEW`, nx, ny - 13);
    });
  }
}

// 處理畫布點擊事件：新增自訂點位
function handleCanvasClick(e) {
  const rect = canvas.getBoundingClientRect();
  
  // 將滑鼠座標縮放為畫布內部解析度
  const x = Math.round((e.clientX - rect.left) * (canvas.width / rect.width));
  const y = Math.round((e.clientY - rect.top) * (canvas.height / rect.height));

  if (currentSelectedTaskId) {
    const selectedTask = tasks.find(t => t.id === currentSelectedTaskId);
    if (selectedTask) {
      if (selectedTask.status === 'completed' && simState === 'paused') {
        // 在暫停模式下，點擊畫布以新增中途節點
        dynamicNewNodes.push([x, y]);
        btnSimReplan.disabled = false; // 啟用重新規劃按鈕
        drawCanvas();
        return;
      }
      
      // 如果不是暫停模式，點擊畫布會載入選取點位並重設為編輯模式
      selectedPoints = [...selectedTask.coords];
      selectedPointsCount.textContent = selectedPoints.length;
    }
    resetToEditMode();
    return;
  }

  selectedPoints.push([x, y]);
  selectedPointsCount.textContent = selectedPoints.length;
  drawCanvas();
}

// 產生隨機座標點位
function handleGenerateRandom() {
  if (currentSelectedTaskId) {
    resetToEditMode();
  }

  const count = parseInt(inputRandomCount.value) || 10;
  if (count < 3 || count > 100) {
    alert('隨機點位數量必須在 3 至 100 之間！');
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

// 清除所有已選取的點位
function handleClearPoints() {
  selectedPoints = [];
  selectedPointsCount.textContent = 0;
  resetToEditMode();
}

// 提交新的 TSP 任務到 Master API
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
    
    // 自動選取剛創建的任務以在畫布上顯示
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

// 重置畫布為編輯模式
function resetToEditMode() {
  currentSelectedTaskId = null;
  canvasModeBadge.textContent = '編輯模式';
  
  if (simulationPanel) simulationPanel.classList.add('hidden');
  resetSimulation();

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
  
  // 移除任務列表中的選取反白樣式
  const rows = document.querySelectorAll('#task-table-body tr');
  rows.forEach(r => r.classList.remove('selected'));
}

// 從 Master 取得任務列表
function fetchTasks() {
  fetch('/api/tasks')
    .then(res => res.json())
    .then(data => {
      tasks = data;
      renderTaskTable();
      
      // 如果目前正在檢視某個任務，更新畫布覆蓋層的詳細資訊
      if (currentSelectedTaskId) {
        const task = tasks.find(t => t.id === currentSelectedTaskId);
        if (task) {
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

// 繪製任務列表
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
      resultDisplay = `<span style="font-weight:600;color:#10b981">${task.result.distance} 公尺</span> <span style="color:#94a3b8;font-size:0.8rem">(${formatTime(task.result.time)})</span>`;
    } else if (task.status === 'failed') {
      resultDisplay = `<span style="color:#ef4444" title="${task.error || ''}">運算失敗</span>`;
    }

    const isSelected = task.id === currentSelectedTaskId ? 'class="selected"' : '';

    html += `
      <tr ${isSelected} onclick="selectTask('${task.id}')">
        <td style="font-weight: 500; word-break: break-word;" title="${task.name}">${task.name}</td>
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

// 選取任務以在畫布上檢視
function selectTask(taskId) {
  currentSelectedTaskId = taskId;
  const task = tasks.find(t => t.id === taskId);
  if (task) {
    canvasModeBadge.textContent = `檢視模式: ${task.name}`;
    setupComparisonPanel(task);

    // 顯示或隱藏模擬控制面板
    if (simulationPanel) {
      if (task.status === 'completed') {
        simulationPanel.classList.remove('hidden');
        resetSimulation(); // 切換任務時重設模擬狀態
      } else {
        simulationPanel.classList.add('hidden');
      }
    }
  }
  drawCanvas();
  renderTaskTable();
}


// 刪除或取消任務
function deleteTask(event, taskId) {
  event.stopPropagation(); // 阻止事件冒泡，避免觸發行點擊選取

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

// 從 Master 取得系統資源監控資料
function fetchMonitor() {
  fetch('/api/monitor')
    .then(res => res.json())
    .then(data => {
      updateMonitorUI(data);
    })
    .catch(err => console.error('Error fetching monitor telemetry:', err));
}

// 更新系統監控 UI 卡片
function updateMonitorUI(data) {
  const { workers, resources } = data;

  // 1. 更新 Master 節點卡片
  const masterStats = resources['Master'] || { cpu: 0, memory: 0 };
  updateNodeStats('Master', 'online', masterStats.cpu, masterStats.memory);

  // 2. 更新各 Worker 節點卡片
  const workerIds = ['Worker A', 'Worker B', 'Worker C'];
  workerIds.forEach(id => {
    // 將 Worker ID 中的空格替換為橫線以符合 HTML 選擇器格式
    const htmlId = id.replace(' ', '-');
    const workerInfo = workers.find(w => w.id === id);
    const workerStats = resources[id] || { cpu: 0, memory: 0 };

    if (workerInfo) {
      updateNodeStats(htmlId, workerInfo.status, workerStats.cpu, workerStats.memory, workerInfo.currentTaskId, workerInfo.containerStatus);
    } else {
      updateNodeStats(htmlId, 'offline', 0, 0, null, 'unknown');
    }
  });
}

// 輔助函式：更新特定節點卡片的 UI 顯示
function updateNodeStats(nodeHtmlId, status, cpu, memory, currentTaskId = null, containerStatus = null) {
  const card = document.getElementById(`node-${nodeHtmlId}`);
  if (!card) return;

  const dot = card.querySelector('.node-dot');
  const footer = card.querySelector('.node-footer');
  
  // 更新控制按鈕狀態（僅 Worker 卡片有控制按鈕）
  const toggleBtn = document.getElementById(`btn-toggle-${nodeHtmlId}`);
  if (toggleBtn && containerStatus) {
    const isTransitioning = transitioningWorkers.has(nodeHtmlId.replace('-', ' '));
    if (isTransitioning) {
      toggleBtn.disabled = true;
      toggleBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 處理中...';
      toggleBtn.className = 'btn-small';
    } else {
      toggleBtn.disabled = false;
      if (containerStatus === 'running') {
        toggleBtn.innerHTML = '<i class="fa-solid fa-power-off"></i> 關閉';
        toggleBtn.className = 'btn-small btn-toggle-stop';
      } else if (containerStatus === 'exited' || containerStatus === 'stopped') {
        toggleBtn.innerHTML = '<i class="fa-solid fa-play"></i> 啟動';
        toggleBtn.className = 'btn-small btn-toggle-start';
      } else {
        toggleBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 偵測中';
        toggleBtn.disabled = true;
        toggleBtn.className = 'btn-small';
      }
    }
  }

  // 更新離線透明度樣式
  if (status === 'offline') {
    card.classList.add('offline-node');
  } else {
    card.classList.remove('offline-node');
  }

  // 更新狀態 LED 小圓點
  dot.className = 'node-dot'; // Reset
  dot.classList.add(status);

  // 更新 CPU 和記憶體的數值顯示
  const cpuText = document.getElementById(`cpu-${nodeHtmlId}`);
  const memText = document.getElementById(`mem-${nodeHtmlId}`);
  if (cpuText) cpuText.textContent = `${cpu}%`;
  if (memText) memText.textContent = `${memory}%`;

  // 更新進度條
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

  // 更新底部狀態文字（僅適用於 Worker 卡片）
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

// 根據百分比決定進度條顏色主題
function getMetricFillClass(val) {
  if (val < 50) return 'fill-low';     // 綠色：低負載
  if (val < 80) return 'fill-medium';  // 橙色：中負載
  return 'fill-high';                  // 紅色：高負載
}

// 子任務比較面板控制函式
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
      distText = `${sub.result.distance} 公尺`;
      timeText = formatTime(sub.result.time);
      
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
        <td style="font-size: 0.75rem; color: var(--text-muted); font-family: monospace; word-break: break-all;" title="${tourText}">${tourText}</td>
        <td style="font-weight: 600; font-family: monospace;">${distText}</td>
        <td style="font-family: monospace;">${timeText}</td>
        <td>${badgeHtml}</td>
      </tr>
    `;
  });
  
  tbody.innerHTML = html;
}

// 控制 Worker 容器啟動或關閉
function toggleWorkerContainer(workerId) {
  if (transitioningWorkers.has(workerId)) return; // 防止重複點擊
  
  transitioningWorkers.add(workerId);
  const htmlId = workerId.replace(' ', '-');
  const toggleBtn = document.getElementById(`btn-toggle-${htmlId}`);
  if (toggleBtn) {
    toggleBtn.disabled = true;
    toggleBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 處理中...';
  }

  fetch(`/api/workers/${workerId}/toggle`, {
    method: 'POST'
  })
  .then(res => {
    if (!res.ok) throw new Error('控制容器狀態失敗');
    return res.json();
  })
  .then(data => {
    // 成功後立即手動觸發一次監控更新，獲得最新狀態
    fetchMonitor();
  })
  .catch(err => {
    alert('伺服器錯誤：' + err.message);
  })
  .finally(() => {
    transitioningWorkers.delete(workerId);
    fetchMonitor();
  });
}

// --- 模擬配送與中途路徑重規劃邏輯 ---

function handleSimToggle() {
  const task = tasks.find(t => t.id === currentSelectedTaskId);
  if (!task || task.status !== 'completed' || !task.result || !task.result.tour) return;

  if (simState === 'idle' || simState === 'paused') {
    simState = 'running';
    if (btnSimToggle) {
      btnSimToggle.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 模擬中...';
      btnSimToggle.disabled = true; // 運行時禁用開始按鈕，只允許點暫停
    }
    if (btnSimPause) btnSimPause.disabled = false;
    if (btnSimReset) btnSimReset.disabled = false;
    if (btnSimReplan) btnSimReplan.disabled = true;
    
    canvasModeBadge.textContent = `模擬配送中: ${task.name}`;
    
    lastFrameTime = performance.now();
    animationFrameId = requestAnimationFrame(updateSimulation);
  }
}

function handleSimPause() {
  if (simState === 'running') {
    simState = 'paused';
    cancelAnimationFrame(animationFrameId);
    
    if (btnSimToggle) {
      btnSimToggle.innerHTML = '<i class="fa-solid fa-play"></i> 繼續模擬';
      btnSimToggle.disabled = false;
    }
    if (btnSimPause) btnSimPause.disabled = true;
    if (btnSimReset) btnSimReset.disabled = false;
    
    // 計算暫停時的精確插值座標
    const task = tasks.find(t => t.id === currentSelectedTaskId);
    if (task && task.result && task.result.tour) {
      const tour = task.result.tour;
      const coords = task.coords;
      
      // 計算段長度與累計距離
      const segmentLengths = [];
      const cumulativeDistances = [0];
      for (let i = 0; i < tour.length - 1; i++) {
        const pt1 = coords[tour[i]];
        const pt2 = coords[tour[i+1]];
        const len = (pt1 && pt2) ? Math.sqrt((pt2[0] - pt1[0])**2 + (pt2[1] - pt1[1])**2) : 0;
        segmentLengths.push(len);
        cumulativeDistances.push(cumulativeDistances[i] + len);
      }
      const totalDist = cumulativeDistances[cumulativeDistances.length - 1];

      let currSegment = 0;
      let t = 0.0;
      
      if (simProgress >= totalDist) {
        currSegment = tour.length - 1;
        t = 0.0;
      } else {
        for (let i = 0; i < segmentLengths.length; i++) {
          if (simProgress >= cumulativeDistances[i] && simProgress < cumulativeDistances[i+1]) {
            currSegment = i;
            const segLen = segmentLengths[i];
            t = segLen > 0 ? (simProgress - cumulativeDistances[i]) / segLen : 0;
            break;
          }
        }
      }
      
      if (currSegment >= tour.length - 1) {
        const lastPt = coords[tour[tour.length - 1]];
        pausedPosition = lastPt ? [...lastPt] : [0, 0];
      } else {
        const pt1 = coords[tour[currSegment]];
        const pt2 = coords[tour[currSegment + 1]];
        if (pt1 && pt2) {
          pausedPosition = [
            pt1[0] + (pt2[0] - pt1[0]) * t,
            pt1[1] + (pt2[1] - pt1[1]) * t
          ];
        } else if (pt1) {
          pausedPosition = [...pt1];
        } else {
          pausedPosition = [0, 0];
        }
      }
    }
    
    canvasModeBadge.textContent = `模擬已暫停 (請點擊地圖新增剩餘路途欲經過的節點)`;
    if (simStatusText) simStatusText.textContent = '模擬已暫停。您現在可以點擊地圖新增節點，然後點擊「重新規劃路徑」！';
    
    // 只有在新增了點位後才啟用重新規劃按鈕
    if (btnSimReplan) btnSimReplan.disabled = dynamicNewNodes.length === 0;
    
    drawCanvas();
  }
}

function handleSimReset() {
  resetSimulation();
}

function resetSimulation() {
  simState = 'idle';
  simProgress = 0.0;
  pausedPosition = null;
  dynamicNewNodes = [];
  if (animationFrameId) {
    cancelAnimationFrame(animationFrameId);
    animationFrameId = null;
  }
  
  if (btnSimToggle) {
    btnSimToggle.innerHTML = '<i class="fa-solid fa-play"></i> 模擬配送';
    btnSimToggle.disabled = false;
  }
  if (btnSimPause) btnSimPause.disabled = true;
  if (btnSimReplan) {
    btnSimReplan.disabled = true;
    btnSimReplan.innerHTML = '<i class="fa-solid fa-route"></i> 重新規劃路徑';
  }
  if (simStatusText) simStatusText.textContent = '點擊「模擬配送」開始移動';
  
  const task = tasks.find(t => t.id === currentSelectedTaskId);
  if (task) {
    canvasModeBadge.textContent = `檢視模式: ${task.name}`;
  }
  
  drawCanvas();
}

function updateSimulation(timestamp) {
  if (simState !== 'running') return;
  
  const task = tasks.find(t => t.id === currentSelectedTaskId);
  if (!task || !task.result || !task.result.tour) {
    resetSimulation();
    return;
  }
  
  const tour = task.result.tour;
  const coords = task.coords;
  
  // 計算總物理像素距離
  let totalDist = 0;
  for (let i = 0; i < tour.length - 1; i++) {
    const pt1 = coords[tour[i]];
    const pt2 = coords[tour[i+1]];
    if (pt1 && pt2) {
      totalDist += Math.sqrt((pt2[0] - pt1[0])**2 + (pt2[1] - pt1[1])**2);
    }
  }
  
  const elapsed = timestamp - lastFrameTime;
  lastFrameTime = timestamp;
  
  // speed: 每秒前進 160 像素 (完全等速)
  const speed = 160; 
  simProgress += (elapsed / 1000) * speed;
  
  if (simProgress >= totalDist) {
    simProgress = totalDist;
    simState = 'idle';
    if (btnSimToggle) {
      btnSimToggle.innerHTML = '<i class="fa-solid fa-check"></i> 配送完成';
      btnSimToggle.disabled = true;
    }
    if (btnSimPause) btnSimPause.disabled = true;
    if (btnSimReplan) btnSimReplan.disabled = true;
    if (simStatusText) simStatusText.textContent = '模擬配送完成！已順利到達目的地。';
    drawCanvas();
  } else {
    const currentPercent = totalDist > 0 ? Math.round((simProgress / totalDist) * 100) : 100;
    if (simStatusText) simStatusText.textContent = `配送中... 已完成 ${currentPercent}% 的路程`;
    drawCanvas();
    animationFrameId = requestAnimationFrame(updateSimulation);
  }
}

function handleSimReplan() {
  const task = tasks.find(t => t.id === currentSelectedTaskId);
  if (!task || simState !== 'paused' || !pausedPosition) return;
  
  const tour = task.result.tour;
  const coords = task.coords;
  
  // 1. 找出當前線段的起點與未走完的原路徑點位
  // 計算累計距離與當前所屬段
  const segmentLengths = [];
  const cumulativeDistances = [0];
  for (let i = 0; i < tour.length - 1; i++) {
    const pt1 = coords[tour[i]];
    const pt2 = coords[tour[i+1]];
    const len = (pt1 && pt2) ? Math.sqrt((pt2[0] - pt1[0])**2 + (pt2[1] - pt1[1])**2) : 0;
    segmentLengths.push(len);
    cumulativeDistances.push(cumulativeDistances[i] + len);
  }
  const totalDist = cumulativeDistances[cumulativeDistances.length - 1];

  let currSegment = 0;
  if (simProgress >= totalDist) {
    currSegment = tour.length - 1;
  } else {
    for (let i = 0; i < segmentLengths.length; i++) {
      if (simProgress >= cumulativeDistances[i] && simProgress < cumulativeDistances[i+1]) {
        currSegment = i;
        break;
      }
    }
  }

  // 未走完的節點：從下一個節點 (currSegment + 1) 到倒數第二個節點 (tour.length - 2)
  const remainingOriginalNodes = [];
  for (let i = currSegment + 1; i < tour.length - 1; i++) {
    const pt = coords[tour[i]];
    if (pt) remainingOriginalNodes.push(pt);
  }
  
  // 固定原終點：最後一個點
  const fixedEndNode = coords[tour[tour.length - 1]];
  
  // 2. 組成新的坐標列表
  // [0] = 暫停座標 (起點)
  // [1..M] = 使用者新點 + 剩餘原點
  // [M+1] = 固定原終點
  const newCoords = [
    pausedPosition,
    ...dynamicNewNodes,
    ...remainingOriginalNodes,
    fixedEndNode
  ];
  
  const newName = `${task.name} (重規劃)`;
  
  if (btnSimReplan) {
    btnSimReplan.disabled = true;
    btnSimReplan.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> 規劃中...';
  }
  
  fetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: newName, coords: newCoords })
  })
  .then(res => {
    if (!res.ok) throw new Error('規劃任務提交失敗');
    return res.json();
  })
  .then(newTask => {
    // 重置模擬狀態
    resetSimulation();
    
    // 選取新產生的任務以顯示在畫布上
    currentSelectedTaskId = newTask.id;
    fetchTasks();
  })
  .catch(err => {
    alert('重新規劃路徑失敗：' + err.message);
    if (btnSimReplan) {
      btnSimReplan.disabled = false;
      btnSimReplan.innerHTML = '<i class="fa-solid fa-route"></i> 重新規劃路徑';
    }
  });
}


