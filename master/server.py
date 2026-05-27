import os
import time
import re
import threading
import subprocess
import requests
import socket
import http.client
import json
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
            # 透過 HTTP POST 將任務資料傳送給 Worker 的 /solve 端點，增加逾時為 10.0 秒以適應高 CPU 負載
            res = requests.post(f"{available_worker['url']}/solve", json=payload, timeout=10.0)
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

def update_task_status_and_workers(task):
    """根據子任務的狀態，重新計算主任務的狀態以及參與的 Worker 列表。"""
    if not task.get("subtasks"):
        return
        
    if task.get("status") in ("completed", "failed"):
        return

    # 1. 重新計算目前「正在參與」或「曾經參與」計算的 Worker 列表
    all_workers = {sub["workerId"] for sub in task["subtasks"] if sub["workerId"]}
    task["workerId"] = ", ".join(sorted(all_workers)) if all_workers else None

    # 2. 檢查子任務狀態
    all_done = True
    any_running = False
    any_queued = False
    
    for sub in task["subtasks"]:
        if sub["status"] == "running":
            any_running = True
            all_done = False
        elif sub["status"] == "queued":
            any_queued = True
            all_done = False

    if all_done:
        completed_subs = [sub for sub in task["subtasks"] if sub["status"] == "completed" and sub["result"]]
        if completed_subs:
            task["status"] = "completed"
            best_sub = min(completed_subs, key=lambda x: x["result"]["distance"])
            task["result"] = best_sub["result"].copy()
            if "startTime" in task:
                elapsed_ms = int((time.time() - task["startTime"]) * 1000)
                task["result"]["time"] = max(1, elapsed_ms)
            print(f"[Master] Task {task['id']} fully completed! Best distance: {task['result']['distance']} via {best_sub['workerId']}")
        else:
            task["status"] = "failed"
            errors = [sub["error"] for sub in task["subtasks"] if sub["error"]]
            task["error"] = errors[0] if errors else "All subtasks failed"
            print(f"[Master] Task {task['id']} failed. All subtasks failed.")
    else:
        if any_running:
            task["status"] = "running"
        elif any_queued:
            # 只有當所有 Worker 都處於離線狀態（無法繼續運算）時，才將狀態設為「等待中」；
            # 或者當有線上 Worker 但全部都忙碌（Busy）於其他任務時，此任務才真正「等待中」；
            # 否則如果有任何 Worker 空閒 (online)，代表即將立刻分派，維持「執行中」以避免前端狀態閃爍。
            if all(w["status"] == "offline" for w in workers):
                task["status"] = "queued"
            elif any(w["status"] == "online" for w in workers):
                task["status"] = "running"
            else:
                # 所有在線 Worker 都忙碌中，任務在排隊等待
                task["status"] = "queued"

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
            
            # 使用統一方法更新狀態與參與者
            update_task_status_and_workers(task)
                
        if worker:
            # 僅清除當前任務，但不立刻標記為離線（offline），交由心跳檢測 (heartbeat_loop) 統一判定
            if worker.get("currentTaskId") == subtask_id:
                worker["currentTaskId"] = None
            handle_worker_failure(worker)

def reclaim_worker_tasks(worker_id):
    """將分配給特定離線 Worker 的所有執行中子任務重新排程。"""
    orphaned_subtasks = []
    with db_lock:
        for t in tasks:
            if t.get("subtasks"):
                for sub in t["subtasks"]:
                    if sub["status"] == "running" and sub["workerId"] == worker_id:
                        orphaned_subtasks.append(sub["id"])
                        
    for subtask_id in orphaned_subtasks:
        reschedule_task(subtask_id, worker_id)

    # 額外安全檢查：確保所有未完成任務的狀態與子任務狀態保持一致
    with db_lock:
        for t in tasks:
            update_task_status_and_workers(t)

def handle_worker_failure(worker):
    """處理 Worker 失敗：累計失敗次數，達到 3 次後標記為離線並重新分配其任務。"""
    worker["failCount"] += 1
    if worker["failCount"] >= 3:
        if worker["status"] != "offline":
            print(f"[Master] Worker {worker['id']} marked OFFLINE (heartbeat missed).")
            worker["status"] = "offline"
            worker["currentTaskId"] = None
            threading.Thread(target=reclaim_worker_tasks, args=(worker["id"],)).start()

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
            
            # 使用統一方法更新狀態與參與者
            update_task_status_and_workers(target_task)
            
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
        "startTime": time.time(),  # 紀錄主任務提交時的起點時間（用於計算總耗時）
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
                
        # 先將完成任務的 Worker 狀態改回 online（空閒），準備接下一個子任務
        # 這樣當 update_task_status_and_workers 檢查是否有可用 Worker 時，此 Worker 已被視為 online
        worker = next((w for w in workers if w["id"] == worker_id), None)
        if worker and worker["currentTaskId"] == subtask_id:
            worker["status"] = "online"
            worker["currentTaskId"] = None
            worker["failCount"] = 0

        if target_task and target_subtask:
            if error:
                target_subtask["status"] = "failed"
                target_subtask["error"] = error
                print(f"[Master] Subtask {subtask_id} failed on {worker_id}: {error}")
            else:
                target_subtask["status"] = "completed"
                target_subtask["result"] = result
                print(f"[Master] Subtask {subtask_id} completed on {worker_id}. Distance: {result['distance']}")

            # 使用統一方法更新狀態與參與者（自動處理 completed / failed / queued / running 等狀態轉換）
            update_task_status_and_workers(target_task)

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

