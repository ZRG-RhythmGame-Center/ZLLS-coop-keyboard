"""检测按键在 pynput 中的代码，输出可直接用于 config bindings 的键名。按 Ctrl+C 退出。"""

from __future__ import annotations

import sys

from pynput import keyboard


def _normalize_key(key) -> str:
    """与项目 keyboard_events._normalize_key 一致：用于 config 的键名字符串。"""
    if key is None:
        return ""
    if isinstance(key, str):
        return key
    char = getattr(key, "char", None)
    if char is not None:
        return char
    name = getattr(key, "name", None)
    if name is not None:
        name = str(name)
        return name if name.startswith("Key") else f"Key{name}"
    return str(key)


def _is_modifier(key) -> str | None:
    """若为修饰键返回 ctrl/alt/shift/meta，否则 None。"""
    name = getattr(key, "name", None)
    if name is None:
        return None
    n = str(name).lower()
    if n in ("ctrl", "control", "ctrl_l", "ctrl_r"):
        return "ctrl"
    if n in ("alt", "alt_l", "alt_r", "alt_gr"):
        return "alt"
    if n in ("shift", "shift_l", "shift_r"):
        return "shift"
    if n in ("cmd", "cmd_l", "cmd_r", "meta", "win"):
        return "meta"
    return None


def main() -> int:
    pressed_modifiers: set[str] = set()

    def on_press(key) -> None:
        mod = _is_modifier(key)
        if mod:
            pressed_modifiers.add(mod)
            return
        norm = _normalize_key(key)
        if pressed_modifiers:
            mod_str = "+".join(
                sorted(pressed_modifiers, key=lambda x: ("ctrl", "alt", "shift", "meta").index(x))
            )
            trigger = f"{mod_str}+{norm}"
        else:
            trigger = norm
        print(f"  键名( config ) = {norm!r}  触发键( bindings ) = {trigger!r}  [pynput: {key!r}]")

    def on_release(key) -> None:
        mod = _is_modifier(key)
        if mod:
            pressed_modifiers.discard(mod)

    print("按键检测（用于编写 config listen_keys / bindings）")
    print("按任意键会打印「键名」与「触发键」；修饰键单独按下不打印。Ctrl+C 退出。\n")
    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
