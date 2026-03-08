from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from python_input_control.backends import BackendExecutionContext
from python_input_control.backends.pynput_keyboard import (
    CharacterKeyPlan,
    KeyboardKey,
    PynputKeyboardBackend,
    PynputKeyboardSink,
    normalize_key_name,
    plan_character_key,
)
from python_input_control.models import BrowserContext, PressKeyCommand, PressShortcutCommand, TypeCommand
from python_input_control.platform import SystemPlatformAdapter


def _browser_context() -> BrowserContext:
    return BrowserContext(
        screen_x=0,
        screen_y=0,
        outer_height=900,
        inner_height=800,
        outer_width=1200,
        inner_width=1200,
        device_pixel_ratio=1.0,
        scroll_x=0,
        scroll_y=0,
    )


@dataclass
class FakeRandom:
    uniform_values: list[float] = field(default_factory=list)
    gauss_values: list[float] = field(default_factory=list)
    random_values: list[float] = field(default_factory=list)

    def random(self) -> float:
        if self.random_values:
            return self.random_values.pop(0)
        return 1.0

    def uniform(self, a: float, b: float) -> float:
        if self.uniform_values:
            return self.uniform_values.pop(0)
        return (a + b) / 2.0

    def gauss(self, mu: float, sigma: float) -> float:
        if self.gauss_values:
            return self.gauss_values.pop(0)
        return mu

    def randint(self, a: int, b: int) -> int:
        return a


@dataclass
class RecordingSink:
    events: list[tuple[str, object]] = field(default_factory=list)

    def press_key(self, key: KeyboardKey) -> None:
        self.events.append(("press_key", key))

    def release_key(self, key: KeyboardKey) -> None:
        self.events.append(("release_key", key))

    def press_character(self, character: str) -> None:
        self.events.append(("press_character", character))

    def release_character(self, character: str) -> None:
        self.events.append(("release_character", character))

    def press_special_key(self, key_name: str) -> None:
        self.events.append(("press_special_key", key_name))

    def release_special_key(self, key_name: str) -> None:
        self.events.append(("release_special_key", key_name))


@dataclass
class FailingSpecialKeySink(RecordingSink):
    failing_key_name: str = "page_down"

    def press_special_key(self, key_name: str) -> None:
        super().press_special_key(key_name)
        if key_name == self.failing_key_name:
            raise RuntimeError("failed to press special key")


@dataclass
class RecordingSleep:
    calls: list[float] = field(default_factory=list)

    def __call__(self, delay_seconds: float) -> None:
        self.calls.append(delay_seconds)



def _runtime(rng: FakeRandom, sleep: RecordingSleep) -> BackendExecutionContext:
    return BackendExecutionContext(platform=SystemPlatformAdapter(), rng=rng, sleep=sleep)


def test_plan_character_key_uses_shift_for_uppercase_and_symbols() -> None:
    assert plan_character_key("A") == CharacterKeyPlan(character="a", requires_shift=True)
    assert plan_character_key("!") == CharacterKeyPlan(character="1", requires_shift=True)


def test_plan_character_key_preserves_unicode_without_shift() -> None:
    assert plan_character_key("é") == CharacterKeyPlan(character="é", requires_shift=False)
    assert plan_character_key("\n") == CharacterKeyPlan(special_key=KeyboardKey.ENTER)


@pytest.mark.parametrize(
    ("raw_key", "expected"),
    [
        ("ctrl", "control"),
        ("esc", "escape"),
        ("page-down", "page_down"),
        ("Page Down", "page_down"),
        ("cmd", "command"),
        ("meta", "command"),
        ("option", "alt"),
        ("arrowleft", "left"),
        ("prtsc", "print_screen"),
    ],
)
def test_normalize_key_name_handles_common_aliases(raw_key: str, expected: str) -> None:
    assert normalize_key_name(raw_key) == expected


def test_press_key_taps_special_key_with_repeat() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()

    backend.press_key(PressKeyCommand(id="tab-1", context=_browser_context(), key="tab", repeat=2), _runtime(FakeRandom(), sleep))

    assert sink.events == [
        ("press_key", KeyboardKey.TAB),
        ("release_key", KeyboardKey.TAB),
        ("press_key", KeyboardKey.TAB),
        ("release_key", KeyboardKey.TAB),
    ]
    assert sleep.calls == []


def test_press_key_supports_alias_special_key_names() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)

    backend.press_key(PressKeyCommand(id="esc-1", context=_browser_context(), key="esc", repeat=1), _runtime(FakeRandom(), RecordingSleep()))

    assert sink.events == [
        ("press_key", KeyboardKey.ESCAPE),
        ("release_key", KeyboardKey.ESCAPE),
    ]


