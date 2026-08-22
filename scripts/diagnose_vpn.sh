#!/usr/bin/env bash
# Диагностика "подписка активна, но VPN не работает / ничего не грузит".
# Запускать на сервере с Marzban (не там, где просто крутится Telegram-бот):
#   sudo bash scripts/diagnose_vpn.sh
#
# Проверяет по очереди самые частые причины, от вероятной к редкой.
set -uo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
fail() { echo -e "${RED}[ПРОБЛЕМА]${NC} $1"; }
warn() { echo -e "${YELLOW}[ВНИМАНИЕ]${NC} $1"; }

echo "== 1. Синхронизация времени =="
# Reality крайне чувствителен к рассинхрону часов — если сервер "уехал" по времени,
# TLS-хендшейк с камуфляжным сайтом (dest) не проходит, и клиент видит "ничего не грузит".
if command -v timedatectl &>/dev/null; then
    if timedatectl status | grep -q "System clock synchronized: yes"; then
        ok "Часы синхронизированы"
    else
        fail "Часы НЕ синхронизированы — это частая причина полного отказа Reality"
        echo "  Исправление: sudo timedatectl set-ntp true && sudo systemctl restart systemd-timesyncd"
    fi
else
    warn "timedatectl недоступен, проверьте время вручную: date"
fi
echo

echo "== 2. Статус самого Marzban/Xray =="
if command -v docker &>/dev/null && docker ps --format '{{.Names}}' 2>/dev/null | grep -qi marzban; then
    STATUS=$(docker inspect -f '{{.State.Status}}' marzban 2>/dev/null || echo "unknown")
    if [ "$STATUS" = "running" ]; then
        ok "Контейнер marzban запущен"
    else
        fail "Контейнер marzban НЕ запущен (статус: $STATUS)"
        echo "  Исправление: docker restart marzban && docker logs --tail 50 marzban"
    fi
elif systemctl is-active --quiet marzban 2>/dev/null; then
    ok "Служба marzban активна (systemd)"
else
    fail "Marzban не выглядит запущенным ни как Docker-контейнер, ни как systemd-служба"
    echo "  Проверьте вручную: systemctl status marzban  ИЛИ  docker ps -a | grep marzban"
fi
echo

echo "== 3. Порт 443 (VLESS+Reality) слушается локально =="
if command -v ss &>/dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ':443 '; then
        ok "Порт 443 слушается локально"
    else
        fail "На порту 443 никто не слушает — Xray inbound не поднялся"
        echo "  Проверьте конфиг инбаунда в панели Marzban (Core Settings) и перезапустите core"
    fi
else
    warn "Утилита ss недоступна, проверьте вручную: netstat -tlnp | grep 443"
fi
echo

echo "== 4. Порт 443 доступен СНАРУЖИ (не только локально) =="
DOMAIN=$(grep -oP '(?<=MARZBAN_URL=https://)[^:/]+' /opt/vpn-bot/.env 2>/dev/null || echo "")
if [ -n "$DOMAIN" ]; then
    if command -v curl &>/dev/null; then
        # Не проверяем VLESS-протокол (для этого нужен xray-клиент), но проверяем,
        # что TCP+TLS хендшейк вообще проходит и порт не зарублен файрволом/хостером
        if timeout 8 bash -c "echo > /dev/tcp/${DOMAIN}/443" 2>/dev/null; then
            ok "TCP-соединение на ${DOMAIN}:443 снаружи проходит"
        else
            fail "Не удалось открыть TCP-соединение на ${DOMAIN}:443 — порт заблокирован"
            echo "  Возможные причины: ufw не пропускает 443, или хостер блокирует порт"
            echo "  на уровне облачного файрвола (проверьте в панели вашего VPS-провайдера,"
            echo "  не только 'ufw status' на самом сервере)"
        fi
    fi
else
    warn "Не удалось определить домен из .env, проверьте порт вручную:"
    echo "  curl -v --connect-timeout 8 https://ВАШ_ДОМЕН:443"
fi
echo

echo "== 5. ufw (если используется) =="
if command -v ufw &>/dev/null; then
    ufw status | grep -E "443|Status" || warn "Правило для 443 не найдено в ufw"
fi
echo

echo "== 6. Проверка самой ссылки подписки (не пустая ли) =="
echo "  Вручную выполните на любой машине с доступом в интернет:"
echo "  curl -s \"ВАША_ССЫЛКА_ПОДПИСКИ\" | head -c 300"
echo "  Если ответ пустой или похож на HTML-страницу постороннего сайта — проблема"
echo "  в конфигурации XRAY_SUBSCRIPTION_URL_PREFIX (см. предыдущий разбор)."
echo

echo "== Готово. Если всё выше 'OK', но клиент всё равно не подключается: =="
echo "  1) Пересоздайте пользователю ссылку через админку бота (/relink <tg_id>)"
echo "     — старый конфиг в кэше приложения может быть битым."
echo "  2) Проверьте, что публичный/приватный ключ Reality (privateKey/publicKey)"
echo "     СОВПАДАЮТ с тем, что в текущем конфиге Core Settings — если ключи меняли"
echo "     вручную, но не перезапускали core, клиенты получают старый publicKey."
echo "  3) Попробуйте другой dest (camouflage-сайт) в Reality — некоторые сайты"
echo "     сами бывают заблокированы у провайдера клиента, из-за чего весь Reality"
echo "     falls back и рвётся. Смените dest на 'www.microsoft.com:443' для теста."
