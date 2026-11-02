FROM python:3.12-slim
WORKDIR /usr/src/bot
COPY . .
ENV PYTHONUNBUFFERED=1 DATABASE_PATH=/app/data/korshun.sqlite3
CMD ["python", "main.py"]
