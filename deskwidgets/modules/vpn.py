"""Определение статуса VPN (подходит для любого VPN-клиента).

Комбинированная проверка, каждый способ включается в настройках:
  a) запущен ли процесс VPN-клиента;
  b) поднят ли сетевой адаптер VPN;
  c) внешний IP и страна (раз в 60 с через публичный сервис). Если задана
     «домашняя страна», то страна ≠ домашней считается признаком включённого VPN.

Итог:
  on   — все включённые проверки прошли        → зелёная точка «VPN включён»
  wait — часть прошла (подключается) / нет сети  → жёлтая точка
  off  — ни одна не прошла                       → серая точка «VPN выключен»
"""

import json
import socket
import threading
import time
import urllib.request

import psutil

from ..logger import log_throttled

IP_PERIOD = 60  # секунд между проверками внешнего IP


def _names(value: str) -> list[str]:
    """«myvpn, wireguard» → ["myvpn", "wireguard"] — можно указать несколько вариантов через запятую."""
    return [p.strip().lower() for p in value.split(",") if p.strip()]


def process_running(pattern: str) -> bool:
    names = _names(pattern)
    if not names:
        return False
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if any(n in name for n in names):
            return True
    return False


def adapter_up(pattern: str) -> bool:
    names = _names(pattern)
    for nic, st in psutil.net_if_stats().items():
        if st.isup and any(n in nic.lower() for n in names):
            return True
    return False


def network_available(vpn_adapter: str) -> bool:
    """Есть ли вообще сеть: хоть один «настоящий» адаптер поднят и имеет IPv4 (не 169.254.x.x)."""
    vpn_names = _names(vpn_adapter)
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    skip = ("loopback", "vethernet", "virtualbox", "vmware", "bluetooth")
    for nic, st in stats.items():
        low = nic.lower()
        if not st.isup or any(s in low for s in skip) or any(n in low for n in vpn_names):
            continue
        for a in addrs.get(nic, []):
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                return True
    return False


def fetch_ip_info() -> dict | None:
    """Внешний IP и страна. Сначала ip-api.com (умеет отвечать по-русски), запасной — ipinfo.io."""
    sources = [
        ("http://ip-api.com/json/?lang=ru&fields=status,country,countryCode,query",
         lambda d: {"ip": d["query"], "country": d["country"], "code": d["countryCode"]}
         if d.get("status") == "success" else None),
        ("https://ipinfo.io/json",
         lambda d: {"ip": d["ip"], "country": d.get("country", ""), "code": d.get("country", "")}),
    ]
    for url, parse in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DeskWidgets/1.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            info = parse(data)
            if info:
                return info
        except Exception as e:  # noqa: BLE001
            log_throttled(f"ip:{url}", f"Не удалось узнать внешний IP через {url}: {e}", period=600)
    return None


def list_vpn_processes(show_all: bool = False) -> list[tuple[str, str]]:
    """Для кнопки «Найти автоматически»: [(имя процесса, путь к exe)]."""
    result: dict[str, str] = {}
    for proc in psutil.process_iter(["name", "exe"]):
        name = proc.info.get("name") or ""
        if not name:
            continue
        low = name.lower()
        if show_all or any(word in low for word in ("vpn", "wireguard")):
            result.setdefault(name, proc.info.get("exe") or "")
    return sorted(result.items(), key=lambda x: x[0].lower())


def list_adapters() -> list[tuple[str, bool, int, str]]:
    """[(имя адаптера, поднят ли, скорость Мбит/с, IPv4)]."""
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    rows = []
    for nic, st in stats.items():
        ipv4 = next((a.address for a in addrs.get(nic, []) if a.family == socket.AF_INET), "")
        rows.append((nic, st.isup, st.speed, ipv4))
    return sorted(rows, key=lambda r: (not r[1], r[0].lower()))


class VpnChecker:
    def __init__(self):
        self.ip_info: dict | None = None
        self._ip_next = 0.0
        self._ip_thread: threading.Thread | None = None
        self._last_state = ""

    def check(self, s: dict) -> dict:
        checks: list[bool] = []
        if s.get("check_process") and s.get("process_name", "").strip():
            checks.append(process_running(s["process_name"]))
        if s.get("check_adapter") and s.get("adapter_name", "").strip():
            checks.append(adapter_up(s["adapter_name"]))

        country = ""
        if s.get("check_ip"):
            self._maybe_refresh_ip()
            info = self.ip_info
            if info:
                country = info.get("country", "")
                home = s.get("home_country", "").strip().upper()
                if home:
                    checks.append(info.get("code", "").upper() != home)
        else:
            self.ip_info = None

        if not network_available(s.get("adapter_name", "")):
            state, text = "wait", "Нет сети"
        elif not checks:
            state, text = "off", "VPN: не настроено"
        elif all(checks):
            state, text = "on", "VPN включён"
        elif any(checks):
            state, text = "wait", "VPN подключается…"
        else:
            state, text = "off", "VPN выключен"

        # Статус сменился — внешний IP, скорее всего, тоже: проверим сразу
        if s.get("check_ip") and self._last_state and state != self._last_state:
            self._ip_next = 0
        self._last_state = state
        return {"state": state, "text": text, "country": country}

    def _maybe_refresh_ip(self) -> None:
        if time.monotonic() < self._ip_next or (self._ip_thread and self._ip_thread.is_alive()):
            return
        self._ip_next = time.monotonic() + IP_PERIOD

        def worker():
            self.ip_info = fetch_ip_info()

        # Сетевой запрос может идти несколько секунд — в отдельном потоке, чтобы не тормозить монитор
        self._ip_thread = threading.Thread(target=worker, name="ip-check", daemon=True)
        self._ip_thread.start()
