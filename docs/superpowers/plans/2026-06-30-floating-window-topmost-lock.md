# Floating Window Topmost Lock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent, user-toggleable floating-window topmost lock and improve macOS UI styling without changing existing device-lock behavior.

**Architecture:** Keep changes scoped to the existing Tkinter GUI module because the application already centralizes desktop UI there. Add helper functions for platform-aware fonts and floating-window config parsing so behavior can be tested without launching a full GUI. The floating window reads and writes `floating_window.topmost_locked` alongside its saved position.

**Tech Stack:** Python 3.9+, Tkinter/ttk, unittest, existing JSON GUI config helpers.

---

## File Structure

- Modify `adbpilot/gui.py`: add platform-aware font helpers, floating topmost config helpers, topmost toggle methods, dynamic menu label, and refined floating window rendering.
- Modify `tests/test_gui_helpers.py`: add unit tests for topmost config defaults, topmost config persistence, and macOS font helper behavior.
- Modify `readme.md`: update GUI documentation so the floating window behavior is discoverable.

## Task 1: Test Helper Behavior

**Files:**
- Modify: `tests/test_gui_helpers.py`
- Modify: `adbpilot/gui.py`

- [x] **Step 1: Write failing helper tests**

Add these imports in `tests/test_gui_helpers.py`:

```python
from adbpilot.gui import (
    device_connection_summary,
    device_connection_type,
    device_display_name,
    floating_topmost_locked_from_config,
    gui_config_path,
    load_gui_config,
    mono_font,
    save_gui_config,
    ui_font,
)
```

Add these tests to `GuiHelperTests`:

```python
    def test_floating_topmost_lock_defaults_to_enabled(self):
        self.assertTrue(floating_topmost_locked_from_config({}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {}}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": "false"}}))

    def test_floating_topmost_lock_reads_boolean_false(self):
        self.assertFalse(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": False}}))
        self.assertTrue(floating_topmost_locked_from_config({"floating_window": {"topmost_locked": True}}))

    def test_platform_fonts_are_macos_friendly(self):
        self.assertEqual(ui_font(12, platform_name="darwin"), ("SF Pro Text", 12))
        self.assertEqual(ui_font(12, "bold", platform_name="darwin"), ("SF Pro Text", 12, "bold"))
        self.assertEqual(mono_font(11, platform_name="darwin"), ("Menlo", 11))
```

- [x] **Step 2: Run tests to verify red**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: FAIL because `floating_topmost_locked_from_config`, `ui_font`, and `mono_font` do not exist yet.

- [x] **Step 3: Implement minimal helper functions**

In `adbpilot/gui.py`, add these functions near the module constants:

```python
def system_name() -> str:
    return platform.system().lower()


def ui_font(size: int, weight: str | None = None, *, platform_name: str | None = None) -> tuple[Any, ...]:
    name = (platform_name or system_name()).lower()
    if name == "darwin":
        family = "SF Pro Text"
    elif name == "windows":
        family = "Microsoft YaHei UI"
    else:
        family = "Noto Sans CJK SC"
    return (family, size, weight) if weight else (family, size)


def mono_font(size: int, *, platform_name: str | None = None) -> tuple[str, int]:
    name = (platform_name or system_name()).lower()
    if name == "darwin":
        return ("Menlo", size)
    if name == "windows":
        return ("Consolas", size)
    return ("DejaVu Sans Mono", size)


def floating_topmost_locked_from_config(config: dict[str, Any]) -> bool:
    floating_window = config.get("floating_window", {})
    if not isinstance(floating_window, dict):
        return True
    value = floating_window.get("topmost_locked", True)
    return value if isinstance(value, bool) else True
```

- [x] **Step 4: Run tests to verify green**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS.

## Task 2: Implement Floating Topmost Toggle

**Files:**
- Modify: `adbpilot/gui.py`
- Modify: `tests/test_gui_helpers.py`

- [x] **Step 1: Write failing persistence test**

Add this test to `tests/test_gui_helpers.py`:

```python
    def test_floating_window_config_preserves_topmost_lock(self):
        with TemporaryDirectory() as temp_dir:
            with patch("pathlib.Path.home", return_value=Path(temp_dir)):
                save_gui_config({"floating_window": {"x": 12, "y": 34, "topmost_locked": False}})

                loaded = load_gui_config()

                self.assertFalse(floating_topmost_locked_from_config(loaded))
                self.assertEqual(loaded["floating_window"], {"x": 12, "y": 34, "topmost_locked": False})
```

