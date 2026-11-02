FROM python:3.11-slim

WORKDIR /app

# 先只复制依赖文件并安装（单独一层，利用 Docker 层缓存：
# 改代码时依赖这层不用重装，构建更快）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制代码
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
