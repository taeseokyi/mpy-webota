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
    man = cl.build_package(files, os.path.join(pk, "t.wpk"), "testapp", "2.0.0", "v2.0.0+test")
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

    print("== CLI")
    base = ["--host", "127.0.0.1:%d" % port, "--token", "t0k", "-y"]
    check("cli ls", cl.main(base + ["ls", "/data"]) == 0)
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
