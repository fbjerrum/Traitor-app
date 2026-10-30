#!/usr/bin/env python3
"""Traitors - multiplayer party game server. Standard library only.

Run:  python3 server.py [port]
Players open http://<your-ip>:<port> on their phones.
"""
import json
import mimetypes
import os
import random
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA_FILE = os.environ.get("TRAITORS_DATA") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
MIN_PLAYERS = 6
MAX_PLAYERS = 30
ACK_SECS = 20
DECISION_SECS = 30
OFFER_SECS = 45

LOCK = threading.RLock()
ROOMS = {}


class GameError(Exception):
    pass


def default_settings():
    return {
        "discussion_secs": 180,
        "vote_secs": 60,
        "night_secs": 60,
        "traitors": 0,  # 0 = auto
        "tempts": 2,  # max tempts per game
    }


def auto_traitors(n):
    return max(1, n // 4)


def max_traitors(n):
    return max(1, n // 3)


class Room:
    def __init__(self, code):
        self.code = code
        self.created = time.time()
        self.players = {}  # pid -> dict
        self.order = []
        self.host = None
        self.settings = default_settings()
        self.phase = "lobby"
        self.deadline = None
        self.round = 0
        self.acts = {}  # pid -> payload, for current phase
        self.log = []  # public events [{round, text}]
        self.winner = None
        self.tempts_used = 0
        self.offer = None  # {"target": pid}
        self.night_note = None  # private feedback for traitors
        self.last_vote = None  # public tally of last vote
        self.last_decision = None
        self.stopped = False

    # ---------- helpers ----------
    def alive(self):
        return [p for p in self.players.values() if p["alive"]]

    def alive_role(self, role):
        return [p for p in self.alive() if p["role"] == role]

    def say(self, text):
        self.log.append({"round": self.round, "text": text})

    def set_phase(self, phase, secs=None):
        self.phase = phase
        self.acts = {}
        self.deadline = time.time() + secs if secs else None

    def needed(self):
        ph = self.phase
        if ph in ("role", "dawn", "result"):
            return [p["id"] for p in self.alive()]
        if ph == "night":
            return [p["id"] for p in self.alive_role("traitor")]
        if ph == "offer":
            return [self.offer["target"]]
        if ph == "vote":
            return [p["id"] for p in self.alive()]
        if ph == "decision":
            return [p["id"] for p in self.alive_role("loyalist")]
        return []  # lobby, discussion, end: timer / host only

    # ---------- lifecycle ----------
    def pid_of(self, user):
        for p in self.players.values():
            if p["user"] == user:
                return p["id"]
        return None

    def add_player(self, user, name):
        if self.phase != "lobby":
            raise GameError("Game already started")
        if len(self.players) >= MAX_PLAYERS:
            raise GameError("Room is full")
        pid = secrets.token_hex(4)
        p = {"id": pid, "user": user, "name": name,
             "role": None, "alive": True, "revealed": False, "banished": None}
        self.players[pid] = p
        self.order.append(pid)
        if self.host is None:
            self.host = pid
        return p

    def remove_player(self, pid):
        self.players.pop(pid, None)
        self.order.remove(pid)
        if self.host == pid and self.order:
            self.host = self.order[0]

    def update_settings(self, s):
        cur = self.settings
        for key, lo, hi in (("discussion_secs", 10, 1800), ("vote_secs", 10, 600),
                            ("night_secs", 10, 600), ("traitors", 0, 10), ("tempts", 0, 5)):
            if key in s:
                try:
                    v = int(s[key])
                except (TypeError, ValueError):
                    raise GameError("Invalid setting")
                cur[key] = max(lo, min(hi, v))

    def start(self):
        n = len(self.players)
        if n < MIN_PLAYERS:
            raise GameError(f"Need at least {MIN_PLAYERS} players")
        t = self.settings["traitors"] or auto_traitors(n)
        t = min(t, max_traitors(n))
        ids = list(self.players)
        random.shuffle(ids)
        for i, pid in enumerate(ids):
            self.players[pid]["role"] = "traitor" if i < t else "loyalist"
        self.round = 0
        self.say(f"The game begins with {n} players. Among them hide {t} traitor{'s' if t != 1 else ''}.")
        self.set_phase("role", ACK_SECS)

    def reset(self):
        for p in self.players.values():
            p.update(role=None, alive=True, revealed=False, banished=None)
        self.log = []
        self.winner = None
        self.tempts_used = 0
        self.offer = None
        self.night_note = None
        self.last_vote = None
        self.last_decision = None
        self.round = 0
        self.set_phase("lobby")

    # ---------- win check ----------
    def check_win(self):
        t = len(self.alive_role("traitor"))
        l = len(self.alive_role("loyalist"))
        if t == 0:
            self.finish("loyalists", "All traitors have been found. The loyalists win!")
            return True
        if t >= l:
            self.finish("traitors", "The traitors outnumber the loyalists. The traitors win!")
            return True
        return False

    def finish(self, winner, text):
        self.winner = winner
        self.say(text)
        for p in self.players.values():
            p["revealed"] = True
        self.set_phase("end")

    # ---------- actions ----------
    def act(self, pid, action, data):
        p = self.players[pid]
        if action == "skip":
            if pid != self.host:
                raise GameError("Only the host can skip")
            if self.phase in ("lobby", "end"):
                raise GameError("Nothing to skip")
            self.advance(force=True)
            return
        if self.phase == "lobby" or self.phase == "end":
            raise GameError("Not available now")
        if not p["alive"] and action != "ready":
            raise GameError("You are out of the game")
        if action == "ready":
            if self.phase in ("role", "dawn", "result", "discussion") and p["alive"]:
                self.acts[pid] = True
        elif action == "night":
            self.do_night(p, data)
        elif action == "offer":
            if self.phase != "offer" or pid != self.offer["target"]:
                raise GameError("No offer for you")
            self.acts[pid] = bool(data.get("accept"))
        elif action == "vote":
            if self.phase != "vote":
                raise GameError("Not voting now")
            target = data.get("target")
            if target in (None, ""):
                self.acts[pid] = None
            else:
                tp = self.players.get(target)
                if not tp or not tp["alive"] or target == pid:
                    raise GameError("Invalid vote")
                self.acts[pid] = target
        elif action == "decision":
            if self.phase != "decision" or p["role"] != "loyalist":
                raise GameError("Not available")
            self.acts[pid] = "stop" if data.get("choice") == "stop" else "continue"
        else:
            raise GameError("Unknown action")
        self.maybe_advance()

    def do_night(self, p, data):
        if self.phase != "night" or p["role"] != "traitor":
            raise GameError("Not available")
        kind = data.get("kind")
        target = self.players.get(data.get("target"))
        if not target or not target["alive"] or target["role"] == "traitor":
            raise GameError("Pick a living loyalist")
        if kind == "tempt" and self.tempts_used >= self.settings["tempts"]:
            raise GameError("No tempts left")
        if kind not in ("murder", "tempt"):
            raise GameError("Invalid choice")
        self.acts[p["id"]] = (kind, target["id"])

    def maybe_advance(self):
        need = self.needed()
        if need and all(pid in self.acts for pid in need):
            self.advance()

    def tick(self):
        if self.deadline and time.time() >= self.deadline:
            self.advance()
            return True
        return False

    # ---------- phase machine ----------
    def advance(self, force=False):
        ph = self.phase
        s = self.settings
        if ph == "role":
            self.start_night()
        elif ph == "night":
            self.resolve_night()
        elif ph == "offer":
            self.resolve_offer()
        elif ph == "dawn":
            self.set_phase("discussion", s["discussion_secs"])
        elif ph == "discussion":
            self.set_phase("vote", s["vote_secs"])
        elif ph == "vote":
            self.resolve_vote()
        elif ph == "result":
            if self.check_win():
                return
            self.set_phase("decision", DECISION_SECS)
        elif ph == "decision":
            self.resolve_decision()

    def start_night(self):
        self.round += 1
        self.night_note = None
        self.dawn_msg = None
        self.set_phase("night", self.settings["night_secs"])

    def resolve_night(self):
        votes = {}
        for kind, target in self.acts.values():
            votes[(kind, target)] = votes.get((kind, target), 0) + 1
        if not votes:
            self.say("Night passes quietly. The traitors could not agree on anything.")
            self.night_note = "You did not act. Nobody was targeted."
            self.to_dawn()
            return
        top = max(votes.values())
        kind, target = random.choice([k for k, v in votes.items() if v == top])
        if kind == "murder":
            self.kill(target, "murdered")
            self.to_dawn()
        else:
            self.tempts_used += 1
            self.offer = {"target": target}
            self.set_phase("offer", OFFER_SECS)

    def resolve_offer(self):
        t = self.players[self.offer["target"]]
        accepted = self.acts.get(t["id"], False)
        if accepted:
            t["role"] = "traitor"
            self.night_note = f"{t['name']} accepted your offer and is now a traitor!"
            self.say("Night passes quietly. Nobody was murdered.")
        else:
            self.night_note = f"{t['name']} refused your offer and was murdered."
            self.kill(t["id"], "murdered")
        self.offer = None
        self.to_dawn()

    def to_dawn(self):
        if not self.check_win():
            self.set_phase("dawn", ACK_SECS)

    def kill(self, pid, how):
        p = self.players[pid]
        p["alive"] = False
        p["revealed"] = True
        p["banished"] = how
        self.say(f"{p['name']} was {how} in the night. They were a {p['role']}.")

    def resolve_vote(self):
        tally = {}
        for v in self.acts.values():
            if v:
                tally[v] = tally.get(v, 0) + 1
        votes = {pid: self.players[v]["name"] if v else None for pid, v in self.acts.items()}
        out = None
        if tally:
            top = max(tally.values())
            leaders = [k for k, v in tally.items() if v == top]
            if len(leaders) == 1:
                out = leaders[0]
        self.last_vote = {
            "round": self.round,
            "tally": [{"name": self.players[k]["name"], "votes": v} for k, v in
                      sorted(tally.items(), key=lambda kv: -kv[1])],
            "votes": [{"voter": self.players[k]["name"], "for": v} for k, v in votes.items()],
            "out": out,
        }
        if out:
            p = self.players[out]
            p["alive"] = False
            p["revealed"] = True
            p["banished"] = "banished"
            self.say(f"{p['name']} was banished by vote. They were a {p['role']}.")
        else:
            self.say("The vote was tied or empty. Nobody was banished.")
        self.set_phase("result", ACK_SECS)

    def resolve_decision(self):
        loy = self.alive_role("loyalist")
        stops = sum(1 for v in self.acts.values() if v == "stop")
        if loy and stops * 2 > len(loy):
            self.say("The loyalists chose to stop the game while traitors remain.")
            self.finish("traitors", "The game ended early with a traitor still among you. The traitors win!")
        else:
            self.say("The loyalists chose to continue.")
            self.start_night()

    # ---------- per-player view ----------
    def view(self, pid):
        me = self.players[pid]
        now = time.time()
        i_traitor = me["role"] == "traitor"
        plist = []
        for q in (self.players[i] for i in self.order):
            role = None
            if q["id"] == pid or q["revealed"] or (i_traitor and q["role"] == "traitor"):
                role = q["role"]
            plist.append({"id": q["id"], "name": q["name"], "alive": q["alive"], "role": role,
                          "out": q["banished"], "host": q["id"] == self.host})
        v = {
            "code": self.code, "phase": self.phase, "round": self.round,
            "remaining": max(0, int(self.deadline - now + 0.99)) if self.deadline else None,
            "settings": self.settings, "players": plist,
            "me": {"id": pid, "name": me["name"], "role": me["role"], "alive": me["alive"],
                   "host": pid == self.host},
            "log": self.log[-30:], "winner": self.winner,
            "min_players": MIN_PLAYERS, "auto_traitors": auto_traitors(len(self.players)),
            "max_traitors": max_traitors(len(self.players)),
            "ready": pid in self.acts, "waiting": 0,
        }
        need = self.needed()
        v["waiting"] = sum(1 for n in need if n not in self.acts)
        v["players_needed"] = len(need)
        if self.phase == "night" and i_traitor and me["alive"]:
            v["night"] = {"mine": list(self.acts.get(pid, [])) or None,
                          "tempts_left": self.settings["tempts"] - self.tempts_used,
                          "targets": [q["id"] for q in self.alive_role("loyalist")]}
        if self.phase == "offer" and self.offer:
            v["offer_for_me"] = pid == self.offer["target"]
            v["answered"] = pid in self.acts
        if self.phase in ("dawn", "discussion", "vote", "result", "decision") and i_traitor:
            v["night_note"] = self.night_note
        if self.phase == "vote":
            v["my_vote"] = self.acts.get(pid)
            v["voted"] = len(self.acts)
        if self.phase in ("result", "decision", "end"):
            v["last_vote"] = self.last_vote
        if self.phase == "decision" and me["role"] == "loyalist" and me["alive"]:
            v["my_decision"] = self.acts.get(pid)
        if self.phase == "end":
            v["roles"] = [{"name": q["name"], "role": q["role"]} for q in self.players.values()]
        return v


# ---------- room registry ----------
def new_code():
    while True:
        code = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4))
        if code not in ROOMS:
            return code


