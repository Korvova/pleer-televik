"""Запись с Televic Plixus: старт/стоп из плеера, метки микрофонов, скачивание файлов по папкам.

Запуск: python plixus_recorder.py  (или «Запустить.bat», или PleerPlixus.exe), плеер откроется на http://localhost:8765
Настройки — config.json рядом с программой ({"host": "...", "rec_dir": "..."}), их же меняет шестерёнка в плеере.
Переменные окружения PLIXUS_HOST и PLIXUS_REC_DIR, если заданы, важнее файла.
Проверка связи без запуска: PleerPlixus.exe --check 10.0.20.32  (код выхода 0 — Plixus ответил).
"""
import asyncio
import json
import os
import re
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import websockets

FROZEN = getattr(sys, "frozen", False)                       # собрано в exe
APP_DIR = Path(sys.executable if FROZEN else __file__).resolve().parent
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))             # где лежит player.html
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_HOST = "10.0.20.32"
WS_PORT = 6111
HTTP_PORT = 8765
HOST = DEFAULT_HOST
REC_DIR = APP_DIR / "Записи"
JOURNAL = REC_DIR / "_journal.json"
STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})h(\d{2})m(\d{2})s")
SYNC_EVERY = 60

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(*args):
    print(datetime.now().strftime("%H:%M:%S"), *args, flush=True)


TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "a b v g d e e zh z i y k l m n o p r s t u f h ts ch sh sch _ y _ e yu ya".split(" ")))


def plixus_prefix(name):
    """Plixus принимает в префиксе только латиницу, цифры, «-» и «_»."""
    out = []
    for ch in name.strip():
        low = ch.lower()
        if low in TRANSLIT:
            t = TRANSLIT[low].strip("_")
            out.append(t.capitalize() if ch.isupper() else t)
        elif ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        elif ch in " .,":
            out.append("_")
    return re.sub(r"_+", "_", "".join(out)).strip("_-")[:24]


