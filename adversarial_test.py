"""
adversarial_test.py
===================
Adversarial Stress Test Suite for Milestone 1:
- action_macro.core.models
- action_macro.core.persistence

Evaluates:
1. Massive scale & stress (5,000 to 10,000 action blocks)
2. Circular references, object graph mutations, deep nesting
3. Extreme input boundaries (floats, huge numbers, negative delays, boolean confusion, whitespace IDs)
4. Corrupted JSON payloads, schema violations, Unicode/surrogate stress
5. Filesystem hazards (permission errors, disk-full ENOSPC simulation, directory targets)
6. Multi-threaded concurrent read/write and atomic replace verification
"""

import errno
import json
import math
import os
import random
import stat
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageMode,
    ImageWaitParams,
    KeyAction,
    KeyInputParams,
    MouseButton,
    MouseClickParams,
    ScenarioModel,
    TimeoutAction,
    ValidationError,
)
from action_macro.core.persistence import (
    PersistenceError,
    ScenarioFileNotFoundError,
    ScenarioJSONDecodeError,
    ScenarioValidationError,
    UnsupportedVersionError,
    load_scenario,
    resolve_template_path,
    save_scenario,
    scenario_from_dict,
    scenario_from_json,
    scenario_to_dict,
    scenario_to_json,
    to_posix_relative_path,
)


# ===========================================================================
# Suite 1: Massive Scale & Stress Harness
# ===========================================================================

def test_stress_5000_action_blocks(tmp_path: Path):
    """Stress test: 5,000 heterogeneous action blocks serialization and roundtrip."""
    count = 5000
    actions = []
    for i in range(count):
        mod = i % 4
        if mod == 0:
            act = ActionBlock(
                id=f"act-click-{i:05d}",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=i % 1920, y=i % 1080, button="left", clicks=1),
                description=f"Click {i}",
            )
        elif mod == 1:
            act = ActionBlock(
                id=f"act-key-{i:05d}",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["ctrl", "s"], action="press"),
                description=f"Key {i}",
            )
        elif mod == 2:
            act = ActionBlock(
                id=f"act-wait-{i:05d}",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=i % 5000),
                description=f"Wait {i}",
            )
        else:
            act = ActionBlock(
                id=f"act-img-{i:05d}",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path=f"templates/icon_{i % 50}.png",
                    confidence=0.85,
                    timeout_seconds=5.0,
                ),
                description=f"Image {i}",
            )
        actions.append(act)

    t0 = time.perf_counter()
    scen = ScenarioModel(name="Massive5k", loop_count=5, actions=actions)
    init_duration = time.perf_counter() - t0
    assert len(scen.actions) == count
    # Scenario initialization with 5k blocks should complete in under 500ms
    assert init_duration < 0.5, f"Scenario init took too long: {init_duration:.3f}s"

    # Save to disk
    file_path = tmp_path / "massive_5000.json"
    t0 = time.perf_counter()
    save_scenario(scen, file_path)
    save_duration = time.perf_counter() - t0
    assert file_path.exists()
    assert save_duration < 2.0, f"save_scenario took too long: {save_duration:.3f}s"

    # Load from disk
    t0 = time.perf_counter()
    loaded = load_scenario(file_path)
    load_duration = time.perf_counter() - t0
    assert len(loaded.actions) == count
    assert loaded.actions[0].id == "act-click-00000"
    assert loaded.actions[-1].id == f"act-img-{count-1:05d}"
    assert load_duration < 2.0, f"load_scenario took too long: {load_duration:.3f}s"


def test_stress_duplicate_id_check_linear_scale():
    """Verify duplicate ID detection remains linear O(N) using set lookup."""
    count = 2000
    actions = [
        ActionBlock(id=f"uniq-{i}", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))
        for i in range(count)
    ]
    # Appending a duplicate ID at the end
    actions.append(
        ActionBlock(id="uniq-0", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))
    )
    t0 = time.perf_counter()
    with pytest.raises(ValueError, match="Duplicate action block IDs"):
        ScenarioModel(name="DuplicateAtScale", actions=actions)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.2, f"Duplicate check took too long: {elapsed:.3f}s"


