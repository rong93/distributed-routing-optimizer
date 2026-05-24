import os
import sys
import time
import re
import threading
import subprocess
import requests
from flask import Flask, request, jsonify
import multiprocessing

# Add current directory to path for solver import
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from solver import solve_task_process

app = Flask(__name__)

PORT = int(os.environ.get('PORT', 4000))
WORKER_ID = os.environ.get('WORKER_ID', 'Worker Test')
MASTER_URL = os.environ.get('MASTER_URL', 'http://localhost:3000')

current_process = None
current_task_id = None

@app.route('/health', methods=['GET'])
def health():
    """Heartbeat and status endpoint."""
    global current_process, current_task_id
    
    # Sync status if process completed in background
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
    """Launch TSP solver on a new task."""
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
    
    # Start TSP Solver in a separate Process to bypass Python's GIL
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
    """Cancel the currently running TSP task."""
    global current_process, current_task_id
    data = request.get_json() or {}
    task_id = data.get('taskId')

    if current_task_id and current_task_id == task_id:
        print(f"[{WORKER_ID}] Cancelling task {task_id}")
        if current_process and current_process.is_alive():
            current_process.terminate()
            current_process.join(timeout=1.0)
            if current_process.is_alive():
                # Force kill if terminate hangs
                current_process.kill()
        
        current_process = None
        current_task_id = None
        return jsonify({"message": "Task cancelled"})
    
    return jsonify({"message": "Task not running on this worker"})

def parse_top_metrics():
    """Execute top -bn 1 -i -c to extract CPU and memory utilization."""
    try:
        result = subprocess.run(['top', '-bn', '1', '-i', '-c'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=2)
        stdout = result.stdout
        
        cpu_usage = None
        mem_usage = None

        # Parse CPU
        idle_match = re.search(r'([\d.,]+)\s+id', stdout)
        if idle_match:
            idle = float(idle_match.group(1).replace(',', '.'))
            cpu_usage = 100.0 - idle
            
        # Parse Memory
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
    """Fallback memory reader using /proc/meminfo."""
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
    """Fallback CPU tracker reading /proc/stat."""
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
    """Periodically report system resources metrics to Master."""
    print(f"[{WORKER_ID}] Telemetry thread active.")
    while True:
        try:
            cpu, mem = parse_top_metrics()
            if cpu is None:
                cpu = get_fallback_cpu()
            if mem is None:
                mem = get_fallback_mem()
            
            # Clamp limits
            cpu = max(0.0, min(100.0, cpu))
            mem = max(0.0, min(100.0, mem))

            payload = {
                "workerId": WORKER_ID,
                "cpu": round(cpu, 1),
                "memory": round(mem, 1)
            }
            # POST stats
            requests.post(f"{MASTER_URL}/api/monitor/report", json=payload, timeout=2)
        except Exception:
            # Silent ignore during master startup phases
            pass
        time.sleep(3)

if __name__ == '__main__':
    # Start resource report daemon
    telemetry_thread = threading.Thread(target=telemetry_thread_loop, daemon=True)
    telemetry_thread.start()

    print(f"[{WORKER_ID}] Launching Flask on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
