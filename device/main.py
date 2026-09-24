# main.py — webota 범용 런처. 앱 코드는 여기 두지 않는다(/webota.json 의 "app" 모듈).
#   WiFi 최소 접속 → OTA 서버(:8266) → 앱 실행. 앱이 죽어도 OTA 는 살아 있다.
import webota
c = webota.load_config()
webota.wifi_up(c)
webota.start(c)
webota.run_app(c)
