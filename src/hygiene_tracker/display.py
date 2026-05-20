from __future__ import annotations

from datetime import date
import os
import textwrap
import time

from .models import TaskStatus
from .schedule import DueState


STATUS_COLORS = {
    DueState.OK: (88, 216, 154),
    DueState.DUE_SOON: (88, 216, 154),
    DueState.DUE_TODAY: (239, 190, 104),
    DueState.OVERDUE: (238, 94, 103),
}

BG = (6, 10, 12)
SURFACE = (18, 29, 30)
SURFACE_RAISED = (28, 43, 42)
SURFACE_SELECTED = (41, 61, 59)
TEXT = (232, 241, 236)
TEXT_MUTED = (172, 188, 184)
TEXT_SOFT = (215, 226, 222)
LINE = (82, 108, 103)
WARNING = (239, 190, 104)
CLEAN_BAR = (88, 216, 154)
USED_BAR = (224, 188, 116)
PLATE_HIGHLIGHT = (70, 96, 91)


def create_dashboard(
    *,
    backend: str,
    width: int,
    height: int,
    fullscreen: bool,
    video_driver: str = "",
    framebuffer: str = "",
    display: str = "",
    button_label_centers: tuple[int, int, int, int] = (56, 123, 190, 257),
):
    if backend == "fbdev":
        from .framebuffer_display import FramebufferDashboard

        return FramebufferDashboard(
            width=width,
            height=height,
            framebuffer=framebuffer,
            button_label_centers=button_label_centers,
        )
    if backend != "pygame":
        raise ValueError("display_backend must be 'pygame' or 'fbdev'")
    return PygameDashboard(
        width=width,
        height=height,
        fullscreen=fullscreen,
        video_driver=video_driver,
        framebuffer=framebuffer,
        display=display,
    )