# ---------- accounts & persistence ----------
ACCOUNTS = {}  # lowercase username -> {name, code}
ADMIN = {"code": ""}
SESSIONS = {}  # session token -> lowercase username
FAILS = {}  # lowercase username -> (count, locked_until)
USER_RE = re.compile(r"^[A-Za-z0-9_ÆØÅæøå-]{2,20}$")


def save():
    data = {"admin": ADMIN["code"], "accounts": ACCOUNTS, "sessions": SESSIONS,
            "rooms": {c: r.__dict__ for c, r in ROOMS.items()}}
    tmp = DATA_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, DATA_FILE)


def load():
    if not os.path.exists(DATA_FILE):
        return
    with open(DATA_FILE) as f:
        data = json.load(f)
    ADMIN["code"] = data.get("admin", "")
    ACCOUNTS.update(data.get("accounts", {}))
    SESSIONS.update(data.get("sessions", {}))
    for code, d in data.get("rooms", {}).items():
        room = Room(code)
        room.__dict__.update(d)
        ROOMS[code] = room


def ticker():
    while True:
        time.sleep(0.5)
        with LOCK:
            changed = False
            for code, room in list(ROOMS.items()):
                if time.time() - room.created > 12 * 3600:
                    del ROOMS[code]
                    changed = True
                elif room.tick():
                    changed = True
            if changed:
                save()


