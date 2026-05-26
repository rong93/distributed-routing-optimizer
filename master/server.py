import os
import time
import re
import threading
import subprocess
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public')
CORS(app)

PORT = 3000

# 執行緒安全鎖：確保多執行緒同時存取任務列表和 Worker 狀態時不會產生資料競爭
db_lock = threading.Lock()

tasks = []  # 所有任務的列表（包含主任務及其子任務）

# Worker 節點清單：定義了三個 Worker Container 的 ID、URL 及初始狀態
workers = [
    { "id": "Worker A", "url": "http://worker-a:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 },
    { "id": "Worker B", "url": "http://worker-b:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 },
    { "id": "Worker C", "url": "http://worker-c:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 }
]

# 系統資源監控資料：紀錄 Master 和各 Worker 的 CPU / 記憶體使用率
resource_stats = {
    'Master': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker A': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker B': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker C': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 }
}

# 提供前端靜態頁面（儀表板 Dashboard）
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

# 提供前端靜態資源（CSS、JS、圖片等）
@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)

def dispatch_pending_tasks():
    """從佇列中找出等待中的子任務，並分配給空閒的 Worker 去執行。"""
    with db_lock:
        pending_subtask = None
        target_task = None
        
        # 依任務 ID 降冪排序，優先處理較新的任務
        for t in sorted(tasks, key=lambda x: x["id"], reverse=True):
            if t["status"] in ("queued", "running") and t.get("subtasks"):
                for sub in t["subtasks"]:
                    if sub["status"] == "queued":
                        pending_subtask = sub
                        target_task = t
                        break
            if pending_subtask:
                break
                
        # 如果沒有等待中的子任務，直接返回
        if not pending_subtask or not target_task:
            return

        # 尋找狀態為 online（空閒）的 Worker
        available_worker = None
        for w in workers:
            if w["status"] == "online":
                available_worker = w
                break

        # 如果沒有空閒的 Worker，直接返回（等下次心跳檢查再試）
        if not available_worker:
            return

        # 預留子任務、主任務和 Worker 的狀態（避免重複分配）
        pending_subtask["status"] = "running"
        pending_subtask["workerId"] = available_worker["id"]
        
        target_task["status"] = "running"
        
        # 更新主任務上的 Worker 顯示，列出所有正在參與計算的 Worker
        active_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
        target_task["workerId"] = ", ".join(sorted(active_workers))
        
        # 將 Worker 標記為忙碌，並記錄目前正在處理的子任務 ID
        available_worker["status"] = "busy"
        available_worker["currentTaskId"] = pending_subtask["id"]

    print(f"[Master] Dispatching subtask {pending_subtask['id']} (firstStep={pending_subtask['first_step']}) to {available_worker['id']}")

    # 組裝要傳送給 Worker 的資料（子任務 ID、座標、指定的第一步）
    payload = {
        "taskId": pending_subtask["id"],
        "coords": target_task["coords"],
        "firstStep": pending_subtask["first_step"]
    }
    
    # 非同步分發子任務給 Worker（避免阻塞主執行緒）
    def async_dispatch():
        try:
            # 透過 HTTP POST 將任務資料傳送給 Worker 的 /solve 端點
            res = requests.post(f"{available_worker['url']}/solve", json=payload, timeout=3)
            if res.status_code != 200:
                print(f"[Master] Worker {available_worker['id']} rejected subtask {pending_subtask['id']}: {res.text}")
                reset_subtask(target_task["id"], pending_subtask["id"], available_worker["id"])
            # 嘗試繼續分發佇列中其他等待的子任務
            threading.Thread(target=dispatch_pending_tasks).start()
        except Exception as e:
            print(f"[Master] Failed to dispatch subtask {pending_subtask['id']} to {available_worker['id']}: {str(e)}")
            reset_subtask(target_task["id"], pending_subtask["id"], available_worker["id"])
            # 分發失敗後也嘗試繼續分發其他子任務
            threading.Thread(target=dispatch_pending_tasks).start()

    # 在背景執行緒中執行網路請求
    threading.Thread(target=async_dispatch).start()

def reset_subtask(task_id, subtask_id, worker_id):
    """當 Worker 分發失敗時，將子任務狀態還原回佇列中，等待重新分配。"""
    with db_lock:
        task = next((t for t in tasks if t["id"] == task_id), None)
        worker = next((w for w in workers if w["id"] == worker_id), None)
        
        if task and task.get("subtasks"):
            subtask = next((s for s in task["subtasks"] if s["id"] == subtask_id), None)
            if subtask and subtask["status"] == "running":
                subtask["status"] = "queued"
                subtask["workerId"] = None
            
            # 重新計算目前仍在參與計算的 Worker 清單
            active_workers = {sub["workerId"] for sub in task["subtasks"] if sub["workerId"]}
            task["workerId"] = ", ".join(sorted(active_workers)) if active_workers else None
            if not active_workers:
                task["status"] = "queued"
                
        if worker:
            worker["status"] = "offline"
            worker["currentTaskId"] = None
            handle_worker_failure(worker)

def handle_worker_failure(worker):
    """處理 Worker 失敗：累計失敗次數，達到 3 次後標記為離線並重新分配其任務。"""
    worker["failCount"] += 1
    if worker["failCount"] >= 3:
        if worker["status"] != "offline":
            print(f"[Master] Worker {worker['id']} marked OFFLINE (heartbeat missed).")
            worker["status"] = "offline"
            
            if worker["currentTaskId"]:
                failed_task_id = worker["currentTaskId"]
                worker["currentTaskId"] = None
                reschedule_task(failed_task_id, worker["id"])

def reschedule_task(subtask_id, worker_id):
    """子任務重新排程：將離線 Worker 正在處理的子任務放回佇列，等待其他 Worker 接手。"""
    with db_lock:
        target_task = None
        target_subtask = None
        for t in tasks:
            if t.get("subtasks"):
                for sub in t["subtasks"]:
                    if sub["id"] == subtask_id:
                        target_task = t
                        target_subtask = sub
                        break
            if target_task:
                break
                
        if target_task and target_subtask and target_subtask["status"] == "running":
            print(f"[Master] Rescheduling subtask {subtask_id} (lost from offline worker {worker_id})")
            target_subtask["status"] = "queued"
            target_subtask["workerId"] = None
            
            # 重新計算目前仍在參與計算的 Worker 清單
            active_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
            target_task["workerId"] = ", ".join(sorted(active_workers)) if active_workers else None
            
    # 等待 0.5 秒後嘗試重新分發佇列中的子任務
    time.sleep(0.5)
    dispatch_pending_tasks()

def send_cancel_to_worker(worker, task_id):
    """通知 Worker 停止計算指定的子任務。"""
    def async_cancel():
        try:
            requests.post(f"{worker['url']}/cancel", json={"taskId": task_id}, timeout=2)
        except Exception as e:
            print(f"[Master] Failed to cancel task on {worker['id']}: {str(e)}")
    threading.Thread(target=async_cancel).start()

# API：提交新任務（前端使用者點擊「開始計算」時呼叫）
@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.get_json() or {}
    name = data.get('name')
    coords = data.get('coords')

    # 驗證座標資料：至少需要 2 個點
    if not coords or not isinstance(coords, list) or len(coords) < 2:
        return jsonify({"error": "Coordinates list must contain at least 2 points"}), 400

    # 驗證每個座標的格式是否為 [x, y] 的數值陣列
    for p in coords:
        if not isinstance(p, list) or len(p) != 2 or not isinstance(p[0], (int, float)) or not isinstance(p[1], (int, float)):
            return jsonify({"error": "Invalid coordinate format"}), 400

    # 產生唯一的任務 ID（時間戳 + 隨機碼）
    task_id = f"task_{int(time.time()*1000)}_{os.urandom(2).hex()}"
    
    # 產生子任務：每個中間節點各產生一個子任務（固定不同的第一步）
    # 例如 15 個點 -> 扣掉起點和終點 -> 產生 13 個子任務
    subtasks = []
    if len(coords) > 2:
        subtasks = [
            {
                "id": f"{task_id}_{f}",
                "first_step": f,       # 指定這個子任務的第一步必須走哪個節點
                "status": "queued",    # 初始狀態為佇列中，等待 Worker 來領取
                "workerId": None,
                "result": None,
                "error": None
            }
            for f in range(1, len(coords) - 1)  # 從節點 1 到節點 N-2
        ]
    else:
        # 當節點數 <= 2 時，只產生 1 個子任務且不限制第一步
        subtasks = [
            {
                "id": f"{task_id}_0",
                "first_step": None,
                "status": "queued",
                "workerId": None,
                "result": None,
                "error": None
            }
        ]

    # 建立主任務物件
    task = {
        "id": task_id,
        "name": name or f"TSP Task #{len(tasks) + 1}",
        "coords": coords,          # 所有節點的座標
        "status": "queued",        # 主任務狀態
        "workerId": None,          # 參與計算的 Worker 名稱（完成後會列出所有參與者）
        "result": None,            # 最終計算結果（所有子任務中距離最短的那個）
        "error": None,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "subtasks": subtasks       # 所有子任務列表
    }

    with db_lock:
        tasks.append(task)

    print(f"[Master] Created task {task['id']} with {len(coords)} points and {len(subtasks)} subtasks.")
    
    # 在背景執行緒中嘗試將子任務分發給空閒的 Worker
    threading.Thread(target=dispatch_pending_tasks).start()
    
    return jsonify(task)

# API：取得所有任務列表（前端儀表板用來輪詢顯示任務狀態）
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    with db_lock:
        sorted_tasks = sorted(tasks, key=lambda x: x["id"], reverse=True)
    return jsonify(sorted_tasks)

# API：取消並刪除指定的任務
@app.route('/api/tasks/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    global tasks
    target_task = None
    
    with db_lock:
        for t in tasks:
            if t["id"] == task_id:
                target_task = t
                break
        
        if not target_task:
            return jsonify({"error": "Task not found"}), 404

        print(f"[Master] Cancelling task {task_id} (Status: {target_task['status']})")
        
        if target_task.get("subtasks"):
            for sub in target_task["subtasks"]:
                if sub["status"] == "running" and sub["workerId"]:
                    worker = next((w for w in workers if w["id"] == sub["workerId"]), None)
                    if worker:
                        send_cancel_to_worker(worker, sub["id"])
                        worker["status"] = "online"
                        worker["currentTaskId"] = None
                
        # 從任務列表中移除該任務
        tasks = [t for t in tasks if t["id"] != task_id]
        
    threading.Thread(target=dispatch_pending_tasks).start()
    return jsonify({"message": "Task deleted successfully"})

# API：Worker 完成計算後的回呼端點（Worker 算完後會主動呼叫此 API 回報結果）
@app.route('/api/tasks/complete', methods=['POST'])
def task_complete():
    data = request.get_json() or {}
    subtask_id = data.get('taskId')
    worker_id = data.get('workerId')
    result = data.get('result')
    error = data.get('error')

    with db_lock:
        target_task = None
        target_subtask = None
        for t in tasks:
            if t.get("subtasks"):
                for sub in t["subtasks"]:
                    if sub["id"] == subtask_id:
                        target_task = t
                        target_subtask = sub
                        break
            if target_task:
                break
                
        if target_task and target_subtask:
            if error:
                target_subtask["status"] = "failed"
                target_subtask["error"] = error
                print(f"[Master] Subtask {subtask_id} failed on {worker_id}: {error}")
            else:
                target_subtask["status"] = "completed"
                target_subtask["result"] = result
                print(f"[Master] Subtask {subtask_id} completed on {worker_id}. Distance: {result['distance']}")

            # 檢查該主任務的所有子任務是否都已完成
            all_done = True
            for sub in target_task["subtasks"]:
                if sub["status"] in ("queued", "running"):
                    all_done = False
                    break
            
            if all_done:
                # 篩選出所有成功完成的子任務
                completed_subs = [sub for sub in target_task["subtasks"] if sub["status"] == "completed" and sub["result"]]
                if completed_subs:
                    # 從所有子任務的結果中，找出距離最短的作為最終最佳解
                    best_sub = min(completed_subs, key=lambda x: x["result"]["distance"])
                    target_task["status"] = "completed"
                    target_task["result"] = best_sub["result"]
                    
                    # 記錄所有參與計算的 Worker
                    all_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
                    target_task["workerId"] = ", ".join(sorted(all_workers))
                    print(f"[Master] Task {target_task['id']} fully completed! Best distance: {target_task['result']['distance']} via {best_sub['workerId']}")
                else:
                    target_task["status"] = "failed"
                    errors = [sub["error"] for sub in target_task["subtasks"] if sub["error"]]
                    target_task["error"] = errors[0] if errors else "All subtasks failed"
                    print(f"[Master] Task {target_task['id']} failed. All subtasks failed.")

        # 將完成任務的 Worker 狀態改回 online（空閒），準備接下一個子任務
        worker = next((w for w in workers if w["id"] == worker_id), None)
        if worker and worker["currentTaskId"] == subtask_id:
            worker["status"] = "online"
            worker["currentTaskId"] = None
            worker["failCount"] = 0

    threading.Thread(target=dispatch_pending_tasks).start()
    return jsonify({"message": "Acknowledged"})

# API：Worker 回報系統資源使用狀況（CPU、記憶體）
@app.route('/api/monitor/report', methods=['POST'])
def monitor_report():
    data = request.get_json() or {}
    worker_id = data.get('workerId')
    cpu = data.get('cpu')
    memory = data.get('memory')

    if worker_id in resource_stats:
        resource_stats[worker_id] = {
            "cpu": float(cpu),
            "memory": float(memory),
            "lastReport": int(time.time() * 1000)
        }
    return jsonify({"message": "Acknowledge stats report"})

# API：取得整體系統監控資訊（前端儀表板用來顯示 Worker 狀態和資源使用率）
@app.route('/api/monitor', methods=['GET'])
def get_monitor():
    now_ms = int(time.time() * 1000)
    with db_lock:
        for w in workers:
            last_rep = resource_stats[w["id"]]["lastReport"]
            if w["status"] == "offline" or (now_ms - last_rep > 10000):
                resource_stats[w["id"]]["cpu"] = 0.0
                resource_stats[w["id"]]["memory"] = 0.0
                
        workers_list = [
            {
                "id": w["id"],
                "status": w["status"],
                "failCount": w["failCount"],
                "currentTaskId": w["currentTaskId"]
            }
            for w in workers
        ]
        
    return jsonify({
        "workers": workers_list,
        "resources": resource_stats
    })

# 心跳檢測迴圈：在背景執行緒中每 3 秒檢查一次所有 Worker 是否存活
def heartbeat_loop():
    print("[Master] Heartbeat thread active.")
    while True:
        for w in workers:
            try:
                res = requests.get(f"{w['url']}/health", timeout=1.5)
                if res.status_code == 200:
                    body = res.json()
                    old_status = w["status"]
                    w["status"] = "busy" if body.get("currentTaskId") else "online"
                    w["currentTaskId"] = body.get("currentTaskId")
                    w["failCount"] = 0
                    resource_stats[w["id"]]["lastReport"] = int(time.time() * 1000)
                    
                    if old_status == "offline":
                        print(f"[Master] Worker {w['id']} recovered and is ONLINE.")
                        threading.Thread(target=dispatch_pending_tasks).start()
                else:
                    handle_worker_failure(w)
            except Exception:
                handle_worker_failure(w)
        
        # 每輪心跳結束後，順便檢查是否有待分發的子任務
        dispatch_pending_tasks()
        time.sleep(3)  # 每 3 秒執行一輪心跳檢查

# Master 自身的系統資源指標收集（透過 top 指令解析 CPU 和記憶體使用率）
def parse_top_metrics():
    try:
        result = subprocess.run(['top', '-bn', '1', '-i', '-c'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        stdout = result.stdout
        
        cpu_usage = None
        mem_usage = None

        # 解析 CPU 使用率（從閒置率反推）
        idle_match = re.search(r'([\d.,]+)\s+id', stdout)
        if idle_match:
            idle = float(idle_match.group(1).replace(',', '.'))
            cpu_usage = 100.0 - idle
            
        # 解析記憶體使用率
        mem_match = re.search(r'(?:Mem|MiB Mem|KiB Mem)\s*:\s*([\d.,]+)\s+total,\s*([\d.,]+)\s+free,\s*([\d.,]+)\s+used', stdout, re.IGNORECASE)
        if mem_match:
            total = float(mem_match.group(1).replace(',', '.'))
            used = float(mem_match.group(3).replace(',', '.'))
            if total > 0:
                mem_usage = (used / total) * 100.0

        return cpu_usage, mem_usage
    except:
        return None, None

def get_fallback_mem():
    """備用方案：當 top 指令無法取得記憶體資訊時，改從 /proc/meminfo 讀取。"""
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                mem[parts[0].rstrip(':')] = int(parts[1])
        total = mem.get('MemTotal', 0)
        free = mem.get('MemFree', 0)
        buffers = mem.get('Buffers', 0)
        cached = mem.get('Cached', 0)
        used = total - free - buffers - cached
        if total > 0:
            return (used / total) * 100.0
    except:
        pass
    return 10.0

prev_idle = 0.0
prev_total = 0.0
def get_fallback_cpu():
    """備用方案：當 top 指令無法取得 CPU 資訊時，改從 /proc/stat 讀取。"""
    global prev_idle, prev_total
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        idle = float(parts[4])
        total = sum(float(x) for x in parts[1:8])
        
        diff_idle = idle - prev_idle
        diff_total = total - prev_total
        prev_idle = idle
        prev_total = total
        
        if diff_total == 0:
            return 0.0
        return (1.0 - diff_idle / diff_total) * 100.0
    except:
        return 5.0

def master_telemetry_loop():
    """Master 自身的遙測迴圈：每 3 秒收集一次 CPU 和記憶體使用率。"""
    print("[Master] Telemetry thread active.")
    while True:
        try:
            cpu, mem = parse_top_metrics()
            if cpu is None:
                cpu = get_fallback_cpu()
            if mem is None:
                mem = get_fallback_mem()
                
            cpu = max(0.0, min(100.0, cpu))
            mem = max(0.0, min(100.0, mem))
            
            resource_stats['Master'] = {
                "cpu": round(cpu, 1),
                "memory": round(mem, 1),
                "lastReport": int(time.time() * 1000)
            }
        except Exception:
            pass
        time.sleep(3)

if __name__ == '__main__':
    # 啟動心跳檢測背景執行緒（持續監控 Worker 是否存活）
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    # 啟動系統資源監控背景執行緒（持續收集 Master 的 CPU / 記憶體使用率）
    telemetry_thread = threading.Thread(target=master_telemetry_loop, daemon=True)
    telemetry_thread.start()
    
    print(f"[Master] Launching Flask on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
