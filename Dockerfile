FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY server.py index.html app.js config.js styles.css curriculum_content.json ./

EXPOSE 10000

CMD ["python", "server.py"]
