# mpy-webota

MicroPython 앱을 위한 **웹 API OTA와 원격 파일 관리** 모듈입니다. USB 없이 WiFi로 다음을 합니다.
- 소스 배포. 바뀐 파일만 올리고, 부팅할 때 적용하고, 실패하면 자동으로 롤백합니다.
- 기기 파일 전체(소스·데이터)의 조회, 수정, 삭제. 경로 제한이 없습니다.

앱 코드를 하나도 import하지 않으므로 어느 MicroPython 프로젝트에나 그대로 붙일 수 있습니다. 클라이언트는 표준 라이브러리만 쓰는 Python 3 스크립트입니다.

## 구성

| 파일 | 위치(기기) | 역할 |
|---|---|---|
| `device/webota.py` | `/webota.py` | OTA 서버. 별도 스레드, 기본 포트 :8266 |
| `device/webota_boot.py` | `/webota_boot.py` | 부팅 때 배포 적용, 롤백, 확인 |
| `device/webota_pkg.py` | `/webota_pkg.py` | 배포 패키지 목록 조회와 설치(HTTPS 클라이언트 포함) |
| `device/webota_ui.html` | `/webota_ui.html` | 설치 화면(`http://<기기>:8266/`) |
| `device/boot.py` | `/boot.py` | `webota_boot.apply()` 한 줄 |
| `device/main.py` | `/main.py` | 범용 런처: WiFi 접속, OTA 서버, 앱 실행 |
| `/webota.json` | `/webota.json` | 설정. 형식은 `device/webota.example.json` 참고 |
| `client/webota.py` | (PC) | CLI 겸 라이브러리(`Client`) |

앱 코드는 `main.py`가 아니라 **`app.py`**(설정 `"app"`)에 둡니다. `main()` 함수가 진입점입니다(설정 `"entry"`).

## 동작
1. **부팅**: `boot.py`가 적용과 롤백을 판단합니다.
   - 커밋된 배포(`pending`)가 있으면 원래 파일을 `/webota/prev`에 백업하고, 새 파일로 교체하고, 시험(`trial`)을 시작합니다.
   - 시험 중인데 확인 없이 3번 부팅하면 롤백합니다. 행이나 WDT로 리셋이 반복되는 경우입니다.
2. **런처**: `main.py`가 순서대로 실행합니다.
   - WiFi 최소 접속(`wifi_file`). 이미 붙어 있으면 건너뜁니다.
   - OTA 서버 스레드.
   - `import app; app.main()`.
3. **앱 예외**: 추적 내용을 `/webota/crash.txt`에 남깁니다.
   - 시험 중이면 롤백하고 리셋합니다.
   - 평시라면 **구조 모드**로 들어갑니다. 리셋하지 않고 OTA만 살려 두어 원격으로 고칠 수 있습니다.
4. **확인**: 앱이 `confirm_s`(기본 90초) 동안 살아 있으면 시험을 끝내고 `last.json`에 `ok`를 남깁니다.
5. **Ctrl-C**(USB REPL)는 그대로 REPL로 넘어갑니다. mpremote를 계속 쓸 수 있습니다.

## 설치 (USB 한 번)
```bash
python3 client/webota.py --host 192.168.0.50 token      # ~/.config/webota/192.168.0.50.token
# /webota.json 에 그 토큰을 넣는다 (webota.example.json 참고)
mpremote fs cp device/webota.py device/webota_boot.py device/webota_pkg.py device/webota_ui.html \
               device/boot.py device/main.py webota.json :
mpremote fs cp app.py :          # 앱
mpremote reset
python3 client/webota.py --host 192.168.0.50 status
```

## 배포 패키지 (.wpk): 기기 화면에서 골라 바로 설치
앱 저장소가 판마다 패키지를 만들어 **GitHub Releases**에 올려 두면, webota를 설치한 기기의 화면(`http://<기기>:8266/`)에서 목록을 보고 골라 설치합니다. 기기가 직접 내려받아 해시를 검증하고, 바뀐 파일만 배포 트랜잭션으로 넘깁니다. 그 뒤 재부팅, 시험, 확인 또는 롤백은 다른 배포와 같습니다.

- **형식** `webota-pkg/1`: `WPK1\n`, 매니페스트 길이, 매니페스트 JSON, 파일 내용을 차례로 이어 붙인 것입니다. 기기가 스트리밍으로 풀 수 있게 압축은 하지 않습니다.
  매니페스트: `{format, app_id, name, version, label, built_at, webota, files:[{path,size,sha}], delete}`
- **만들기**: `webota.py pack --app-id myapp --version 1.2.0 --out dist/`(프로젝트 `map` 기준). 빌드 단계가 있으면 라이브러리 `build_package(files, out, app_id, version, label)`를 씁니다.
- **올리기**: `gh release create v1.2.0 dist/myapp-v1.2.0.wpk`. 판마다 릴리스가 쌓입니다.
- **기기 설정** `/webota.json`:
  ```json
  {"app_id": "myapp", "packages": {"github": "owner/repo"}}
  ```
  `"asset": "*.wpk"`(기본값)와 `"max": 15`를 지정할 수 있습니다. 자체 호스팅은 `{"index": "http://.../index.json"}`(`[{tag,name,url,size,published}]`)으로 합니다.
- **안전장치**:
  - `app_id`가 다른 패키지는 거부합니다.
  - 해시가 맞지 않으면 아무것도 바꾸지 않습니다.
  - 앱 가드(측정 중 등)를 내려받기 전에 먼저 확인합니다.
  - 화면의 '고급'에서 가드 검사를 무시할 수 있습니다.
