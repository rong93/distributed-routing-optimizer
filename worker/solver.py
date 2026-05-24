import math
import time
import requests

def distance(p1, p2):
    """Calculate Euclidean distance between two points."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

def backtrack_partial_path(dp, mask, end_node):
    path = [end_node]
    curr_mask = mask
    curr_node = end_node
    while curr_node != 0:
        parent = dp[(curr_mask, curr_node)][1]
        path.append(parent)
        curr_mask = curr_mask ^ (1 << (curr_node - 1))
        curr_node = parent
    return list(reversed(path))

def extract_dp_table_for_size(dp, s, intermediate_nodes):
    table = []
    for (mask, v), (d, parent) in dp.items():
        if bin(mask).count('1') == s:
            subset = []
            for node_idx in intermediate_nodes:
                if mask & (1 << (node_idx - 1)):
                    subset.append(node_idx + 1)
            table.append({
                "subset": sorted(subset),
                "last": v + 1,
                "dist": round(d, 2) if d != float('inf') else None,
                "parent": parent + 1 if parent != 0 else 1
            })
    table.sort(key=lambda x: (x["subset"], x["last"]))
    return table

def solve_tsp(coords, first_step=None):
    """
    Solve open TSP from start (0) to end (n-1) visiting intermediate nodes.
    If first_step is specified, the path must start 0 -> first_step -> ...
    Uses Held-Karp Dynamic Programming algorithm.
    """
    start_time = time.time()
    n = len(coords)
    if n <= 1:
        return {"tour": list(range(n)), "distance": 0.0, "time": 0, "dp_steps": []}
    if n == 2:
        dist = distance(coords[0], coords[1])
        tour = [0, 1]
        return {
            "tour": tour, 
            "distance": round(dist, 2), 
            "time": int((time.time() - start_time) * 1000),
            "dp_steps": [{
                "step": 1, 
                "path": tour, 
                "distance": round(dist, 2),
                "table": [{
                    "subset": [],
                    "last": 2,
                    "dist": round(dist, 2),
                    "parent": 1
                }]
            }]
        }

    intermediate_nodes = list(range(1, n - 1))
    m = len(intermediate_nodes)

    # DP table: dp[(subset_mask, last_node)] = (min_distance, parent_node)
    dp = {}
    dp_steps = []

    # Initialize DP table
    if first_step is not None:
        mask = 1 << (first_step - 1)
        dp[(mask, first_step)] = (distance(coords[0], coords[first_step]), 0)
    else:
        for u in intermediate_nodes:
            mask = 1 << (u - 1)
            dp[(mask, u)] = (distance(coords[0], coords[u]), 0)

    # Record step 1
    best_dist_1 = float('inf')
    best_key_1 = None
    for (mask, u), (d, parent) in dp.items():
        if bin(mask).count('1') == 1:
            if d < best_dist_1:
                best_dist_1 = d
                best_key_1 = (mask, u)
    if best_key_1:
        mask, u = best_key_1
        dp_steps.append({
            "step": 1,
            "path": backtrack_partial_path(dp, mask, u),
            "distance": round(best_dist_1, 2),
            "table": extract_dp_table_for_size(dp, 1, intermediate_nodes)
        })

    # Iterate subset sizes from 2 to m
    import itertools
    for s in range(2, m + 1):
        for comb in itertools.combinations(intermediate_nodes, s):
            if first_step is not None and first_step not in comb:
                continue

            mask = 0
            for u in comb:
                mask |= 1 << (u - 1)

            for v in comb:
                if first_step is not None and v == first_step:
                    continue

                prev_mask = mask ^ (1 << (v - 1))
                best_dist = float('inf')
                best_parent = -1

                for u in comb:
                    if u == v:
                        continue
                    if (prev_mask, u) in dp:
                        d = dp[(prev_mask, u)][0] + distance(coords[u], coords[v])
                        if d < best_dist:
                            best_dist = d
                            best_parent = u

                if best_dist != float('inf'):
                    dp[(mask, v)] = (best_dist, best_parent)

        # Record step s after completing size s
        best_s_dist = float('inf')
        best_s_key = None
        for (mask, v), (d, parent) in dp.items():
            if bin(mask).count('1') == s:
                if d < best_s_dist:
                    best_s_dist = d
                    best_s_key = (mask, v)
        if best_s_key:
            mask, v = best_s_key
            dp_steps.append({
                "step": s,
                "path": backtrack_partial_path(dp, mask, v),
                "distance": round(best_s_dist, 2),
                "table": extract_dp_table_for_size(dp, s, intermediate_nodes)
            })

    # Connect to the final destination n-1
    full_mask = (1 << m) - 1
    best_dist = float('inf')
    last_node = -1

    for u in intermediate_nodes:
        if (full_mask, u) in dp:
            d = dp[(full_mask, u)][0] + distance(coords[u], coords[n - 1])
            if d < best_dist:
                best_dist = d
                last_node = u

    # Reconstruct path
    if last_node == -1:
        tour = list(range(n))
        best_dist = 0.0
    else:
        path = [n - 1]
        curr_mask = full_mask
        curr_node = last_node

        while curr_node != 0:
            path.append(curr_node)
            parent = dp[(curr_mask, curr_node)][1]
            curr_mask = curr_mask ^ (1 << (curr_node - 1))
            curr_node = parent

        path.append(0)
        tour = list(reversed(path))
        
        # Append final complete path (step m + 1)
        final_table = [{
            "subset": [node_idx + 1 for node_idx in intermediate_nodes],
            "last": n,
            "dist": round(best_dist, 2) if best_dist != float('inf') else None,
            "parent": last_node + 1
        }]
        dp_steps.append({
            "step": m + 1,
            "path": tour,
            "distance": round(best_dist, 2),
            "table": final_table
        })

    duration_ms = int((time.time() - start_time) * 1000)
    return {
        "tour": tour,
        "distance": round(best_dist, 2),
        "time": max(1, duration_ms),
        "dp_steps": dp_steps
    }

def solve_task_process(task_id, coords, master_url, worker_id, first_step=None):
    """
    Subprocess worker target. Solves TSP and reports results to Master callback.
    """
    try:
        print(f"[{worker_id}] (Process) Starting DP computation for task {task_id} (first_step={first_step})...")
        result = solve_tsp(coords, first_step=first_step)
        
        # Report completion
        payload = {
            "taskId": task_id,
            "workerId": worker_id,
            "result": result
        }
        res = requests.post(f"{master_url}/api/tasks/complete", json=payload, timeout=5)
        print(f"[{worker_id}] (Process) Result reported to master. Status code: {res.status_code}")
    except Exception as e:
        print(f"[{worker_id}] (Process) Error encountered: {str(e)}")
        # Report failure
        try:
            payload = {
                "taskId": task_id,
                "workerId": worker_id,
                "error": str(e)
            }
            requests.post(f"{master_url}/api/tasks/complete", json=payload, timeout=5)
        except Exception as post_err:
            print(f"[{worker_id}] (Process) Failed to report failure: {str(post_err)}")