- [x] **Step 2: Run tests to verify red or baseline coverage**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS if the config round-trip already preserves unknown fields. If it passes, keep this as regression coverage and continue because the missing production behavior is in GUI methods that are hard to instantiate headlessly.

- [x] **Step 3: Add topmost methods and config writes**

In `AdbPilotGui`, add:

```python
    def _floating_topmost_locked(self) -> bool:
        return floating_topmost_locked_from_config(self.gui_config)

    def _set_floating_topmost_locked(self, locked: bool) -> None:
        floating_window = self.gui_config.get("floating_window", {})
        if not isinstance(floating_window, dict):
            floating_window = {}
        floating_window["topmost_locked"] = locked
        self.gui_config["floating_window"] = floating_window
        save_gui_config(self.gui_config)
        self._apply_floating_topmost_lock()
        self._render_floating_devices()

    def _toggle_floating_topmost_lock(self) -> None:
        self._set_floating_topmost_locked(not self._floating_topmost_locked())

    def _apply_floating_topmost_lock(self) -> None:
        if self.floating_window is None or not self.floating_window.winfo_exists():
            return
        try:
            self.floating_window.attributes("-topmost", self._floating_topmost_locked())
        except tk.TclError:
            return
        if self._floating_topmost_locked():
            self.floating_window.lift()
```

Update `_show_floating_window()`:

```python
            self._apply_floating_window_chrome()
            self._apply_floating_topmost_lock()
```

Update the existing unconditional `self.floating_window.attributes("-topmost", True)` call by removing it.

Update `_save_floating_window_position()`:

```python
        floating_window = self.gui_config.get("floating_window", {})
        if not isinstance(floating_window, dict):
            floating_window = {}
        floating_window.update({
            "x": int(self.floating_window.winfo_x()),
            "y": int(self.floating_window.winfo_y()),
            "topmost_locked": self._floating_topmost_locked(),
        })
        self.gui_config["floating_window"] = floating_window
```

- [x] **Step 4: Run tests**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS.

## Task 3: Dynamic Floating Menu and Visual State

**Files:**
- Modify: `adbpilot/gui.py`

- [x] **Step 1: Add dynamic menu item**

Update `_build_floating_menu()` so the first section includes:

```python
        self.floating_menu.add_command(label=self._floating_topmost_menu_label(), command=self._toggle_floating_topmost_lock)
```

Add:

```python
    def _floating_topmost_menu_label(self) -> str:
        return "取消置顶锁定" if self._floating_topmost_locked() else "置顶锁定"
```

Update `_show_floating_menu()` before `tk_popup`:

```python
        self.floating_menu.entryconfigure(1, label=self._floating_topmost_menu_label())
```

- [x] **Step 2: Add visible lock pill**

In `_draw_floating_sphere()`, compute:

```python
        lock_label = "置顶已锁" if self._floating_topmost_locked() else "未置顶"
        lock_bg = "#dbeafe" if self._floating_topmost_locked() else "#e2e8f0"
        lock_fg = "#1d4ed8" if self._floating_topmost_locked() else "#475569"
```

Draw a small rounded pill in the upper-right and shift the footer/status text down so the connection footer does not collide with it.

- [x] **Step 3: Run tests**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS.

## Task 4: macOS UI Polish

**Files:**
- Modify: `adbpilot/gui.py`

- [x] **Step 1: Replace hard-coded fonts**

Replace hard-coded UI font tuples in `_configure_style()`, `_style_text()`, `_draw_floating_sphere()`, and `_build_floating_menu()` with `ui_font(...)` or `mono_font(...)`.

- [x] **Step 2: Add platform-specific floating chrome**

Add:

```python
    def _floating_transparent_color(self) -> str:
        return "#f3f6fb" if system_name() == "darwin" else "#ff00ff"

    def _apply_floating_window_chrome(self) -> None:
        if self.floating_window is None:
            return
        transparent_color = self._floating_transparent_color()
        self.floating_window.configure(bg=transparent_color)
        try:
            self.floating_window.attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            pass
```

Use `transparent_color = self._floating_transparent_color()` when creating `self.floating_canvas`.

- [x] **Step 3: Soften floating drawing**

In `_draw_floating_sphere()`, use a lighter mac-friendly shadow and smaller radius:

```python
        shadow_color = "#94a3b8" if system_name() == "darwin" else "#0f172a"
        shadow_stipple = "gray25" if system_name() == "darwin" else "gray50"
```

