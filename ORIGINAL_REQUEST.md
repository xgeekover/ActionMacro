# Original User Request

## Initial Request — 2026-10-17T13:02:27Z

BlueStacks에서 실행 중인 모바일 게임의 반복 작업을 자동화하기 위한 크로스 플랫폼(Mac/Windows) 마우스 & 키보드 매크로 도구. 개인 사용 목적의 실용적인 데스크톱 애플리케이션. Python 기반으로 구현하며, GUI에서 시각적으로 매크로 시나리오를 조립하고 실행할 수 있어야 한다.

Working directory: ~/teamwork_projects/action_macro
Integrity mode: demo

## Requirements

### R1. 비주얼 매크로 빌더 GUI
Python(PyQt 또는 Tkinter 등)으로 만든 GUI 데스크톱 앱. 사용자는 드래그 앤 드롭 또는 클릭으로 액션 블록(마우스 클릭, 키 입력, 대기 등)을 시각적으로 조립하여 매크로 시나리오를 만들 수 있어야 한다. Mac과 Windows 양쪽에서 동작해야 한다.

### R2. 정밀 마우스/키보드 제어 및 내장 좌표 피커
마우스 클릭은 지정된 화면 좌표에 ±1px 이내로 정확하게 수행되어야 한다. 키보드 입력(단일 키, 조합 키)도 지원해야 한다. 좌표를 쉽게 잡기 위한 좌표 캡처/피커 기능이 앱 내에 내장되어야 하며, 화면 위에서 마우스를 올리면 실시간 좌표가 표시되고, 클릭하면 해당 좌표가 매크로 액션에 자동 입력되어야 한다.

### R3. 대기 시스템 (고정 시간 + 이미지 인식)
두 가지 대기 방식을 지원해야 한다:
- **고정 시간 대기**: 지정된 시간(초/밀리초)만큼 대기
- **이미지 인식 대기**: 화면의 특정 영역에서 지정된 이미지(스크린샷 조각)가 나타나거나 사라질 때까지 대기. 타임아웃 설정이 가능해야 한다.

### R4. 시나리오 관리 및 실행 제어
- 매크로 시나리오를 파일로 저장하고 불러올 수 있어야 한다
- 매크로를 지정 횟수 또는 무한 루프로 반복 실행할 수 있어야 한다
- 글로벌 핫키로 긴급 정지가 가능해야 한다 (매크로 실행 중 언제든 즉시 중단)
- 실행 로그와 현재 상태(진행 중인 액션, 반복 횟수 등)가 실시간으로 표시되어야 한다

## Acceptance Criteria

### 크로스 플랫폼 동작
- [ ] Mac에서 앱이 실행되고 매크로를 생성/실행할 수 있다
- [ ] Windows에서 앱이 실행되고 매크로를 생성/실행할 수 있다
- [ ] 한 플랫폼에서 저장한 시나리오 파일을 다른 플랫폼에서 불러올 수 있다

### GUI 매크로 빌더
- [ ] 최소 4가지 액션 타입(마우스 클릭, 키 입력, 고정 대기, 이미지 대기)을 블록으로 추가/삭제/재정렬할 수 있다
- [ ] 앱 내 좌표 피커를 활성화하면 마우스 위치의 실시간 좌표가 표시되고, 클릭 시 좌표가 매크로 액션에 자동 삽입된다

### 마우스 정밀도
- [ ] 지정 좌표에 마우스 클릭 시 실제 클릭 위치가 ±1px 이내임을 자동화된 테스트로 검증한다 (예: 클릭 후 마우스 좌표를 읽어 비교)

### 대기 시스템
- [ ] 고정 시간 대기가 지정된 시간만큼 정확히 대기한다
- [ ] 이미지 인식 대기: 테스트 이미지가 화면에 나타나면 대기가 해제되는 것을 데모 시나리오로 검증한다
- [ ] 이미지 인식 대기에 타임아웃이 동작한다 (타임아웃 초과 시 다음 액션으로 진행하거나 에러 처리)

### 실행 제어
- [ ] 매크로가 지정 횟수만큼 반복 실행된다
- [ ] 글로벌 핫키로 실행 중인 매크로가 1초 이내에 중단된다
- [ ] 실행 중 현재 진행 상황(몇 번째 반복, 현재 액션)이 GUI에 실시간 표시된다

### 시나리오 관리
- [ ] 매크로 시나리오를 파일로 저장하고 다시 불러올 수 있다
- [ ] 불러온 시나리오가 원본과 동일한 액션 시퀀스를 가진다


## Follow-up — 2026-10-17T23:45:50Z