def test_stress_deep_clone_large_scenario():
    """Verify scenario.clone() scales cleanly without stack overflow or shared references."""
    count = 1000
    actions = [
        ActionBlock(id=f"act-{i}", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=i, y=i))
        for i in range(count)
    ]
    scen = ScenarioModel(name="OriginalClone", actions=actions)
    t0 = time.perf_counter()
    cloned = scen.clone()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert len(cloned.actions) == count
    # Mutating cloned does not mutate original
    cloned.actions[0].description = "Mutated Clone"
    assert scen.actions[0].description == ""


# ===========================================================================
# Suite 2: Circular References, Mutations & Object Graph Stress
# ===========================================================================

def test_circular_reference_in_target_resolution(tmp_path: Path):
    """Circular reference in target_resolution dictionary raises PersistenceError cleanly."""
    scen = ScenarioModel(name="CircularScenario")
    circ = {"width": 1920, "height": 1080}
    circ["self"] = circ
    scen.target_resolution = circ

    file_path = tmp_path / "circular.json"
    with pytest.raises(PersistenceError, match="Serialization failed"):
        save_scenario(scen, file_path)

    # Ensure no leftover temporary files
    leftover = list(tmp_path.glob("*.tmp*"))
    assert len(leftover) == 0, f"Leftover temp files found: {leftover}"


def test_circular_reference_in_cloning():
    """ScenarioModel.clone() handles circular references in metadata safely."""
    scen = ScenarioModel(name="CircularClone")
    circ = {"width": 1920}
    circ["self"] = circ
    scen.target_resolution = circ
    cloned = scen.clone()
    assert cloned.target_resolution is not None
    assert cloned.target_resolution["self"] is cloned.target_resolution


def test_sequential_scenario_mutations():
    """1000 sequential additions, moves, and deletions preserve scenario consistency."""
    scen = ScenarioModel(name="MutationStress")
    for i in range(100):
        scen.add_action(
            ActionBlock(id=f"block-{i}", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=i))
        )
    assert len(scen.actions) == 100

    # Perform 300 random moves
    rng = random.Random(42)
    for _ in range(300):
        f = rng.randint(0, len(scen.actions) - 1)
        t = rng.randint(0, len(scen.actions) - 1)
        scen.move_action(f, t)
    assert len(scen.actions) == 100

    # Perform 50 deletions
    for i in range(50):
        res = scen.remove_action(f"block-{i}")
        assert res is True
    assert len(scen.actions) == 50

    # Ensure all remaining IDs are unique and present
    remaining_ids = {a.id for a in scen.actions}
    assert len(remaining_ids) == 50
    for i in range(50, 100):
        assert f"block-{i}" in remaining_ids


def test_move_action_boundary_indices():
    """move_action rejects out-of-bounds negative and excessive indices."""
    scen = ScenarioModel(
        name="MoveBounds",
        actions=[
            ActionBlock(id="a1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1)),
            ActionBlock(id="a2", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=2)),
        ],
    )
    with pytest.raises(IndexError):
        scen.move_action(-1, 1)

    with pytest.raises(IndexError):
        scen.move_action(0, -1)

    with pytest.raises(IndexError):
        scen.move_action(2, 0)

    with pytest.raises(IndexError):
        scen.move_action(0, 2)


# ===========================================================================
# Suite 3: Extreme Input Boundaries & Type Integrity
# ===========================================================================

def test_float_coordinates_rejected_with_type_error():
    """Float coordinates in MouseClickParams are strictly rejected with TypeError."""
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100.5, y=200)

    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100, y=200.7)


