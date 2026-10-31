"""Image recognition on the real application wiring.

These tests assemble the application exactly like production does
(``MainWindow()`` with no injected drivers, ``QAction.trigger()``, the CLI
``main()`` headless path, the real ``MSSScreenAdapter`` and ``PynputAdapter``)
and replace only the OS boundaries:

- ``mss.MSS`` / ``mss.mss``: a fake that serves frames from a synthetic virtual
  desktop (no real screen capture),
- ``pynput.mouse.Controller`` / ``pynput.keyboard.Controller``: recording fakes
  (no real input events),
- ``pynput.keyboard.GlobalHotKeys``: an inert fake (no real global hook).

Covered defects: the missing screen driver on the real execution paths,
Retina (2x) templates against nominal-resolution (1x) captures, and
multi-monitor capture with negative virtual-desktop coordinates.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Optional, Tuple, Union

import cv2
import mss
import numpy as np
import pytest
from mss.models import Size
from mss.screenshot import ScreenShot

from action_macro.__main__ import main as cli_main
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.engine import screen_adapter as screen_adapter_module
from action_macro.engine.screen_adapter import MSSScreenAdapter
from action_macro.engine.wait_system import WaitSystem
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.state_machine import EngineState

Monitor = Dict[str, int]

# Logical (click-space) rectangle of the target button in the synthetic scenes.
BUTTON_X, BUTTON_Y, BUTTON_W, BUTTON_H = 300, 180, 120, 50
BUTTON_CENTER = (BUTTON_X + BUTTON_W // 2, BUTTON_Y + BUTTON_H // 2)


# =============================================================================
# Synthetic scenes
# =============================================================================

def _ui_clutter(height: int, width: int, seed: int = 7) -> np.ndarray:
    """Builds a textured, UI-like BGR background so correlation scores are meaningful."""
    rng = np.random.default_rng(seed)
    img = np.full((height, width, 3), 40, np.uint8)
    for _ in range(max(40, (height * width) // 4000)):
        x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
        w, h = int(rng.integers(10, width // 6)), int(rng.integers(8, height // 8))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, -1)
    for _ in range(max(20, (height * width) // 8000)):
        x, y = int(rng.integers(0, width)), int(rng.integers(12, height))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        cv2.putText(img, "OK", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)
    return img


def _draw_button(img: np.ndarray, x: int, y: int, w: int, h: int, scale: int = 1) -> None:
    """Draws a distinctive 'START' button at (x, y, w, h) in the image's own pixel units."""
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (30, 160, 60), -1)
    cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1), (10, 10, 10), 2 * scale)
    cv2.putText(
        img,
        "START",
        (x + 12 * scale, y + 34 * scale),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9 * scale,
        (255, 255, 255),
        2 * scale,
    )


def _single_monitor(width: int, height: int) -> List[Monitor]:
    mon = {"left": 0, "top": 0, "width": width, "height": height}
    return [dict(mon), dict(mon)]


class RetinaScene:
    """A Retina screen: 2x physical backing store and its nominal (1x) capture.

    mss on macOS captures with ``kCGWindowImageNominalResolution`` (1 pixel per
    point), while Cmd+Shift+4 saves the 2x backing pixels.
    """

    def __init__(self, width: int = 640, height: int = 400) -> None:
        phys = _ui_clutter(height * 2, width * 2)
        self.physical_without = phys.copy()
        _draw_button(phys, BUTTON_X * 2, BUTTON_Y * 2, BUTTON_W * 2, BUTTON_H * 2, scale=2)
        self.physical_with = phys

        def nominal(img: np.ndarray) -> np.ndarray:
            return cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)

        self.capture_without = nominal(self.physical_without)
        self.capture_with = nominal(self.physical_with)
        # Template a user takes with Cmd+Shift+4 on the Retina panel (backing pixels).
        self.template_2x = self.physical_with[
            BUTTON_Y * 2 : (BUTTON_Y + BUTTON_H) * 2, BUTTON_X * 2 : (BUTTON_X + BUTTON_W) * 2
        ].copy()
        # Template captured in the same pipeline as the screen (1x).
        self.template_1x = self.capture_with[
            BUTTON_Y : BUTTON_Y + BUTTON_H, BUTTON_X : BUTTON_X + BUTTON_W
        ].copy()
        self.monitors = _single_monitor(width, height)


