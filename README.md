# 分散式 TSP 路徑優化平台 (Distributed TSP Routing Optimizer)

本專案是一個容器化的分散式運算平台，旨在解決旅行推銷員問題 (TSP)。系統由一個 Master 節點與三個 Worker 節點（Worker A、Worker B、Worker C）組成。

## 先決條件

請確保您的系統已安裝以下軟體：

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

---

## 啟動步驟

在專案根目錄下執行以下指令，以建置並啟動整個服務集群（包含 Master 與 3 個 Worker）：

```bash
docker compose up --build
```

此指令將會執行以下步驟：

1. 建置 Master 與 Worker 服務的 Docker 映像檔。
2. 初始化名為 `tsp-net` 的橋接網路（bridge network）。
3. 啟動 `tsp-master` 容器（監聽 Port `3000`）。
4. 啟動 `tsp-worker-a`、`tsp-worker-b` 與 `tsp-worker-c` 容器（分別對外對應 Port `4001`、`4002` 與 `4003`）。

容器啟動完成後，您可以透過以下網址存取網頁管理與監控介面：
👉 **[http://localhost:3000](http://localhost:3000)**

---

## 停止步驟

若要停止容器並清理網路，可在執行容器的終端機視窗中按下 `Ctrl + C`，或者在專案根目錄下執行：

```bash
docker compose down
```