def test_nan_and_inf_coordinates_rejected():
    """NaN and Inf coordinates are floats and must be rejected with TypeError."""
    with pytest.raises(TypeError):
        MouseClickParams(x=float("nan"), y=100)

    with pytest.raises(TypeError):
        MouseClickParams(x=100, y=float("inf"))


def test_boolean_int_confusion_rejected():
    """Booleans (True/False) passed to integer fields must be rejected with TypeError."""
    # MouseClickParams
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=True, y=100)
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100, y=False)
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100, y=200, clicks=True)
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100, y=200, interval_ms=False)
    with pytest.raises(TypeError, match="must be an int"):
        MouseClickParams(x=100, y=200, press_duration_ms=True)

    # KeyInputParams
    with pytest.raises(TypeError, match="must be an int"):
        KeyInputParams(keys=["enter"], press_duration_ms=False)

    # FixedWaitParams
    with pytest.raises(TypeError, match="must be an int"):
        FixedWaitParams(duration_ms=True)

    # ScenarioModel
    with pytest.raises(TypeError, match="must be an int"):
        ScenarioModel(name="Test", loop_count=True)
    with pytest.raises(TypeError, match="must be an int"):
        ScenarioModel(name="Test", loop_count=False)


def test_huge_arbitrary_precision_numbers():
    """Python arbitrary-precision integers are safely represented and serialized."""
    huge_int = 10**30
    p = MouseClickParams(x=huge_int, y=-huge_int)
    assert p.x == huge_int
    assert p.y == -huge_int

    scen = ScenarioModel(
        name="HugeNumbers",
        loop_count=10**18,
        actions=[ActionBlock(id="huge-click", type=ActionType.MOUSE_CLICK, params=p)],
    )
    json_str = scenario_to_json(scen)
    reloaded = scenario_from_json(json_str)
    assert reloaded.loop_count == 10**18
    assert reloaded.actions[0].params.x == huge_int


def test_empty_and_whitespace_ids_rejected():
    """Empty or whitespace-only ActionBlock and Scenario IDs are rejected."""
    with pytest.raises(ValueError, match="cannot be empty"):
        ActionBlock(id="", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))

    with pytest.raises(ValueError, match="cannot be empty"):
        ActionBlock(id="   \t\n  ", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))

    with pytest.raises(TypeError, match="must be a string"):
        ActionBlock(id=12345, type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))  # type: ignore[arg-type]


def test_image_wait_confidence_boundaries():
    """ImageWaitParams confidence: exact 0.0 and 1.0 valid; out-of-bounds rejected."""
    assert ImageWaitParams(template_path="t.png", confidence=0.0).confidence == 0.0
    assert ImageWaitParams(template_path="t.png", confidence=1.0).confidence == 1.0

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        ImageWaitParams(template_path="t.png", confidence=-0.0001)

    with pytest.raises(ValueError, match="between 0.0 and 1.0"):
        ImageWaitParams(template_path="t.png", confidence=1.0001)

    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", confidence=float("nan"))


def test_image_wait_roi_strict_validation():
    """ImageWaitParams ROI: requires 4-tuple of non-bool positive-dimension ints."""
    valid_roi = (0, 0, 1920, 1080)
    p = ImageWaitParams(template_path="t.png", roi=valid_roi)
    assert p.roi == (0, 0, 1920, 1080)

    # Negative coordinates allowed for multi-monitor, but w, h must be positive
    p_multi = ImageWaitParams(template_path="t.png", roi=(-1920, 0, 1920, 1080))
    assert p_multi.roi == (-1920, 0, 1920, 1080)

    # Width zero or negative
    with pytest.raises(ValueError, match="must be positive"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 0, 100))
    with pytest.raises(ValueError, match="must be positive"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, -10, 100))

    # Height zero or negative
    with pytest.raises(ValueError, match="must be positive"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 100, 0))
    with pytest.raises(ValueError, match="must be positive"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 100, -5))

    # Float dimensions
    with pytest.raises(TypeError, match="must be an integer"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 100.5, 200))  # type: ignore[arg-type]

    # Bool dimensions
    with pytest.raises(TypeError, match="must be an integer"):
        ImageWaitParams(template_path="t.png", roi=(0, 0, True, 200))  # type: ignore[arg-type]