def safe_name(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")[:80]


def channel_label(file_name, match, suffix=""):
    rest = file_name[match.end():] if match else file_name
    rest = re.sub(r"\.[^.]+$", "", rest).lstrip("_- ")
    if suffix and rest.endswith(suffix):
        rest = rest[: -len(suffix)].rstrip("_- ")
    return re.sub(r"^(\d+)-", r"\1 ", rest) or file_name


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_config():
    cfg = read_json(CONFIG_PATH, {})
    host = (os.environ.get("PLIXUS_HOST") or cfg.get("host") or DEFAULT_HOST).strip()
    rec = os.environ.get("PLIXUS_REC_DIR") or cfg.get("rec_dir") or str(APP_DIR / "Записи")
    port = int(cfg.get("port") or HTTP_PORT)
    return host, Path(os.path.expandvars(rec)).expanduser(), port


def apply_config(host, rec_dir, port=None):
    global HOST, REC_DIR, JOURNAL, HTTP_PORT
    HOST, REC_DIR = host, Path(rec_dir)
    JOURNAL = REC_DIR / "_journal.json"
    if port:
        HTTP_PORT = port


def ensure_dir(path):
    """Создать папку и убедиться, что в неё можно писать."""
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()


async def check_plixus(host, timeout=5):
    """Plixus отвечает и у него есть модуль записи?"""
    try:
        async with websockets.connect(f"ws://{host}:{WS_PORT}", subprotocols=["unicos-v2"],
                                      open_timeout=timeout, ping_interval=None) as ws:
            await ws.send(json.dumps({"module": "connection", "name": "get_api_versions", "data": None}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            modules = [m.get("name") for m in (msg.get("data") or [])]
            if "recording" not in modules:
                return False, f"{host} ответил, но модуля записи у него нет"
            return True, f"Plixus {host} отвечает"
    except Exception:
        return False, f"Нет ответа от {host} (порт {WS_PORT}). Проверьте адрес и что компьютер в той же сети"


apply_config(*load_config())


class Plixus:
    def __init__(self, loop):
        self.loop = loop
        self.ws = None
        self.connected = False
        self.state = "idle"
        self.settings = None
        self.files = {"internal": [], "external": []}
        self.seat_labels = {}
        self.live = {}            # место -> микрофон включён
        self.session = None       # текущая запись
        self.waiters = []
        self.sync_lock = asyncio.Lock()
        self.finishing = 0        # сколько остановленных записей ещё разбирается
        self.sync_text = ""
        self.version = 0          # растёт, когда в папке записей что-то изменилось
        self.last_error = ""
        self.journal = read_json(JOURNAL, {})
        self.reconnect_now = False

    # ---------- настройки ----------

    def settings_info(self):
        return {"host": HOST, "rec_dir": str(REC_DIR), "default_host": DEFAULT_HOST,
                "config": str(CONFIG_PATH)}

    async def apply_settings(self, host, rec_dir):
        if self.state != "idle" or self.session or self.finishing:
            raise RuntimeError("Во время записи настройки менять нельзя")
        host = (host or "").strip()
        if not host:
            raise RuntimeError("Укажите адрес Plixus")
        rec = Path(os.path.expandvars((rec_dir or "").strip() or str(REC_DIR))).expanduser()
        try:
            await asyncio.to_thread(ensure_dir, rec)
        except OSError:
            raise RuntimeError(f"Нет доступа к папке {rec}")
        cfg = read_json(CONFIG_PATH, {})
        cfg.update({"host": host, "rec_dir": str(rec)})
        try:
            write_json(CONFIG_PATH, cfg)
        except OSError:
            raise RuntimeError(f"Не удалось сохранить настройки в {CONFIG_PATH}")
        old_host = HOST
        async with self.sync_lock:
            apply_config(host, rec)
            self.journal = {**read_json(JOURNAL, {}), **self.journal}
            write_json(JOURNAL, self.journal)
            self.version += 1
        log("Настройки сохранены: Plixus", host, "| записи:", rec)
        if host != old_host:
            self.files = {"internal": [], "external": []}
            self.reconnect_now = True
            if self.ws:
                await self.ws.close()

    # ---------- связь ----------

    async def send(self, module, name, data=None):
        if not self.ws:
            raise RuntimeError("Нет связи с Plixus")
        await self.ws.send(json.dumps({"module": module, "name": name, "data": data}))

    def waiter(self, pred):
        fut = self.loop.create_future()
        self.waiters.append((pred, fut))
        return fut

    async def request(self, module, name, data, pred, timeout=5):
        fut = self.waiter(pred)
        try:
            await self.send(module, name, data)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.waiters = [w for w in self.waiters if w[1] is not fut]

    async def run(self):
        asyncio.create_task(self.sync_loop())
        while True:
            try:
                async with websockets.connect(f"ws://{HOST}:{WS_PORT}", subprotocols=["unicos-v2"],
                                              open_timeout=8, ping_interval=None) as ws:
                    self.ws, self.connected = ws, True
                    log("Plixus подключён:", HOST)
                    mods = ["connection", "recording", "discussion", "seat"]
                    await self.send("connection", "subscribe_modules", {
                        "client_id": "player-recorder", "modules": [{"name": m, "version": 0} for m in mods]})
                    await asyncio.sleep(0.3)
                    for module, name, data in [("recording", "get_settings", None),
                                               ("recording", "get_current_state", None),
                                               ("seat", "get_seats", None),
                                               ("discussion", "get_all_audio_input_states", None),
                                               ("recording", "get_files", "internal"),
                                               ("recording", "get_files", "external")]:
                        await self.send(module, name, data)
                    pinger = asyncio.create_task(self.lifecheck())
                    try:
                        async for raw in ws:
                            try:
                                self.handle(json.loads(raw))
                            except Exception as e:
                                log("Ошибка разбора сообщения:", e)
                    finally:
                        pinger.cancel()
            except Exception as e:
                if self.connected or not self.last_error:
                    log("Нет связи с Plixus:", e)
            self.ws, self.connected = None, False
            await asyncio.sleep(0.3 if self.reconnect_now else 5)
            self.reconnect_now = False

    async def lifecheck(self):
        while True:
            await asyncio.sleep(10)
            await self.send("connection", "lfc")

    def handle(self, msg):
        module, name, data = msg.get("module"), msg.get("name"), msg.get("data")
        if module == "recording":
            if name == "current_state":
                self.on_state(data)
            elif name == "settings":
                self.settings = data
            elif name == "files" and isinstance(data, dict):
                self.files[data.get("storage", "internal")] = [f["name"] for f in data.get("files", [])]
        elif module == "discussion":
            if name == "audio_input_state" and isinstance(data, dict):
                self.on_mic(data.get("seat_id"), data.get("state"))
            elif name == "all_audio_input_states" and isinstance(data, list):
                for item in data:
                    self.on_mic(item.get("seat_id"), item.get("state"))
        elif module == "seat" and name == "seats" and isinstance(data, list):
            for seat in data:
                sid = seat.get("seat_id")
                self.seat_labels[str(sid)] = (seat.get("label") or "").strip() or f"Мик {sid}"
        for pred, fut in list(self.waiters):
            if not fut.done() and pred(msg):
                fut.set_result(msg)

    # ---------- запись и метки ----------

    def offset(self):
        s = self.session
        if not s or s["t0"] is None:
            return 0.0
        paused = s["paused_total"] + (time.monotonic() - s["paused_at"] if s["paused_at"] else 0)
        return round(time.monotonic() - s["t0"] - paused, 2)

    def label(self, seat):
        return self.seat_labels.get(str(seat), f"Мик {seat}")

    def on_mic(self, seat, state):
        if seat is None:
            return
        on = state == "on"
        was = self.live.get(seat, False)
        self.live[seat] = on
        s = self.session
        if not s or s["t0"] is None or on == was:
            return
        if on:
            s["open"][seat] = self.offset()
        elif seat in s["open"]:
            start = s["open"].pop(seat)
            s["marks"].append({"seat": seat, "start": start, "end": self.offset()})

    def on_state(self, new):
        old, self.state = self.state, new
        if new == old:
            return
        log("Состояние записи Plixus:", old, "→", new)
        s = self.session
        if new == "active":
            if s is None:  # запись запустили не из плеера, метки всё равно пишем
                s = self.session = self.new_session("", None)
            if s["t0"] is None:
                s["t0"] = time.monotonic()
                s["started"] = datetime.now().isoformat(timespec="seconds")
                s["open"] = {seat: 0.0 for seat, on in self.live.items() if on}
            elif s["paused_at"]:
                s["paused_total"] += time.monotonic() - s["paused_at"]
                s["paused_at"] = None
        elif new == "paused" and s and s["t0"] is not None:
            s["paused_at"] = time.monotonic()
        elif new == "idle" and s and s["t0"] is not None:
            end = self.offset()
            for seat, start in s["open"].items():
                s["marks"].append({"seat": seat, "start": start, "end": end})
            s["open"] = {}
            s["duration"] = end
            self.session = None
            self.finishing += 1  # пока запись не разобрана, плановое скачивание её не трогает
            asyncio.create_task(self.finish(s))

    def new_session(self, name, old_prefix):
        stamps = {m.group(0) for paths in self.files.values() for p in paths if (m := STAMP.search(p))}
        return {"name": name, "old_prefix": old_prefix, "snapshot": stamps, "t0": None, "started": None,
                "paused_at": None, "paused_total": 0.0, "open": {}, "marks": [], "duration": None}

    async def start(self, name):
        name = name.strip()
        if not name:
            raise RuntimeError("Введите название записи")
        if not self.connected:
            raise RuntimeError("Нет связи с Plixus")
        if self.state != "idle":
            raise RuntimeError("Запись уже идёт")
        await self.request("recording", "get_files", "internal",
                           lambda m: m.get("name") == "files" and m["data"].get("storage") == "internal")
        if not self.settings:
            await self.request("recording", "get_settings", None, lambda m: m.get("name") == "settings")
        old_prefix = self.settings.get("prefix", "")
        prefix = plixus_prefix(name)
        changed = None
        if prefix != old_prefix:
            reply = await self.request("recording", "set_settings", {**self.settings, "prefix": prefix},
                                       lambda m: m.get("module") == "recording" and m.get("name") in ("settings", "record_error"))
            if reply.get("name") == "settings":
                changed = old_prefix
            else:
                log("Plixus не принял префикс", prefix, reply.get("data"))
        self.session = self.new_session(name, changed)
        try:
            await self.request("recording", "set_current_state", "active",
                               lambda m: m.get("name") == "current_state" and m.get("data") == "active", timeout=8)
        except Exception:
            self.session = None
            await self.restore_prefix(changed)
            raise RuntimeError("Plixus не начал запись")
        log("Запись начата:", name or "(без названия)", "| префикс:", prefix or "—")

    async def stop(self):
        if self.state not in ("active", "paused"):
            raise RuntimeError("Запись не идёт")
        await self.request("recording", "set_current_state", "idle",
                           lambda m: m.get("name") == "current_state" and m.get("data") == "idle", timeout=8)

    async def restore_prefix(self, old_prefix):
        if old_prefix is None or not self.settings:
            return
        try:
            await self.request("recording", "set_settings", {**self.settings, "prefix": old_prefix},
                               lambda m: m.get("module") == "recording" and m.get("name") in ("settings", "record_error"))
        except Exception as e:
            log("Не удалось вернуть префикс:", e)

    async def finish(self, s):
        try:
            await self._finish(s)
        finally:
            self.finishing -= 1
        await self.sync(force=True)

    async def _finish(self, s):
        await self.restore_prefix(s["old_prefix"])
        new = []
        for _ in range(6):
            await asyncio.sleep(2)
            try:
                await self.request("recording", "get_files", "internal",
                                   lambda m: m.get("name") == "files" and m["data"].get("storage") == "internal")
            except Exception:
                continue
            stamps = {m.group(0) for paths in self.files.values() for p in paths if (m := STAMP.search(p))}
            new = sorted(stamps - s["snapshot"])
            if new:
                break
        if not new:
            self.last_error = "После остановки Plixus не показал новых файлов"
            log(self.last_error)
            return
        split = (self.settings or {}).get("auto_split_time", 3600) if (self.settings or {}).get("auto_split") else None
        for i, stamp in enumerate(new):
            lo, hi = (i * split, (i + 1) * split) if split and len(new) > 1 else (0, float("inf"))
            marks = [{**m, "start": round(max(m["start"], lo) - lo, 2), "end": round(min(m["end"], hi) - lo, 2)}
                     for m in s["marks"] if m["end"] > lo and m["start"] < hi]
            self.journal[stamp] = {
                "name": s["name"] + (f" (часть {i + 1})" if len(new) > 1 else ""),
                "started": s["started"],
                "marks": [{**m, "label": self.label(m["seat"])} for m in marks],
            }
        write_json(JOURNAL, self.journal)
        log("Запись завершена:", ", ".join(new), "| меток:", len(s["marks"]))

    # ---------- скачивание в папки ----------

    async def sync_loop(self):
        await asyncio.sleep(5)  # первое скачивание — сразу после запуска
        while True:
            await self.sync()
            await asyncio.sleep(SYNC_EVERY)

    async def sync(self, force=False):
        if not self.connected or self.state != "idle":
            return
        if not force and (self.finishing or self.sync_lock.locked()):
            return
        async with self.sync_lock:
            try:
                for storage in ("internal", "external"):
                    try:
                        await self.request("recording", "get_files", storage,
                                           lambda m, st=storage: m.get("name") == "files" and m["data"].get("storage") == st)
                    except asyncio.TimeoutError:
                        pass
                await asyncio.to_thread(self.download_all)
            except Exception as e:
                self.last_error = f"Скачивание: {e}"
                log(self.last_error)
            finally:
                self.sync_text = ""

    def folders_by_stamp(self):
        found = {}
        for meta_path in REC_DIR.glob("*/meta.json"):
            meta = read_json(meta_path, {})
            stamp = meta.get("stamp")
            if stamp and (stamp not in found or meta.get("name")):
                found[stamp] = meta_path.parent
        return found

    def download_all(self):
        suffix = (self.settings or {}).get("suffix", "")
        groups = {}
        for paths in self.files.values():
            for path in paths:
                file_name = path.rsplit("/", 1)[-1]
                m = STAMP.search(file_name)
                if m:
                    prefix = file_name[:m.start()].strip("_- ")
                    groups.setdefault(m.group(0), []).append((path, channel_label(file_name, m, suffix), prefix))
        folders = self.folders_by_stamp()
        changed = False
        for stamp, items in sorted(groups.items()):
            info = self.journal.get(stamp, {})
            folder = folders.get(stamp)
            meta = read_json(folder / "meta.json", {}) if folder else {}
            # название: из плеера → уже записанное → префикс Plixus (запись запущена из админки)
            prefix_name = next((p for _, _, p in items if p), "").replace("_", " ").strip()
            name = info.get("name") or meta.get("name") or prefix_name
            y, mo, d, h, mi, se = STAMP.match(stamp).groups()
            title = f"{y}-{mo}-{d} {h}-{mi}-{se}" + (f" {safe_name(name)}" if name else "")
            if folder is None:
                folder = REC_DIR / title
                folder.mkdir(parents=True, exist_ok=True)
                # meta.json сразу, чтобы следующий проход нашёл папку по времени старта, а не завёл вторую
                meta = {"stamp": stamp, "name": name, "channels": [], "marks": info.get("marks", [])}
                write_json(folder / "meta.json", meta)
                changed = True
            elif name and folder.name != title and not (REC_DIR / title).exists():
                try:
                    folder = folder.rename(REC_DIR / title)
                    changed = True
                except OSError as e:
                    log("Не удалось переименовать папку", folder.name, e)
            meta_path = folder / "meta.json"
            channels = {c["label"]: c for c in meta.get("channels", [])}
            for path, label, _ in sorted(items, key=lambda x: (x[1] != "Floor", x[1])):
                ext = Path(path).suffix or ".mp3"
                target = folder / (safe_name(label) + ext)
                url = f"http://{HOST}{quote(path)}"
                if not target.exists():
                    self.sync_text = f"Скачиваю {folder.name}: {label}"
                    log(self.sync_text)
                    part = target.with_suffix(target.suffix + ".part")
                    with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
                        while chunk := resp.read(1 << 16):
                            out.write(chunk)
                    part.replace(target)
                    changed = True
                channels[label] = {"label": label, "file": target.name, "plixus": path}
            y, mo, d, h, mi, se = STAMP.match(stamp).groups()
            new_meta = {
                "stamp": stamp,
                "name": name,
                "plixus_start": f"{y}-{mo}-{d}T{h}:{mi}:{se}",
                "started": info.get("started", meta.get("started")),
                "channels": sorted(channels.values(), key=lambda c: (c["label"] != "Floor", c["label"])),
                "marks": info.get("marks", meta.get("marks", [])),
            }
            if new_meta != meta:
                write_json(meta_path, new_meta)
                changed = True
        if changed:
            self.version += 1

    def recordings(self):
        out = []
        seen = set()
        # если у одной записи вдруг две папки — показываем ту, что с названием
        metas = [(p, read_json(p, {})) for p in REC_DIR.glob("*/meta.json")]
        metas.sort(key=lambda pm: (not pm[1].get("name"), pm[0].parent.name))
        for meta_path, meta in metas:
            stamp = meta.get("stamp", "")
            if stamp and stamp in seen:
                continue
            seen.add(stamp)
            folder = meta_path.parent.name
            channels = [{"label": c["label"], "url": f"/rec/{quote(folder)}/{quote(c['file'])}"}
                        for c in meta.get("channels", []) if (meta_path.parent / c["file"]).exists()]
            if channels:
                out.append({"id": folder, "title": folder, "name": meta.get("name", ""), "stamp": meta.get("stamp", ""),
                            "channels": channels, "marks": meta.get("marks", [])})
        return sorted(out, key=lambda r: r["stamp"], reverse=True)

    def status(self):
        s = self.session
        return {
            "host": HOST,
            "plixus": self.connected,
            "state": self.state,
            "name": s["name"] if s else "",
            "elapsed": self.offset() if s and s["t0"] is not None else None,
            "live": [{"seat": seat, "label": self.label(seat)} for seat, on in sorted(self.live.items()) if on],
            "sync": self.sync_text,
            "error": self.last_error,
            "version": self.version,
            "folder": str(REC_DIR),
        }


class Handler(BaseHTTPRequestHandler):
    plx: Plixus = None

    def log_message(self, *args):
        pass

    def reply_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def call(self, coro, timeout=20):
        return asyncio.run_coroutine_threadsafe(coro, self.plx.loop).result(timeout)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self.serve(RES_DIR / "player.html", "text/html; charset=utf-8")
        if path == "/api/status":
            return self.reply_json(self.plx.status())
        if path == "/api/settings":
            return self.reply_json(self.plx.settings_info())
        if path == "/api/recordings":
            return self.reply_json(self.plx.recordings())
        if path.startswith("/rec/"):
            target = (REC_DIR / path[5:]).resolve()
            if REC_DIR.resolve() in target.parents and target.is_file():
                return self.serve(target)
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if path == "/api/check":
            ok, text = self.call(check_plixus(str(body.get("host", "")).strip() or HOST))
            return self.reply_json({"ok": ok, "message": text})
        try:
            if path == "/api/settings":
                self.call(self.plx.apply_settings(body.get("host"), body.get("rec_dir")))
                return self.reply_json({"ok": True, **self.plx.settings_info()})
            if path == "/api/start":
                self.call(self.plx.start(str(body.get("name", ""))[:80]))
            elif path == "/api/stop":
                self.call(self.plx.stop())
            elif path == "/api/sync":
                asyncio.run_coroutine_threadsafe(self.plx.sync(), self.plx.loop)
            else:
                return self.send_error(404)
            self.plx.last_error = ""
            self.reply_json({"ok": True})
        except Exception as e:
            msg = str(e) or type(e).__name__
            self.plx.last_error = msg
            self.reply_json({"ok": False, "error": msg}, 400)

    def serve(self, file_path, ctype=None):
        size = file_path.stat().st_size
        ctype = ctype or {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}.get(
            file_path.suffix.lower(), "application/octet-stream")
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and size:
            a, b = m.groups()
            if a:
                start, end = int(a), min(int(b) if b else size - 1, size - 1)
            elif b:
                start = max(0, size - int(b))
            code = 206
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if ctype.startswith("text/html"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            with open(file_path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass


def main():
    if "--check" in sys.argv:
        i = sys.argv.index("--check")
        host = sys.argv[i + 1] if i + 1 < len(sys.argv) else HOST
        ok, text = asyncio.run(check_plixus(host))
        print(text)
        sys.exit(0 if ok else 1)
    try:
        ensure_dir(REC_DIR)
    except OSError:
        fallback = APP_DIR / "Записи"
        log("Нет доступа к папке записей", REC_DIR, "— пишу в", fallback)
        apply_config(HOST, fallback)
        ensure_dir(REC_DIR)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    plx = Plixus(loop)
    asyncio.run_coroutine_threadsafe(plx.run(), loop)
    Handler.plx = plx
    try:
        server = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    except OSError:
        log(f"Порт {HTTP_PORT} занят: программа, похоже, уже запущена. Открываю плеер.")
        webbrowser.open(f"http://localhost:{HTTP_PORT}")
        return
    url = f"http://localhost:{HTTP_PORT}"
    log("Плеер:", url)
    log("Записи складываются в:", REC_DIR)
    log("Чтобы остановить программу, закройте это окно.")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
