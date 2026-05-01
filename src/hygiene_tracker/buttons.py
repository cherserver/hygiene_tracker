from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .config import ButtonConfig


@dataclass(frozen=True)
class ButtonActions:
    up: Callable[[], None]
    down: Callable[[], None]
    done: Callable[[], None]
    menu: Callable[[], None]


class ButtonController:
    def __init__(self, config: ButtonConfig, actions: ButtonActions) -> None:
        self.config = config
        self.actions = actions
        self._buttons: list[object] = []
        self.available = False
        self.error: Exception | None = None

    def start(self) -> None:
        try:
            from gpiozero import Button

            mapping = [
                (self.config.up, self.actions.up),
                (self.config.down, self.actions.down),
                (self.config.done, self.actions.done),
                (self.config.menu, self.actions.menu),
            ]
            for pin, callback in mapping:
                button = Button(pin, bounce_time=self.config.bounce_time)
                button.when_pressed = callback
                self._buttons.append(button)
            self.available = True
        except Exception as exc:
            self.error = exc
            self.available = False

    def close(self) -> None:
        for button in self._buttons:
            close = getattr(button, "close", None)
            if close is not None:
                close()
        self._buttons.clear()