# ===========================================================================
# Suite 4: Corrupted JSON Payloads & Schema Integrity
# ===========================================================================

def test_truncated_json_payload(tmp_path: Path):
    """Truncated JSON raises ScenarioJSONDecodeError."""
    file_path = tmp_path / "truncated.json"
    file_path.write_text('{"name": "Truncated Macro", "actions": [')
    with pytest.raises(ScenarioJSONDecodeError, match="Malformed scenario JSON"):
        load_scenario(file_path)


def test_empty_and_whitespace_file_payloads(tmp_path: Path):
    """Empty and whitespace-only files raise ScenarioJSONDecodeError."""
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("")
    with pytest.raises(ScenarioJSONDecodeError, match="is empty"):
        load_scenario(empty_file)

    ws_file = tmp_path / "whitespace.json"
    ws_file.write_text("   \n\t  \r\n ")
    with pytest.raises(ScenarioJSONDecodeError, match="is empty"):
        load_scenario(ws_file)


def test_non_dict_root_json_types():
    """Non-dictionary JSON roots (array, string, int, bool, null) raise ScenarioValidationError."""
    for invalid_payload in [
        "[]",
        '"a string root"',
        "12345",
        "true",
        "null",
    ]:
        with pytest.raises((ScenarioValidationError, ScenarioJSONDecodeError)):
            scenario_from_json(invalid_payload)


def test_missing_mandatory_root_fields():
    """Missing any mandatory root field raises ScenarioValidationError."""
    base_dict = {
        "name": "Test",
        "version": "1.0.0",
        "loop_count": 1,
        "actions": [],
    }
    for req in ("name", "version", "loop_count", "actions"):
        corrupted = dict(base_dict)
        del corrupted[req]
        with pytest.raises(ScenarioValidationError, match=f"Missing required root field: '{req}'"):
            scenario_from_dict(corrupted)


def test_malformed_action_block_in_dict():
    """Malformed action block raises ScenarioValidationError."""
    # Non-dict action
    with pytest.raises(ScenarioValidationError, match="must be a dictionary"):
        scenario_from_dict({
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": ["not_a_dict"],
        })

    # Missing id
    with pytest.raises(ScenarioValidationError, match="missing required 'id' field"):
        scenario_from_dict({
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [{"type": "FIXED_WAIT", "params": {"duration_ms": 100}}],
        })

    # Empty id
    with pytest.raises(ScenarioValidationError, match="must be a non-empty string"):
        scenario_from_dict({
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [{"id": "  ", "type": "FIXED_WAIT", "params": {"duration_ms": 100}}],
        })

    # Unknown type
    with pytest.raises(ScenarioValidationError, match="Unknown action type"):
        scenario_from_dict({
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [{"id": "a1", "type": "QUANTUM_TELEPORT", "params": {}}],
        })


def test_semver_unsupported_future_version():
    """Future major schema version raises UnsupportedVersionError."""
    with pytest.raises(UnsupportedVersionError, match="is newer than supported version"):
        scenario_from_dict({
            "name": "FutureMacro",
            "version": "2.0.0",
            "loop_count": 1,
            "actions": [],
        })


def test_semver_malformed_version_strings():
    """Malformed version strings raise ScenarioValidationError."""
    for bad_ver in ["1.0", "1.0.0.0", "v1.0.0", "alpha", "1.x.3", ""]:
        with pytest.raises(ScenarioValidationError):
            scenario_from_dict({
                "name": "BadVersion",
                "version": bad_ver,
                "loop_count": 1,
                "actions": [],
            })


