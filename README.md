# 병원 시술 안내 릴스 자동 제작

**사진만 `00_inbox` 에 올리면** 기획 → 대본(후킹 문구 포함) → 시나리오 → 음향 → 9:16 릴스 영상까지 GitHub 에서 자동으로 만들어집니다.
사람은 대본을 검수해 `승인` 으로 바꾸기만 하면 됩니다. 도구는 모두 무료입니다 (GitHub Actions, Gemini API 무료 등급, Edge TTS, FFmpeg, OFL 폰트).

## 폴더와 흐름

```
00_inbox/                사진 넣는 곳 ─────────────────────────────┐ 누구나: 사진 올리기
01_plan/<주차>/          기획   source/(원본 사진) analysis.json brief.yml manifest.yml
02_script/<주차>/        대본   script.md  ← 검수 후 '상태: 승인'      ┐ 사람: 검수·승인
03_scenario/<주차>/      시나리오 scenario.yml (장면별 연출·전환)      │
04_audio/<주차>/         음향   voice_XX.mp3 timeline.json mix.m4a     │ 자동
05_video/<주차>/         영상   reel.mp4(승인본) cover.jpg qa.txt qa_sheet.jpg
06_publish/<주차>/       발행   post.yml (캡션·해시태그·발행 결과)     ┘
config/brand.yml         병원 정보·색·음성      config/style.yml  폰트 테마·효과·자막·음향
assets/fonts·bgm·sfx·brand   폰트(상업용 무료 OFL)·배경음·효과음·로고
pipeline/                프로그램 (run.py 가 전체 실행)
```

- 단계들은 **주차 폴더 이름**(ISO 주차, 예: `2026-W49`)으로 연결됩니다.
- `01_plan/<주차>/manifest.yml` 에 단계별 입력 지문이 기록되어, **앞 단계를 고치면 그 뒤 단계만 다시** 만들어집니다.
- 사람이 고친 파일은 덮어쓰지 않습니다: 대본(script.md)은 한 번 만들어지면 그대로, 시나리오는 `자동생성: false` 로 바꾸면 그대로, 사진 분석은 `"수동": true` 면 그대로.

## 매주 할 일 (누구나)

1. GitHub 저장소 → `00_inbox` 폴더 → **Add file → Upload files** → 포스터·시술 사진(jpg/png/webp, 여러 장 가능)을 **한 번에** 올리고 Commit.
   - 특정 주차로 넣고 싶으면 `00_inbox/2026-W50/` 처럼 주차 이름 폴더에 올립니다. 아니면 비어 있는 다음 주차가 자동 배정됩니다.
2. 5~10분 뒤 **Actions → 1. 릴스 자동 제작 → 실행 기록 → Artifacts** 에서 `preview.mp4`(검수용 미리보기)를 받아 확인합니다.
3. `02_script/<주차>/script.md` 를 열어 검수합니다.
   - `## 후킹` : 첫 2초 큰 문구 (후보 3개 중 하나 선택 가능). `**단어**` 는 강조 박스.
   - `## 나레이션` : 한 줄 = 장면 하나. `**단어**` 는 강조색, `|` 는 화면 줄바꿈.
   - `## 캡션`, `## 해시태그` : 인스타그램 게시글.
   - 맨 아래 검수 체크리스트(의료광고 주의) 확인 후 맨 위 `상태: 승인`, `검수자: 이름` 으로 저장.
4. 승인하면 확정 영상 `05_video/<주차>/reel.mp4` 가 만들어지고 `06_publish/<주차>/post.yml` 발행 대기열에 올라갑니다.

## 영상 연출 (config/style.yml)

- 장면 종류: **후킹**(얼굴·제목으로 펀치 줌 + 단어별 팝업 + 강조 박스), **포스터**(줌·이동·초점 + 빛 반사), **패널**(포스터의 시술 카드를 잘라 띄우고 명조 제목이 드러남), **문구**(떠오름·마스크·팝·타자 4가지 키네틱 글자), **문의**(연락처 맥박 + 버튼·화살표), **엔딩**(병원명·연락처·주소·고지문·심의번호).
- 자막: 말하는 단어가 강조색으로 차오르는 **단어 강조** 방식 (Edge TTS 단어 시점 사용) 또는 `팝업`.
- 공통 효과: 빛번짐, 떠오르는 빛 입자, 비네팅, 필름 질감, 장면 전환 12종(스무스·원형·슬라이드·화이트 등).
- 폰트 테마 3종(럭셔리·임팩트·부드러움) — 역할별로 다른 폰트: 후킹 Black Han Sans, 제목 나눔명조/도현/고운바탕, 본문·자막 Pretendard, 부드러움 테마 주아·고운돋움. 모두 SIL OFL(상업적 이용 가능, `assets/fonts/licenses`).
- 음향: 직접 합성한 배경음(저작권 걱정 없음, 음성이 나올 때 자동으로 작아짐) + 전환 '휙'·후킹 '팝'·반짝임 효과음, 음량 -14 LUFS. 원하는 배경음 파일을 `assets/bgm/` 에 넣고 `배경음: 파일명` 으로 바꿀 수 있습니다.

## 처음 한 번만 할 일 (관리자)

1. Settings → Secrets and variables → Actions
   - Secret `GEMINI_API_KEY` (사진 분석·대본 작성. 없으면 기본 틀로 진행)
   - 무료 등급은 입력이 Google 제품 개선에 쓰일 수 있습니다. **환자 개인정보가 담긴 사진은 올리지 마세요.**
2. Settings → Actions → General → Workflow permissions → **Read and write permissions**
3. `config/brand.yml` 병원명·연락처·주소·색·고지문 확인

## 발행 (준비 중)

- `2. 인스타그램 발행` 워크플로: `06_publish/<주차>/post.yml` 이 `대기` 이고 예약일이 지난 영상을 발행합니다. 기본은 점검만(`go` 끔).
- 필요한 것: GitHub Pages 켜기(영상 공개 주소), Secret `IG_ACCESS_TOKEN`, (비즈니스 포트폴리오·페이스북 페이지 방식이면) Variable `IG_USER_ID`.

## 직접 실행 (선택)

```
pip install pyyaml edge-tts pillow numpy    # + ffmpeg
python pipeline/run.py                      # 00_inbox 처리 + 바뀐 주차 다시 제작
python pipeline/run.py --week 2026-W49      # 한 주차만
python pipeline/run.py --publish            # 발행 점검 (--go 를 붙이면 실제 발행)
```

## 의료광고 주의

효과 보장·최상급·비교·체험담·전후 비교·가격 할인 유인 표현은 쓰지 않습니다. 포스터 자체에 가격·이벤트 문구가 있으면 영상에도 보이므로 의료광고 심의 여부를 확인하세요. 최종 게시 전 반드시 사람이 검수합니다.
