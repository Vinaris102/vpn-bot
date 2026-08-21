# Единый образ для всех трёх процессов бота
FROM python:3.12-slim

# Устанавливаем переменные окружения для Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Устанавливаем системные зависимости (если нужны)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     curl \
#     && rm -rf /var/lib/apt/lists/*

# Отдельным слоем — зависимости, кэшируются при неизменном requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Копируем код приложения
COPY app ./app

# Создаём папку для базы данных и устанавливаем права
RUN mkdir -p /app/data && \
    useradd --create-home --shell /usr/sbin/nologin vpnbot && \
    chown -R vpnbot:vpnbot /app

# Переключаемся на непривилегированного пользователя
USER vpnbot

# Команда по умолчанию — запуск бота (можно переопределить в docker-compose.yml)
CMD ["python", "-m", "app.bot"]