def _write_png(path: Path, img: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert cv2.imwrite(str(path), img)
    return path


# =============================================================================
# OS boundary fakes
# =============================================================================

class FakeDesktop:
    """Synthetic virtual desktop served to the fake mss instances.

    Args:
        monitors: mss-style monitor list; ``monitors[0]`` is the virtual desktop
            bounding box in global coordinates.
        frame: BGR image covering ``monitors[0]`` (1 pixel per click unit), or a
            callable receiving the global grab number and returning such an image.
    """

    def __init__(
        self,
        monitors: List[Monitor],
        frame: Union[np.ndarray, Callable[[int], np.ndarray]],
    ) -> None:
        self.monitors = [dict(m) for m in monitors]
        self._frame = frame
        self.instances: List["FakeMSS"] = []
        self.grabs: List[Tuple[Monitor, int]] = []
        self._lock = threading.Lock()

    def frame_for(self, grab_number: int) -> np.ndarray:
        return self._frame(grab_number) if callable(self._frame) else self._frame


class FakeMSS:
    """Stand-in for ``mss.MSS`` that crops grabs out of a FakeDesktop."""

    def __init__(self, desktop: FakeDesktop) -> None:
        self.desktop = desktop
        self.created_thread = threading.get_ident()
        self.closed = False
        self.closed_thread: Optional[int] = None
        desktop.instances.append(self)

    @property
    def monitors(self) -> List[Monitor]:
        return [dict(m) for m in self.desktop.monitors]

    def grab(self, box: Union[Monitor, Tuple[int, int, int, int]]) -> ScreenShot:
        assert not self.closed, "grab() on a closed mss instance"
        if isinstance(box, tuple):
            box = {"left": box[0], "top": box[1], "width": box[2] - box[0], "height": box[3] - box[1]}
        box = {k: int(box[k]) for k in ("left", "top", "width", "height")}
        with self.desktop._lock:
            self.desktop.grabs.append((dict(box), threading.get_ident()))
            number = len(self.desktop.grabs)
        img = self.desktop.frame_for(number)
        vd = self.desktop.monitors[0]
        x0, y0 = box["left"] - vd["left"], box["top"] - vd["top"]
        assert x0 >= 0 and y0 >= 0, f"grab {box} starts outside the virtual desktop {vd}"
        crop = img[y0 : y0 + box["height"], x0 : x0 + box["width"]]
        assert crop.shape[:2] == (box["height"], box["width"]), f"grab {box} leaves the virtual desktop {vd}"
        bgra = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2BGRA)
        return ScreenShot(bytearray(bgra.tobytes()), box, size=Size(box["width"], box["height"]))

    def close(self) -> None:
        self.closed = True
        self.closed_thread = threading.get_ident()


def install_fake_mss(monkeypatch: pytest.MonkeyPatch, desktop: FakeDesktop) -> None:
    """Routes every mss factory to the fake desktop (no real capture possible)."""

    def factory(*_args: object, **_kwargs: object) -> FakeMSS:
        return FakeMSS(desktop)

    monkeypatch.setattr(mss, "MSS", factory)
    monkeypatch.setattr(mss, "mss", factory)


class FakeMouseController:
    """Recording replacement for ``pynput.mouse.Controller``."""

    instances: List["FakeMouseController"] = []

    def __init__(self) -> None:
        self._position = (0, 0)
        self.events: List[tuple] = []
        FakeMouseController.instances.append(self)

    @property
    def position(self) -> Tuple[int, int]:
        return self._position

    @position.setter
    def position(self, pos: Tuple[int, int]) -> None:
        self._position = (int(pos[0]), int(pos[1]))
        self.events.append(("move", self._position))

    def press(self, button: object) -> None:
        self.events.append(("press", str(button), self._position))

    def release(self, button: object) -> None:
        self.events.append(("release", str(button), self._position))

    def click(self, button: object, count: int = 1) -> None:
        self.events.append(("click", str(button), count, self._position))

    def scroll(self, dx: int, dy: int) -> None:
        self.events.append(("scroll", dx, dy))

    def move(self, dx: int, dy: int) -> None:
        self.position = (self._position[0] + dx, self._position[1] + dy)