# Docker API 輔助工具：透過 Unix Socket 與 Docker Daemon 通訊
class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)

def docker_api_request(method, path):
    """透過 Unix Socket /var/run/docker.sock 對 Docker API 發送 HTTP 請求。"""
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        print(f"[Docker API] Socket file not found at {socket_path}")
        return None
    try:
        conn = UnixHTTPConnection(socket_path)
        conn.request(method, path)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        if res.status in (200, 201, 204):
            if data:
                return json.loads(data.decode("utf-8"))
            return True
        else:
            print(f"[Docker API] HTTP {res.status}: {data.decode('utf-8')}")
            return None
    except Exception as e:
        print(f"[Docker API] Error communicating with socket: {e}")
        return None

# Worker ID 與 Docker 容器名稱對照表
WORKER_CONTAINERS = {
    "Worker A": "tsp-worker-a",
    "Worker B": "tsp-worker-b",
    "Worker C": "tsp-worker-c"
}

def get_container_status(container_name):
    """查詢容器目前的運行狀態。"""
    res = docker_api_request("GET", f"/containers/{container_name}/json")
    if res and "State" in res and "Status" in res["State"]:
        return res["State"]["Status"]  # 例如 "running", "exited"
    return "unknown"

def toggle_container(container_name):
    """根據容器當前狀態，進行啟動或關閉。"""
    status = get_container_status(container_name)
    if status == "running":
        print(f"[Docker API] Stopping container {container_name}...")
        # 呼叫 Docker Stop API，帶入 t=1 參數（優雅退場時間為 1 秒），避免預設 10 秒的卡頓
        res = docker_api_request("POST", f"/containers/{container_name}/stop?t=1")
        return "stopped" if res else "error"
    else:
        print(f"[Docker API] Starting container {container_name}...")
        # 呼叫 Docker Start API
        res = docker_api_request("POST", f"/containers/{container_name}/start")
        return "running" if res else "error"

# API：控制 Worker 容器的開啟與關閉
@app.route('/api/workers/<worker_id>/toggle', methods=['POST'])
def toggle_worker_container(worker_id):
    container_name = WORKER_CONTAINERS.get(worker_id)
    if not container_name:
        return jsonify({"error": f"Worker {worker_id} not found"}), 404
        
    res_status = toggle_container(container_name)
    if res_status == "error":
        return jsonify({"error": "Failed to toggle container state"}), 500
        
    # 如果容器被手動關閉，後端主動立即標記狀態為離線，並回收未完成的子任務
    if res_status == "stopped":
        with db_lock:
            worker = next((w for w in workers if w["id"] == worker_id), None)
            if worker:
                worker["status"] = "offline"
                worker["currentTaskId"] = None
                # 在背景非同步重分配任務，防止 API 卡死
                threading.Thread(target=reclaim_worker_tasks, args=(worker["id"],)).start()
                    
    return jsonify({"status": "success", "containerStatus": res_status})

# 背景快取：定期從 Docker Daemon 取得各 Worker 容器的實際運行狀態（避免在 API 請求中即時查詢造成鎖阻塞）
container_status_cache = {
    "Worker A": "unknown",
    "Worker B": "unknown",
    "Worker C": "unknown"
}

def container_status_poller():
    """背景執行緒：每 2 秒更新一次各 Worker 容器的 Docker 運行狀態快取。"""
    print("[Master] Container status poller thread active.")
    while True:
        for worker_id, container_name in WORKER_CONTAINERS.items():
            try:
                status = get_container_status(container_name)
                container_status_cache[worker_id] = status
            except Exception:
                container_status_cache[worker_id] = "unknown"
        time.sleep(2)

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
                
        workers_list = []
        for w in workers:
            workers_list.append({
                "id": w["id"],
                "status": w["status"],
                "failCount": w["failCount"],
                "currentTaskId": w["currentTaskId"],
                "containerStatus": container_status_cache.get(w["id"], "unknown")
            })
        
    return jsonify({
        "workers": workers_list,
        "resources": resource_stats
    })