이 프로젝트는 이전 팀이 M1-M3까지 완료한 기존 코드베이스를 이어서 완성하는 작업이다. BlueStacks 게임 자동화를 위한 크로스 플랫폼(Mac/Windows) 마우스 & 키보드 매크로 도구의 **GUI 레이어(M4)와 최종 통합 검증(M5)**을 구현해야 한다.

Working directory: ~/teamwork_projects/action_macro
Integrity mode: demo

## Existing Codebase (DO NOT rewrite — extend it)

이미 구현 완료되어 441개 테스트가 통과하는 코드베이스가 존재한다. 반드시 기존 코드를 읽고 이해한 후 그 위에 GUI를 올려야 한다.

**완료된 모듈:**
- `src/action_macro/core/` — ActionBlock, ScenarioModel, ActionType, JSON persistence, key mapping
- `src/action_macro/engine/` — InputAdapter (pynput, ±1px), CoordinateTransformer (Retina/DPI), ScreenAdapter (MSS), ImageMatcher (OpenCV), WaitSystem (sliced wait + image wait)
- `src/action_macro/runner/` — ExecutionEngine (repeat/infinite loop), StateMachine (6 states), HotkeyListener (global emergency stop <1s)

**중요한 인터페이스 계약 (PROJECT.md에 상세 기술됨):**
- `ExecutionSignals(QObject)`: `state_changed`, `progress_updated`, `action_started`, `action_completed`, `log_emitted`, `execution_finished` 시그널 제공
- `InputAdapterProtocol`: `move_to`, `click`, `get_position`, `press_key`, `release_key`, `hotkey`, `safety_flush`
- `WaitSystemProtocol`: `wait`, `wait_image`

**기존 테스트:** `tests/unit/`과 `tests/e2e/` (Tier 1-4) 모두 통과 중. 기존 테스트를 깨뜨리지 않아야 한다.

## Requirements

### R1. PySide6 비주얼 매크로 빌더 GUI
기존 core/engine/runner 레이어 위에 PySide6 GUI를 구현한다. 사용자는 드래그 앤 드롭 또는 클릭으로 액션 블록(마우스 클릭, 키 입력, 고정 대기, 이미지 대기)을 시각적으로 조립하여 매크로 시나리오를 만들 수 있어야 한다. 블록 추가, 삭제, 재정렬이 가능해야 한다. Mac과 Windows 양쪽에서 동작해야 한다.

### R2. 내장 좌표 피커 오버레이
화면 위에 반투명 오버레이를 띄워 마우스 위치의 실시간 좌표를 표시하고, 클릭하면 해당 좌표가 현재 편집 중인 매크로 액션에 자동 삽입되는 좌표 캡처/피커 기능이 앱 내에 내장되어야 한다.

### R3. 실행 대시보드 및 로그 콘솔
매크로 실행 시 현재 진행 상황(몇 번째 반복, 현재 액션, 상태)이 GUI에 실시간 표시되어야 한다. 실행 로그가 콘솔 형태로 표시되어야 한다. 시나리오를 파일로 저장/불러오기할 수 있는 메뉴가 있어야 한다.

### R4. 기존 테스트 유지 및 최종 통합 검증
기존 441개 테스트를 깨뜨리지 않아야 한다. GUI 관련 추가 테스트를 작성하고, 전체 E2E 시나리오(시나리오 생성 → 저장 → 불러오기 → 실행 → 긴급 정지)가 동작함을 검증해야 한다.

## Acceptance Criteria

### GUI 매크로 빌더
- [ ] 최소 4가지 액션 타입(마우스 클릭, 키 입력, 고정 대기, 이미지 대기)을 블록으로 추가/삭제/재정렬할 수 있다
- [ ] 앱 내 좌표 피커를 활성화하면 마우스 위치의 실시간 좌표가 표시되고, 클릭 시 좌표가 매크로 액션에 자동 삽입된다
- [ ] 매크로 시나리오를 파일로 저장하고 다시 불러올 수 있다

### 실행 대시보드
- [ ] 실행 중 현재 진행 상황(몇 번째 반복, 현재 액션)이 GUI에 실시간 표시된다
- [ ] 실행 로그가 콘솔에 실시간으로 출력된다
- [ ] 글로벌 핫키로 실행 중인 매크로가 1초 이내에 중단된다

### 기존 코드 호환성
- [ ] 기존 441개 테스트가 모두 통과한다 (python -m pytest tests/ 실행으로 검증)
- [ ] 앱이 `python -m action_macro`로 실행된다

### 크로스 플랫폼
- [ ] Mac에서 앱이 실행되고 매크로를 생성/실행할 수 있다
- [ ] Windows에서 앱이 실행되고 매크로를 생성/실행할 수 있다
