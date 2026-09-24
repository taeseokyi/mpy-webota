# mpy-webota

MicroPython 앱을 위한 **웹 API OTA와 원격 파일 관리** 모듈입니다. USB 없이 WiFi로 다음을 합니다.
- 소스 배포. 바뀐 파일만 올리고, 부팅할 때 적용하고, 실패하면 자동으로 롤백합니다.
- 기기 파일 전체(소스·데이터)의 조회, 수정, 삭제. 경로 제한이 없습니다.

앱 코드를 하나도 import하지 않으므로 어느 MicroPython 프로젝트에나 그대로 붙일 수 있습니다. 클라이언트는 표준 라이브러리만 쓰는 Python 3 스크립트입니다.

## WiFi: webota가 전담합니다
WiFi는 원격 배포와 설치 화면이 기기에 닿는 길 그 자체라서 **webota가 맡고, 앱은 만지지 않습니다.** 앱이 죽어도 WiFi와 설정 화면은 살아 있습니다. 앱은 `webota_net.is_connected()`, `ip()`, `ap_active()`, `status()`로 **상태만 읽습니다.**

- **부팅**: `/webota.json`의 `wifi`로 접속을 시도합니다. 실패하거나 설정이 없으면 **설정용 AP**(`ap.ssid`/`ap.pass`, 기본 `webota-XXXX`/`webota1234`)를 올립니다.
- **처음 설정**: 휴대폰을 그 AP에 붙이고 `http://192.168.4.1:8266/`에 들어갑니다. **AP로 붙은 기기는 토큰 없이** WiFi 카드(상태, 스캔, 저장)를 쓸 수 있습니다. AP 비밀번호가 인증입니다. 저장하면 곧바로 접속하고, 새 주소를 보여 준 뒤 잠시 있다가 AP를 내립니다.
- **유지**: 끊기면 30초마다 재접속하고, 45초 넘게 안 붙으면 AP를 다시 올립니다.
- **이름**: `hostname`을 설정하면 `http://<이름>.local`(mDNS)로도 접속할 수 있습니다.
- 옛 `wifi_file`(앱이 쓰던 `{ssid, pass}` 파일)이 있으면 첫 부팅 때 `/webota.json`으로 한 번 옮겨 옵니다.
- WiFi 자격증명은 webota 설정이라서 설정 초기화나 데이터 초기화로 지워지지 않습니다.

## 두 가지 쓰는 법 (함께 씁니다)
| | 누가·언제 | 어떻게 |
|---|---|---|
| **패키지로 간단히 설치** | 운영자. 휴대폰으로도 됩니다 | 기기 화면 `http://<기기>:8266/`에서 판을 골라 '설치'. 기기가 GitHub Releases에서 직접 내려받습니다 |
| **WSL에서 세부 조정** | 개발자 | `webota.py deploy`(작업 트리의 바뀐 파일만), `put`, `get`, `rm`, `mv`, `reset` 등으로 파일 하나 단위까지 다룹니다 |

두 경로 모두 같은 배포 트랜잭션과 같은 안전장치(부팅 적용, 시험, 확인 또는 롤백, 가드)를 거칩니다. 이력도 한곳(`history`)에 쌓입니다.

**수동 변경 추적**: 파일 API(`put`, `rm`, `mv`)로 코드를 손대면 기기는 더 이상 '현재 판' 그대로가 아닙니다. 그 경로를 `/webota/modified.json`에 기록하고, 화면과 `status`에 `+ 수동 변경 N`으로 표시합니다. 데이터 디렉토리(`data_dirs`, 기본 `/data`)는 세지 않습니다. 패키지 설치나 배포가 그 파일을 다시 덮으면 목록에서 빠지므로, **패키지를 다시 설치하면 판 그대로 돌아갑니다.**

## 파일 구분: 코드 · 설정 · 데이터 (선언은 앱 패키지만)
패키지를 설치하면 기기가 **"모두 지우고 패키지를 푼 것"과 같은 상태**가 됩니다. 실제로는 차이만 처리합니다. 없는 파일은 지우고, 내용이 다른 파일만 씁니다. 그래서 같은 파일을 다시 쓰지 않고, 롤백하면 지운 파일도 되살아납니다. 남는 것은 **패키지가 선언한** 설정과 데이터, 그리고 webota 자신뿐입니다.

