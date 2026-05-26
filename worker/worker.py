import os
import sys
import time
import re
import threading
import subprocess
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

def parse_top_metrics():
    """透過 top 指令解析目前的 CPU 和記憶體使用率。"""
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

def telemetry_thread_loop():
    """定期向 Master 回報系統資源使用狀況（CPU、記憶體）。
    直接從 /proc/stat 和 /proc/meminfo 讀取，避免 top 指令在高負載下搶占 CPU。"""
    print(f"[{WORKER_ID}] Telemetry thread active.")
    while True:
        try:
            cpu = get_fallback_cpu()
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