class FakeKeyboardController:
    """Recording replacement for ``pynput.keyboard.Controller``."""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    def press(self, key: object) -> None:
        self.events.append(("press", str(key)))

    def release(self, key: object) -> None:
        self.events.append(("release", str(key)))

    def tap(self, key: object) -> None:
        self.events.append(("tap", str(key)))

    def type(self, text: str) -> None:
        self.events.append(("type", text))


class FakeGlobalHotKeys:
    """Inert replacement for ``pynput.keyboard.GlobalHotKeys`` (no OS hook)."""

    def __init__(self, hotkeys: dict, *args: object, **kwargs: object) -> None:
        self.hotkeys = hotkeys
        self.daemon = True
        self._alive = False

    def start(self) -> None:
        self._alive = True

    def stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: Optional[float] = None) -> None:
        return None


@pytest.fixture
def fake_os_input(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replaces pynput's controllers and global hotkey hook with fakes."""
    import pynput.keyboard
    import pynput.mouse

    FakeMouseController.instances = []
    monkeypatch.setattr(pynput.mouse, "Controller", FakeMouseController)
    monkeypatch.setattr(pynput.keyboard, "Controller", FakeKeyboardController)
    monkeypatch.setattr(pynput.keyboard, "GlobalHotKeys", FakeGlobalHotKeys)
    return SimpleNamespace(mice=FakeMouseController.instances)


@pytest.fixture
def retina_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates a macOS Retina display (backing scale factor 2.0)."""
    monkeypatch.setattr(screen_adapter_module, "detect_display_scale_factors", lambda *a, **k: (2.0,))


@pytest.fixture
def standard_display(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates displays that all have a scale factor of 1.0."""
    monkeypatch.setattr(screen_adapter_module, "detect_display_scale_factors", lambda *a, **k: (1.0,))


def _image_wait_scenario(template_rel: str, then_click: bool) -> ScenarioModel:
    actions = [
        ActionBlock(
            id="wait-start",
            type=ActionType.IMAGE_WAIT,
            params=ImageWaitParams(
                template_path=template_rel,
                mode="appear",
                confidence=0.8,
                timeout_seconds=5.0,
                poll_interval_seconds=0.02,
            ),
            description="Wait for START button",
        )
    ]
    if then_click:
        actions.append(
            ActionBlock(
                id="click-start",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=BUTTON_CENTER[0], y=BUTTON_CENTER[1]),
                description="Click START",
            )
        )
    else:
        actions.append(
            ActionBlock(id="settle", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))
        )
    return ScenarioModel(name="Image wait wiring", loop_count=1, actions=actions)


def _appearing_desktop(scene: RetinaScene, appears_on_grab: int = 3) -> FakeDesktop:
    """Desktop that shows the button only from the given grab number on."""
    return FakeDesktop(
        scene.monitors,
        lambda n: scene.capture_with if n >= appears_on_grab else scene.capture_without,
    )


# =============================================================================
# (a) GUI: MainWindow() exactly as run_app builds it
# =============================================================================

