#!/usr/bin/env python3
"""webota 종단 시험(1.0.0) — CPython 에서 **실제 서버를 띄우고** 클라이언트로 두드린다.

기기 파일시스템 루트는 임시 디렉토리(webota_boot.ROOT), machine.reset 은 reset_hook 으로
바꾼다. '부팅'은 webota_boot.apply() 를 직접 불러 흉내 낸다. 패키지 출처는 로컬 HTTP(302 →
chunked) — 서명은 openssl 로 만든 시험 키.

★1.0.0: 원격으로는 **서명된 패키지 설치**와 **수동 정리**만 된다. 상태는 기기 파일을 직접
써서 만든다(wr) — 파일 API 는 없다.

    python3 tests/test_webota.py
"""
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
DEV = os.path.join(HERE, "..", "device")
sys.path.insert(0, DEV)
import webota_boot as wb  # noqa: E402
import webota  # noqa: E402
import webota_pkg  # noqa: E402
import webota_sig  # noqa: E402

_spec = importlib.util.spec_from_file_location("webota_client", os.path.join(HERE, "..", "client", "webota.py"))
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)

fail = 0


def check(name, ok, extra=None):
    global fail
    if ok:
        print("  [PASS] " + name)
    else:
        fail += 1
        print("  [FAIL] " + name + ("" if extra is None else " — %r" % (extra,)))


def raises(fn, needle):
    try:
        fn()
    except cl.WebotaError as e:
        return needle in str(e)
    return False


def raises_sys(fn):
    try:
        fn()
    except SystemExit as e:
        return e.code not in (0, None)
    return False


def rd(path):
    with open(wb.p(path), "rb") as f:
        return f.read()


