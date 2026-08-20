# 기여 방법

버그 제보에는 사용한 게임 해상도, HUD 배율, 문제가 발생한 영상 또는 화면 캡처, 기대 결과를 함께 적어 주세요.

코드 변경은 다음 순서로 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q score_overlay
```

새 HUD 인식 규칙은 임의 좌표를 추가하기 전에 실제 1920×1080 관전자 화면에서 확인해야 합니다. 실시간 루프에는 신경망 OCR을 추가하지 않습니다. 팀명 OCR처럼 사용자가 직접 실행하는 일회성 작업만 별도로 허용합니다.