@pytest.mark.e2e
def test_gui_main_window_without_injected_drivers_waits_for_image(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_os_input: SimpleNamespace
) -> None:
    """Start on a MainWindow() with no drivers must recognise the image and finish successfully."""
    from action_macro.gui.main_window import MainWindow

    scene = RetinaScene()
    desktop = _appearing_desktop(scene, appears_on_grab=3)
    install_fake_mss(monkeypatch, desktop)

    _write_png(tmp_path / "templates" / "start.png", scene.template_1x)
    scenario_path = tmp_path / "wiring.json"
    ScenarioPersistence.save(_image_wait_scenario("templates/start.png", then_click=False), scenario_path)

    win = MainWindow()  # identical to gui/app.py run_app()
    qtbot.addWidget(win)
    assert win.screen_driver is None and win.input_driver is None
    assert win.open_scenario(str(scenario_path))

    # The dashboard's "Start" button, as a user clicks it. (Run > Start / F5 currently passes
    # QAction's `checked` flag as the loop count and runs forever; that is a separate GUI defect.)
    win.dashboard.btn_start.click()
    assert win.engine is not None

    qtbot.waitUntil(lambda: len(win.engine.signals.finished) > 0, timeout=10_000)
    qtbot.waitUntil(
        lambda: win._worker_thread is None or not win._worker_thread.is_alive(), timeout=5_000
    )

    assert win.engine.signals.finished == [(True, "Completed 1 loops")]
    assert [c[:2] for c in win.engine.signals.completed_actions] == [(0, True), (1, True)]
    assert not [log for log in win.engine.signals.logs if log[0] == "ERROR"]
    assert len(desktop.grabs) >= 3, "the wait must keep polling until the image appears"

    # The adapter was created lazily on the worker thread, used there, and closed there.
    assert len(desktop.instances) == 1
    sct = desktop.instances[0]
    main_thread = threading.main_thread().ident
    assert sct.created_thread != main_thread
    assert {thread for _, thread in desktop.grabs} == {sct.created_thread}
    assert sct.closed and sct.closed_thread == sct.created_thread
    assert win.engine.screen_driver is None, "engine-owned adapter must be released after the run"


# =============================================================================
# (b) Headless CLI: __main__.main([...]) exactly as the console script runs
# =============================================================================

@pytest.mark.e2e
def test_headless_cli_waits_for_image_then_clicks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_os_input: SimpleNamespace
) -> None:
    """`ActionMacro scenario.json --headless` must wait for the image and continue to the click."""
    scene = RetinaScene()
    desktop = _appearing_desktop(scene, appears_on_grab=3)
    install_fake_mss(monkeypatch, desktop)

    _write_png(tmp_path / "templates" / "start.png", scene.template_1x)
    scenario_path = tmp_path / "headless.json"
    ScenarioPersistence.save(_image_wait_scenario("templates/start.png", then_click=True), scenario_path)

    exit_code = cli_main([str(scenario_path), "--headless", "--loops", "1"])

    assert exit_code == 0
    assert len(desktop.grabs) >= 3
    assert len(desktop.instances) == 1
    sct = desktop.instances[0]
    assert sct.closed and sct.closed_thread == sct.created_thread
    # The click after the wait went through the real PynputAdapter to the fake controller.
    moves = [e[1] for mouse in fake_os_input.mice for e in mouse.events if e[0] == "move"]
    assert BUTTON_CENTER in moves


@pytest.mark.e2e
@pytest.mark.skipif(sys.platform != "darwin", reason="Retina templates are scaled on macOS only")
def test_headless_cli_retina_template_click_lands_on_logical_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_os_input: SimpleNamespace,
    retina_display: None,
) -> None:
    """A Cmd+Shift+4 (2x) template must be found in the 1x capture on the headless path."""
    scene = RetinaScene()
    desktop = _appearing_desktop(scene, appears_on_grab=2)
    install_fake_mss(monkeypatch, desktop)

    _write_png(tmp_path / "templates" / "start@2x.png", scene.template_2x)
    scenario_path = tmp_path / "retina.json"
    ScenarioPersistence.save(_image_wait_scenario("templates/start@2x.png", then_click=True), scenario_path)

    assert cli_main([str(scenario_path), "--headless", "--loops", "1"]) == 0
    moves = [e[1] for mouse in fake_os_input.mice for e in mouse.events if e[0] == "move"]
    assert BUTTON_CENTER in moves


# =============================================================================
# Engine-owned screen driver lifecycle
# =============================================================================

@pytest.mark.e2e
def test_engine_creates_and_closes_owned_adapter_in_worker_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = RetinaScene()
    desktop = FakeDesktop(scene.monitors, scene.capture_with)
    install_fake_mss(monkeypatch, desktop)
    template = _write_png(tmp_path / "start.png", scene.template_1x)

    engine = ExecutionEngine(_image_wait_scenario(str(template), then_click=False), base_dir=tmp_path)
    engine.start_async()
    assert engine.wait_for_completion(timeout=10.0)

    assert engine.signals.finished == [(True, "Completed 1 loops")]
    assert len(desktop.instances) == 1
    sct = desktop.instances[0]
    assert sct.created_thread == engine._worker_thread.ident
    assert sct.closed and sct.closed_thread == engine._worker_thread.ident
    assert engine.screen_driver is None


