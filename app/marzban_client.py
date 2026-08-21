"""
Обёртка над Marzban REST API.
Документация API живёт прямо на сервере: {MARZBAN_URL}/docs
"""
import logging
from datetime import datetime, timedelta

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class MarzbanClient:
    def __init__(self):
        self.base_url = settings.marzban_url.rstrip("/")
        self._token: str | None = None

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        async with httpx.AsyncClient(verify=True) as client:
            resp = await client.post(
                f"{self.base_url}/api/admin/token",
                data={
                    "username": settings.marzban_username,
                    "password": settings.marzban_password,
                },
            )
            resp.raise_for_status()
            self._token = resp.json()["access_token"]
            return self._token

    async def _headers(self) -> dict:
        token = await self._get_token()
        return {"Authorization": f"Bearer {token}"}

    async def create_user(self, username: str, days: int) -> dict:
        """Создаёт пользователя в Marzban со сроком действия = days дней."""
        expire_ts = int((datetime.utcnow() + timedelta(days=days)).timestamp())
        payload = {
            "username": username,
            "proxies": {"vless": {}},
            "inbounds": {"vless": [settings.marzban_inbound_tag]},
            "expire": expire_ts,
            "data_limit": 0,  # 0 = без ограничения по трафику
            "status": "active",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/user",
                json=payload,
                headers=await self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def user_exists(self, username: str) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers=await self._headers(),
            )
            if resp.status_code == 404:
                return False
            resp.raise_for_status()
            return True

    async def get_user_info(self, username: str) -> dict | None:
        """Возвращает сырые данные пользователя из Marzban (для профиля:
        трафик, лимит, статус) или None, если пользователя нет."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers=await self._headers(),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def extend_user(self, username: str, days: int) -> dict:
        """Продлевает существующего пользователя на days дней от текущей даты
        окончания (если она в будущем) или от сейчас (если истекла)."""
        async with httpx.AsyncClient() as client:
            headers = await self._headers()
            current = await client.get(f"{self.base_url}/api/user/{username}", headers=headers)
            current.raise_for_status()
            data = current.json()

            now_ts = int(datetime.utcnow().timestamp())
            base_ts = data["expire"] if data["expire"] and data["expire"] > now_ts else now_ts
            new_expire = base_ts + int(timedelta(days=days).total_seconds())

            resp = await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"expire": new_expire, "status": "active"},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    def _resolve_sub_url(self, raw_sub_url: str) -> str:
        """Marzban может вернуть subscription_url как относительный путь ('/sub/xxx')
        ИЛИ как уже полный URL с доменом (если в Marzban настроен
        XRAY_SUBSCRIPTION_URL_PREFIX / SUBSCRIPTION своим доменом).
        Раньше код всегда добавлял base_url спереди — из-за этого при полном URL
        домен задваивался и ссылка ломалась. Теперь проверяем сами."""
        if raw_sub_url.startswith("http://") or raw_sub_url.startswith("https://"):
            return raw_sub_url
        return f"{self.base_url}{raw_sub_url}"

    async def get_subscription_link(self, username: str) -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/user/{username}",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            if "subscription_url" not in data:
                logger.error(f"В ответе Marzban для {username} нет поля subscription_url: {data}")
                raise ValueError("Marzban не вернул subscription_url — проверьте версию панели/API")
            return self._resolve_sub_url(data["subscription_url"])

    async def regenerate_subscription_link(self, username: str) -> str:
        """Отзывает старую ссылку на подписку и выдаёт новую (новый UUID),
        не трогая срок действия и лимиты. Полезно, если старая ссылка
        перестала открываться (например, была скомпрометирована/заблокирована)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/user/{username}/revoke_sub",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return self._resolve_sub_url(data["subscription_url"])

    async def disable_user(self, username: str) -> None:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{self.base_url}/api/user/{username}",
                json={"status": "disabled"},
                headers=await self._headers(),
            )


marzban = MarzbanClient()