def test_press_key_taps_single_character() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)

    backend.press_key(PressKeyCommand(id="char-1", context=_browser_context(), key="A", repeat=1), _runtime(FakeRandom(), RecordingSleep()))

    assert sink.events == [
        ("press_key", KeyboardKey.SHIFT),
        ("press_character", "a"),
        ("release_character", "a"),
        ("release_key", KeyboardKey.SHIFT),
    ]


def test_press_key_taps_single_whitespace_character_as_special_key() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)

    backend.press_key(PressKeyCommand(id="space-1", context=_browser_context(), key=" ", repeat=1), _runtime(FakeRandom(), RecordingSleep()))

    assert sink.events == [
        ("press_key", KeyboardKey.SPACE),
        ("release_key", KeyboardKey.SPACE),
    ]


def test_press_key_supports_non_enum_special_keys() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)

    backend.press_key(PressKeyCommand(id="page-down-1", context=_browser_context(), key="Page Down", repeat=1), _runtime(FakeRandom(), RecordingSleep()))

    assert sink.events == [
        ("press_special_key", "page_down"),
        ("release_special_key", "page_down"),
    ]


def test_press_shortcut_holds_modifiers_and_taps_last_key() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()
    runtime = _runtime(FakeRandom(uniform_values=[80.0]), sleep)

    backend.press_shortcut(
        PressShortcutCommand(id="shortcut-1", context=_browser_context(), keys=("control", "shift", "p")),
        runtime,
    )

    assert sink.events == [
        ("press_key", KeyboardKey.CONTROL),
        ("press_key", KeyboardKey.SHIFT),
        ("press_character", "p"),
        ("release_character", "p"),
        ("release_key", KeyboardKey.SHIFT),
        ("release_key", KeyboardKey.CONTROL),
    ]
    assert sleep.calls == [0.08]


def test_press_shortcut_accepts_aliases_for_modifiers_and_terminal_key() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()

    backend.press_shortcut(
        PressShortcutCommand(id="shortcut-2", context=_browser_context(), keys=("ctrl", "cmd", "esc")),
        _runtime(FakeRandom(uniform_values=[60.0]), sleep),
    )

    assert sink.events == [
        ("press_key", KeyboardKey.CONTROL),
        ("press_key", KeyboardKey.COMMAND),
        ("press_key", KeyboardKey.ESCAPE),
        ("release_key", KeyboardKey.ESCAPE),
        ("release_key", KeyboardKey.COMMAND),
        ("release_key", KeyboardKey.CONTROL),
    ]
    assert sleep.calls == [0.06]


def test_press_shortcut_releases_already_held_keys_when_pressing_later_key_fails() -> None:
    sink = FailingSpecialKeySink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)

    with pytest.raises(RuntimeError, match="failed to press special key"):
        backend.press_shortcut(
            PressShortcutCommand(id="shortcut-3", context=_browser_context(), keys=("control", "Page Down", "a")),
            _runtime(FakeRandom(), RecordingSleep()),
        )

    assert sink.events == [
        ("press_key", KeyboardKey.CONTROL),
        ("press_special_key", "page_down"),
        ("release_key", KeyboardKey.CONTROL),
    ]


def test_type_text_emits_shift_sequences_and_direct_unicode() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()
    runtime = _runtime(FakeRandom(), sleep)

    backend.type_text(TypeCommand(id="type-1", context=_browser_context(), text="A@é", wpm=80.0), runtime)

    assert sink.events == [
        ("press_key", KeyboardKey.SHIFT),
        ("press_character", "a"),
        ("release_character", "a"),
        ("release_key", KeyboardKey.SHIFT),
        ("press_key", KeyboardKey.SHIFT),
        ("press_character", "2"),
        ("release_character", "2"),
        ("release_key", KeyboardKey.SHIFT),
        ("press_character", "é"),
        ("release_character", "é"),
    ]
    assert sleep.calls == [0.15, 0.15]


def test_type_text_adds_extra_pause_after_space() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()
    runtime = _runtime(FakeRandom(random_values=[0.0], uniform_values=[200.0]), sleep)

    backend.type_text(TypeCommand(id="type-2", context=_browser_context(), text="a b", wpm=80.0), runtime)

    assert sleep.calls == [0.15, 0.35]
    assert sink.events[-2:] == [
        ("press_character", "b"),
        ("release_character", "b"),
    ]


def test_pynput_keyboard_sink_resolves_alias_special_keys() -> None:
    controller = SimpleNamespace(press=lambda _key: None, release=lambda _key: None)
    key_namespace = SimpleNamespace(esc="ESC", ctrl="CTRL", page_down="PGDN", print_screen="PRTSC")
    sink = PynputKeyboardSink(controller=controller, key_namespace=key_namespace)

    assert sink._resolve_special_key("esc") == "ESC"
    assert sink._resolve_special_key("ctrl") == "CTRL"
    assert sink._resolve_special_key("Page Down") == "PGDN"
    assert sink._resolve_special_key("prtsc") == "PRTSC"
