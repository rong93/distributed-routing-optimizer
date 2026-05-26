# 使用 Python 3.9 精簡版作為基礎映像檔
FROM python:3.9-slim

# 設定工作目錄
WORKDIR /app

# 複製依賴清單並安裝 Python 套件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安裝 procps 套件以支援 top 指令（用於系統資源遙測回報）
RUN apt-get update && apt-get install -y procps && rm -rf /var/lib/apt/lists/*

# 複製所有程式碼到容器中
COPY . .

# 開放 Worker 的 API 服務 Port
EXPOSE 4000

# 啟動 Worker 伺服器
CMD ["python", "worker.py"]
