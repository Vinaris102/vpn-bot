#!/usr/bin/env bash
# Первичный деплой vpn-bot на чистый Ubuntu 22.04.
# Запускать от root: sudo bash deploy/deploy.sh
set -euo pipefail

APP_DIR="/opt/vpn-bot"
LOG_DIR="/var/log/vpnbot"
SERVICE_USER="vpnbot"

echo "==> Обновление системы и установка зависимостей"
apt update && apt upgrade -y
apt install -y python3-venv python3-pip nginx certbot python3-certbot-nginx git ufw

echo "==> Создание системного пользователя"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Копирование проекта в $APP_DIR"
mkdir -p "$APP_DIR"
# Предполагается, что скрипт запускается из корня уже склонированного репозитория
cp -r ./* "$APP_DIR"/
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "==> Настройка виртуального окружения"
sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "==> ВНИМАНИЕ: .env не найден, копирую .env.example — ЗАПОЛНИТЕ ЕГО перед запуском!"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
fi

echo "==> Логи"
mkdir -p "$LOG_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_DIR"

echo "==> Установка systemd unit-файлов"
cp "$APP_DIR/deploy/vpnbot-bot.service" /etc/systemd/system/
cp "$APP_DIR/deploy/vpnbot-webhook.service" /etc/systemd/system/
cp "$APP_DIR/deploy/vpnbot-scheduler.service" /etc/systemd/system/
systemctl daemon-reload

echo "==> Настройка nginx"
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/vpnbot
ln -sf /etc/nginx/sites-available/vpnbot /etc/nginx/sites-enabled/vpnbot
nginx -t

echo "==> Firewall"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

cat <<'EOF'

========================================================
ДЕПЛОЙ ПОЧТИ ГОТОВ. Осталось вручную:

1. Заполнить /opt/vpn-bot/.env реальными значениями
   (BOT_TOKEN, MARZBAN_*, YOOKASSA_*, WEBHOOK_BASE_URL)

2. Заменить "ваш-домен.com" в /etc/nginx/sites-available/vpnbot
   на реальный домен и перезапустить nginx:
     systemctl restart nginx

3. Выпустить SSL-сертификат:
     certbot --nginx -d ваш-домен.com

4. Запустить сервисы:
     systemctl enable --now vpnbot-bot vpnbot-webhook vpnbot-scheduler

5. Проверить статус и логи:
     systemctl status vpnbot-bot
     tail -f /var/log/vpnbot/*.log

6. В личном кабинете ЮKassa указать URL вебхука:
     https://ваш-домен.com/yookassa/webhook
========================================================
EOF