def room_of(user):
    for room in ROOMS.values():
        if room.pid_of(user):
            return room
    return None


def register(body):
    name = " ".join(str(body.get("name", "")).split())
    if not USER_RE.match(name):
        raise GameError("Username: 2-20 letters, numbers, space, _ or -")
    key = name.lower()
    if key in ACCOUNTS:
        raise GameError("Username already taken")
    code = str(body.get("code", ""))
    if not 4 <= len(code) <= 30:
        raise GameError("Login code must be 4-30 characters")
    ACCOUNTS[key] = {"name": name, "code": code}
    return {"name": name, "token": new_session(key)}


def new_session(key):
    token = secrets.token_urlsafe(24)
    SESSIONS[token] = key
    return token


def admin_users(body):
    until = FAILS.get("\0admin", (0, 0))[1]
    if until > time.time():
        raise GameError(f"Too many attempts. Try again in {int(until - time.time()) + 1}s")
    if not secrets.compare_digest(str(body.get("admin", "")).encode(), ADMIN["code"].encode()):
        count = FAILS.get("\0admin", (0, 0))[0] + 1
        FAILS["\0admin"] = (0, time.time() + 30) if count >= 5 else (count, 0)
        raise GameError("Wrong admin password")
    FAILS.pop("\0admin", None)
    return {"users": sorted(({"name": a["name"], "code": a["code"], "game": bool(room_of(k))}
                            for k, a in ACCOUNTS.items()), key=lambda u: u["name"].lower())}


