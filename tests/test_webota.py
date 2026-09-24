#!/usr/bin/env python3
"""webota 종단 시험 — CPython 에서 **실제 서버를 띄우고** 클라이언트로 두드린다.

기기 파일시스템 루트는 임시 디렉토리(webota_boot.ROOT), machine.reset 은 reset_hook 으로
바꾼다. '부팅'은 webota_boot.apply() 를 직접 불러 흉내 낸다.

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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "device"))
import webota_boot as wb  # noqa: E402
import webota  # noqa: E402  (기기 쪽 서버)

_spec = importlib.util.spec_from_file_location("webota_client",
                                               os.path.join(HERE, "..", "client", "webota.py"))
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


def rd(path):
    with open(wb.p(path), "rb") as f:
        return f.read()


def wr(path, data):
    wb.makedirs(wb.parent(path))
    with open(wb.p(path), "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def wait_resets(resets, n, secs=3.0):
    """서버 스레드의 리셋은 응답 뒤 0.5초에 비동기로 온다 — n 번째가 올 때까지 기다린다."""
    end = time.time() + secs
    while len(resets) < n and time.time() < end:
        time.sleep(0.05)
    return len(resets) >= n


def serve_dir(d):
    """패키지 출처 흉내 — /redir/<x> 는 302, /chunked/<x> 는 chunked 로 준다."""
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


def _all_under(root, dirs):
    out = []
    for d in dirs:
        for dp, _dn, fn in os.walk(root + d):
            out += [dp[len(root):] + "/" + n for n in fn]
    return out


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
    port = free_port()
    wr("/webota.json", json.dumps({"token": "t0k", "port": port, "confirm_s": 0}))
    c = webota.load_config()
    webota.start(c)
    for _ in range(50):                                   # 리스너가 뜰 때까지
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    api = cl.Client("127.0.0.1:%d" % port, "t0k")

    print("== 인증")
    check("토큰 없으면 401", raises(lambda: cl.Client("127.0.0.1:%d" % port, "").status(), "401"))
    check("틀린 토큰 401", raises(lambda: cl.Client("127.0.0.1:%d" % port, "x").status(), "401"))
    check("맞는 토큰 status", api.status()["webota"] == webota.VERSION)

    print("== 파일 API (제한 없음)")
    lp = os.path.join(local, "a.txt")
    with open(lp, "wb") as f:
        f.write(b"hello" * 1000)
    r = api.put(lp, "/data/sub/a.txt")
    check("put — 상위 디렉토리 자동 생성", rd("/data/sub/a.txt") == b"hello" * 1000)
    check("put — sha 반환", r["sha"] == cl.sha_of(lp))
    check("sha 불일치 거부", raises(lambda: api._req("PUT", "/fs/data/b.txt", {"sha": "00"},
                                                    body=b"xyz"), "SHA256"))
    check("sha 불일치면 파일 안 남음", not wb.exists("/data/b.txt") and not wb.exists("/data/b.txt.part"))
    got = api.get("/data/sub/a.txt", os.path.join(local, "back.txt"))
    check("get 파일", open(got[0], "rb").read() == b"hello" * 1000)
    wr("/boot.py", "x=1")
    check("boot.py 도 덮어쓰기 가능", api.put(lp, "/boot.py")["ok"] and rd("/boot.py") == b"hello" * 1000)
    ents = api.ls("/", recursive=True)
    paths = [e["path"] for e in ents]
    check("ls -r", "/data/sub/a.txt" in paths and "/data/sub" in paths, paths)
    check("ls 파일 하나", api.ls("/data/sub/a.txt")[0]["size"] == 5000)
    api.mkdir("/data/x/y")
    check("mkdir 상위 포함", wb.is_dir("/data/x/y"))
    api.mv("/data/sub/a.txt", "/data/x/a2.txt")
    check("mv", wb.exists("/data/x/a2.txt") and not wb.exists("/data/sub/a.txt"))
    check("비어 있지 않은 디렉토리 rm 거부", raises(lambda: api.rm("/data/x"), "409"))
    api.rm("/data/x", recursive=True)
    check("rm -r", not wb.exists("/data/x"))
    check("'..' 거부", raises(lambda: api._req("GET", "/fs/../etc"), "400"))
    wr("/data/q.bak", "1"); wr("/data/r.bak", "2"); wr("/data/keep.json", "{}")
    check("글롭 expand", sorted(api.expand("/data/*.bak")) == ["/data/q.bak", "/data/r.bak"])
    check("없는 파일 get 404", raises(lambda: api.get("/nope", local), "404"))

    print("== 배포: 바뀐 파일만 · 적용 · 확인")
    src = os.path.join(local, "src")
    os.makedirs(os.path.join(src, "www"))
    for n, body in (("app.py", "V = 1\n"), ("lib.py", "L = 1\n")):
        with open(os.path.join(src, n), "w") as f:
            f.write(body)
    with open(os.path.join(src, "www", "i.html"), "w") as f:
        f.write("<p>1</p>")
    wr("/lib.py", "L = 1\n")                               # 같은 내용 — 올리지 않아야 한다
    wr("/old.py", "gone")
    project = {"_root": src, "map": [{"src": "*.py", "dst": "/"}, {"src": "www/", "dst": "/www/"}]}
    files, scopes = cl.map_files(project)
    check("map_files", set(files) == {"/app.py", "/lib.py", "/www/i.html"}, files)
    extra = cl.extra_remote(api, files, scopes)
    check("extra_remote — map 범위의 원격 전용 파일만", "/old.py" in extra and "/boot.py" in extra
          and not any(p.startswith("/data") for p in extra), extra)
    res = api.deploy(files, ["/old.py"], wait=False, log=lambda *_: None, label="v1+aaa")
    check("바뀐 파일만", sorted(res["changed"]) == ["/app.py", "/www/i.html"], res)
    check("커밋 후 리셋 요청", wait_resets(resets, 1))
    check("pending 기록", wb.exists("/webota/pending.json"))
    check("적용 전에는 그대로", not wb.exists("/app.py") and wb.exists("/old.py"))
    wb.apply()                                             # ← 부팅
    check("부팅 적용", rd("/app.py") == b"V = 1\n" and rd("/www/i.html") == b"<p>1</p>")
    check("부팅 삭제", not wb.exists("/old.py"))
    check("trial 시작", wb.in_trial() and not wb.exists("/webota/pending.json"))
    webota._trial = True
    webota.app_state = "running"
    webota._tick()                                         # confirm_s=0 → 곧바로 확인
    last = wb.read_json("/webota/last.json")
    check("확인 → last ok", last and last["result"] == "ok" and last["id"] == res["id"], last)
    check("라벨이 last 에", last.get("label") == "v1+aaa", last)
    check("unchanged", api.deploy(files, wait=False, log=lambda *_: None)["result"] == "unchanged")

    print("== 롤백: 확인 없이 3회 부팅")
    with open(os.path.join(src, "app.py"), "w") as f:
        f.write("V = 2\n")
    with open(os.path.join(src, "new.py"), "w") as f:
        f.write("N = 1\n")
    files, _ = cl.map_files(project)
    res2 = api.deploy(files, wait=False, log=lambda *_: None)
    wait_resets(resets, 2)
    wb.apply()
    check("v2 적용", rd("/app.py") == b"V = 2\n" and wb.exists("/new.py"))
    wb.apply(); wb.apply()
    check("부팅 2회 — 아직 시험 중", wb.in_trial())
    wb.apply()
    check("3회째 롤백 — 원래 내용", rd("/app.py") == b"V = 1\n")
    check("롤백 — 새 파일 제거", not wb.exists("/new.py"))
    last = wb.read_json("/webota/last.json")
    check("last rolled_back", last["result"] == "rolled_back" and last["id"] == res2["id"], last)
    h = api.history(5)
    check("history — ok 다음 rolled_back", [e["result"] for e in h] == ["ok", "rolled_back"]
          and h[0]["label"] == "v1+aaa", h)

    print("== 앱 예외: 시험 중이면 롤백 · 아니면 구조 모드")
    sys.path.insert(0, root)
    with open(os.path.join(src, "app.py"), "w") as f:
        f.write("raise ImportError('깨진 판')\n")
    files, _ = cl.map_files(project)
    res3 = api.deploy(files, wait=False, log=lambda *_: None)
    wait_resets(resets, 3)
    wb.apply()
    n0 = len(resets)
    sys.modules.pop("app", None)
    webota.run_app({"app": "app", "entry": "main"})
    check("시험 중 예외 → 롤백", rd("/app.py") == b"V = 1\n")
    check("롤백 후 리셋", len(resets) == n0 + 1)
    check("crash.txt 기록", "깨진 판" in rd("/webota/crash.txt").decode())
    check("last rolled_back(앱 예외)", wb.read_json("/webota/last.json")["id"] == res3["id"])
    wr("/app.py", "raise RuntimeError('평시 고장')\n")
    sys.modules.pop("app", None)
    n0 = len(resets)
    webota.run_app({"app": "app", "entry": "main"})
    check("평시 예외 → 구조 모드(리셋 없음)", webota.app_state == "rescue" and len(resets) == n0)
    st = api.status()
    check("구조 모드에서도 OTA 응답 + 오류 문구", st["app"]["state"] == "rescue"
          and "평시 고장" in st["app"]["error"], st["app"])
    wr("/app.py", "def main():\n    pass\n")
    sys.modules.pop("app", None)
    webota.run_app({"app": "app", "entry": "main"})
    check("정상 앱 — main 끝나면 exited", webota.app_state == "exited")

    print("== 가드")
    with open(os.path.join(src, "app.py"), "w") as f:
        f.write("V = 3\n")
    files, _ = cl.map_files(project)
    webota.set_guard(lambda: (False, "측정 중"))
    check("가드 거부 → 423", raises(lambda: api.deploy(files, wait=False, log=lambda *_: None),
                                    "측정 중"))
    check("가드 거부 — pending 없음", not wb.exists("/webota/pending.json"))
    check("reset 도 가드", raises(lambda: api.reset(), "423"))
    res4 = api.deploy(files, force=True, wait=False, log=lambda *_: None)
    wait_resets(resets, len(resets) + 1)
    check("force 로 통과", res4["result"] == "committed" and wb.exists("/webota/pending.json"))
    webota.set_guard(None)
    wb.apply()
    check("force 배포 적용", rd("/app.py") == b"V = 3\n")

    print("== 트랜잭션 id")
    check("begin 없이 커밋 거부", raises(lambda: api._req("POST", "/deploy/zzz/commit", body={}),
                                       "409"))

    print("== 배포 패키지(.wpk)")
    pk = os.path.join(local, "pkgs")
    os.makedirs(pk)
    with open(os.path.join(src, "app.py"), "w") as f:
        f.write("V = 20\n")
    files, _ = cl.map_files(project)
    man = cl.build_package(files, os.path.join(pk, "t.wpk"), "testapp", "2.0.0", "v2.0.0+test",
                           data=["/data"])
    check("pack — 매니페스트", cl.read_manifest(os.path.join(pk, "t.wpk"))["label"] == "v2.0.0+test"
          and len(man["files"]) == len(files))
    bad = bytearray(open(os.path.join(pk, "t.wpk"), "rb").read())
    bad[-3] ^= 0xFF
    open(os.path.join(pk, "bad.wpk"), "wb").write(bytes(bad))
    cl.build_package(files, os.path.join(pk, "other.wpk"), "otherapp", "9.0.0")
    hp = serve_dir(pk)
    base_url = "http://127.0.0.1:%d" % hp
    with open(os.path.join(pk, "index.json"), "w") as f:
        json.dump([{"tag": "v2.0.0", "name": "v2.0.0", "url": base_url + "/redir/t.wpk", "size": 1}], f)
    webota.cfg["packages"] = {"index": base_url + "/index.json"}
    webota.cfg["app_id"] = "testapp"
    lst = api.pkg_list(fresh=True)
    check("pkg/list — index 출처", lst["ok"] and lst["packages"][0]["tag"] == "v2.0.0", lst)
    wr("/webota_ui.html", open(os.path.join(HERE, "..", "device", "webota_ui.html"), "rb").read())
    ui = cl.Client("127.0.0.1:%d" % port, "")._req("GET", "/")[1]
    check("ui — 토큰 없이 설치 화면", b"/pkg/install" in ui)
    n0 = len(resets)
    r = api.pkg_install(base_url + "/redir/t.wpk", wait=False, log=lambda *_: None)
    check("pkg/install — 리다이렉트+chunked 로 받아 커밋", r == "committed" and wait_resets(resets, n0 + 1))
    wb.apply()
    check("패키지 적용", rd("/app.py") == b"V = 20\n")
    check("패키지 라벨 → trial", (wb.read_json("/webota/trial.json") or {}).get("label") == "v2.0.0+test")
    check("status.current = 패키지 라벨", api.status()["current"] == "v2.0.0+test")
    check("같은 패키지 재설치 → unchanged",
          api.pkg_install(base_url + "/t.wpk", wait=False, log=lambda *_: None) == "unchanged")
    check("다른 앱 패키지 거부", raises(lambda: api.pkg_install(base_url + "/other.wpk", wait=False,
                                                         log=lambda *_: None), "다른 앱"))
    check("손상 패키지 거부", raises(lambda: api.pkg_install(base_url + "/bad.wpk", wait=False,
                                                       log=lambda *_: None), "해시 불일치"))
    check("손상 — pending·stage 없음", not wb.exists("/webota/pending.json") and not wb.exists("/webota/stage"))
    webota.set_guard(lambda: (False, "측정 중"))
    check("패키지 설치도 가드", raises(lambda: api.pkg_install(base_url + "/other.wpk", force=False,
                                                         wait=False, log=lambda *_: None), "측정 중"))
    webota.set_guard(None)

    print("== 수동 변경 추적(WSL 세부 조정 ↔ 패키지)")
    wb.remove("/webota/modified.json")
    with open(lp, "w") as f:
        f.write("TWEAK = 1\n")
    api.put(lp, "/app.py")
    api.put(lp, "/data/cfg.json")
    m = api.status()["modified"]
    check("코드 수정은 기록, 데이터는 제외", m and m["paths"] == ["/app.py"], m)
    api.rm("/data/cfg.json")
    check("데이터 삭제도 제외", api.status()["modified"]["paths"] == ["/app.py"])
    r = api.pkg_install(base_url + "/t.wpk", wait=False, log=lambda *_: None)
    check("수동 변경 뒤 패키지 설치 → 그 파일을 되돌림", r == "committed")
    wait_resets(resets, len(resets) + 1)
    wb.apply()
    check("패키지 적용 → 수동 변경 해제", api.status()["modified"] is None and rd("/app.py") == b"V = 20\n")

    print("== 출처(저장소) 관리 · 앱 교체")
    check("sources — 옛 packages 한 개를 읽는다", api.pkg_sources() == [base_url + "/index.json"])
    check("sources add — 저장소 URL", api.pkg_sources(add="https://github.com/foo/bar.git")[-1] == "foo/bar")
    disk = wb.read_json("/webota.json")
    check("sources — 설정 파일에 저장(토큰 유지)", disk.get("token") == "t0k" and
          {"github": "foo/bar"} in disk.get("sources", []) and "packages" not in disk, disk)
    check("sources default", api.pkg_sources(default="foo/bar")[0] == "foo/bar")
    check("sources remove", api.pkg_sources(remove="foo/bar") == [base_url + "/index.json"])
    check("sources add — 모르는 주소 거부", raises(lambda: api.pkg_sources(add="https://gitlab.com/a/b"), "400"))
    check("pkg/list — src 지정", api.pkg_list(src=base_url + "/index.json")["src"] == base_url + "/index.json")
    dev = os.path.join(HERE, "..", "device")
    with_wo = dict(files)
    for n in ("webota.py", "webota_boot.py", "main.py", "boot.py"):
        with_wo["/" + n] = os.path.join(dev, n)
    with open(os.path.join(src, "app.py"), "w") as f:
        f.write("OTHER = 1\n")
    files2, _ = cl.map_files(project)
    with_wo.update(files2)
    cl.build_package(with_wo, os.path.join(pk, "otherapp-v1.0.0.wpk"), "otherapp", "1.0.0", "v1.0.0+x",
                     app="app", entry="main")
    cl.build_package(files2, os.path.join(pk, "bare-v1.0.0.wpk"), "bareapp", "1.0.0")
    with open(os.path.join(pk, "index.json"), "w") as f:
        json.dump([{"tag": "v1.0.0", "name": "other", "url": base_url + "/otherapp-v1.0.0.wpk"}], f)
    lst = api.pkg_list(fresh=True)
    check("목록 — 파일 이름에서 app_id", lst["packages"][0]["app_id"] == "otherapp", lst["packages"])
    e409 = None
    try:
        api._req("POST", "/pkg/install", body={"url": base_url + "/otherapp-v1.0.0.wpk"})
    except cl.WebotaError as e:
        e409 = str(e)
    check("다른 앱 — 409 app_mismatch", e409 and "409" in e409, e409)
    check("webota 없는 패키지로는 교체 거부", raises(lambda: api.pkg_install(
        base_url + "/bare-v1.0.0.wpk", switch_app=True, wait=False, log=lambda *_: None), "webota 가 없다"))
    n0 = len(resets)
    r = api.pkg_install(base_url + "/otherapp-v1.0.0.wpk", switch_app=True, src=base_url + "/index.json",
                        wait=False, log=lambda *_: None)
    check("앱 교체 — 커밋", r == "committed" and wait_resets(resets, n0 + 1))
    before = wb.read_json("/webota.json")
    wb.apply()
    after = wb.read_json("/webota.json")
    check("앱 교체 — 설정도 같은 트랜잭션(app_id·token 유지)", after.get("app_id") == "otherapp"
          and after.get("token") == "t0k" and after.get("app") == "app", after)
    check("앱 교체 — 새 앱 코드", rd("/app.py") == b"OTHER = 1\n")
    wb.apply(); wb.apply(); wb.apply()                     # 새 앱이 자리를 못 잡음 → 롤백
    check("앱 교체 롤백 — 설정도 원래대로", wb.read_json("/webota.json") == before and
          rd("/app.py") != b"OTHER = 1\n")

    print("== 설치 = 코드를 패키지 그대로(설정·데이터 구분) · 정리")
    webota.set_guard(None)
    p3 = os.path.join(local, "p3")
    os.makedirs(os.path.join(p3, "www"))
    open(os.path.join(p3, "app.py"), "w").write("V = 30\n")
    open(os.path.join(p3, "www", "i.html"), "w").write("<p>3</p>")
    open(os.path.join(p3, "config.json"), "w").write('{"a": 1}')
    files3 = {"/app.py": os.path.join(p3, "app.py"), "/www/i.html": os.path.join(p3, "www", "i.html"),
              "/config.json": os.path.join(p3, "config.json")}
    check("데이터 경로 파일은 패키지에 못 넣음", raises(lambda: cl.build_package(
        {"/logs/x": lp}, os.path.join(pk, "no.wpk"), "testapp", "3", data=["/logs"]), "데이터"))
    m3 = cl.build_package(files3, os.path.join(pk, "p3.wpk"), "testapp", "3.0.0", "v3.0.0",
                          settings=["/config.json", "/etc"], data=["/data", "/logs"])
    check("매니페스트 — 설정 기본값 kind", [f.get("kind") for f in m3["files"] if f["path"] == "/config.json"]
          == ["setting"] and m3["settings"] == ["/config.json", "/etc"])
    for path, body in (("/stray.py", "x"), ("/www/old.js", "x"), ("/www/vendor/x.js", "x"),
                       ("/config.json", '{"a": 9}'), ("/etc/net.json", "{}"), ("/logs/l.txt", "log"),
                       ("/data/keep.json", "{}")):
        wr(path, body)
    n0 = len(resets)
    check("설치 커밋", api.pkg_install(base_url + "/p3.wpk", wait=False, log=lambda *_: None) == "committed"
          and wait_resets(resets, n0 + 1))
    wb.apply()
    check("코드 — 패키지에 없는 파일 삭제", not wb.exists("/stray.py") and not wb.exists("/www/old.js")
          and not wb.exists("/www/vendor/x.js"))
    check("빈 디렉토리 정리", not wb.exists("/www/vendor") and wb.exists("/www/i.html"))
    check("설정 — 기기 값 유지(덮어쓰지 않음)", rd("/config.json") == b'{"a": 9}')
    check("설정 디렉토리 · 앱 데이터 · /data 유지", wb.exists("/etc/net.json") and wb.exists("/logs/l.txt")
          and wb.exists("/data/keep.json"))
    check("webota 자신(CORE)·기기 설정 유지", wb.exists("/boot.py") and wb.exists("/webota.json"))
    inst = wb.read_json("/webota/installed.json")
    check("installed.json — 파일·설정·데이터", inst["files"] == sorted(files3) and inst["data"] == ["/data", "/logs"], inst)
    webota._trial = True; webota.app_state = "running"; webota._tick()
    check("확인 → 백업(prev) 삭제", not wb.exists("/webota/prev"))
    st = api.status()
    check("status.keep — 설정·데이터 목록", "/config.json" in st["keep"]["settings"] and "/logs" in st["keep"]["data"], st["keep"])
    wb.remove("/config.json")
    n0 = len(resets)
    api.pkg_install(base_url + "/p3.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply()
    check("설정 기본값 — 기기에 없을 때만 들어감", rd("/config.json") == b'{"a": 1}')
    wb.confirm()
    api.put(lp, "/extra.py")
    api.put(lp, "/etc/new.json")
    m = api.status()["modified"]
    check("설정 수정은 수동 변경 아님", m and "/etc/new.json" not in m["paths"] and "/extra.py" in m["paths"], m)
    o = api.orphans()
    check("orphans — 남은 코드만(설정·데이터 제외)", [e["path"] for e in o["orphans"]] == ["/extra.py"], o)
    r = api.clean(["/extra.py", "/config.json", "/app.py", "/data/keep.json"])
    check("clean — 남은 파일만 지우고 나머지 거부", r["deleted"] == ["/extra.py"] and
          sorted(r["refused"]) == ["/app.py", "/config.json", "/data/keep.json"], r)
    c2 = cl.Client("127.0.0.1:%d" % port, "t0k", settings=["/config.json"])
    open(os.path.join(p3, "config.json"), "w").write('{"a": 2}')
    open(os.path.join(p3, "app.py"), "w").write("V = 31\n")
    res = c2.deploy(files3, wait=False, log=lambda *_: None)
    check("WSL 배포 — 기기에 있는 설정은 올리지 않음", res["changed"] == ["/app.py"], res)
    wait_resets(resets, len(resets) + 1); wb.apply()
    check("WSL 배포 — installed 에 설정 경로", wb.read_json("/webota/installed.json")["settings"] == ["/config.json"])
    # 롤백하면 지운 코드 파일도 돌아온다
    wb.confirm()
    wr("/stray2.py", "y")
    n0 = len(resets)
    api.pkg_install(base_url + "/p3.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply()
    check("설치 적용 — stray2 삭제", not wb.exists("/stray2.py"))
    wb.apply(); wb.apply(); wb.apply()
    check("롤백 — 지운 코드 파일 복원 · installed 원래대로", wb.exists("/stray2.py") and
          wb.read_json("/webota/installed.json")["settings"] == ["/config.json"])

    print("== 선언은 앱 패키지만 · 선언이 없으면 전부 정리 · 설치 계획")
    check("기기 설정에는 data_dirs 가 없다", "data_dirs" not in webota.DEFAULTS)
    wb.confirm()
    # 지금 판을 p3(데이터 /data·/logs, 설정 /config.json·/etc 선언)로 맞춘다
    wr("/etc/net.json", "{}")
    n0 = len(resets)
    api.pkg_install(base_url + "/p3.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply(); wb.confirm()
    wr("/left.py", "z"); wr("/data/legacy.json", "{}")
    legacy = os.path.join(pk, "legacy.wpk")
    cl.build_package({"/app.py": os.path.join(p3, "app.py")}, legacy, "testapp", "0.9", "v0.9")
    raw = open(legacy, "rb").read()
    n = int(raw[5:raw.index(b"\n", 5)])
    off = raw.index(b"\n", 5) + 1
    m = json.loads(raw[off:off + n]); m.pop("settings"); m.pop("data")
    mj = json.dumps(m).encode()
    open(legacy, "wb").write(b"WPK1\n" + str(len(mj)).encode() + b"\n" + mj + raw[off + n:])
    pl = api.pkg_plan(base_url + "/legacy.wpk")
    check("계획 — 선언 없음 표시", pl["declared"] is False and pl["keep_data"] == ["/webota"], pl["keep_data"])
    check("계획 — 지울 것에 코드·설정·데이터 전부", "/left.py" in pl["delete"] and "/data/legacy.json" in pl["delete"]
          and "/config.json" in pl["delete"] and "/www/i.html" in pl["delete"], pl["delete"])
    check("계획 — 지금 설정·데이터가 지워지는 것 표시", "/data/legacy.json" in pl["delete_kept_now"]
          and "/config.json" in pl["delete_kept_now"] and "/left.py" not in pl["delete_kept_now"])
    check("계획 — webota 자신·기기 설정은 안 지움", not any(x in pl["delete"] for x in
          ("/boot.py", "/main.py", "/webota.py", "/webota.json")) and not any(x.startswith("/webota/") for x in pl["delete"]))
    check("계획만 — 아무것도 안 바뀜", wb.exists("/left.py") and not wb.exists("/webota/pending.json"))
    n0 = len(resets)
    api.pkg_install(base_url + "/legacy.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply()
    check("선언 없는 패키지 — webota 말고 전부 정리", not wb.exists("/left.py") and not wb.exists("/data/legacy.json")
          and not wb.exists("/config.json") and not wb.exists("/etc/net.json") and wb.exists("/app.py")
          and wb.exists("/webota.json") and wb.exists("/boot.py"))
    wb.apply(); wb.apply(); wb.apply()
    check("롤백 — 정리된 데이터·설정도 복원", wb.exists("/data/legacy.json") and wb.exists("/config.json")
          and wb.exists("/left.py"))
    # 새 판이 데이터를 /store 로 옮겨 선언 — 이전 판의 /data 는 선언이 없으므로 정리 대상
    wr("/store/s.json", "{}")
    cl.build_package(files3, os.path.join(pk, "p4.wpk"), "testapp", "4.0.0", "v4.0.0",
                     settings=["/config.json"], data=["/store"])
    pl = api.pkg_plan(base_url + "/p4.wpk")
    check("계획 — 이전 판 데이터는 정리 대상(★표시)", "/data/legacy.json" in pl["delete_kept_now"]
          and "/store/s.json" not in pl["delete"], pl)
    n0 = len(resets)
    api.pkg_install(base_url + "/p4.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply()
    check("새 선언만 보존", wb.exists("/store/s.json") and wb.exists("/config.json") and not wb.exists("/data/legacy.json"))
    st = api.status()["keep"]
    check("보존 목록 = 새 판 선언만", st["data"] == ["/webota", "/store"] and st["settings"] == ["/webota.json", "/config.json"], st)
    wb.confirm()

    print("== 설정 · 데이터 강제 초기화")
    # 지금 판을 p3(설정 /config.json·/etc, 데이터 /data·/logs)로 되돌리고 운영 중 상태를 만든다
    open(os.path.join(p3, "config.json"), "w").write('{"a": 1}')
    cl.build_package(files3, os.path.join(pk, "p3.wpk"), "testapp", "3.0.0", "v3.0.0",
                     settings=["/config.json", "/etc"], data=["/data", "/logs"])
    n0 = len(resets)
    api.pkg_install(base_url + "/p3.wpk", wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply(); wb.confirm()
    wr("/config.json", '{"a": 9}'); wr("/etc/net.json", "{}"); wr("/data/m.dat", "1 2 3"); wr("/logs/l.txt", "log")
    pl = api.pkg_plan(base_url + "/p3.wpk", reset_settings=True)
    check("계획 — 설정 초기화: 기본값 다시 쓰기 · 기본값 없는 설정 삭제", "/config.json" in pl["write"]
          and pl["delete_reset"] == ["/etc/net.json"] and "/webota.json" not in pl["delete"], pl)
    check("계획 — 설정 초기화는 데이터 안 건드림", not any(x.startswith(("/data/", "/logs/")) for x in pl["delete"]))
    n0 = len(resets)
    api.pkg_install(base_url + "/p3.wpk", reset_settings=True, wait=False, log=lambda *_: None)
    wait_resets(resets, n0 + 1); wb.apply()
    check("설정 초기화 — 기본값으로 · 나머지 설정 삭제", rd("/config.json") == b'{"a": 1}' and not wb.exists("/etc/net.json"))
    check("설정 초기화 — webota 설정(토큰)·데이터 유지", wb.read_json("/webota.json").get("token") == "t0k"
          and wb.exists("/data/m.dat") and wb.exists("/logs/l.txt"))
    wb.confirm()
    pl = api.pkg_plan(base_url + "/p3.wpk", reset_data=True)
    check("계획 — 데이터 초기화: 선언된 데이터 전부(/webota 제외)", sorted(pl["delete_reset"]) ==
          sorted(e for e in _all_under(root, ("/data", "/logs"))) and not any(x.startswith("/webota") for x in pl["delete"]), pl["delete_reset"])
    n0 = len(resets)
    r = api.pkg_install(base_url + "/p3.wpk", reset_data=True, wait=False, log=lambda *_: None)
    check("같은 판 + 데이터 초기화 → 커밋(초기화만)", r == "committed" and wait_resets(resets, n0 + 1))
    wb.apply()
    check("데이터 초기화 — 데이터 비움 · 코드·설정 유지", not wb.exists("/data/m.dat") and not wb.exists("/logs/l.txt")
          and wb.exists("/app.py") and wb.exists("/config.json"))
    wb.apply(); wb.apply(); wb.apply()
    check("데이터 초기화 롤백 — 데이터 복원", wb.exists("/data/m.dat") and rd("/logs/l.txt") == b"log")

    print("== CLI")
    base = ["--host", "127.0.0.1:%d" % port, "--token", "t0k", "-y"]
    check("cli ls", cl.main(base + ["ls", "/data"]) == 0)
    wr("/data/q.bak", "1"); wr("/data/r.bak", "2"); wr("/data/keep.json", "{}")
    check("cli rm 글롭", cl.main(base + ["rm", "/data/*.bak"]) == 0
          and not wb.exists("/data/q.bak") and wb.exists("/data/keep.json"))
    with open(os.path.join(src, cl.PROJECT_FILE), "w") as f:
        json.dump({"map": project["map"]}, f)
    cwd = os.getcwd()
    os.chdir(src)
    try:
        with open("app.py", "w") as f:
            f.write("V = 4\n")
        check("cli deploy --dry-run", cl.main(base + ["deploy", "--dry-run"]) == 0
              and not wb.exists("/webota/pending.json"))
    finally:
        os.chdir(cwd)

    webota._stop = True
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(local, ignore_errors=True)
    print("\n%s — 실패 %d건" % ("ALL PASS" if not fail else "FAIL", fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
