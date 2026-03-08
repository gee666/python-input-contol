from __future__ import annotations

from dataclasses import dataclass, field

from python_input_control.backends import BackendExecutionContext
from python_input_control.backends.pynput_keyboard import (
    CharacterKeyPlan,
    KeyboardKey,
    PynputKeyboardBackend,
    keyboard_key_for_modifier,
    plan_character_key,
)
from python_input_control.models import BrowserContext, KeyEscapeCommand, KeyTabCommand, ModifierKey, SelectAllAndDeleteCommand, TypeCommand
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


def test_press_tab_taps_tab_key() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()

    backend.press_tab(KeyTabCommand(id="tab-1", context=_browser_context()), _runtime(FakeRandom(), sleep))

    assert sink.events == [
        ("press_key", KeyboardKey.TAB),
        ("release_key", KeyboardKey.TAB),
    ]
    assert sleep.calls == []


def test_press_escape_taps_escape_key() -> None:
    sink = RecordingSink()
    backend = PynputKeyboardBackend(sink_factory=lambda: sink)
    sleep = RecordingSleep()

    backend.press_escape(KeyEscapeCommand(id="esc-1", context=_browser_context()), _runtime(FakeRandom(), sleep))

    assert sink.events == [
        ("press_key", KeyboardKey.ESCAPE),
        ("release_key", KeyboardKey.ESCAPE),
    ]
    assert sleep.calls == []


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


def test_select_all_and_delete_uses_modifier_specific_combo() -> None:
    for modifier, expected_key in (
        (ModifierKey.CONTROL, KeyboardKey.CONTROL),
        (ModifierKey.COMMAND, KeyboardKey.COMMAND),
    ):
        sink = RecordingSink()
        backend = PynputKeyboardBackend(sink_factory=lambda sink=sink: sink)
        runtime = _runtime(FakeRandom(uniform_values=[80.0]), sleep=RecordingSleep())

        backend.select_all_and_delete(
            SelectAllAndDeleteCommand(id="clear-1", context=_browser_context()),
            modifier,
            runtime,
        )

        assert keyboard_key_for_modifier(modifier) == expected_key
        assert sink.events == [
            ("press_key", expected_key),
            ("press_character", "a"),
            ("release_character", "a"),
            ("release_key", expected_key),
            ("press_key", KeyboardKey.DELETE),
            ("release_key", KeyboardKey.DELETE),
        ]
        assert runtime.sleep.calls == [0.08]