- **CLI**: `webota.py pkg-list`, `webota.py pkg-install <URL>`.
- ★공개 저장소 전제입니다(토큰 없이 내려받음). 기기에 CA 묶음이 없어 **TLS 인증서를 검증하지 않습니다.** 파일 무결성은 매니페스트 해시로 확인하지만, 매니페스트도 같은 출처에서 오므로 경로 위조까지 막지는 못합니다.
- ★옛 판 패키지를 설치하면 그 판에 들어 있는 webota로 내려갈 수 있습니다. 패키지 기능이 없는 판(webota < 0.3.0)으로 내려가면 이 화면도 사라집니다.

## 클라이언트
```bash
webota.py status
webota.py ls /data -r [--sha]
webota.py get /data/log.txt [로컬]       # 디렉토리면 재귀
webota.py put 로컬 /원격
webota.py rm '/data/*.bak' [-r]          # 글롭은 기기 쪽에서 푼다 — 따옴표
webota.py mkdir /d ; webota.py mv /a /b
webota.py reset [--force]
webota.py deploy [--delete] [--dry-run] [--force] [--no-reset] [--label L]
webota.py history [-n 20]                # 배포 결과 이력
```
- `deploy`는 프로젝트 루트(현재 디렉토리부터 위로 찾음)의 `webota.project.json`을 읽습니다.
  ```json
  {"host": "192.168.0.50",
   "map": [{"src": "src/*.py", "dst": "/"}, {"src": "www/", "dst": "/www/"}],
   "exclude": ["*.pyc"]}
  ```
  해시를 비교해 바뀐 파일만 올리고, 커밋하고, 리셋한 뒤 새 판이 `ok`가 되거나 롤백될 때까지 기다립니다. `--delete`를 주면 map 범위 안에서 원격에만 있는 파일을 지웁니다.
- 토큰을 찾는 순서: `--token`, `$WEBOTA_TOKEN`, `--token-file`, 프로젝트의 `token_file`, `~/.config/webota/<host>.token`.
- `boot.py`, `main.py`, `webota*.py`, `/webota.json`은 잘못 바꾸면 USB로만 복구되므로, 바꿀 때 한 번 더 묻습니다(`-y`로 생략).

### 라이브러리로 쓰기
빌드 단계(gzip, 버전 스탬프 등)가 있는 프로젝트에서 씁니다.
```python
from webota import Client
c = Client("192.168.0.50", open(token_file).read().strip())
c.deploy({"/app.py": "build/app.py", "/www/i.html.gz": "build/i.html.gz"})
```

## HTTP API (모든 요청에 `X-Token` 필요)
| 요청 | 설명 |
|---|---|
| `GET /status` | 가동 시간, 메모리, FS, 앱 상태(`running`/`rescue`...), 오류, 시험·마지막 결과 |
| `GET /history[?n=10]` | 배포 결과 이력(`/webota/history.jsonl`, 최근 50건) |
| `GET /fs/<경로>[?r=1&sha=1]` | 파일 내용 또는 디렉토리 목록 |
| `PUT /fs/<경로>[?sha=]` | 쓰기(임시 파일, 해시 확인, 교체). 상위 디렉토리 자동 생성 |
| `DELETE /fs/<경로>[?r=1]` | 삭제 |
| `POST /fs/<경로>?op=mkdir` / `?op=mv&to=` | 디렉토리 생성 / 이동 |
| `POST /sha {"paths":[...]}` | 경로별 SHA256 |
| `POST /deploy/begin` | 배포 트랜잭션 시작. id를 돌려줌 |
| `PUT /deploy/<id>/<경로>?sha=` | 스테이징 |
| `POST /deploy/<id>/commit {files,delete,reset,label}` | 확정. 다음 부팅에 적용. `label`은 이력에 남음 |
| `DELETE /deploy` | 트랜잭션 폐기 |
| `POST /reset` | 리셋 |
| `GET /` | 설치 화면(이 페이지만 토큰 없이 열림. API 호출은 화면에서 입력한 토큰으로) |
| `GET /pkg/list[?fresh=1]` | 패키지 목록과 현재 판 |
| `POST /pkg/install {url,force}` | 기기가 패키지를 내려받아 검증하고 배포 |

**앱 가드**: 앱이 `webota.set_guard(fn)`으로 `fn() -> (ok, msg)`를 등록하면, `commit`과 `reset` 전에 이 함수를 부릅니다. 거부하면 423을 돌려줍니다(예: 측정 중 배포 금지). `?force=1`(CLI `--force`)로 무시할 수 있습니다. 파일 API는 가드를 거치지 않습니다.

## 보안
- 토큰이 설정되지 않으면 **모든 요청을 거부**합니다.
- 토큰은 평문 HTTP로 오갑니다. LAN 전용을 전제로 합니다.
- 경로 제한이 없으므로 토큰을 가진 쪽은 기기 전체를 다룰 수 있습니다.

## 버전과 배포 이력
- webota 자체 판은 `device/webota.py`와 `client/webota.py`의 `VERSION`에 있고, `status`의 `"webota"`로 보입니다. 릴리스마다 `git tag v<판>`을 답니다.
- 배포마다 **라벨**(예: 앱 판과 커밋 `v1.1.0+abc1234`)을 붙이면 `trial`, `last`, `history`에 남습니다. CLI의 기본 라벨은 프로젝트의 `git describe --tags --always --dirty`입니다.
- 롤백은 바로 이전 판 **1단계**입니다(`/webota/prev`). 더 이전 판은 저장소의 태그에서 다시 배포합니다.
- 앱 프로젝트에 파일을 복사(vendor)해 쓸 때는 머리에 출처 커밋을 적고, 원본에서 고친 뒤 다시 복사합니다.

## 시험
```bash
python3 tests/test_webota.py     # CPython 에서 실제 서버를 띄워 종단 시험
```