@pytest.mark.e2e
def test_engine_releases_owned_adapter_when_image_wait_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = RetinaScene()
    desktop = FakeDesktop(scene.monitors, scene.capture_without)
    install_fake_mss(monkeypatch, desktop)
    template = _write_png(tmp_path / "start.png", scene.template_1x)

    scenario = ScenarioModel(
        name="timeout",
        loop_count=1,
        actions=[
            ActionBlock(
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(template_path=str(template), timeout_seconds=0.1, poll_interval_seconds=0.02),
            )
        ],
    )
    engine = ExecutionEngine(scenario, base_dir=tmp_path)
    with pytest.raises(TimeoutError):
        engine.run()

    assert engine.state == EngineState.ERROR
    assert desktop.instances and all(inst.closed for inst in desktop.instances)
    assert engine.screen_driver is None


@pytest.mark.e2e
def test_engine_never_closes_injected_screen_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = RetinaScene()
    desktop = FakeDesktop(scene.monitors, scene.capture_with)
    install_fake_mss(monkeypatch, desktop)
    template = _write_png(tmp_path / "start.png", scene.template_1x)

    injected = MSSScreenAdapter()
    engine = ExecutionEngine(
        _image_wait_scenario(str(template), then_click=False), screen_driver=injected, base_dir=tmp_path
    )
    assert engine.run() is True

    assert engine.screen_driver is injected
    assert len(desktop.instances) == 1 and not desktop.instances[0].closed
    injected.close()
    assert desktop.instances[0].closed


# =============================================================================
# (c) Retina scale: 2x template vs 1x capture, through the real MSSScreenAdapter
# =============================================================================
# Only the macOS adapter scales templates (captures there are in points), so the tests that
# expect a 0.5x match build it with platform_name="darwin" and run on every OS.

def _wait(adapter: MSSScreenAdapter, template: Path, roi=None, timeout: float = 0.3):
    params = ImageWaitParams(
        template_path=str(template), roi=roi, timeout_seconds=timeout, poll_interval_seconds=0.02
    )
    return WaitSystem.wait_image(adapter, params)


def _assert_near(actual: Tuple[int, int], expected: Tuple[int, int], tol: int = 1) -> None:
    assert abs(actual[0] - expected[0]) <= tol and abs(actual[1] - expected[1]) <= tol, (actual, expected)


@pytest.mark.e2e
def test_retina_2x_template_matches_1x_capture_at_logical_coordinates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retina_display: None
) -> None:
    scene = RetinaScene()
    install_fake_mss(monkeypatch, FakeDesktop(scene.monitors, scene.capture_with))
    template = _write_png(tmp_path / "start@2x.png", scene.template_2x)

    with MSSScreenAdapter(platform_name="darwin") as adapter:
        assert 0.5 in adapter.get_template_scales()
        matched, coords = _wait(adapter, template)

    assert matched is True
    _assert_near(coords, BUTTON_CENTER)


@pytest.mark.e2e
def test_1x_template_still_matches_1x_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retina_display: None
) -> None:
    scene = RetinaScene()
    install_fake_mss(monkeypatch, FakeDesktop(scene.monitors, scene.capture_with))
    template = _write_png(tmp_path / "start.png", scene.template_1x)

    with MSSScreenAdapter() as adapter:
        matched, coords = _wait(adapter, template)

    assert matched is True
    assert coords == BUTTON_CENTER


@pytest.mark.e2e
def test_retina_2x_template_larger_than_roi_matches_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retina_display: None
) -> None:
    """A 240x100 Retina template inside a 160x70 ROI must match (at 0.5x), not raise."""
    scene = RetinaScene()
    desktop = FakeDesktop(scene.monitors, scene.capture_with)
    install_fake_mss(monkeypatch, desktop)
    template = _write_png(tmp_path / "start@2x.png", scene.template_2x)
    roi = (BUTTON_X - 20, BUTTON_Y - 10, BUTTON_W + 40, BUTTON_H + 20)
    assert scene.template_2x.shape[1] > roi[2] and scene.template_2x.shape[0] > roi[3]

    with MSSScreenAdapter(platform_name="darwin") as adapter:
        matched, coords = _wait(adapter, template, roi=roi)

    assert matched is True
    _assert_near(coords, BUTTON_CENTER)
    assert desktop.grabs[-1][0] == {"left": roi[0], "top": roi[1], "width": roi[2], "height": roi[3]}


