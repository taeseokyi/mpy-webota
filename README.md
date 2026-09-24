# mpy-webota

MicroPython 앱을 위한 **서명된 배포 패키지 설치 모듈**입니다. 기기 화면(`http://<기기>:8266/`)에서 판을 골라 설치하면, 기기가 GitHub Releases에서 직접 내려받아 **서명을 확인한 뒤에만** 설치합니다. 설치 뒤 재부팅, 90초 시험, 자동 롤백까지 스스로 처리합니다. WiFi 접속과 설정용 AP도 맡습니다.

앱 코드를 하나도 import하지 않으므로 어느 MicroPython 프로젝트에나 그대로 붙일 수 있습니다. 예제는 [mpy-webota-demo](https://github.com/taeseokyi/mpy-webota-demo)입니다.

## 보안 모델 (1.0.0)
**가장 중요한 것은 변형된 패키지를 설치하지 않는 것입니다.** 기기에 코드가 들어가는 길은 두 가지뿐입니다.

1. **USB**: 기기를 손에 쥔 사람이 올리는 것입니다.
2. **서명된 패키지**: 사용자만 가진 개인키로 서명한 것입니다.

| 장치 | 무엇을 막는가 |
|---|---|
| **패키지 서명** (RSA-2048, PKCS#1 v1.5, SHA-256) | 릴리스 체부파일이 바뀌어도(계정 탈취, 협업자, CI 실수) 서명이 맞지 않아 설치되지 않습니다. 매니페스트에 파일마다 SHA256이 있어 파일 한 바이트만 바뀌어도 거부합니다. |
| **공개키는 USB로만** (`/webota.json`의 `pkg_keys`) | 웹으로 바꿀 길이 없습니다. 공개키가 없는 기기는 어떤 패키지도 설치하지 않습니다. |
| **원격 명령 없음** | 파일 API(ls, get, put, rm), 원격 배포, 원격 리셋, 토큰 바꾸기, 출처 더하기를 모두 없앴습니다. 원격으로는 **서명된 패키지 설치**와 **수동 정리**만 됩니다. |
| **출처는 USB로 정한 것만** | 웹에서 아무 저장소나 더할 수 없습니다. |
| **TLS 인증서 검증** (`/webota_ca.pem`) | 가짜 GitHub 서버를 막습니다. CA 묶음이 없으면 연결하지 않습니다. |
| **`/webota.json`은 패키지가 못 건드림** | 공개키와 토큰을 바꾸려는 패키지는 서명이 맞아도 거부합니다. |

**지켜야 할 것은 PC의 서명 개인키입니다** (`~/.config/webota/signing-key.pem`).
- 기본으로 **암호가 걸린 키**를 만들고, 서명할 때마다 암호를 묻습니다. 암호는 webota가 직접 두 번 받아 확인하고, openssl에는 환경변수로만 넘깁니다. 무인 빌드는 `WEBOTA_SIGN_PASS` 환경변수로 할 수 있지만 권하지 않습니다.
- 서명은 검토한 커밋에서만 합니다.
- 키가 샜다고 의심되면 새 키를 만들고 공개키를 USB로 다시 심습니다.

**GitHub 토큰은 되도록 심지 마십시오.**
- MicroPython에는 격리가 없어서 기기의 모든 코드가 그 토큰을 읽을 수 있습니다. 서명된 패키지 안의 코드도 마찬가지입니다.
- 공개 저장소라면 토큰이 필요 없습니다. 토큰 없는 요청 한도(시간당 60회)로 충분합니다.
- 무결성은 토큰이 아니라 서명이 지킵니다.
- 비공개 저장소가 꼭 필요하다면 **읽기 전용, 저장소 하나, 짧은 만료**로 발급합니다. `device-config --github-token-file`로 USB에서만 심고, 기기는 `api.github.com`과 `github.com`에만 보냅니다.

## 원격으로 할 수 있는 것 (설치 화면 `:8266/`, 기기 토큰 필요)
- **패키지 설치**: 출처의 판 목록에서 고르면 먼저 **설치 계획**을 보여 줍니다. 계획에는 서명 키, 쓸 파일, 지울 파일, 보존할 경로가 나옵니다. 그다음 설치, 재부팅, 시험, 확인 또는 롤백을 거칩니다.
  - 다른 앱이면 '앱 교체'가 되고, 새 `/webota.json`이 같은 트랜잭션으로 들어갑니다. 공개키와 토큰은 유지됩니다.
  - 강제 초기화: **설정 초기화**(패키지 기본값으로), **데이터 초기화**(선언된 데이터 전부).
- **수동 정리**: 지금 판에 없는 남은 코드 파일을 보여 주고 지웁니다. 설정과 데이터는 거부합니다.
- 그 밖에 기기 등록(설정용 AP에서 한 번), WiFi 설정(설정용 AP에서는 토큰 없이), 토큰을 크롬 비밀번호 관리자에 저장하는 로그인 폼이 있습니다.

## 파일 구분: 코드 · 설정 · 데이터 (선언은 앱 패키지만)
설치는 **"모두 지우고 패키지를 푼 것"**과 같은 결과를 냅니다. 실제로는 차이만 처리합니다. 없는 파일은 지우고, 다른 파일만 씁니다. 지운 파일도 백업되므로 롤백하면 되살아납니다.

| 구분 | 어디 | 설치 | 정리 |
|---|---|---|---|
| 코드 | 선언되지 않은 나머지 전부 | 패키지 그대로 | 대상 |
| 설정 | 패키지가 선언한 `settings` (데이터 안이어도 설정이 먼저) | 덮어쓰지 않음. 기본값은 없을 때만 | 제외 |
| 데이터 | 패키지가 선언한 `data` | 건드리지 않음 | 제외 |
| webota | `boot.py`, `main.py`, `webota*.py`, `webota_ca.pem`, `webota_ui.html`, `/webota.json`, `/.webota/` | 갱신만 함 | 제외 |

- 선언은 프로젝트 파일 `webota.project.json`에 적습니다(`"settings"`, `"data"`). **선언이 없으면 webota를 뺀 모든 것이 정리 대상**입니다.
- ★앱이 실행 중에 만드는 파일은 선언한 경로 안에 두십시오. `/lib`는 코드이므로 패키지에 넣습니다.

## 구성
| 파일 | 역할 |
|---|---|
| `device/webota.py` | 설치 서버(:8266) · 설치 · 정리 · 등록 · 로그인 |
| `device/webota_pkg.py` | 목록 · 내려받기(검증된 TLS) · 패키지 풀기 |
| `device/webota_sig.py` | 서명 검증(순수 파이썬 RSA — 실기에서 47ms) |
| `device/webota_boot.py` | 부팅 때 적용 · 롤백 · 확인 |
| `device/webota_net.py` | WiFi · 설정용 AP · NTP(인증서 유효 기간 확인용) |
| `device/webota_ca.pem` | GitHub용 루트 CA(USERTrust ECC, Sectigo E46, ISRG X1/X2, DigiCert G2) |
| `device/webota_ui.html` | 설치 화면 |
| `device/boot.py`, `device/main.py` | 부팅 분기(webota의 파일이라 앱은 갖지 않습니다) |
| `client/webota.py` | CLI 겸 라이브러리 · 서명 · 패키지 만들기 · 기기 설정 |

앱은 `app.py`의 `main()`만 제공합니다. 무엇을 띄울지는 webota 설정(`app`, `entry`)이 정합니다.

## 설치 (USB 한 번)
```bash
python3 client/webota.py signing-key init            # 서명 키(암호) — 한 번
python3 client/webota.py device-config --out webota.json   # 기기 토큰 + ★공개키 (+ 선택: GitHub 토큰)
mpremote fs cp device/*.py device/webota_ca.pem device/webota_ui.html webota.json : + reset
```
설정 파일을 넣지 않으면 기기가 첫 부팅 때 기본값을 만들고 설정용 AP를 올립니다. 그때는 휴대폰으로 등록과 WiFi를 설정합니다. 다만 **공개키가 없으면 설치가 막히므로**, 공개키는 결국 USB로 넣어야 합니다.

## 다른 사람 기기에 설치하기 (내 패키지를 믿는 기기)
**공개키는 공개해도 되는 정보입니다.** 내 공개키를 심은 기기는 **내가 서명한 패키지만** 설치합니다. 다른 사람에게 개인키나 토큰을 줄 필요가 없습니다.

**패키지 작성자(나)가 한 번 할 일**
```bash
python3 client/webota.py signing-key publish     # 앱 저장소의 webota.project.json 에 device.pkg_keys 로 넣는다
git commit -am "공개키 공개" && git push           # 저장소에 공개키가 들어간다
```

**설치하는 사람이 할 일** (USB 한 번, 자기 PC에서)
```bash
git clone https://github.com/<나>/<앱>            # 앱 저장소(webota 기기 파일이 vendored 돼 있다)
cd <앱>
python3 tools/webota.py usb-install --port COM5   # webota + 기기 설정(내 공개키·출처·그 사람의 토큰)만 올린다
```
- **앱은 USB로 올리지 않습니다.** 기기가 부팅하면 설치 화면 `http://<기기>:8266/`에서 판을 골라 설치합니다. 첫 설치가 그 앱을 받아들입니다.
- WiFi는 설정용 AP(`webota-XXXX`)에 붙어 설정 화면에서 정합니다.
- **기기 토큰은 설치한 사람의 것입니다.** 그 사람의 PC에서 새로 만들어지므로(`~/.config/webota/…token`), 그 기기의 설치 화면은 그 사람만 씁니다. 토큰 칸에서 '저장'하면 크롬에 저장됩니다.
- 설치한 사람은 패키지를 **만들 수 없습니다.** 개인키가 없기 때문입니다. 자기 키도 함께 믿게 하려면 `device.pkg_keys`에 자기 공개키를 더해 `usb-install`하면 됩니다. 공개키는 여러 개를 둘 수 있습니다.
- ★그 기기는 **내 키를 믿게 됩니다.** 내가 서명한 것이면 무엇이든 설치될 수 있으니, 설치하는 사람이 나를 믿는다는 전제입니다.
- mpremote가 필요합니다(`pip install mpremote`). 윈도에서는 `py tools\webota.py usb-install --port COM5`로 실행합니다.

## 판 만들기와 릴리스 (서명)
```bash
python3 client/webota.py pack --app-id myapp --version 1.2.0 --out dist/   # 서명 암호를 묻는다
gh release create v1.2.0 dist/myapp-v1.2.0.wpk
```
패키지 형식은 `webota-pkg/2`입니다. `WPK2\n`, 매니페스트 길이, 매니페스트, 서명 길이, 서명, 파일들 순서로 이어 붙입니다. 서명 없는 옛 형식(`WPK1`)은 설치하지 않습니다.

## CLI
```bash
webota.py status | history | sources | pkg-list [--fresh]
webota.py pkg-install <URL> [--switch-app] [--reset-settings] [--reset-data]   # 계획을 먼저 보여 준다
webota.py clean [-y]
webota.py signing-key init|show|publish · pack · device-config · usb-install --port COMx · token · claim
```

## HTTP API
| 요청 | 설명 |
|---|---|
| `GET /` · `GET /hello` | 설치 화면 · `{webota, claimed, from_ap}`(무인증) |
| `POST /claim` · `POST /login` | 기기 등록(토큰이 없을 때, 설정용 AP에서만) · 로그인 폼(크롬 저장) |
| `GET/POST /wifi` · `GET /wifi/scan` | WiFi(설정용 AP에서는 토큰 없이) |
| `GET /status` · `GET /history` | 상태(비밀은 있다/없다만) · 배포 이력 |
| `GET /pkg/sources` · `GET /pkg/list` | 출처(보기만) · 패키지 목록 |
| `POST /pkg/plan` · `POST /pkg/install` | 계획(서명 확인) · 설치 |
| `GET /pkg/orphans` · `POST /pkg/clean` | 수동 정리 |

## 시험
```bash
python3 tests/test_webota.py     # CPython 에서 실제 서버 · 서명 · 변조 거부 · 롤백 · WiFi 까지(76 항목)
```
MicroPython 실기에서만 드러나는 것들도 있었습니다.
- 디렉토리 이름이 import와 충돌했습니다(상태 디렉토리를 `/.webota`로 옮김).
- 읽지 않은 본문이 있으면 lwIP가 RST를 보냈습니다.
- 크롬의 빈 예비 연결이 서버를 붙잡았습니다.

이런 문제 때문에 판을 낼 때마다 실기에서 설치를 확인합니다.
