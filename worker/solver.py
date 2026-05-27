import math
import time
import requests

def distance(p1, p2):
    """計算兩點之間的歐幾里得距離 (Euclidean distance)。"""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)

def solve_tsp(coords, first_step=None):
    """
    解決開放式的旅行推銷員問題 (TSP)，從起點 (0) 走到終點 (n-1) 並經過所有中間節點。
    如果指定了 first_step，則路徑必須是 0 -> first_step -> ...
    目前使用基因演算法 (Genetic Algorithm) 來找出最佳化路徑。
    """
    start_time = time.time()
    n = len(coords)
    if n <= 1:
        return {"tour": list(range(n)), "distance": 0.0, "time": 0}
    if n == 2:
        dist = distance(coords[0], coords[1])
        tour = [0, 1]
        return {
            "tour": tour, 
            "distance": round(dist, 2), 
            "time": int((time.time() - start_time) * 1000)
        }

    # 決定固定的前綴路徑 (Prefix) 與可變動的節點
    fixed_prefix = []
    if first_step is not None:
        fixed_prefix = [first_step]
        variable_nodes = [node for node in range(1, n - 1) if node != first_step]
    else:
        variable_nodes = list(range(1, n - 1))

    # 輔助函式：計算給定路徑的總距離
    def get_path_dist(var_path):
        full_path = [0] + fixed_prefix + var_path + [n - 1]
        d = 0.0
        for i in range(len(full_path) - 1):
            d += distance(coords[full_path[i]], coords[full_path[i+1]])
        return d

    # 如果可變動節點只有 0 個或 1 個，則路徑是唯一且確定的
    if len(variable_nodes) <= 1:
        best_var = list(variable_nodes)
        best_dist = get_path_dist(best_var)
        tour = [0] + fixed_prefix + best_var + [n - 1]
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "tour": tour,
            "distance": round(best_dist, 2),
            "time": max(1, duration_ms)
        }

    # 基因演算法 (GA) 參數設定
    pop_size = 50          # 族群大小：每一代有多少條路徑
    generations = 60       # 演化代數：總共要繁衍幾代
    mutation_rate = 0.15   # 突變機率：15%
    elites_count = max(2, int(pop_size * 0.1)) # 菁英保留數量：保留前 10% 表現最好的路徑

    # 初始化族群 (隨機產生第一代路徑)
    import random
    population = []
    for _ in range(pop_size):
        ind = list(variable_nodes)
        random.shuffle(ind)
        population.append(ind)

    best_global_var = None
    best_global_dist = float('inf')

    # 演化迴圈 (開始繁衍下一代)
    for gen in range(1, generations + 1):
        # 計算適應度 (Fitness：根據路徑總距離)
        scored_pop = []
        for ind in population:
            d = get_path_dist(ind)
            scored_pop.append((d, ind))
        
        scored_pop.sort(key=lambda x: x[0])
        current_best_dist, current_best_var = scored_pop[0]

        if current_best_dist < best_global_dist:
            best_global_dist = current_best_dist
            best_global_var = list(current_best_var)

        # 選擇 (Selection)、交配 (Crossover) 與突變 (Mutation) 來產生下一代
        new_pop = [x[1] for x in scored_pop[:elites_count]]  # 保留菁英直接進入下一代

        while len(new_pop) < pop_size:
            # 錦標賽選擇法 (Tournament selection)，每次隨機抽 3 個，選出距離最短的作為父母代
            p1 = min(random.sample(population, 3), key=get_path_dist)
            p2 = min(random.sample(population, 3), key=get_path_dist)

            # 交配：部分對映交配法 (Ordered Crossover, OX)
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
            
            # 突變：交換突變 (Swap Mutation)，隨機交換路徑中的兩個節點
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
        "time": max(1, duration_ms)
    }

def solve_task_process(task_id, coords, master_url, worker_id, first_step=None):
    """
    Subprocess 的執行目標。負責執行 TSP 演算法並將結果回報給 Master 的 webhook API。
    """
    try:
        print(f"[{worker_id}] (Process) Starting GA computation for task {task_id} (first_step={first_step})...")
        result = solve_tsp(coords, first_step=first_step)
        
        # 回報執行完成並附帶結果
        payload = {
            "taskId": task_id,
            "workerId": worker_id,
            "result": result
        }
        res = requests.post(f"{master_url}/api/tasks/complete", json=payload, timeout=5)
        print(f"[{worker_id}] (Process) Result reported to master. Status code: {res.status_code}")
    except Exception as e:
        print(f"[{worker_id}] (Process) Error encountered: {str(e)}")
        # 回報執行失敗的錯誤訊息
        try:
            payload = {
                "taskId": task_id,
                "workerId": worker_id,
                "error": str(e)
            }
            requests.post(f"{master_url}/api/tasks/complete", json=payload, timeout=5)
        except Exception as post_err:
            print(f"[{worker_id}] (Process) Failed to report failure: {str(post_err)}")