| 구분 | 어디 | 패키지 설치 | 정리 |
|---|---|---|---|
| 코드 | 선언되지 않은 나머지 전부(`*.py`, `/www/**`, `/lib/**` …) | 패키지 그대로 맞춤 | 대상 |
| 설정 | 패키지가 선언한 `settings` | **덮어쓰지 않음.** 패키지의 기본값은 기기에 없을 때만 넣음 | 제외 |
| 데이터 | 패키지가 선언한 `data` | 건드리지 않음 | 제외 |
| webota | `boot.py`, `main.py`, `webota*.py`, `webota_ui.html`, `/webota.json`, `/webota/` | 갱신만 하고 지우지 않음 | 제외 |

- **선언은 앱 패키지만 합니다.** 프로젝트 파일 `webota.project.json`에 `"settings": [...]`, `"data": [...]`(파일이나 디렉토리)를 적으면 매니페스트에 실립니다. 기기 설정에는 두지 않습니다.
- ★**선언이 없으면 모든 것이 정리 대상입니다.** 설치하는 패키지의 선언만이 기준이고, 이전 판이나 이전 앱의 선언은 이어지지 않습니다. 앱을 교체할 때도 새 앱이 선언하지 않은 이전 앱의 데이터는 지워집니다.
- **그래서 설치 전에 계획을 보여 줍니다**(`POST /pkg/plan`, 설치 화면의 확인 창, `pkg-install`). 쓸 파일, 지울 파일, 보존할 경로를 알려 주고, 지금 설정이나 데이터인 파일이 지워지면 ★로 표시합니다. 기기는 패키지의 머리(매니페스트)만 읽고 계산합니다.
- **설정이 데이터보다 먼저입니다.** 데이터 디렉토리 안의 파일(예: `/data/devices.json`)도 `settings`로 선언하면 설정으로 다룹니다. 설정 초기화의 대상이 되고, 데이터 초기화에서는 빠집니다.
- ★앱이 실행 중에 만드는 파일은 반드시 선언한 경로 안에 두십시오. `mip`으로 받은 `/lib`는 코드이므로 패키지에 넣습니다.
- 데이터 경로의 파일은 패키지에 넣을 수 없습니다(`pack`이 거부). 설정 파일을 넣으면 '기본값'이 됩니다. WSL `deploy`도 기기에 이미 있는 설정은 올리지 않습니다(바꾸려면 `put`).
- 새 판이 확인되면 롤백용 백업 `/webota/prev`를 지웁니다. 옛 판이 필요하면 목록에서 다시 설치합니다.

**강제 초기화**(설치 화면 '고급' · `pkg-install --reset-settings/--reset-data`):
- **설정 초기화**: 선언된 설정을 패키지 기본값으로 되돌립니다. 기본값이 없는 설정 파일은 지웁니다.
- **데이터 초기화**: 선언된 데이터를 모두 지웁니다.
- 두 초기화 모두 webota 자신(`/webota.json`의 토큰·WiFi, `/webota/`)과 webota가 WiFi에 붙을 때 읽는 파일(`wifi_file`)은 건드리지 않습니다. 이 파일을 지우면 원격 접속이 끊깁니다.
- 지금 판을 다시 설치하면서 켜면 초기화만 합니다.
- 새 판이 90초를 못 버티면 초기화한 것도 되살아납니다. 확인이 끝나면 되돌릴 수 없습니다.

**정리(남은 파일)**: 지금 판(`installed.json`)에 없는 코드 파일을 설치 화면의 '정리'나 `webota.py clean`으로 보여 주고, 확인하면 지웁니다. WSL로 손대서 생긴 파일이 주로 대상입니다. 선언된 설정과 데이터는 목록에 나오지 않고, 지우라고 해도 거부합니다.

## 구성

| 파일 | 위치(기기) | 역할 |
|---|---|---|
| `device/webota.py` | `/webota.py` | OTA 서버. 별도 스레드, 기본 포트 :8266 |
| `device/webota_boot.py` | `/webota_boot.py` | 부팅 때 배포 적용, 롤백, 확인 |
| `device/webota_pkg.py` | `/webota_pkg.py` | 배포 패키지 목록 조회와 설치(HTTPS 클라이언트 포함) |
| `device/webota_net.py` | `/webota_net.py` | WiFi 접속, 설정용 AP 폴백, 스캔과 저장(앱은 상태만 읽음) |
| `device/webota_ui.html` | `/webota_ui.html` | 설치 화면(`http://<기기>:8266/`) |
| `device/boot.py` | `/boot.py` | `webota_boot.apply()` 한 줄 |
| `device/main.py` | `/main.py` | 범용 런처: WiFi 접속, OTA 서버, 앱 실행 |
| `/webota.json` | `/webota.json` | 설정. 형식은 `device/webota.example.json` 참고 |
| `client/webota.py` | (PC) | CLI 겸 라이브러리(`Client`) |