# 心跳檢測迴圈：在背景執行緒中每 3 秒檢查一次所有 Worker 是否存活
def heartbeat_loop():
    print("[Master] Heartbeat thread active.")
    while True:
        for w in workers:
            # 如果容器已被使用者手動停止，直接標記為 offline，跳過 TCP 健康檢查
            # 避免對已停止容器的 5 秒 TCP 逾時等待拖慢整個心跳迴圈
            cached_cstatus = container_status_cache.get(w["id"], "running")
            if cached_cstatus != "running":
                with db_lock:
                    if w["status"] != "offline":
                        w["status"] = "offline"
                        w["currentTaskId"] = None
                        print(f"[Master] Worker {w['id']} container stopped, marking offline.")
                        threading.Thread(target=reclaim_worker_tasks, args=(w["id"],)).start()
                continue

            try:
                res = requests.get(f"{w['url']}/health", timeout=2.0)
                if res.status_code == 200:
                    body = res.json()
                    with db_lock:
                        old_status = w["status"]
                        w["status"] = "busy" if body.get("currentTaskId") else "online"
                        w["currentTaskId"] = body.get("currentTaskId")
                        w["failCount"] = 0
                        resource_stats[w["id"]]["lastReport"] = int(time.time() * 1000)
                        
                        if old_status == "offline":
                            print(f"[Master] Worker {w['id']} recovered and is ONLINE.")
                            threading.Thread(target=dispatch_pending_tasks).start()
                else:
                    with db_lock:
                        handle_worker_failure(w)
            except Exception:
                with db_lock:
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

def get_container_mem_percent():
    try:
        host_total_bytes = 0
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        host_total_bytes = int(line.split()[1]) * 1024
                        break
        except:
            pass

        if os.path.exists('/sys/fs/cgroup/memory.current'):
            with open('/sys/fs/cgroup/memory.current', 'r') as f:
                usage = int(f.read().strip())
            limit = None
            if os.path.exists('/sys/fs/cgroup/memory.max'):
                with open('/sys/fs/cgroup/memory.max', 'r') as f:
                    limit_str = f.read().strip()
                    if limit_str != 'max':
                        limit = int(limit_str)
            if not limit or limit <= 0:
                limit = host_total_bytes
            if limit > 0:
                return (usage / limit) * 100.0

        elif os.path.exists('/sys/fs/cgroup/memory/memory.usage_in_bytes'):
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
                usage = int(f.read().strip())
            limit = None
            if os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes'):
                with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                    limit = int(f.read().strip())
            if not limit or limit <= 0 or limit > 9000000000000000000:
                limit = host_total_bytes
            if limit > 0:
                return (usage / limit) * 100.0
    except:
        pass
    return None

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

prev_container_cpu_val = 0.0
prev_container_cpu_time = 0.0

def get_container_cpu_percent():
    global prev_container_cpu_val, prev_container_cpu_time
    try:
        cpu_val = None
        if os.path.exists('/sys/fs/cgroup/cpu.stat'):
            with open('/sys/fs/cgroup/cpu.stat', 'r') as f:
                for line in f:
                    if line.startswith('usage_usec'):
                        cpu_val = float(line.split()[1]) / 1000000.0
                        break
        elif os.path.exists('/sys/fs/cgroup/cpuacct/cpuacct.usage'):
            with open('/sys/fs/cgroup/cpuacct/cpuacct.usage', 'r') as f:
                cpu_val = float(f.read().strip()) / 1000000000.0

        if cpu_val is not None:
            now = time.time()
            if prev_container_cpu_time > 0:
                time_delta = now - prev_container_cpu_time
                cpu_delta = cpu_val - prev_container_cpu_val
                if time_delta > 0 and cpu_delta >= 0:
                    cores = os.cpu_count() or 1
                    pct = (cpu_delta / time_delta) * 100.0 / cores
                    prev_container_cpu_val = cpu_val
                    prev_container_cpu_time = now
                    return min(100.0, max(0.0, pct))
            prev_container_cpu_val = cpu_val
            prev_container_cpu_time = now
            return 0.0
    except:
        pass
    return None

def master_telemetry_loop():
    """Master 自身的遙測迴圈：每 3 秒收集一次 CPU 和記憶體使用率。"""
    print("[Master] Telemetry thread active.")
    while True:
        try:
            cpu = get_container_cpu_percent()
            mem = get_container_mem_percent()

            if cpu is None:
                cpu, _ = parse_top_metrics()
                if cpu is None:
                    cpu = get_fallback_cpu()
            if mem is None:
                _, mem = parse_top_metrics()
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
        time.sleep(1)  # 將遙測間隔縮短為 1 秒，讓資源狀態呈現更即時

if __name__ == '__main__':
    # 啟動心跳檢測背景執行緒（持續監控 Worker 是否存活）
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    # 啟動系統資源監控背景執行緒（持續收集 Master 的 CPU / 記憶體使用率）
    telemetry_thread = threading.Thread(target=master_telemetry_loop, daemon=True)
    telemetry_thread.start()
    
    # 啟動 Docker 容器狀態輪詢背景執行緒（每 2 秒快取一次各 Worker 的容器運行狀態）
    container_poller_thread = threading.Thread(target=container_status_poller, daemon=True)
    container_poller_thread.start()
    
    print(f"[Master] Launching Flask on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
