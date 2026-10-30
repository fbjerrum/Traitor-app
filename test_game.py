"""End-to-end test: python3 test_game.py (starts its own server on port 8123)."""
import json
import os
import tempfile
import threading
import urllib.request

os.environ["TRAITORS_DATA"] = os.path.join(tempfile.mkdtemp(), "data.json")
import server


def call(path, **body):
    req = urllib.request.Request(f"http://127.0.0.1:8123{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read())["error"]}


def main():
    threading.Thread(target=server.ticker, daemon=True).start()
    srv = server.ThreadingHTTPServer(("127.0.0.1", 8123), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    codes = [f"secret{i}" for i in range(7)]
    regs = [call("/api/register", name=f"P{i}", code=codes[i]) for i in range(7)]
    toks = [r["token"] for r in regs]
    assert "error" in call("/api/register", name="p1", code="whatever"), "duplicate username"
    assert "error" in call("/api/register", name="Short", code="abc"), "code too short"
    assert "error" in call("/api/login", name="P1", code="wrongcode")
    assert "error" in call("/api/state", token="bogus"), "bad token"

    server.ADMIN["code"] = "adminpw"
    assert "error" in call("/api/admin", admin="nope")
    users = call("/api/admin", admin="adminpw")["users"]
    assert {u["name"]: u["code"] for u in users} == {f"P{i}": codes[i] for i in range(7)}

    # logout invalidates the session; login with the code works again
    call("/api/logout", token=toks[1])
    assert "error" in call("/api/state", token=toks[1])
    toks[1] = call("/api/login", name="p1", code=codes[1])["token"]

    code = call("/api/create", token=toks[0])["code"]
    assert "error" in call("/api/create", token=toks[0]), "already in game"
    for i in range(1, 7):
        assert call("/api/join", token=toks[i], code=code)["phase"] == "lobby"
    st = lambda i: call("/api/state", token=toks[i])
    act = lambda i, a, **d: call("/api/action", token=toks[i], action=a, data=d)

    assert "error" in call("/api/start", token=toks[1]), "non-host start"
    call("/api/settings", token=toks[0], settings={"discussion_secs": 30, "tempts": 1, "traitors": 2})
    v = call("/api/start", token=toks[0])
    assert v["phase"] == "role"
    roles = {i: st(i)["me"]["role"] for i in range(7)}
    tr = [i for i, r in roles.items() if r == "traitor"]
    loy = [i for i, r in roles.items() if r == "loyalist"]
    assert len(tr) == 2, roles
    # loyalists never see other roles
    assert all(p["role"] is None for p in st(loy[0])["players"] if p["id"] != st(loy[0])["me"]["id"])
    # the server restarts mid-game; players log in again and keep their seats
    with server.LOCK:
        server.save()
        server.ROOMS.clear(); server.ACCOUNTS.clear(); server.SESSIONS.clear()
        server.load()
    toks = [call("/api/login", name=f"P{i}", code=codes[i])["token"] for i in range(7)]
    assert {i: st(i)["me"]["role"] for i in range(7)} == roles, "roles survive restart"
    for i in range(7):
        act(i, "ready")
    assert st(0)["phase"] == "night"

    # traitors tempt loyalist; loyalist accepts
    ids = {p["name"]: p["id"] for p in st(0)["players"]}
    target = loy[0]
    for i in tr:
        assert act(i, "night", kind="tempt", target=ids[f"P{target}"])["phase"] in ("night", "offer")
    s = st(target)
    assert s["phase"] == "offer" and s["offer_for_me"]
    act(target, "offer", accept=True)
    assert st(target)["me"]["role"] == "traitor"
    assert st(0)["phase"] == "dawn"
    # tempt limit
    for i in range(7):
        act(i, "ready")
    assert st(0)["phase"] == "discussion"
    call("/api/action", token=toks[0], action="skip")
    assert st(0)["phase"] == "vote"
    # everyone votes for a loyalist (P with remaining loyalist role)
    loy_left = [i for i in loy if i != target]
    out = loy_left[0]
    for i in range(7):
        act(i, "vote", target=ids[f"P{out if i != out else loy_left[1]}"])
    s = st(0)
    assert s["phase"] == "result" and s["last_vote"]["out"] == ids[f"P{out}"], s["phase"]
    for i in range(7):
        act(i, "ready")
    # traitors 3 vs loyalists 3 -> traitors win
    s = st(0)
    assert s["phase"] == "end" and s["winner"] == "traitors", s["phase"]
    assert call("/api/restart", token=toks[0])["phase"] == "lobby"
    assert call("/api/leave", token=toks[3])["home"]
    assert st(3)["home"]
    print("OK")


if __name__ == "__main__":
    main()