**부팅 분기는 webota가 가집니다.** `boot.py`와 `main.py`는 webota의 파일이라 앱은 갖지 않고, 원본을 그대로 씁니다. 무엇을 띄울지는 `/webota.json`의 `"app"`(모듈)과 `"entry"`(함수)가 정하고, 앱을 교체하면 패키지 매니페스트의 값으로 바뀝니다. 앱은 **`app.py`의 `main()`**만 제공합니다.

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
USB로는 **webota 파일만** 올리면 됩니다. `/webota.json`은 webota가 만듭니다.
```bash
mpremote fs cp device/webota.py device/webota_boot.py device/webota_pkg.py device/webota_net.py \
               device/webota_ui.html device/boot.py device/main.py : + reset
```
1. 설정 파일이 없으면 첫 부팅 때 **기본값으로 만들고**, 토큰도 WiFi도 없으니 **설정용 AP**(`webota-XXXX` / `webota1234`)를 올립니다.
2. 휴대폰을 그 AP에 붙이고 `http://192.168.4.1:8266/`에 들어가 두 가지를 합니다.
   - **기기 등록**: 토큰을 정하거나 '새로 만들기'를 누릅니다. 설정용 AP에서만, 한 번만 할 수 있습니다.
   - **WiFi**: 공유기를 고르고 저장합니다.
3. 등록한 토큰을 PC의 `~/.config/webota/<기기주소>.token`에 넣습니다. PC가 AP에 붙어 있다면 `webota.py --host 192.168.4.1 claim`으로 PC의 토큰 파일을 그대로 등록해도 됩니다.
4. 이제 설치 화면의 패키지 목록에서 앱을 고르거나, WSL에서 `webota.py deploy`로 올립니다.

**미리 설정해서 굽기**(선택): 앱 프로젝트의 선언으로 설정 파일을 만들어 함께 올리면 AP와 등록을 건너뜁니다.
```bash
python3 client/webota.py device-config --out webota.json   # 프로젝트 app_id·device 절 + 토큰(없으면 만든다)
mpremote fs cp webota.json :
```
★**`webota.json`은 이 명령(또는 기기의 첫 부팅)으로만 만듭니다.** 앱 저장소에는 생성 코드를 두지 않고, 앱은 프로젝트 파일에 선언만 합니다.
```json
{"app_id": "myapp",
 "device": {"ap": {"ssid": "myapp-setup", "pass": "…"}, "hostname": "myapp",
            "sources": [{"github": "owner/repo"}]}}
```
토큰 바꾸기: `webota.py set-token --new-token-file 새파일`(지금 토큰으로 인증).

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
| `GET /hello` | 무인증 — `{webota, claimed, from_ap}` |
| `POST /claim {token}` | 토큰 없는 기기 등록 — 설정용 AP에서만, 한 번 |
| `POST /token {token}` | 토큰 바꾸기(지금 토큰 필요) |
| `GET /wifi` · `GET /wifi/scan` · `POST /wifi {ssid,pass}` | WiFi 상태·스캔·저장. 설정용 AP로 붙은 기기는 토큰 없이 |
| `GET /` | 설치 화면(이 페이지만 토큰 없이 열림. API 호출은 화면에서 입력한 토큰으로) |
| `GET /pkg/list[?fresh=1]` | 패키지 목록과 현재 판 |
| `POST /pkg/plan {url,reset_settings,reset_data}` | 설치 계획(쓸·지울·보존할 것) — 매니페스트만 읽는다 |
| `POST /pkg/install {url,src,force,switch_app,reset_settings,reset_data}` | 기기가 패키지를 내려받아 검증하고 배포(선언 외 전부 정리) |
| `GET /pkg/orphans` · `POST /pkg/clean {paths}` | 남은 코드 파일 목록 · 지우기(설정·데이터는 거부) |

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