def wr(path, data):
    wb.makedirs(wb.parent(path))
    with open(wb.p(path), "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def wait_resets(resets, n, secs=3.0):
    end = time.time() + secs
    while len(resets) < n and time.time() < end:
        time.sleep(0.05)
    return len(resets) >= n


def serve_dir(d):
    import http.server
    import threading

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=d, **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/redir/"):
                self.send_response(302)
                self.send_header("Location", "/chunked/" + self.path[7:])
                self.end_headers()
                return
            if self.path.startswith("/chunked/"):
                data = open(os.path.join(d, self.path[9:]), "rb").read()
                self.protocol_version = "HTTP/1.1"
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
                for i in range(0, len(data), 1000):
                    part = data[i:i + 1000]
                    self.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                return
            return super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    root = tempfile.mkdtemp(prefix="webota-test-")
    local = tempfile.mkdtemp(prefix="webota-local-")
    wb.ROOT = root
    resets = []
    webota.reset_hook = lambda: resets.append(time.time())
    webota._idle_forever = False

    key = os.path.join(local, "sign.pem")            # 시험 키(암호 없음) · 가짜 개발자 키
    rec = cl.signing_key_init(key, passphrase=False)
    other = os.path.join(local, "other.pem")
    cl.signing_key_init(other, passphrase=False)

    port = free_port()
    wr("/webota.json", json.dumps({"token": "t0k", "port": port, "confirm_s": 0, "app_id": "testapp",
                                   "pkg_keys": [rec], "github_token": "ghp_SECRET_never_leave"}))
    wr("/webota_ui.html", open(os.path.join(DEV, "webota_ui.html"), "rb").read())
    webota.start(webota.load_config())
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    api = cl.Client("127.0.0.1:%d" % port, "t0k")
    anon = cl.Client("127.0.0.1:%d" % port, "")

    pk = os.path.join(local, "pkgs")
    os.makedirs(pk)
    base = "http://127.0.0.1:%d" % serve_dir(pk)
    webota.cfg["sources"] = [{"index": base + "/index.json"}]
    json.dump([], open(os.path.join(pk, "index.json"), "w"))
    src = os.path.join(local, "src")

    def srcfile(name, body):
        p = os.path.join(src, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)
        return p

    def pack(name, files, app_id="testapp", version="1", sign_key=key, **kw):
        cl.build_package(files, os.path.join(pk, name), app_id, version, "v" + version, sign_key=sign_key, **kw)
        return base + "/" + name

    def install(url, **kw):
        n0 = len(resets)
        r = api.pkg_install(url, wait=False, log=lambda *_: None, **kw)
        if r == "committed":
            wait_resets(resets, n0 + 1)
            wb.apply()
        return r

    def try_install(url, **kw):
        return lambda: api.pkg_install(url, wait=False, log=lambda *_: None, **kw)

    def confirm():
        webota._trial = True
        webota.app_state = "running"
        webota._tick()

    print("== 인증 · 없앤 API")
    check("토큰 없으면 401", raises(lambda: anon.status(), "401"))
    check("틀린 토큰 401", raises(lambda: cl.Client("127.0.0.1:%d" % port, "x").status(), "401"))
    check("맞는 토큰 status", api.status()["webota"] == webota.VERSION)
    for m, path in (("GET", "/fs/"), ("GET", "/fs/webota.json"), ("PUT", "/fs/x.py"), ("DELETE", "/fs/app.py"),
                    ("POST", "/deploy/begin"), ("POST", "/sha"), ("POST", "/reset"), ("POST", "/token"),
                    ("POST", "/pkg/sources")):
        check("없앤 API %s %s → 404" % (m, path),
              raises(lambda: api._req(m, path, body=(b"{}" if m != "GET" else None)), "404"))

    print("== 비밀은 어떤 응답에도 나가지 않는다")
    blobs = [json.dumps(api.status()), json.dumps(api._req("GET", "/hello")[1]),
             json.dumps(api.pkg_list(fresh=True)), json.dumps(api.history(50)),
             api._req("GET", "/")[1].decode(errors="replace")]
    check("GitHub 토큰 문자열 없음", not any("ghp_SECRET" in b for b in blobs))
    check("기기 토큰 문자열 없음", not any('"t0k"' in b for b in blobs))
    sec = api.status()["security"]
    check("status.security — 있다/없다만", sec == {"pkg_keys": [rec["id"]], "github_token": True, "ca": False}, sec)

    print("== 서명 검증(webota_sig) — openssl 과 맞물림")
    msg = b"hello manifest"
    sig = cl.sign(msg, key)
    check("맞는 서명 → 키 id", webota_sig.verify(msg, sig, [rec]) == rec["id"])
    check("본문이 바뀌면 거부", webota_sig.verify(msg + b"!", sig, [rec]) is None)
    check("다른 키로 서명하면 거부", webota_sig.verify(msg, cl.sign(msg, other), [rec]) is None)
    bad = bytearray(sig)
    bad[10] ^= 1
    check("서명이 한 비트 바뀌면 거부", webota_sig.verify(msg, bytes(bad), [rec]) is None)
    check("키가 없으면 거부", webota_sig.verify(msg, sig, []) is None)

    print("== 암호 걸린 서명 키(webota 가 암호를 묻고 openssl 에는 환경변수로)")
    os.environ[cl.PASS_ENV] = "test-pass-1234"
    kp = os.path.join(local, "enc.pem")
    rec_p = cl.signing_key_init(kp)
    check("암호 걸린 키 생성 · 공개키 파일", cl._encrypted(kp) and os.path.exists(kp + ".pub.json"))
    check("암호 키로 서명 → 검증", webota_sig.verify(b"m", cl.sign(b"m", kp), [rec_p]) == rec_p["id"])
    os.environ[cl.PASS_ENV] = "wrong-pass-00"
    cl._pass_cache.clear()
    check("틀린 암호 → 서명 거부", raises(lambda: cl.sign(b"m", kp), "openssl 실패"))
    check("공개키는 암호 없이(.pub.json)", cl.pubkey_record(kp) == rec_p)
    del os.environ[cl.PASS_ENV]
    cl._pass_cache.clear()

    print("== 서명된 패키지 설치 · 바뀐 파일만 · 확인")
    files = {"/app.py": srcfile("app.py", "V = 1\n"), "/lib.py": srcfile("lib.py", "L = 1\n"),
             "/www/i.html": srcfile("www/i.html", "<p>1</p>")}
    wr("/lib.py", "L = 1\n")
    u1 = pack("p1.wpk", files, version="1.0.0", data=["/data"])
    check("pack 은 WPK2(서명)", open(os.path.join(pk, "p1.wpk"), "rb").read(5) == b"WPK2\n")
    pl = api.pkg_plan(u1)
    check("계획 — 서명 키 id · 바뀐 파일만", pl["key"] == rec["id"] and "/app.py" in pl["write"]
          and "/lib.py" not in pl["write"], pl)
    check("설치(리다이렉트 + chunked)", install(u1.replace(base, base + "/redir")) == "committed")
    check("적용", rd("/app.py") == b"V = 1\n" and rd("/www/i.html") == b"<p>1</p>")
    confirm()
    last = wb.read_json(wb.DIR + "/last.json")
    check("확인 → last ok · prev 정리", last["result"] == "ok" and not wb.exists(wb.DIR + "/prev"), last)
    check("같은 패키지 → unchanged", try_install(u1)() == "unchanged")

    print("== ★변형된 패키지는 설치하지 않는다")
    raw = open(os.path.join(pk, "p1.wpk"), "rb").read()
    e1 = raw.index(b"\n", 5)
    n = int(raw[5:e1])
    man = json.loads(raw[e1 + 1:e1 + 1 + n])
    after_man = raw[e1 + 1 + n:]                              # b"<서명 길이>\n" + 서명 + 파일들
    e2 = after_man.index(b"\n")
    sn = int(after_man[:e2])
    body = after_man[e2 + 1 + sn:]
    man2 = dict(man)
    man2["version"] = "6.6.6"
    mj2 = json.dumps(man2).encode()
    open(os.path.join(pk, "t_man.wpk"), "wb").write(b"WPK2\n" + str(len(mj2)).encode() + b"\n" + mj2 + after_man)
    b2 = bytearray(raw)
    b2[-3] ^= 0x20
    open(os.path.join(pk, "t_file.wpk"), "wb").write(bytes(b2))
    mj1 = json.dumps(man).encode()
    open(os.path.join(pk, "t_v1.wpk"), "wb").write(b"WPK1\n" + str(len(mj1)).encode() + b"\n" + mj1 + body)
    files_evil = dict(files, **{"/app.py": srcfile("evil_app.py", "import evil\n")})
    u_other = pack("t_other.wpk", files_evil, version="9", sign_key=other, data=["/data"])
    for label, url, needle, plan_too in (("매니페스트 변조", base + "/t_man.wpk", "서명 불일치", True),
                                         ("파일 바이트 변조", base + "/t_file.wpk", "해시 불일치", False),
                                         ("다른 키로 서명", u_other, "서명 불일치", True),
                                         ("서명 없는 WPK1", base + "/t_v1.wpk", "서명 없는", True)):
        check("%s → 설치 거부" % label, raises(try_install(url), needle))
        if plan_too:
            check("%s → 계획도 거부" % label, raises(lambda: api.pkg_plan(url), needle))
    check("변조 시도 뒤 기기는 그대로", rd("/app.py") == b"V = 1\n" and not wb.exists(wb.DIR + "/pending.json")
          and not wb.exists(wb.DIR + "/stage"))
    evil_cfg = srcfile("evil.json", '{"pkg_keys": []}')
    u_cfg = pack("t_cfg.wpk", dict(files, **{"/webota.json": evil_cfg}), version="2", data=["/data"])
    check("/webota.json 을 담은 패키지 거부(서명이 맞아도)", raises(try_install(u_cfg), "건드리면 안 되는"))
    keys = webota.cfg.pop("pkg_keys")
    check("공개키 없는 기기 → 어떤 패키지도 거부", raises(try_install(u1), "공개키가 없다"))
    webota.cfg["pkg_keys"] = keys

    print("== 롤백 · 앱 예외 · 가드")
    f2 = dict(files, **{"/app.py": srcfile("app2.py", "V = 2\n"), "/new.py": srcfile("new.py", "N = 1\n")})
    install(pack("p2.wpk", f2, version="2.0.0", data=["/data"]))
    check("v2 적용", rd("/app.py") == b"V = 2\n" and wb.exists("/new.py"))
    wb.apply(); wb.apply(); wb.apply()
    check("확인 없이 3회 부팅 → 롤백", rd("/app.py") == b"V = 1\n" and not wb.exists("/new.py")
          and wb.read_json(wb.DIR + "/last.json")["result"] == "rolled_back")
    sys.path.insert(0, root)
    install(pack("p3.wpk", dict(files, **{"/app.py": srcfile("app3.py", "raise ImportError('깨진 판')\n")}),
                 version="3.0.0", data=["/data"]))
    sys.modules.pop("app", None)
    n0 = len(resets)
    webota.run_app({"app": "app", "entry": "main"})
    check("시험 중 앱 예외 → 롤백 + 리셋", rd("/app.py") == b"V = 1\n" and len(resets) == n0 + 1)
    wr("/app.py", "raise RuntimeError('평시 고장')\n")
    sys.modules.pop("app", None)
    webota.run_app({"app": "app", "entry": "main"})
    check("평시 앱 예외 → 구조 모드", webota.app_state == "rescue" and "평시 고장" in api.status()["app"]["error"])
    wr("/app.py", "V = 1\n")
    f4 = dict(files, **{"/app.py": srcfile("app4.py", "V = 4\n")})
    u4 = pack("p4.wpk", f4, version="4.0.0", data=["/data"])
    webota.set_guard(lambda: (False, "측정 중"))
    check("가드 거부 → 423", raises(try_install(u4), "측정 중"))
    check("force 로 통과", install(u4, force=True) == "committed" and rd("/app.py") == b"V = 4\n")
    webota.set_guard(None)
    confirm()

    print("== 코드 미러 · 설정/데이터 선언 · 계획 · 초기화 · 정리")
    wr("/stray.py", "x"); wr("/www/vendor/old.js", "x"); wr("/config.json", '{"a": 9}'); wr("/etc/net.json", "{}")
    wr("/data/m.dat", "1 2 3"); wr("/logs/l.txt", "log")
    f5 = dict(f4, **{"/config.json": srcfile("config.json", '{"a": 1}')})
    u5 = pack("p5.wpk", f5, version="5.0.0", settings=["/config.json", "/etc"], data=["/data", "/logs"])
    pl = api.pkg_plan(u5)
    check("계획 — 남은 코드 삭제 · 설정/데이터 보존", "/stray.py" in pl["delete"] and "/www/vendor/old.js" in pl["delete"]
          and "/config.json" not in pl["delete"]
          and not any(x.startswith(("/data/", "/logs/", "/etc/")) for x in pl["delete"]), pl)
    install(u5)
    check("미러 적용 · 빈 디렉토리 정리", not wb.exists("/stray.py") and not wb.exists("/www/vendor"))
    check("설정 유지(덮어쓰지 않음) · 데이터 유지", rd("/config.json") == b'{"a": 9}' and wb.exists("/etc/net.json")
          and wb.exists("/data/m.dat") and wb.exists("/logs/l.txt"))
    check("webota 설정(비밀 포함) 유지", wb.read_json("/webota.json")["github_token"] == "ghp_SECRET_never_leave")
    confirm()
    pl = api.pkg_plan(u5, reset_settings=True)
    check("설정 초기화 계획", "/config.json" in pl["write"] and pl["delete_reset"] == ["/etc/net.json"], pl)
    install(u5, reset_settings=True)
    check("설정 초기화", rd("/config.json") == b'{"a": 1}' and not wb.exists("/etc/net.json") and wb.exists("/data/m.dat"))
    confirm()
    install(u5, reset_data=True)
    check("데이터 초기화(같은 판 → 초기화만)", not wb.exists("/data/m.dat") and not wb.exists("/logs/l.txt")
          and wb.exists("/app.py"))
    wb.apply(); wb.apply(); wb.apply()
    check("데이터 초기화 롤백 → 데이터 복원", wb.exists("/data/m.dat") and rd("/logs/l.txt") == b"log")
    wr("/extra.py", "e")
    o = api.orphans()
    check("정리 목록 — 남은 코드만", [e["path"] for e in o["orphans"]] == ["/extra.py"], o)
    r = api.clean(["/extra.py", "/app.py", "/data/m.dat", "/webota.json"])
    check("정리 — 남은 파일만 지우고 앱·데이터·설정 거부", r["deleted"] == ["/extra.py"]
          and sorted(r["refused"]) == ["/app.py", "/data/m.dat", "/webota.json"], r)
    u6 = pack("p6.wpk", f4, version="6.0.0")
    check("선언 없는 패키지 계획 — /data 까지 정리 대상(★)", "/data/m.dat" in api.pkg_plan(u6)["delete_kept_now"])

    print("== 앱 교체 · 첫 설치 받아들이기 · 출처는 보기만")
    check("출처 = 설정의 것만(보기)", api.pkg_sources() == [base + "/index.json"])
    dev_files = {"/" + n: os.path.join(DEV, n) for n in ("webota.py", "webota_boot.py", "main.py", "boot.py")}
    fo = dict(f4, **dev_files, **{"/app.py": srcfile("appo.py", "OTHER = 1\n")})
    uo = pack("otherapp-v1.0.0.wpk", fo, app_id="otherapp", version="1.0.0", data=["/data"])
    check("다른 앱 → 409 app_mismatch", raises(lambda: api._req("POST", "/pkg/install", body={"url": uo}), "409"))
    before = wb.read_json("/webota.json")
    install(uo, switch_app=True)
    after = wb.read_json("/webota.json")
    check("앱 교체 — 설정도 같은 트랜잭션(공개키·토큰 유지)", after["app_id"] == "otherapp"
          and after["pkg_keys"] == before["pkg_keys"] and after["github_token"] == before["github_token"])
    wb.apply(); wb.apply(); wb.apply()
    check("앱 교체 롤백 — 설정도 원래대로", wb.read_json("/webota.json") == before)
    d = wb.read_json("/webota.json")
    d.pop("app_id")
    wb.write_json("/webota.json", d)
    webota.load_config()
    webota.cfg["sources"] = [{"index": base + "/index.json"}]
    install(pack("fresh.wpk", f4, app_id="freshapp", version="1.0.0", data=["/data"]))
    check("앱 없는 기기의 첫 설치 → 그 앱을 받아들임", wb.read_json("/webota.json").get("app_id") == "freshapp")
    confirm()

    print("== TLS: CA 없으면 연결하지 않는다 · GitHub 토큰은 GitHub 호스트에만")
    wb.remove("/webota_ca.pem")
    try:
        webota_pkg.get("https://127.0.0.1:1/x")
        ok = False
    except OSError as e:
        ok = "CA 묶음이 없다" in str(e)
    check("CA 묶음 없으면 https 거부", ok)
    sent = []

    class FakeSock:
        def __init__(self):
            self.n = 0

        def sendall(self, b):
            sent.append(b.decode())

        def makefile(self, m):
            return self

        def readline(self):
            self.n += 1
            return b"HTTP/1.1 404 Not Found\r\n" if self.n == 1 else b"\r\n"

        def read(self, n):
            return b""

        def close(self):
            pass
    orig = webota_pkg._connect
    webota_pkg._connect = lambda h, p, t: FakeSock()
    for url in ("https://api.github.com/x", "https://release-assets.githubusercontent.com/x"):
        try:
            webota_pkg.get(url, token="ghp_T")
        except OSError:
            pass
    webota_pkg._connect = orig
    check("GitHub 토큰은 api.github.com 에만", len(sent) == 2 and "Bearer ghp_T" in sent[0] and "Bearer" not in sent[1], sent)

    print("== WiFi 는 webota 가 · AP 에서 토큰 없이 설정 · NTP")
    import webota_net as net
    GOOD = {"HomeNet": "pw123"}

    class W:
        def __init__(self, kind):
            self.kind, self.on, self.conn, self.cfg = kind, False, False, {"mac": b"\x01\x02\x03\x04\xab\xcd"}

        def active(self, v=None):
            if v is None:
                return self.on
            self.on = v

        def isconnected(self):
            return self.conn

        def connect(self, ssid, pw):
            self.conn = GOOD.get(ssid) == pw

        def disconnect(self):
            self.conn = False

        def config(self, *a, **kw):
            if kw:
                self.cfg.update(kw)
                return None
            return self.cfg.get(a[0])

        def ifconfig(self):
            return ("10.0.0.5",) if self.kind == "sta" else ("127.0.0.1",)

        def scan(self):
            return [(b"HomeNet", b"", 1, -50, 3, 0)]

        def status(self, k):
            return -50
    fake = types.ModuleType("network")
    fake.STA_IF, fake.AP_IF, fake.AUTH_WPA_WPA2_PSK = 0, 1, 3
    _w = {0: W("sta"), 1: W("ap")}
    fake.WLAN = lambda i: _w[i]
    fake.hostname = lambda n: None
    ntp_calls = []
    ntpm = types.ModuleType("ntptime")
    ntpm.settime = lambda: ntp_calls.append(1)
    sys.modules["network"] = fake
    sys.modules["ntptime"] = ntpm
    net._sta = net._ap = net._down_since = net._up_since = net._last_try = net._ntp_at = net._ntp_try = None
    webota.cfg["wifi"] = {"ssid": "HomeNet", "pass": "bad"}
    webota.cfg["wifi_timeout_s"] = 1
    check("부팅 — 접속 실패면 AP", net.boot(webota.cfg) is False and net.ap_active())
    r = anon._req("POST", "/wifi", body={"ssid": "HomeNet", "pass": "pw123"})[1]
    check("AP 에서 토큰 없이 WiFi 저장", r["ok"] and wb.read_json("/webota.json")["wifi"]["ssid"] == "HomeNet")
    net.tick(webota.cfg)                              # 재접속 시작
    net.tick(webota.cfg)                              # 붙은 뒤 틱 — NTP
    check("재접속 · 접속하면 NTP", net.is_connected() and len(ntp_calls) >= 1, ntp_calls)
    check("AP 여도 다른 API 는 토큰 필요", raises(lambda: anon.status(), "401"))
    del sys.modules["network"]
    del sys.modules["ntptime"]
    net._sta = net._ap = None
    webota.cfg.pop("wifi", None)

    print("== 첫 부팅 · 등록 · 로그인 폼 · 빈 예비 연결 · 상태 디렉토리")
    wb.remove("/other.json")
    check("설정 파일 없으면 기본값 생성", webota.load_config("/other.json").get("token") is None and wb.exists("/other.json"))
    webota.load_config()
    webota.cfg["sources"] = [{"index": base + "/index.json"}]
    check("등록된 기기 재등록 409", raises(lambda: anon._req("POST", "/claim", body={"token": "x" * 32}), "409"))
    import http.client
    import urllib.parse

    def login(pw):
        cn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        cn.request("POST", "/login", body=urllib.parse.urlencode({"username": "w", "password": pw}),
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        rr = cn.getresponse()
        loc = rr.getheader("Location")
        rr.read()
        cn.close()
        return rr.status, loc
    check("로그인 폼 맞음/틀림 → 303", login("t0k") == (303, "/?login=ok") and login("no") == (303, "/?login=bad"))
    idle = socket.create_connection(("127.0.0.1", port), 2)
    t0 = time.time()
    ok = api.status()["webota"] == webota.VERSION
    took = time.time() - t0
    idle.settimeout(5)
    try:
        left = idle.recv(100)
    except OSError:
        left = b"?"
    idle.close()
    check("빈 예비 연결이 뒤 요청을 막지 않고 응답 없이 닫힘", ok and took < webota.HEAD_TIMEOUT_S + 2 and left == b"",
          (took, left))
    check("상태 디렉토리는 import 로 닿지 않는 이름", not wb.DIR.strip("/").isidentifier())

    print("== CLI")
    ba = ["--host", "127.0.0.1:%d" % port, "--token", "t0k", "-y"]
    check("cli status", cl.main(ba + ["status"]) == 0)
    check("cli sources", cl.main(ba + ["sources"]) == 0)
    check("cli pkg-list", cl.main(ba + ["pkg-list", "--fresh"]) == 0)
    check("cli clean -y", cl.main(ba + ["clean"]) == 0)
    check("cli 없앤 명령(ls) → 인자 오류", raises_sys(lambda: cl.main(ba + ["ls", "/"])))
    check("cli signing-key show", cl.main(["signing-key", "show", "--key", key]) == 0)
    dc = cl.device_config({"app_id": "myapp", "device": {"hostname": "myapp"}}, "T" * 32, [rec], None)
    check("device-config — 공개키 · 토큰 없으면 github_token 안 넣음", dc["pkg_keys"] == [rec]
          and "github_token" not in dc and dc["token"] == "T" * 32 and dc["hostname"] == "myapp")

    webota._stop = True
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(local, ignore_errors=True)
    print("\n%s — 실패 %d건" % ("ALL PASS" if not fail else "FAIL", fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