@pytest.mark.e2e
def test_scale_detection_failure_falls_back_to_common_scales(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(screen_adapter_module, "detect_display_scale_factors", lambda *a, **k: None)
    scene = RetinaScene()
    install_fake_mss(monkeypatch, FakeDesktop(scene.monitors, scene.capture_with))
    template = _write_png(tmp_path / "start@2x.png", scene.template_2x)

    with MSSScreenAdapter(platform_name="darwin") as adapter:
        scales = adapter.get_template_scales()
        matched, coords = _wait(adapter, template)

    assert scales[0] == 1.0
    for display_scale in (1.25, 1.5, 2.0):
        assert any(abs(s - 1.0 / display_scale) < 1e-6 for s in scales), scales
    assert matched is True
    _assert_near(coords, BUTTON_CENTER)


@pytest.mark.e2e
def test_template_too_large_at_every_scale_raises_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retina_display: None
) -> None:
    scene = RetinaScene()
    install_fake_mss(monkeypatch, FakeDesktop(scene.monitors, scene.capture_with))
    template = _write_png(tmp_path / "start@2x.png", scene.template_2x)

    with MSSScreenAdapter() as adapter, pytest.raises(ValueError, match="exceed ROI"):
        _wait(adapter, template, roi=(BUTTON_X, BUTTON_Y, 40, 20))


@pytest.mark.unit
def test_match_template_multiscale_reports_winning_scale_and_skips_oversized_sizes() -> None:
    """The best-scoring scale wins; sizes larger than the search area are skipped, not raised."""
    from action_macro.engine.image_matcher import ImageMatcher

    scene = RetinaScene()
    center, score, scale = ImageMatcher.match_template_multiscale(
        scene.capture_with, scene.template_2x, scales=(1.0, 0.5)
    )
    assert scale == 0.5 and score >= 0.9
    _assert_near(center, BUTTON_CENTER)

    roi = (BUTTON_X - 10, BUTTON_Y - 10, BUTTON_W + 20, BUTTON_H + 20)  # smaller than the 2x template
    center, score, scale = ImageMatcher.match_template_multiscale(
        scene.capture_with, scene.template_2x, scales=(1.0, 0.5), roi=roi
    )
    assert scale == 0.5 and score >= 0.9
    _assert_near(center, BUTTON_CENTER)

    with pytest.raises(ValueError, match="every candidate scale"):
        ImageMatcher.match_template_multiscale(
            scene.capture_with, scene.template_2x, scales=(1.0, 0.5), roi=(0, 0, 50, 20)
        )


@pytest.mark.unit
def test_detect_display_scale_factors_reads_quartz_display_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scale factors come from display modes (pixel width / point width), no capture involved."""
    modes = {1: (5120, 2560), 2: (1920, 1920)}  # display id -> (pixel width, point width)
    fake_quartz = SimpleNamespace(
        CGGetActiveDisplayList=lambda max_displays, a, b: (0, (1, 2), 2),
        CGDisplayCopyDisplayMode=lambda display_id: display_id,
        CGDisplayModeGetPixelWidth=lambda mode: modes[mode][0],
        CGDisplayModeGetWidth=lambda mode: modes[mode][1],
    )
    monkeypatch.setitem(__import__("sys").modules, "Quartz", fake_quartz)

    assert screen_adapter_module.detect_display_scale_factors(platform="darwin") == (1.0, 2.0)
    assert screen_adapter_module.detect_display_scale_factors(platform="win32") is None


@pytest.mark.unit
def test_detect_display_scale_factors_returns_none_when_quartz_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object) -> None:
        raise RuntimeError("no display server")

    monkeypatch.setitem(
        __import__("sys").modules, "Quartz", SimpleNamespace(CGGetActiveDisplayList=boom)
    )
    assert screen_adapter_module.detect_display_scale_factors(platform="darwin") is None


@pytest.mark.unit
def test_candidate_template_scales() -> None:
    cand = screen_adapter_module.candidate_template_scales
    assert cand((1.0,)) == (1.0,)
    assert cand((1.0, 2.0)) == (1.0, 0.5)
    fallback = cand(None)
    assert fallback[0] == 1.0
    assert sorted(round(1.0 / s, 4) for s in fallback[1:]) == [1.25, 1.5, 2.0]


@pytest.mark.unit
def test_macos_adapter_always_tries_retina_scale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Templates shot on a Retina panel must still match when only 1x displays are attached."""
    monkeypatch.setattr(screen_adapter_module, "detect_display_scale_factors", lambda *a, **k: (1.0,))
    assert MSSScreenAdapter(platform_name="darwin").get_template_scales() == (1.0, 0.5)


