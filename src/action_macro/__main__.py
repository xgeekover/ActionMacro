"""
action_macro.__main__
=========================
Command-line entry point supporting `python -m action_macro` and headless execution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    """CLI and GUI launcher entry point."""
    parser = argparse.ArgumentParser(
        prog="ActionMacro",
        description="ActionMacro Automation Desktop Tool",
    )
    parser.add_argument(
        "scenario",
        nargs="?",
        default=None,
        help="Path to scenario JSON file to load on launch",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Execute scenario headlessly in background without opening GUI",
    )
    parser.add_argument(
        "--loops",
        type=int,
        default=None,
        help="Override scenario loop count for headless execution (0 for infinite)",
    )

    args = parser.parse_args(argv)

    if args.headless:
        if not args.scenario:
            print("Error: --headless requires a scenario file path", file=sys.stderr)
            return 1

        path = Path(args.scenario).resolve()
        if not path.is_file():
            print(f"Error: Scenario file not found: {path}", file=sys.stderr)
            return 1

        from action_macro.core.persistence import ScenarioPersistence
        from action_macro.engine.macos_keyboard import refresh_keyboard_layout_cache
        from action_macro.runner.execution_engine import ExecutionEngine
        from action_macro.runner.hotkey_listener import GlobalHotkeyListener

        scenario = ScenarioPersistence.load(path)
        if args.loops is not None:
            scenario.loop_count = args.loops

        refresh_keyboard_layout_cache()  # main thread; pynput's listener thread reuses it (macOS)
        print(f"Executing '{scenario.name}' headlessly ({scenario.loop_count} loops)...")
        engine = ExecutionEngine(scenario=scenario, base_dir=path.parent)
        hotkey_listener = GlobalHotkeyListener(engine=engine)
        try:
            hotkey_listener.start()
        except Exception as exc:
            print(f"Notice: Hotkey listener failed to start: {exc}", file=sys.stderr)

        try:
            success = engine.run()
            print("Execution finished successfully." if success else "Execution stopped/aborted.")
            return 0 if success else 2
        finally:
            try:
                hotkey_listener.stop()
            except Exception:
                pass

    # GUI Mode
    from action_macro.gui.app import run_app
    return run_app(scenario_path=args.scenario)


if __name__ == "__main__":
    sys.exit(main())
