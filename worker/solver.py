import math
import time
import requests

def distance(p1, p2):
    """Calculate Euclidean distance between two points."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

def solve_tsp(coords, first_step=None):
    """
    Solve open TSP from start (0) to end (n-1) visiting intermediate nodes.
    If first_step is specified, the path must start 0 -> first_step -> ...
    Uses Genetic Algorithm.
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
                "table": []
            }]
        }

    # Determine fixed prefix and variable nodes
    fixed_prefix = []
    if first_step is not None:
        fixed_prefix = [first_step]
        variable_nodes = [node for node in range(1, n - 1) if node != first_step]
    else:
        variable_nodes = list(range(1, n - 1))

    # Helper function to compute path distance
    def get_path_dist(var_path):
        full_path = [0] + fixed_prefix + var_path + [n - 1]
        d = 0.0
        for i in range(len(full_path) - 1):
            d += distance(coords[full_path[i]], coords[full_path[i+1]])
        return d

    # If 0 or 1 variable nodes, the path is trivial and unique
    if len(variable_nodes) <= 1:
        best_var = list(variable_nodes)
        best_dist = get_path_dist(best_var)
        tour = [0] + fixed_prefix + best_var + [n - 1]
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "tour": tour,
            "distance": round(best_dist, 2),
            "time": max(1, duration_ms),
            "dp_steps": [{
                "step": 1,
                "path": tour,
                "distance": round(best_dist, 2),
                "table": []
            }]
        }

    # Genetic Algorithm Parameters
    pop_size = 50
    generations = 60
    mutation_rate = 0.15
    elites_count = max(2, int(pop_size * 0.1))

    # Initialize population
    import random
    population = []
    for _ in range(pop_size):
        ind = list(variable_nodes)
        random.shuffle(ind)
        population.append(ind)

    best_global_var = None
    best_global_dist = float('inf')
    dp_steps = []

    # Evolutionary loop
    for gen in range(1, generations + 1):
        # Calculate fitness
        scored_pop = []
        for ind in population:
            d = get_path_dist(ind)
            scored_pop.append((d, ind))
        
        scored_pop.sort(key=lambda x: x[0])
        current_best_dist, current_best_var = scored_pop[0]

        if current_best_dist < best_global_dist:
            best_global_dist = current_best_dist
            best_global_var = list(current_best_var)

        # Record this generation's step (map to dp_steps for UI playback compatibility)
        best_full_path = [0] + fixed_prefix + best_global_var + [n - 1]
        dp_steps.append({
            "step": gen,
            "path": best_full_path,
            "distance": round(best_global_dist, 2),
            "table": []
        })

        # Selection, Crossover, and Mutation to produce next generation
        new_pop = [x[1] for x in scored_pop[:elites_count]]  # Keep elites

        while len(new_pop) < pop_size:
            # Tournament selection (size 3)
            p1 = min(random.sample(population, 3), key=get_path_dist)
            p2 = min(random.sample(population, 3), key=get_path_dist)

            # Crossover: Ordered Crossover (OX)
            size = len(p1)
            start, end = sorted(random.sample(range(size), 2))
            child = [None] * size
            child[start:end] = p1[start:end]
            
            p2_idx = 0
            for c_idx in range(size):
                if child[c_idx] is None:
                    while p2[p2_idx] in child:
                        p2_idx += 1
                    child[c_idx] = p2[p2_idx]
            
            # Mutation: Swap Mutation
            if random.random() < mutation_rate:
                idx1, idx2 = random.sample(range(size), 2)
                child[idx1], child[idx2] = child[idx2], child[idx1]

            new_pop.append(child)

        population = new_pop

    tour = [0] + fixed_prefix + best_global_var + [n - 1]
    duration_ms = int((time.time() - start_time) * 1000)

    return {
        "tour": tour,
        "distance": round(best_global_dist, 2),
        "time": max(1, duration_ms),
        "dp_steps": dp_steps
    }

def solve_task_process(task_id, coords, master_url, worker_id, first_step=None):
    """
    Subprocess worker target. Solves TSP and reports results to Master callback.
    """
    try:
        print(f"[{worker_id}] (Process) Starting GA computation for task {task_id} (first_step={first_step})...")
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
