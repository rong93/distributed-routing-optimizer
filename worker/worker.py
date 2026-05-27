import os
import sys
import time
import threading
import requests
from flask import Flask, request, jsonify
import multiprocessing

# 將目前目錄加入模組搜尋路徑，以便匯入 solver 模組
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from solver import solve_task_process

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 4000))           # Worker 監聽的 Port
WORKER_ID = os.environ.get('WORKER_ID', 'Worker Test')  # Worker 的識別名稱
MASTER_URL = os.environ.get('MASTER_URL', 'http://localhost:3000')  # Master 的 URL

current_process = None   # 目前正在執行的 TSP 求解子程序
current_task_id = None   # 目前正在處理的子任務 ID

@app.route('/health', methods=['GET'])
def health():
    """心跳檢測端點：Master 每 3 秒會呼叫此 API 確認 Worker 是否存活。"""
    global current_process, current_task_id
    
    # 如果子程序已結束（正常完成或異常退出），清除狀態
    if current_process and not current_process.is_alive():
        current_process = None
        current_task_id = None
        
    return jsonify({
        "status": "ok",
        "workerId": WORKER_ID,
        "currentTaskId": current_task_id
    })

@app.route('/solve', methods=['POST'])
def solve():
    """接收 Master 分派的 TSP 子任務並啟動求解程序。"""
    global current_process, current_task_id
    
    if current_process and not current_process.is_alive():
        current_process = None
        current_task_id = None

    if current_task_id:
        return jsonify({"error": "Worker is busy"}), 400

    data = request.get_json() or {}
    task_id = data.get('taskId')
    coords = data.get('coords')
    first_step = data.get('firstStep')

    if not task_id or not coords or not isinstance(coords, list):
        return jsonify({"error": "Invalid parameters"}), 400

    current_task_id = task_id
    
    # 在獨立的子程序 (Process) 中啟動 TSP 求解器，以繞過 Python 的 GIL 限制
    current_process = multiprocessing.Process(
        target=solve_task_process,
        args=(task_id, coords, MASTER_URL, WORKER_ID, first_step)
    )
    current_process.daemon = True
    current_process.start()

    print(f"[{WORKER_ID}] Started solver process for task {task_id} (PID: {current_process.pid})")
    return jsonify({"message": "Solving started"})

@app.route('/cancel', methods=['POST'])
def cancel():
    """取消目前正在執行的 TSP 子任務（由 Master 呼叫）。"""
    global current_process, current_task_id
    data = request.get_json() or {}
    task_id = data.get('taskId')

    if current_task_id and current_task_id == task_id:
        print(f"[{WORKER_ID}] Cancelling task {task_id}")
        if current_process and current_process.is_alive():
            current_process.terminate()
            current_process.join(timeout=1.0)
            if current_process.is_alive():
                # 如果 terminate 無法停止，強制殺掉子程序
                current_process.kill()
        
        current_process = None
        current_task_id = None
        return jsonify({"message": "Task cancelled"})
    
    return jsonify({"message": "Task not running on this worker"})


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

def telemetry_thread_loop():
    """定期向 Master 回報系統資源使用狀況（CPU、記憶體）。
    優先讀取 cgroups，若讀取失敗則備用讀取主機資訊。"""
    print(f"[{WORKER_ID}] Telemetry thread active.")
    while True:
        try:
            cpu = get_container_cpu_percent()
            mem = get_container_mem_percent()

            if cpu is None:
                cpu = get_fallback_cpu()
            if mem is None:
                mem = get_fallback_mem()
            
            # 將數值限制在 0~100 的合理範圍內
            cpu = max(0.0, min(100.0, cpu))
            mem = max(0.0, min(100.0, mem))

            payload = {
                "workerId": WORKER_ID,
                "cpu": round(cpu, 1),
                "memory": round(mem, 1)
            }
            # 透過 HTTP POST 將資源使用數據回報給 Master，增加逾時保護以防 Master 忙碌
            requests.post(f"{MASTER_URL}/api/monitor/report", json=payload, timeout=5)
        except Exception:
            # Master 啟動期間可能尚未就緒，靜默忽略錯誤
            pass
        time.sleep(1)  # 將遙測回報間隔改為 1 秒以達到即時更新效果

if __name__ == '__main__':
    # 啟動系統資源回報的背景執行緒（每 3 秒回報一次）
    telemetry_thread = threading.Thread(target=telemetry_thread_loop, daemon=True)
    telemetry_thread.start()

    print(f"[{WORKER_ID}] Launching Flask on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