def test_unicode_and_special_character_resilience(tmp_path: Path):
    """Multi-lingual Unicode (CJK, Emoji, Math symbols) serializes and deserializes identically."""
    unicode_name = "블루스택 ⚔️ 매크로: 퀘스트 & 战斗 🚀 (α + β = γ)"
    unicode_desc = "한글/日本語/中文/Emoji 테스트: 💾 🔄 ⚡ 🎯"
    scen = ScenarioModel(
        name=unicode_name,
        description=unicode_desc,
        actions=[
            ActionBlock(
                id="act-unicode",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["한글입력", "Enter", "Space"]),
                description="특수 키 조합 테스트",
            )
        ],
    )
    file_path = tmp_path / "unicode_stress.json"
    save_scenario(scen, file_path)
    loaded = load_scenario(file_path)

    assert loaded.name == unicode_name
    assert loaded.description == unicode_desc
    assert loaded.actions[0].params.keys == ["한글입력", "Enter", "Space"]
    assert loaded.actions[0].description == "특수 키 조합 테스트"


# ===========================================================================
# Suite 5: Filesystem Hazards, Disk-Full Simulation & Permission Denied
# ===========================================================================

def test_save_scenario_make_dirs_behavior(tmp_path: Path):
    """make_dirs=True creates nested parents; make_dirs=False raises PersistenceError if missing."""
    scen = ScenarioModel(name="DirTest")

    # Deep nested path with make_dirs=True
    nested_path = tmp_path / "level1" / "level2" / "level3" / "macro.json"
    save_scenario(scen, nested_path, make_dirs=True)
    assert nested_path.exists()

    # Non-existent path with make_dirs=False
    non_existent = tmp_path / "missing_dir" / "macro.json"
    with pytest.raises(PersistenceError):
        save_scenario(scen, non_existent, make_dirs=False)


def test_save_scenario_target_is_existing_directory(tmp_path: Path):
    """Saving to a path that is an existing directory raises PersistenceError."""
    target_dir = tmp_path / "directory_target"
    target_dir.mkdir()
    scen = ScenarioModel(name="DirCollision")
    with pytest.raises(PersistenceError, match="Failed to write scenario file"):
        save_scenario(scen, target_dir)


#: chmod cannot make a folder read-only or a file unreadable on Windows (only the file's
#: read-only attribute exists), so these two run where POSIX permission bits apply.
posix_permissions = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


@posix_permissions
def test_save_scenario_permission_denied_directory(tmp_path: Path):
    """Saving to a read-only directory raises PersistenceError cleanly."""
    ro_dir = tmp_path / "readonly_dir"
    ro_dir.mkdir()
    ro_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # No write permission
    file_path = ro_dir / "scen.json"
    scen = ScenarioModel(name="PermissionDenied")

    try:
        with pytest.raises(PersistenceError, match="Permission denied"):
            save_scenario(scen, file_path)
    finally:
        ro_dir.chmod(stat.S_IRWXU)


