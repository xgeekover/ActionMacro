# ActionMacro 🎯

> **Cross-Platform Precision Desktop Automation & Macro Tool**  
> 모바일 에뮬레이터(BlueStacks 등) 게임 자동화 및 데스크톱 일반 반복 업무(RPA)를 위한 크로스 플랫폼(macOS & Windows) 정밀 매크로 도구입니다.

---

## 📖 목차 (Table of Contents)

1. [핵심 기능 (Key Features)](#-핵심-기능-key-features)
2. [아키텍처 (Architecture)](#-아키텍처-architecture)
3. [설치 및 요구사항 (Installation & Requirements)](#-설치-및-요구사항-installation--requirements)
4. [실행 방법 (Quick Start)](#-실행-방법-quick-start)
5. [상세 사용자 메뉴얼 가이드 (User Manual)](#-상세-사용자-메뉴얼-가이드-user-manual)
   - [Step 1. 액션 블록 구성](#step-1-액션-블록-구성-visual-builder)
   - [Step 2. 정밀 화면 좌표 측정 (돋보기 피커)](#step-2-정밀-화면-좌표-측정-coordinate-picker)
   - [Step 3. 듀얼 대기 시스템 (고정 대기 & 이미지 인식)](#step-3-듀얼-대기-시스템-dual-wait-system)
   - [Step 4. 시나리오 저장 및 불러오기](#step-4-시나리오-저장-및-불러오기-persistence)
   - [Step 5. 매크로 실행 및 긴급 정지](#step-5-매크로-실행-및-긴급-정지-execution--safety)
6. [macOS 필수 보안 권한 설정](#-macos-필수-보안-권한-설정)
7. [테스트 및 품질 검증 (Testing & QA)](#-테스트-및-품질-검증-testing--qa)
8. [라이선스 (License)](#-라이선스-license)

---

## ✨ 핵심 기능 (Key Features)

- **🎨 비주얼 시나리오 빌더 (Visual Scenario Builder)**
  - PySide6 (Qt 6) 기반의 현대적인 다크 테마 데스크톱 GUI
  - 4가지 액션 블록(**마우스 클릭**, **키보드 입력**, **고정 대기**, **이미지 인식 대기**)을 카드로 드래그 앤 드롭하여 시각적으로 조립/재정렬/삭제
- **📍 8배율 돋보기 전광판 좌표 피커 (Precision Coordinate Picker)**
  - 앱 내 좌표 선택 시 화면 전체에 반투명 HUD 오버레이 및 십자선 표시
  - 마우스 커서 주변을 8배율로 실시간 확대하는 돋보기 뷰와 RGB 색상 코드 표시
  - 클릭 한 번으로 목표 좌표를 자동 캡처하여 파라미터에 즉시 입력
  - macOS Retina 및 Windows HiDPI 배율 자동 보정으로 **$\pm 1$px 이내 정밀 타격**
- **⏳ 듀얼 대기(Waiting) 시스템**
  - **고정 시간 대기**: 20ms 단위 슬라이스 루프로 즉각적인 중단(Cancel) 반응성 보장
  - **이미지 인식 대기**: OpenCV 템플릿 매칭 기반. 화면에서 특정 아이콘/버튼이 **나타날 때(appear)** 또는 **사라질 때(disappear)**까지 스마트 대기 (ROI 탐색 영역 지정, 유사도 임계값, 타임아웃 지원)
- **🛡️ 실행 제어 및 안전 장치 (Safety & Execution Control)**
  - N회 지정 반복 또는 무한 루프 실행 제어
  - **글로벌 긴급 정지 핫키 (`<Ctrl>+<Shift>+<S>` 또는 `<Esc>`)**: 매크로 실행 중 언제든 즉각 중단 — 진행 중인 다중 클릭·길게 누르기도 50ms 이내에 끊고 OS 입력 안전 해제(Safety Flush)
  - 실행 중 현재 진행 루프 및 실행 중인 액션 카드 실시간 하이라이트
  - 5,000줄 메모리 버퍼 상한이 적용된 실시간 컬러 로그 콘솔
- **💾 크로스 플랫폼 시나리오 호환 (Cross-Platform JSON)**
  - 시나리오를 단일 JSON 파일로 저장 및 복원
  - macOS(`Command`, `Option`)와 Windows(`Ctrl`, `Alt`) 간 키 매핑 및 파일 경로 자동 정규화
- **⚡ 무인 헤드리스 CLI 모드 (Headless Execution)**
  - GUI를 띄우지 않고 백그라운드 터미널 환경에서 매크로를 직접 실행 가능

---

## 🏗️ 아키텍처 (Architecture)

```
┌────────────────────────────────────────────────────────────────────────┐
│                        GUI 레이어 (PySide6)                            │
│  - MainWindow, ScenarioBuilder (카드 드래그앤드롭), PropertyInspector  │
│  - CoordinatePickerOverlay (8x 돋보기 십자선 HUD + 좌표 캡처)         │
│  - LogConsole & ExecutionDashboard (스레드 안전 Qt Signal 연동)        │
├────────────────────────────────────────────────────────────────────────┤
│                      실행 컨트롤러 (Runner)                            │
│  - ExecutionEngine: N회 / 무한 루프 반복 실행 제어 엔진                 │
│  - StateMachine: 6-상태 유한 상태 머신 (IDLE, RUNNING, PAUSED 등)      │
│  - HotkeyListener: 글로벌 긴급 정지 핫키 (<Ctrl>+<Shift>+<S> / <Esc>)  │
├────────────────────────────────────────────────────────────────────────┤
│                     자동화 엔진 (Engine)                               │
│  - InputAdapter: pynput 기반 정밀 마우스/키보드 제어 (오차 ≤ 0.5px)   │
│  - CoordinateTransformer: Logical Points <-> Physical Pixels 변환      │
│  - WaitSystem: 슬라이스 고정 시간 대기 & OpenCV 템플릿 매칭 대기       │
│  - ScreenAdapter: MSS 기반 초고속 디스플레이 화면 캡처 어댑터          │
├────────────────────────────────────────────────────────────────────────┤
│                       핵심 코어 (Core)                                 │
│  - ActionBlock & ScenarioModel 데이터 모델                             │
│  - ScenarioPersistence: 원자적(Atomic) JSON 저장/불러오기              │
│  - KeyMapping: 크로스 플랫폼 추상 키 코드 매핑                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 💻 설치 및 요구사항 (Installation & Requirements)

### 요구사항
- Python 3.10 이상 (Python 3.12, 3.13 지원)
- macOS (Apple Silicon / Intel) 또는 Windows 10/11
- [uv](https://docs.astral.sh/uv/) (권장) 또는 standard `pip`

### 1. 저장소 복제 (Clone)
```bash
git clone https://github.com/xgeekover/ActionMacro.git
cd ActionMacro
```

### 2. 가상환경 생성 및 의존성 설치
**방법 A. `uv` 사용 시 (초고속 권장):**
```bash
# 가상환경 생성 및 전체 패키지 동기화
uv venv
source .venv/bin/activate  # (Windows의 경우: .venv\Scripts\activate)
uv sync --all-extras
```

**방법 B. 일반 `pip` 사용 시:**
```bash
python3 -m venv .venv
source .venv/bin/activate  # (Windows의 경우: .venv\Scripts\activate)
pip install -e ".[dev]"
```

---

## 🚀 실행 방법 (Quick Start)

### 1. GUI 대시보드 실행
```bash
# 가상환경 활성화 상태에서:
python -m action_macro

# 또는 등록된 실행 명령 사용 (소문자 별칭 `actionmacro` 도 같은 동작):
ActionMacro
```

### 2. 저장된 시나리오 파일 직접 열기
```bash
ActionMacro my_scenario.json
```

### 3. 백그라운드 무인(Headless) 실행
GUI 창 없이 백그라운드에서 특정 횟수만큼 시나리오를 돌리고자 할 때 사용합니다:
```bash
# 10회 반복 실행
ActionMacro --headless my_scenario.json --loops 10

# 무한 반복 실행 (긴급 정지는 <Ctrl>+<Shift>+<S> 또는 Ctrl+C)
ActionMacro --headless my_scenario.json --loops 0
```

---

## 📘 상세 사용자 메뉴얼 가이드 (User Manual)

### Step 1. 액션 블록 구성 (Visual Builder)
1. 프로그램 상단의 버튼을 눌러 원하는 액션을 시나리오에 추가합니다:
   - **`+ Add Mouse Click`**: 지정 좌표를 클릭합니다 (좌클릭, 우클릭, 더블클릭, 클릭 유지 시간 등 지원).
   - **`+ Add Key Input`**: 특정 키 입력 또는 단축키 조합(예: `ctrl+c`, `cmd_or_ctrl+shift+t`, `enter`, `f5`)을 실행합니다.
     - `+`로 이은 키는 **하나의 단축키로 동시에** 눌립니다: 앞의 키들을 순서대로 누른 채 마지막 키를 `Press Duration`만큼 누르고, 역순으로 뗍니다. (예: `ctrl+c` → Ctrl 누름 → C 누름 → C 뗌 → Ctrl 뗌)
     - `Action Mode`: `press`는 한 번 누르고 떼기, `down`은 모든 키를 순서대로 누른 채 유지, `up`은 그 키들을 역순으로 뗍니다. (예: `down shift` → 마우스 클릭 → `up shift`로 Shift+클릭)
   - **`+ Add Fixed Wait`**: 지정한 시간(ms) 동안 대기합니다.
   - **`+ Add Image Wait`**: 특정 이미지(버튼, 완료 화면 등)가 화면에 감지될 때까지 대기합니다.
2. 카드 우측의 **`▲`**, **`▼`** 버튼 또는 **마우스 드래그 앤 드롭**으로 실행 순서를 자유롭게 바꿀 수 있습니다.

---

### Step 2. 정밀 화면 좌표 측정 (Coordinate Picker)
마우스 클릭 위치를 수치로 직접 입력할 필요 없이 화면에서 바로 찍을 수 있습니다:
1. 추가한 **Mouse Click 카드**를 클릭하여 선택합니다.
2. 우측 속성 패널의 **`📍 Pick Screen Coordinate`** 버튼(또는 상단 툴바의 **`📍`** 아이콘 / 단축키 `Ctrl+P`)을 누릅니다.
3. 화면 전체에 반투명 오버레이와 함께 **8배율 돋보기 십자선**이 마우스 커서를 따라다닙니다.
4. 블루스택이나 대상 프로그램의 버튼 위치에 십자선을 올리고 **마우스 왼쪽 클릭**을 누르면 해당 좌표가 자동으로 액션에 쏙 들어갑니다.
   - 주 모니터의 왼쪽이나 위쪽에 둔 보조 모니터에서는 좌표가 음수(예: `X: -1200`)로 들어옵니다. 음수도 그대로 저장되어 그 위치를 클릭합니다.
5. 측정을 취소하려면 키보드의 **`ESC`**를 누르세요.

---

### Step 3. 듀얼 대기 시스템 (Dual Wait System)

#### A. 고정 시간 대기 (Fixed Wait)
- 게임 로딩이나 애니메이션 사이의 단순 대기 시간을 밀리초(ms) 단위로 지정합니다. (예: `2000` = 2초)

#### B. 스마트 이미지 인식 대기 (Image Wait)
- **동작 모드**:
  - `appear` (나타날 때까지 대기): 전투 완료 화면, 승리 팝업, 확인 버튼 등이 화면에 뜰 때까지 대기
  - `disappear` (사라질 때까지 대기): 로딩 화면이나 쿨타임 표시가 사라질 때까지 대기
- **설정 항목**:
  - `Template Image`: 인식할 버튼/아이콘의 PNG 이미지 경로
  - `Confidence (0.0 ~ 1.0)`: 이미지 일치율 (기본값 `0.8` 권장)
  - `Timeout (초)`: 지정 시간 동안 이미지를 찾지 못했을 때의 처리 시간
  - `Timeout Policy`: 타임아웃 초과 시 매크로를 중단할지(`raise`), 다음 동작으로 넘어갈지(`skip`) 선택
  - `Region of Interest (ROI)` (선택): 탐색 영역 `X, Y, Width, Height`. 클릭 좌표와 같은 **전역 화면 좌표**입니다 — 주 모니터 왼쪽 위가 `(0, 0)`이고, 주 모니터의 왼쪽·위쪽에 놓인 보조 모니터는 음수 좌표입니다. ROI를 지정하지 않으면 **연결된 모든 모니터**(가상 데스크톱 전체)에서 찾습니다.
- **템플릿 배율 자동 보정**: 화면은 클릭 좌표와 같은 단위(macOS는 포인트, Windows는 물리 픽셀)로 캡처됩니다. macOS Retina에서 `Cmd+Shift+4`로 찍은 템플릿은 2배 크기라서, 템플릿을 원래 크기와 `1/디스플레이 배율` 크기로 각각 맞춰 보고 일치율이 가장 높은 결과를 씁니다. macOS는 디스플레이 배율을 직접 조회하고(Retina 2배는 항상 포함), 조회할 수 없거나 Windows에서는 1.25·1.5·2배를 가정합니다. 찾은 위치는 항상 클릭에 쓰는 좌표로 돌려줍니다. 줄인 템플릿이 ROI보다 큰 배율은 건너뛰며, 모든 배율에서 ROI보다 크면 오류로 알려 줍니다.

---

### Step 4. 시나리오 저장 및 불러오기 (Persistence)
- 상단 메뉴의 **File $\to$ Save Scenario (`Ctrl+S`)**로 작성한 매크로를 `.json` 파일로 저장할 수 있습니다.
- 저장된 시나리오는 다른 PC(Mac $\leftrightarrow$ Windows)에서도 완벽하게 불러와 실행할 수 있습니다.

---

### Step 5. 매크로 실행 및 긴급 정지 (Execution & Safety)
1. 우측 대시보드의 **Repeat Count**에서 반복 실행 횟수를 지정하거나 **Infinite Loop**를 체크하여 **무한 반복**을 설정합니다.
2. 대시보드의 **`▶ Start`** 버튼, 상단 툴바나 **Run** 메뉴의 **Start Execution**, 단축키 **`F5`** 중 어느 것으로 시작해도 똑같이 **Repeat Count**만큼 실행합니다.
3. 매크로가 도는 동안 **현재 실행 중인 카드가 황금색 테두리로 하이라이트**되며, 진행률과 상세 로그가 실시간 출력됩니다.
4. **긴급 정지 (Emergency Stop)**:
   - 게임 도중 긴급하게 멈춰야 할 때는 화면의 정지 버튼을 누르거나, 키보드의 **`<Ctrl> + <Shift> + <S>`** 또는 **`<Esc>`**를 누르세요.
   - 진행 중인 다중 클릭·길게 누르기·단축키 입력도 0.05초(50ms) 이내에 끊기고 그 뒤로는 새 입력이 나가지 않으며, 눌려 있던 마우스/키보드 상태가 즉시 원상 복구됩니다.

---

## 🍎 macOS 필수 보안 권한 설정

macOS에서 마우스 자동 제어 및 화면 돋보기 캡처가 온전히 동작하려면 시스템 권한이 필요합니다:

1. **시스템 설정 (System Settings)** $\to$ **개인정보 보호 및 보안 (Privacy & Security)**
2. **손쉬운 사용 (Accessibility)**:
   - 터미널 앱(Terminal / iTerm2 / VS Code 등)을 **허용**으로 설정합니다. (마우스/키보드 입력 제어에 필요)
3. **화면 및 시스템 오디오 녹음 (Screen Recording)**:
   - 터미널 앱을 **허용**으로 설정합니다. (돋보기 화면 캡처 및 이미지 인식에 필요)

---

## 🧪 테스트 및 품질 검증 (Testing & QA)

ActionMacro는 엄격한 멀티 에이전트 품질 게이트와 독립 포렌식 감사를 거쳐 제작되었습니다:

- **자동화 테스트 스위트**: 전체 통과, `xfail`로 숨겨 둔 확인된 결함 없음 (보조 모니터 음수 좌표 회귀 테스트 포함)
- **마우스 타격 오차**: 1,000회 무작위 좌표 정밀도 검증 결과 평균 오차 $\le 0.5$px
- **긴급 정지 지연시간**: 진행 중인 다중 클릭·길게 누르기 도중 정지해도 실행 스레드 종료·눌린 버튼/키 해제까지 50ms 이내, 정지 50ms 뒤부터 새 입력 0건 (실측: 스레드 종료 평균 약 1ms, 최대 약 2ms). 실제 `PynputAdapter` 경로에서 OS 컨트롤러만 가짜로 바꿔 검증: `tests/e2e/test_input_engine_integration.py`

```bash
# 전체 테스트 실행 (헤드리스 Qt 환경)
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/

# 단위 테스트만 실행
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/unit/
```

---

## 📄 라이선스 (License)

This project is licensed under the MIT License.
