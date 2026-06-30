# Floating Window Topmost Lock Design

## Context

AdbPilot's Tkinter GUI already supports device locking. When a device is locked, GUI actions target that device and the floating window only displays that device. The floating window is also currently always created with Tk's `-topmost` attribute enabled.

The requested change is a separate floating-window layer lock: users can toggle whether the floating window stays above other windows, and the choice is remembered across app restarts. This must not change the existing device-lock behavior.

## Product Behavior

- The floating window has a persistent "topmost lock" state.
- Default state is locked/on, preserving the current always-on-top behavior for existing users.
- When locked/on, the floating window applies `attributes("-topmost", True)` and raises itself when shown.
- When unlocked/off, the floating window applies `attributes("-topmost", False)` and behaves like a normal floating window.
- The floating window visibly shows the state in the upper-right status pill:
  - Locked/on: `置顶已锁`
  - Unlocked/off: `未置顶`
- The right-click floating menu includes a dynamic item:
  - Locked/on: `取消置顶锁定`
  - Unlocked/off: `置顶锁定`
- The dynamic topmost lock is separate from the existing `锁定当前设备` and `解除锁定` menu items.

## macOS UI Direction

- Use platform-aware fonts:
  - macOS: `-apple-system`, `SF Pro Text`, and `PingFang SC` style font choices via Tk-compatible family names.
  - Windows keeps the current `Microsoft YaHei UI` defaults.
  - Linux uses a common sans fallback.
- Keep the floating window compact and mac-friendly:
  - Remove reliance on the visible magenta transparent-color border on macOS.
  - Use a light rounded card, subtle border, and softer shadow.
  - Keep the right-click menu entry visible but de-emphasized.
- Avoid fake macOS traffic-light controls in the borderless floating window because they imply native close/minimize behavior that the floating window does not provide.

## Implementation Shape

- Add small helper functions for platform UI constants:
  - `system_name()`
  - `ui_font(size, weight=None)`
  - `mono_font(size)`
- Store topmost lock under the existing GUI config object:
  - `floating_window.x`
  - `floating_window.y`
  - `floating_window.topmost_locked`
- Add methods on `AdbPilotGui`:
  - `_floating_topmost_locked() -> bool`
  - `_set_floating_topmost_locked(locked: bool) -> None`
  - `_toggle_floating_topmost_lock() -> None`
  - `_apply_floating_topmost_lock() -> None`
- Update `_save_floating_window_position()` so saving position preserves `topmost_locked`.
- Update `_build_floating_menu()` or refresh it before popup so the topmost action label reflects current state.
- Update `_draw_floating_sphere()` to render the lock state pill.

## Error Handling

- Tk attributes can be platform-dependent. Calls to `attributes("-topmost", ...)` remain wrapped or isolated so a `tk.TclError` does not break the GUI.
- If the config file is missing or malformed, `load_gui_config()` continues returning `{}` and the default topmost lock is on.
- If `floating_window.topmost_locked` exists but is not a boolean, treat it as on for compatibility and safety.

## Testing

- Add helper-level tests for:
  - default topmost lock config resolves to `True`
  - stored `False` resolves to `False`
  - saving floating position preserves `topmost_locked`
  - platform font helper returns macOS-friendly families when platform is Darwin
- Existing GUI helper tests continue to cover config round-trip and device display behavior.