@posix_permissions
def test_load_scenario_permission_denied_file(tmp_path: Path):
    """Loading an unreadable file raises PersistenceError cleanly."""
    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text('{"name": "Valid", "version": "1.0.0", "loop_count": 1, "actions": []}')
    unreadable.chmod(0)  # No read permission

    try:
        with pytest.raises(PersistenceError, match="Permission denied"):
            load_scenario(unreadable)
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_disk_full_enospc_simulation_atomic_guarantee(tmp_path: Path):
    """
    Simulated ENOSPC (Disk Full) during write:
    1. save_scenario raises PersistenceError.
    2. Existing original scenario file remains UNTOUCHED and uncorrupted.
    3. Temporary files are cleaned up.
    """
    file_path = tmp_path / "critical_scenario.json"
    original = ScenarioModel(
        name="Original Valid Macro",
        actions=[ActionBlock(id="crit-1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=500))],
    )
    save_scenario(original, file_path)
    assert file_path.exists()

    # Create an updated version to save
    updated = ScenarioModel(
        name="Updated Macro Should Fail",
        actions=[ActionBlock(id="crit-2", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=999))],
    )

    real_open = open

    def failing_write_open(*args, **kwargs):
        f = real_open(*args, **kwargs)
        mode = args[1] if len(args) > 1 else kwargs.get("mode", "r")
        if "w" in mode:
            def enospc_write(content):
                raise OSError(errno.ENOSPC, "No space left on device")
            f.write = enospc_write
        return f

    with patch("builtins.open", side_effect=failing_write_open):
        with pytest.raises(PersistenceError, match="No space left on device"):
            save_scenario(updated, file_path)

    # 1. Verify original file content is still intact
    reloaded = load_scenario(file_path)
    assert reloaded.name == "Original Valid Macro"
    assert reloaded.actions[0].id == "crit-1"

    # 2. Verify no stray temp files remain
    temp_files = list(tmp_path.glob("*.tmp*"))
    assert len(temp_files) == 0, f"Stray temp files remained after ENOSPC: {temp_files}"


# ===========================================================================
# Suite 6: Multi-Threaded Concurrent Read/Write & Race Condition Stress
# ===========================================================================

def test_concurrent_multithreaded_readers_and_writers(tmp_path: Path):
    """
    Stress concurrency: 4 writer threads and 6 reader threads operating
    concurrently on the same scenario file. Verifies zero torn reads,
    zero unhandled exceptions, and strict atomic file swapping.
    """
    file_path = tmp_path / "concurrent_stress.json"
    initial = ScenarioModel(
        name="Initial Scenario",
        actions=[ActionBlock(id="init-1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=100))],
    )
    save_scenario(initial, file_path)

    stop_event = threading.Event()
    read_errors = []
    write_errors = []
    stats = {"reads": 0, "writes": 0}
    lock = threading.Lock()

    def writer_thread(tid: int):
        for i in range(40):
            if stop_event.is_set():
                break
            try:
                scen = ScenarioModel(
                    name=f"Writer-{tid}-Iteration-{i}",
                    loop_count=i + 1,
                    actions=[
                        ActionBlock(
                            id=f"w_{tid}_{j}",
                            type=ActionType.FIXED_WAIT,
                            params=FixedWaitParams(duration_ms=(j + 1) * 10),
                        )
                        for j in range(random.randint(1, 15))
                    ],
                )
                save_scenario(scen, file_path)
                with lock:
                    stats["writes"] += 1
            except Exception as e:
                with lock:
                    write_errors.append((tid, i, str(e)))
            time.sleep(0.001)

    def reader_thread(tid: int):
        while not stop_event.is_set():
            try:
                scen = load_scenario(file_path)
                assert scen.name.startswith(("Initial", "Writer-"))
                assert len(scen.actions) >= 1
                with lock:
                    stats["reads"] += 1
            except Exception as e:
                with lock:
                    read_errors.append((tid, str(e)))
            time.sleep(0.0005)

    threads = []
    for w in range(4):
        threads.append(threading.Thread(target=writer_thread, args=(w,)))
    for r in range(6):
        threads.append(threading.Thread(target=reader_thread, args=(r,)))

    for t in threads:
        t.start()

    # Wait for writers to complete
    for t in threads[:4]:
        t.join()

    # Signal readers to stop and wait
    stop_event.set()
    for t in threads[4:]:
        t.join()

    assert len(write_errors) == 0, f"Concurrent write errors encountered: {write_errors}"
    assert len(read_errors) == 0, f"Concurrent read errors encountered: {read_errors}"
    assert stats["writes"] == 160, f"Expected 160 writes, got {stats['writes']}"
    assert stats["reads"] > 300, f"Expected >300 reads, got {stats['reads']}"


# ===========================================================================
# Standalone CLI Runner
# ===========================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING ADVERSARIAL STRESS TEST HARNESS (adversarial_test.py)")
    print("=" * 70)
    # Run pytest directly against this file
    retcode = pytest.main(["-v", __file__])
    sys.exit(retcode)
