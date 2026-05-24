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

# Thread safety lock for task list and worker statuses
db_lock = threading.Lock()

tasks = []
workers = [
    { "id": "Worker A", "url": "http://worker-a:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 },
    { "id": "Worker B", "url": "http://worker-b:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 },
    { "id": "Worker C", "url": "http://worker-c:4000", "status": "offline", "lastHeartbeat": 0, "currentTaskId": None, "failCount": 0 }
]

resource_stats = {
    'Master': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker A': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker B': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 },
    'Worker C': { 'cpu': 0.0, 'memory': 0.0, 'lastReport': 0 }
}

# Serve static frontend dashboard
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)

def dispatch_pending_tasks():
    """Finds queued subtasks and assigns them to available online workers."""
    with db_lock:
        pending_subtask = None
        target_task = None
        
        # Sort tasks by ID descending to process newer tasks first
        for t in sorted(tasks, key=lambda x: x["id"], reverse=True):
            if t["status"] in ("queued", "running") and t.get("subtasks"):
                for sub in t["subtasks"]:
                    if sub["status"] == "queued":
                        pending_subtask = sub
                        target_task = t
                        break
            if pending_subtask:
                break
                
        if not pending_subtask or not target_task:
            return

        available_worker = None
        for w in workers:
            if w["status"] == "online":
                available_worker = w
                break

        if not available_worker:
            return

        # Reserve subtask, main task, and worker
        pending_subtask["status"] = "running"
        pending_subtask["workerId"] = available_worker["id"]
        
        target_task["status"] = "running"
        
        # Update workerDisplay on the main task to list active workers
        active_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
        target_task["workerId"] = ", ".join(sorted(active_workers))
        
        available_worker["status"] = "busy"
        available_worker["currentTaskId"] = pending_subtask["id"]

    print(f"[Master] Dispatching subtask {pending_subtask['id']} (firstStep={pending_subtask['first_step']}) to {available_worker['id']}")

    payload = {
        "taskId": pending_subtask["id"],
        "coords": target_task["coords"],
        "firstStep": pending_subtask["first_step"]
    }
    
    def async_dispatch():
        try:
            res = requests.post(f"{available_worker['url']}/solve", json=payload, timeout=3)
            if res.status_code != 200:
                print(f"[Master] Worker {available_worker['id']} rejected subtask {pending_subtask['id']}: {res.text}")
                reset_subtask(target_task["id"], pending_subtask["id"], available_worker["id"])
            # Trigger another dispatch try
            threading.Thread(target=dispatch_pending_tasks).start()
        except Exception as e:
            print(f"[Master] Failed to dispatch subtask {pending_subtask['id']} to {available_worker['id']}: {str(e)}")
            reset_subtask(target_task["id"], pending_subtask["id"], available_worker["id"])
            # Trigger another dispatch try
            threading.Thread(target=dispatch_pending_tasks).start()

    # Run network request asynchronously
    threading.Thread(target=async_dispatch).start()

def reset_subtask(task_id, subtask_id, worker_id):
    """Reverts subtask status on immediate worker dispatch failures."""
    with db_lock:
        task = next((t for t in tasks if t["id"] == task_id), None)
        worker = next((w for w in workers if w["id"] == worker_id), None)
        
        if task and task.get("subtasks"):
            subtask = next((s for s in task["subtasks"] if s["id"] == subtask_id), None)
            if subtask and subtask["status"] == "running":
                subtask["status"] = "queued"
                subtask["workerId"] = None
            
            # Recalculate active workers
            active_workers = {sub["workerId"] for sub in task["subtasks"] if sub["workerId"]}
            task["workerId"] = ", ".join(sorted(active_workers)) if active_workers else None
            if not active_workers:
                task["status"] = "queued"
                
        if worker:
            worker["status"] = "offline"
            worker["currentTaskId"] = None
            handle_worker_failure(worker)

def handle_worker_failure(worker):
    """Handles incrementing failures and triggering eviction."""
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
    """Subtask reallocation logic to recover failed subtask states."""
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
            
            # Recalculate active workers
            active_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
            target_task["workerId"] = ", ".join(sorted(active_workers)) if active_workers else None
            
    # Trigger dispatch try
    time.sleep(0.5)
    dispatch_pending_tasks()