Keep warning and connected colors unchanged enough that existing state recognition still works.

- [x] **Step 4: Run tests**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS.

## Task 5: Documentation and Full Verification

**Files:**
- Modify: `readme.md`

- [x] **Step 1: Update README**

Update the GUI floating-window bullets to mention:

```markdown
- 悬浮窗模式：简洁状态条，显示已连接设备名、连接类型（USB 或 Wi-Fi）和状态，多设备时展示所有在线设备摘要；支持置顶锁定开关，并记忆上次置顶状态。
```

Update the paragraph describing floating behavior:

```markdown
悬浮窗所有操作通过右键菜单触发，双击浮窗可回到主界面，位置和置顶锁定状态会记忆到下次打开。
```

- [x] **Step 2: Run focused tests**

Run:

```bash
python -m unittest tests.test_gui_helpers
```

Expected: PASS.

- [x] **Step 3: Run full tests**

Run:

```bash
python -m unittest discover -s tests
```

Expected: PASS.

- [x] **Step 4: Review diff**

Run:

```bash
git diff -- adbpilot/gui.py tests/test_gui_helpers.py readme.md
```

Expected: diff only contains topmost-lock, platform font, floating UI, test, and README changes.

## Task 6: Full-Test Environment Isolation Fix

**Files:**
- Modify: `adbpilot/platform_adapter.py`

- [x] **Step 1: Reproduce the full-test failure**

Run:

```bash
python -m unittest discover -s tests
```

Observed: `test_packaged_adb_candidate_from_pyinstaller_root` failed because `ANDROID_HOME` from the real shell environment was used even though the test created `PlatformAdapter(env={})`.

- [x] **Step 2: Identify root cause**

`PlatformAdapter.__init__` used `self.env = env or os.environ`, so an explicit empty dict was treated the same as no argument. This broke test isolation and made real machine state override packaged adb candidates.

- [x] **Step 3: Implement minimal fix**

Change:

```python
self.env = env or os.environ
```

to:

```python
self.env = env if env is not None else os.environ
```

- [x] **Step 4: Verify focused and full tests**

Run:

```bash
python -m unittest tests.test_platform_adapter tests.test_gui_helpers
python -m unittest discover -s tests
```

Expected: PASS.

## Task 7: Native macOS Floating Helper

**Files:**
- Add: `adbpilot/macos_floating_helper.swift`
- Modify: `adbpilot/gui.py`
- Modify: `tests/test_gui_helpers.py`
- Modify: `packaging/macos/build_macos.sh`
- Modify: `packaging/macos/AdbPilot.spec`

- [x] **Step 1: Identify why Tk topmost is insufficient**

macOS can reorder Tk `-topmost`/`MacWindowStyle` windows behind other apps after activation changes. Codex pet uses a native overlay module backed by AppKit/CoreGraphics, so AdbPilot needs a native macOS floating panel for reliable always-on-top behavior.

- [x] **Step 2: Add testable Python helper boundaries**

Add tests for the native helper source path, cached binary path, and rebuild detection.

- [x] **Step 3: Add Swift/AppKit helper**

Implement a borderless non-activating `NSPanel` with screen-saver-level locked mode, all-space/full-screen collection behavior, simple native drawing, drag persistence events, double-click restore, and right-click actions over JSON lines.

- [x] **Step 4: Wire GUI lifecycle**

Start the Swift helper on macOS when entering floating mode, sync device/status/topmost state over stdin, process helper events on the Tk result queue, persist moved positions, stop the helper on restore/quit, and keep the Tk floating window as fallback.

- [x] **Step 5: Package helper**

Compile `AdbPilotFloatingHelper` during macOS packaging and include it in the PyInstaller bundle.

- [x] **Step 6: Verify**

Run:

```bash
swiftc adbpilot/macos_floating_helper.swift -o /tmp/AdbPilotFloatingHelper
python -m unittest tests.test_gui_helpers
python -m unittest discover -s tests
python -m py_compile adbpilot/gui.py
```

Expected: PASS.

- [x] **Step 7: Fix drag jitter**

Root cause: drag handling used window-local mouse coordinates while the window origin was changing, and regular status refreshes resent the last saved `x/y`, which could pull the panel back during an active drag.

Fix: Swift helper now drags from global mouse coordinates and ignores external `x/y` updates while dragging. Python sends coordinates only for the initial helper placement; later state updates omit position.