class PygameDashboard:
    def __init__(
        self,
        width: int,
        height: int,
        fullscreen: bool,
        video_driver: str = "",
        framebuffer: str = "",
        display: str = "",
    ) -> None:
        os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        if video_driver:
            os.environ["SDL_VIDEODRIVER"] = video_driver
        if framebuffer:
            os.environ["SDL_FBDEV"] = framebuffer
        if display:
            os.environ["DISPLAY"] = display

        import pygame

        self.pygame = pygame
        pygame.init()
        pygame.font.init()
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((width, height), flags)
        pygame.mouse.set_visible(False)
        pygame.display.set_caption("Hygiene")
        print(
            "Hygiene display initialized: "
            f"driver={pygame.display.get_driver()} "
            f"size={self.screen.get_width()}x{self.screen.get_height()} "
            f"fullscreen={fullscreen}",
            flush=True,
        )
        self.clock = pygame.time.Clock()
        self.width = width
        self.height = height
        self.font_title = pygame.font.SysFont("DejaVu Sans", 20, bold=True)
        self.font_task = pygame.font.SysFont("DejaVu Sans", 17, bold=True)
        self.font_meta = pygame.font.SysFont("DejaVu Sans", 14)
        self.font_meta_bold = pygame.font.SysFont("DejaVu Sans", 14, bold=True)
        self.font_small = pygame.font.SysFont("DejaVu Sans", 12)
        self.font_tiny = pygame.font.SysFont("DejaVu Sans", 11)
        self.flash_message = ""
        self.flash_until = 0.0
        self.show_history = False

    def set_flash(self, message: str, seconds: float = 1.8) -> None:
        self.flash_message = message
        self.flash_until = time.monotonic() + seconds

    def toggle_history(self) -> None:
        self.show_history = not self.show_history

    def draw(
        self,
        statuses: list[TaskStatus],
        selected_index: int | None,
        history_lines: list[str] | None = None,
        gpio_available: bool = True,
    ) -> None:
        pygame = self.pygame
        self.screen.fill(BG)
        self._draw_header(gpio_available, statuses)
        if self.show_history:
            self._draw_history(history_lines or [])
        else:
            self._draw_tasks(statuses, selected_index)
        self._draw_footer()
        pygame.display.flip()

    def poll_events(self) -> list[str]:
        pygame = self.pygame
        actions: list[str] = []
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                actions.append("quit")
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    actions.append("quit")
                elif event.key in (pygame.K_UP, pygame.K_w):
                    actions.append("up")
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    actions.append("down")
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    actions.append("done")
                elif event.key in (pygame.K_TAB, pygame.K_h):
                    actions.append("menu")
        return actions

    def tick(self, fps: int = 12) -> None:
        self.clock.tick(fps)

    def close(self) -> None:
        self.pygame.quit()

    def _draw_header(self, gpio_available: bool, statuses: list[TaskStatus]) -> None:
        pygame = self.pygame
        pygame.draw.rect(self.screen, SURFACE, (0, 0, self.width, 38))
        pygame.draw.line(self.screen, LINE, (0, 37), (self.width, 37), 1)

        title = self.font_title.render("Hygiene", True, TEXT)
        self.screen.blit(title, (12, 7))

        today_text = date.today().strftime("%a %b %d")
        today = self.font_meta_bold.render(today_text, True, TEXT_SOFT)
        pill_pad_x = 12
        pill_w = today.get_width() + pill_pad_x * 2
        pill_rect = (self.width - pill_w - 10, 8, pill_w, 22)
        pygame.draw.rect(self.screen, (31, 45, 47), pill_rect, border_radius=7)
        pygame.draw.rect(self.screen, LINE, pill_rect, width=1, border_radius=7)
        if statuses and all(status.due_state in (DueState.OK, DueState.DUE_SOON) for status in statuses):
            pygame.draw.rect(self.screen, CLEAN_BAR, pill_rect, width=1, border_radius=7)
        self.screen.blit(today, (pill_rect[0] + pill_pad_x, pill_rect[1] + 3))
        if not gpio_available:
            warn = self.font_tiny.render("GPIO fallback", True, WARNING)
            self.screen.blit(warn, (118, 24))

    def _draw_tasks(self, statuses: list[TaskStatus], selected_index: int | None) -> None:
        if not statuses:
            self._draw_empty("No active tasks", "Add tasks in the web UI")
            return

        top = 44
        bottom = self.height - 30
        gap = 4
        available = bottom - top
        row_height = max(32, (available - gap * (len(statuses) - 1)) // max(1, len(statuses)))
        for index, status in enumerate(statuses):
            y = top + index * (row_height + gap)
            self._draw_task_row(status, y, row_height, index == selected_index)

    def _draw_task_row(self, status: TaskStatus, y: int, height: int, selected: bool) -> None:
        pygame = self.pygame
        card = (8, y, self.width - 16, height)
        color = _status_color(status)
        bg = SURFACE_SELECTED if selected else SURFACE_RAISED
        border = color if selected else LINE
        shadow_rect = (card[0] + 1, card[1] + 2, card[2], card[3])
        pygame.draw.rect(self.screen, (1, 4, 5), shadow_rect, border_radius=7)
        if status.due_state == DueState.DUE_TODAY:
            glow = _mix(color, BG, 0.52)
            pygame.draw.rect(self.screen, glow, (card[0] + 2, card[1] + 4, 8, card[3] - 8), border_radius=4)
        pygame.draw.rect(self.screen, bg, card, border_radius=7)
        pygame.draw.rect(self.screen, border, card, width=2 if selected else 1, border_radius=7)
        pygame.draw.line(
            self.screen,
            PLATE_HIGHLIGHT if not selected else _mix(color, TEXT, 0.45),
            (card[0] + 16, card[1] + 1),
            (card[0] + card[2] - 16, card[1] + 1),
            1,
        )
        pygame.draw.rect(self.screen, color, (card[0] + 8, card[1] + 9, 4, card[3] - 18), border_radius=2)

        dot_center = (32, y + height // 2)
        self._draw_task_mark(dot_center, status.task.name, color, large=False)
        if selected:
            pygame.draw.circle(self.screen, (232, 246, 243), dot_center, 2)

        right_w = min(78, max(68, self.width // 4))
        name_x = 52
        name_w = self.width - right_w - name_x - 18
        name_lines = _wrap_text_for_width(status.task.name, self.font_task, name_w)[:2]
        line_step = 17
        progress_h = 6
        progress_gap = 5
        text_group_h = len(name_lines) * line_step + progress_gap + progress_h
        name_y = y + max(6, (height - text_group_h) // 2)
        for offset, line in enumerate(name_lines):
            name = self.font_task.render(line, True, TEXT)
            self.screen.blit(name, (name_x, name_y + offset * line_step))

        progress_x = name_x
        progress_y = name_y + len(name_lines) * line_step + progress_gap
        progress_w = max(38, name_w)
        gauge = (progress_x, progress_y, progress_w, progress_h)
        pygame.draw.rect(self.screen, CLEAN_BAR, gauge, border_radius=4)
        used_color = USED_BAR
        used_w = progress_w if status.due_state in (DueState.DUE_TODAY, DueState.OVERDUE) else max(4, int(progress_w * _progress(status)))
        pygame.draw.rect(self.screen, used_color, (progress_x, progress_y, used_w, progress_h), border_radius=4)
        pygame.draw.rect(self.screen, (20, 30, 31), gauge, width=1, border_radius=4)
        if 5 < used_w < progress_w - 2:
            tick_x = progress_x + used_w
            pygame.draw.line(self.screen, (236, 247, 232), (tick_x, progress_y - 1), (tick_x, progress_y + progress_h), 1)

        due = self.font_meta_bold.render(_format_due_date(status.next_due), True, TEXT)
        status_label, status_detail = _status_text(status)
        state_text = f"{status_label} {status_detail}".strip()
        state = self.font_small.render(state_text, True, color)
        due_x = self.width - due.get_width() - 22
        state_x = self.width - state.get_width() - 22
        due_y = y + max(8, (height - 34) // 2)
        self.screen.blit(due, (due_x, due_y))
        self.screen.blit(state, (state_x, due_y + 18))

    def _draw_task_mark(self, center: tuple[int, int], task_name: str, color: tuple[int, int, int], *, large: bool) -> None:
        pygame = self.pygame
        x, y = center
        name = task_name.lower()
        radius = 13 if large else 7
        glyph = BG
        scale = 1 if large else 0
        pygame.draw.circle(self.screen, color, center, radius)

        if "fountain" in name or "water" in name:
            pygame.draw.circle(self.screen, glyph, (x, y - 2 - scale), 3 + scale)
            pygame.draw.line(self.screen, glyph, (x - 5 - scale, y + 5), (x + 5 + scale, y + 5), 2)
            return
        if "air" in name or "filter" in name or "conditioner" in name:
            for offset, width in ((-4, 5), (0, 7), (4, 5)):
                pygame.draw.line(self.screen, glyph, (x - width, y + offset), (x + width, y + offset), 2)
            return
        if "floor" in name or "steam" in name or "clean" in name:
            pygame.draw.line(self.screen, glyph, (x - 5, y + 5), (x + 5, y - 5), 2)
            pygame.draw.line(self.screen, glyph, (x + 1, y - 6), (x + 6, y - 6), 2)
            return
        pygame.draw.circle(self.screen, glyph, center, 4 + scale, width=2)

    def _draw_history(self, history_lines: list[str]) -> None:
        heading = self.font_task.render("Log", True, TEXT)
        self.screen.blit(heading, (12, 48))
        y = 74
        for line in history_lines[:7]:
            surface = self.font_meta.render(line, True, TEXT_SOFT)
            self.screen.blit(surface, (12, y))
            y += 20
        if not history_lines:
            empty = self.font_meta.render("No completions yet", True, TEXT_MUTED)
            self.screen.blit(empty, (12, y))

    def _draw_footer(self) -> None:
        pygame = self.pygame
        pygame.draw.rect(self.screen, SURFACE, (0, self.height - 26, self.width, 26))
        pygame.draw.line(self.screen, LINE, (0, self.height - 26), (self.width, self.height - 26), 1)
        if time.monotonic() < self.flash_until:
            text = self.flash_message
        else:
            text = "UP      DOWN      DONE      LOG"
        surface = self.font_tiny.render(text, True, TEXT_MUTED)
        self.screen.blit(surface, (10, self.height - 17))

    def _draw_empty(self, title: str, detail: str) -> None:
        pygame = self.pygame
        rect = (12, 62, self.width - 24, 82)
        pygame.draw.rect(self.screen, SURFACE_RAISED, rect, border_radius=8)
        pygame.draw.rect(self.screen, LINE, rect, width=1, border_radius=8)
        title_surface = self.font_task.render(title, True, TEXT)
        detail_surface = self.font_meta.render(detail, True, TEXT_MUTED)
        self.screen.blit(title_surface, (rect[0] + 16, rect[1] + 20))
        self.screen.blit(detail_surface, (rect[0] + 16, rect[1] + 46))


def _wrap_text_for_width(text: str, font, max_width: int) -> list[str]:
    words = textwrap.wrap(text, width=max(8, len(text)))
    lines: list[str] = []
    current = ""
    for word in " ".join(words).split():
        candidate = word if not current else f"{current} {word}"
        if font.size(candidate)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = _fit_text(word, font, max_width)
    if current:
        lines.append(current)
    return lines or [text]


def _fit_text(text: str, font, max_width: int) -> str:
    if font.size(text)[0] <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and font.size(trimmed + ellipsis)[0] > max_width:
        trimmed = trimmed[:-1]
    return trimmed + ellipsis if trimmed else ellipsis


def _format_due_date(value: date) -> str:
    return value.strftime("%b %d")


def _status_color(status: TaskStatus) -> tuple[int, int, int]:
    color = STATUS_COLORS[status.due_state]
    if status.due_state == DueState.DUE_TODAY:
        return _pulse_color(color, (248, 205, 124), 0.18)
    return color


def _pulse_color(base: tuple[int, int, int], peak: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    phase = (time.monotonic() % 2.4) / 2.4
    wave = 0.5 + 0.5 * abs(phase * 2 - 1)
    mix = amount * wave
    return tuple(int(base[index] + (peak[index] - base[index]) * mix) for index in range(3))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(int(a[index] + (b[index] - a[index]) * amount) for index in range(3))


def _status_text(status: TaskStatus) -> tuple[str, str]:
    days = (status.next_due - date.today()).days
    if days < 0:
        return _format_day_count(abs(days)), ""
    if days == 0:
        return "Today", ""
    return _format_day_count(days), ""


def _format_day_count(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def _progress(status: TaskStatus) -> float:
    if status.due_state in (DueState.DUE_TODAY, DueState.OVERDUE):
        return 1.0
    days_left = max(0, (status.next_due - date.today()).days)
    total = _estimated_interval_days(status)
    if total <= 0:
        return 0.0
    return max(0.08, min(1.0, 1.0 - (days_left / total)))


def _estimated_interval_days(status: TaskStatus) -> int:
    if status.previous_due is not None:
        return max(1, (status.next_due - status.previous_due).days)
    if status.following_due is not None:
        return max(1, (status.following_due - status.next_due).days)
    if status.last_completed is not None:
        return max(1, (status.next_due - status.last_completed).days)
    if status.task.interval_unit == "days":
        return max(1, status.task.interval_value)
    return max(1, status.task.interval_value * 30)