def send_cancel_to_worker(worker, task_id):
    """Instructs worker to stop computation."""
    def async_cancel():
        try:
            requests.post(f"{worker['url']}/cancel", json={"taskId": task_id}, timeout=2)
        except Exception as e:
            print(f"[Master] Failed to cancel task on {worker['id']}: {str(e)}")
    threading.Thread(target=async_cancel).start()

# API: Submit task
@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.get_json() or {}
    name = data.get('name')
    coords = data.get('coords')

    if not coords or not isinstance(coords, list) or len(coords) < 2:
        return jsonify({"error": "Coordinates list must contain at least 2 points"}), 400

    if len(coords) > 15:
        return jsonify({"error": "Maximum 15 nodes allowed"}), 400

    for p in coords:
        if not isinstance(p, list) or len(p) != 2 or not isinstance(p[0], (int, float)) or not isinstance(p[1], (int, float)):
            return jsonify({"error": "Invalid coordinate format"}), 400

    task_id = f"task_{int(time.time()*1000)}_{os.urandom(2).hex()}"
    
    # Generate subtasks
    subtasks = []
    if len(coords) > 2:
        subtasks = [
            {
                "id": f"{task_id}_{f}",
                "first_step": f,
                "status": "queued",
                "workerId": None,
                "result": None,
                "error": None
            }
            for f in range(1, len(coords) - 1)
        ]
    else:
        # For N <= 2, just 1 subtask with no first_step constraint
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

    task = {
        "id": task_id,
        "name": name or f"TSP Task #{len(tasks) + 1}",
        "coords": coords,
        "status": "queued",
        "workerId": None,
        "result": None,
        "error": None,
        "createdAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "subtasks": subtasks
    }

    with db_lock:
        tasks.append(task)

    print(f"[Master] Created task {task['id']} with {len(coords)} points and {len(subtasks)} subtasks.")
    
    # Try dispatching in background
    threading.Thread(target=dispatch_pending_tasks).start()
    
    return jsonify(task)

# API: Get tasks list
@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    with db_lock:
        sorted_tasks = sorted(tasks, key=lambda x: x["id"], reverse=True)
    return jsonify(sorted_tasks)

# API: Cancel and delete task
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
                
        # Purge from list
        tasks = [t for t in tasks if t["id"] != task_id]
        
    threading.Thread(target=dispatch_pending_tasks).start()
    return jsonify({"message": "Task deleted successfully"})

# API: Worker callback on completion
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

            # Check if all subtasks of target_task are finished
            all_done = True
            for sub in target_task["subtasks"]:
                if sub["status"] in ("queued", "running"):
                    all_done = False
                    break
            
            if all_done:
                completed_subs = [sub for sub in target_task["subtasks"] if sub["status"] == "completed" and sub["result"]]
                if completed_subs:
                    best_sub = min(completed_subs, key=lambda x: x["result"]["distance"])
                    target_task["status"] = "completed"
                    target_task["result"] = best_sub["result"]
                    
                    all_workers = {sub["workerId"] for sub in target_task["subtasks"] if sub["workerId"]}
                    target_task["workerId"] = ", ".join(sorted(all_workers))
                    print(f"[Master] Task {target_task['id']} fully completed! Best distance: {target_task['result']['distance']} via {best_sub['workerId']}")
                else:
                    target_task["status"] = "failed"
                    errors = [sub["error"] for sub in target_task["subtasks"] if sub["error"]]
                    target_task["error"] = errors[0] if errors else "All subtasks failed"
                    print(f"[Master] Task {target_task['id']} failed. All subtasks failed.")

        worker = next((w for w in workers if w["id"] == worker_id), None)
        if worker and worker["currentTaskId"] == subtask_id:
            worker["status"] = "online"
            worker["currentTaskId"] = None
            worker["failCount"] = 0

    threading.Thread(target=dispatch_pending_tasks).start()
    return jsonify({"message": "Acknowledged"})

# API: Workers telemetry report
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

# API: Get overall system stats
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

# Heartbeat loop running in background thread
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
        
        # Run periodic dispatch check
        dispatch_pending_tasks()
        time.sleep(3)

# Master system resource metrics collection
def parse_top_metrics():
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
    # Start heartbeat checker
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    # Start resource logger
    telemetry_thread = threading.Thread(target=master_telemetry_loop, daemon=True)
    telemetry_thread.start()
    
    print(f"[Master] Launching Flask on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, threaded=True)