@pytest.mark.unit
@pytest.mark.parametrize("platform_name", ["win32", "linux"])
def test_non_macos_adapter_tries_native_size_only(platform_name: str) -> None:
    """Off macOS captures and screenshots share physical pixels, so only 1.0 is tried.

    Extra scales there would only let a template match a smaller look-alike and slow
    every poll (and with it the emergency stop during an image wait).
    """
    assert MSSScreenAdapter(platform_name=platform_name).get_template_scales() == (1.0,)


@pytest.mark.unit
def test_macos_adapter_without_detection_falls_back_to_common_scales(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the macOS display query fails, the common factors are assumed."""
    monkeypatch.setattr(screen_adapter_module, "detect_display_scale_factors", lambda *a, **k: None)
    scales = MSSScreenAdapter(platform_name="darwin").get_template_scales()
    assert scales[0] == 1.0
    assert sorted(round(1.0 / s, 4) for s in scales[1:]) == [1.25, 1.5, 2.0]


# =============================================================================
# (d) Multi-monitor: secondary monitor left of the primary (negative coordinates)
# =============================================================================

PRIMARY = {"left": 0, "top": 0, "width": 640, "height": 400}
LEFT = {"left": -640, "top": 100, "width": 640, "height": 400}
VIRTUAL = {"left": -640, "top": 0, "width": 1280, "height": 500}
SECONDARY_BUTTON = (-450, 260)  # global top-left of the button on the left monitor


def _dual_monitor_scene() -> SimpleNamespace:
    """Virtual desktop canvases without/with the button on the left (negative-x) monitor."""
    without = _ui_clutter(VIRTUAL["height"], VIRTUAL["width"], seed=11)
    canvas = without.copy()
    bx, by = SECONDARY_BUTTON[0] - VIRTUAL["left"], SECONDARY_BUTTON[1] - VIRTUAL["top"]
    _draw_button(canvas, bx, by, BUTTON_W, BUTTON_H)
    template = canvas[by : by + BUTTON_H, bx : bx + BUTTON_W].copy()
    center = (SECONDARY_BUTTON[0] + BUTTON_W // 2, SECONDARY_BUTTON[1] + BUTTON_H // 2)
    return SimpleNamespace(without=without, canvas=canvas, template=template, center=center)


@pytest.fixture
def dual_monitor_desktop(monkeypatch: pytest.MonkeyPatch, standard_display: None) -> SimpleNamespace:
    scene = _dual_monitor_scene()
    desktop = FakeDesktop([VIRTUAL, PRIMARY, LEFT], scene.canvas)
    install_fake_mss(monkeypatch, desktop)
    return SimpleNamespace(desktop=desktop, canvas=scene.canvas, template=scene.template, center=scene.center)


@pytest.mark.e2e
def test_headless_cli_negative_roi_on_left_monitor_waits_and_reports_global_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_os_input: SimpleNamespace,
    standard_display: None,
) -> None:
    """A scenario whose ROI lies on a monitor left of the primary one, run through the CLI."""
    scene = _dual_monitor_scene()
    desktop = FakeDesktop(
        [VIRTUAL, PRIMARY, LEFT], lambda n: scene.canvas if n >= 3 else scene.without
    )
    install_fake_mss(monkeypatch, desktop)

    engines: List[ExecutionEngine] = []
    real_init = ExecutionEngine.__init__

    def recording_init(self: ExecutionEngine, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)
        engines.append(self)

    monkeypatch.setattr(ExecutionEngine, "__init__", recording_init)

    roi = (-500, 200, 300, 150)  # entirely on the left monitor
    _write_png(tmp_path / "templates" / "left_btn.png", scene.template)
    scenario = ScenarioModel(
        name="Left monitor wait",
        loop_count=1,
        actions=[
            ActionBlock(
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="templates/left_btn.png",
                    roi=roi,
                    timeout_seconds=5.0,
                    poll_interval_seconds=0.02,
                ),
            ),
            ActionBlock(type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10)),
        ],
    )
    scenario_path = tmp_path / "left_monitor.json"
    ScenarioPersistence.save(scenario, scenario_path)

    assert cli_main([str(scenario_path), "--headless", "--loops", "1"]) == 0

    assert len(desktop.grabs) >= 3, "the wait must keep polling until the image appears"
    roi_box = {"left": roi[0], "top": roi[1], "width": roi[2], "height": roi[3]}
    assert all(box == roi_box for box, _ in desktop.grabs)
    assert len(engines) == 1
    assert engines[0]._last_detected_coords == scene.center


@pytest.mark.e2e
def test_negative_roi_on_left_monitor_is_captured_in_global_coordinates(
    dual_monitor_desktop: SimpleNamespace,
) -> None:
    env = dual_monitor_desktop
    roi = (-500, 200, 300, 150)
    with MSSScreenAdapter() as adapter:
        frame = adapter.capture_screen(roi=roi)

    assert env.desktop.grabs[-1][0] == {"left": -500, "top": 200, "width": 300, "height": 150}
    x0, y0 = roi[0] - VIRTUAL["left"], roi[1] - VIRTUAL["top"]
    assert np.array_equal(frame, env.canvas[y0 : y0 + roi[3], x0 : x0 + roi[2]])


@pytest.mark.e2e
def test_image_wait_with_negative_roi_returns_global_click_coordinates(
    tmp_path: Path, dual_monitor_desktop: SimpleNamespace
) -> None:
    env = dual_monitor_desktop
    template = _write_png(tmp_path / "left_btn.png", env.template)

    with MSSScreenAdapter() as adapter:
        matched, coords = _wait(adapter, template, roi=(-500, 200, 300, 150))

    assert matched is True
    assert coords == env.center


@pytest.mark.e2e
def test_image_wait_without_roi_searches_every_monitor(
    tmp_path: Path, dual_monitor_desktop: SimpleNamespace
) -> None:
    env = dual_monitor_desktop
    template = _write_png(tmp_path / "left_btn.png", env.template)

    with MSSScreenAdapter() as adapter:
        matched, coords = _wait(adapter, template)

    assert env.desktop.grabs[-1][0] == VIRTUAL
    assert matched is True
    assert coords == env.center


@pytest.mark.e2e
def test_roi_outside_virtual_desktop_is_rejected(dual_monitor_desktop: SimpleNamespace) -> None:
    with MSSScreenAdapter() as adapter:
        with pytest.raises(ValueError, match="virtual desktop"):
            adapter.capture_screen(roi=(-700, 200, 100, 100))
        with pytest.raises(ValueError, match="virtual desktop"):
            adapter.capture_screen(roi=(600, 450, 100, 100))
    assert dual_monitor_desktop.desktop.grabs == []


@pytest.mark.e2e
def test_pixel_color_uses_global_coordinates(dual_monitor_desktop: SimpleNamespace) -> None:
    env = dual_monitor_desktop
    gx, gy = -600, 150
    b, g, r = env.canvas[gy - VIRTUAL["top"], gx - VIRTUAL["left"]]
    with MSSScreenAdapter() as adapter:
        assert adapter.get_pixel_color(gx, gy) == (int(r), int(g), int(b))
    assert env.desktop.grabs[-1][0] == {"left": gx, "top": gy, "width": 1, "height": 1}