def login(body):
    key = " ".join(str(body.get("name", "")).split()).lower()
    count, until = FAILS.get(key, (0, 0))
    if until > time.time():
        raise GameError(f"Too many attempts. Try again in {int(until - time.time()) + 1}s")
    acc = ACCOUNTS.get(key)
    code = str(body.get("code", ""))[:30]
    ok = secrets.compare_digest(code.encode(), (acc["code"] if acc else "").encode())
    if not acc or not ok:
        count += 1
        FAILS[key] = (0, time.time() + 30) if count >= 5 else (count, 0)
        raise GameError("Wrong username or login code")
    FAILS.pop(key, None)
    return {"name": acc["name"], "token": new_session(key)}


def handle_api(path, body):
    with LOCK:
        result = _handle_api(path, body)
        if path not in ("/api/state", "/api/login", "/api/admin"):
            save()
        return result


def _handle_api(path, body):
    if path == "/api/register":
        return register(body)
    if path == "/api/login":
        return login(body)
    if path == "/api/admin":
        return admin_users(body)
    token = str(body.get("token", ""))
    user = SESSIONS.get(token)
    if not user:
        raise GameError("Not logged in")
    acc = ACCOUNTS[user]
    if path == "/api/logout":
        SESSIONS.pop(token, None)
        return {"ok": True}
    room = room_of(user)
    if path == "/api/create":
        if room:
            raise GameError("You are already in a game")
        room = Room(new_code())
        ROOMS[room.code] = room
        room.add_player(user, acc["name"])
        return room.view(room.pid_of(user))
    if path == "/api/join":
        if room:
            raise GameError("You are already in a game")
        target = ROOMS.get(str(body.get("code", "")).upper().strip())
        if not target:
            raise GameError("Room not found")
        target.add_player(user, acc["name"])
        return target.view(target.pid_of(user))
    if not room:
        if path == "/api/state":
            return {"home": True, "name": acc["name"]}
        raise GameError("You are not in a game")
    pid = room.pid_of(user)
    host_only = pid == room.host
    if path == "/api/state":
        return room.view(pid)
    if path == "/api/leave":
        if room.phase not in ("lobby", "end"):
            raise GameError("You can only leave in the lobby or after the game")
        room.remove_player(pid)
        if not room.players:
            del ROOMS[room.code]
        return {"home": True, "name": acc["name"]}
    if path == "/api/settings":
        if not host_only or room.phase != "lobby":
            raise GameError("Only the host can change settings in the lobby")
        room.update_settings(body.get("settings", {}))
    elif path == "/api/start":
        if not host_only or room.phase != "lobby":
            raise GameError("Only the host can start")
        room.start()
    elif path == "/api/kick":
        if not host_only or room.phase != "lobby":
            raise GameError("Not allowed")
        if body.get("id") != pid:
            room.remove_player(body.get("id"))
    elif path == "/api/restart":
        if not host_only or room.phase != "end":
            raise GameError("Not allowed")
        room.reset()
    elif path == "/api/action":
        room.act(pid, body.get("action"), body.get("data") or {})
    else:
        raise GameError("Unknown endpoint")
    return room.view(pid)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            path = "/index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, path.lstrip("/")))
        if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
            return self.send(404, {"error": "Not found"})
        with open(full, "rb") as f:
            self.send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 10000:
                raise GameError("Request too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise GameError("Bad request")
            self.send(200, handle_api(urlparse(self.path).path, body))
        except GameError as e:
            self.send(400, {"error": str(e)})
        except (ValueError, KeyError):
            self.send(400, {"error": "Bad request"})


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    load()
    env_admin = os.environ.get("TRAITORS_ADMIN")
    if env_admin:
        ADMIN["code"] = env_admin
    elif not ADMIN["code"]:
        ADMIN["code"] = secrets.token_urlsafe(9)
    save()
    print(f"Admin password: {ADMIN['code']}  (open /#admin to see all login codes)")
    threading.Thread(target=ticker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"Traitors running. Players on the same Wi-Fi open: http://{lan_ip()}:{port}")
    srv.serve_forever()
