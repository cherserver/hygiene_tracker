from __future__ import annotations

from datetime import date
from pathlib import Path
import os
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

HEADER_H = 38
FOOTER_H = 28
CONTENT_PAD = 5
ROW_GAP = 4
TASK_X = 52
RIGHT_W = 68
RIGHT_PAD = 14


class FramebufferDashboard:
    def __init__(
        self,
        width: int,
        height: int,
        framebuffer: str = "",
        button_label_centers: tuple[int, int, int, int] = (56, 123, 190, 257),
    ) -> None:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError("fbdev display_backend requires Pillow: sudo apt install python3-pil") from exc

        self.Image = Image
        self.ImageDraw = ImageDraw
        self.ImageFont = ImageFont
        self.width = width
        self.height = height
        self.button_label_centers = button_label_centers
        self.framebuffer = Path(framebuffer or _default_framebuffer())
        self.fb_name = _fb_sys_value(self.framebuffer, "name", "unknown")
        self.bpp = int(_fb_sys_value(self.framebuffer, "bits_per_pixel", "16"))
        self.line_length = int(_fb_sys_value(self.framebuffer, "stride", "0") or "0")
        if self.line_length <= 0:
            self.line_length = self.width * (self.bpp // 8)

        self.font_title = _font(ImageFont, 17, bold=True)
        self.font_task = _font(ImageFont, 16, bold=True, condensed=True)
        self.font_task_small = _font(ImageFont, 14, bold=True, condensed=True)
        self.font_date = _font(ImageFont, 14, bold=True)
        self.font_meta = _font(ImageFont, 12, bold=True)
        self.font_small = _font(ImageFont, 11, bold=True)
        self.font_today = _font(ImageFont, 12, bold=True)
        self.font_button = _font(ImageFont, 11, bold=True)
        self.flash_message = ""
        self.flash_until = 0.0
        self.show_history = False
        print(
            "Hygiene display initialized: "
            f"driver=fbdev device={self.framebuffer} name={self.fb_name} "
            f"size={self.width}x{self.height} bpp={self.bpp}",
            flush=True,
        )

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
        image = self.Image.new("RGB", (self.width, self.height), BG)
        draw = self.ImageDraw.Draw(image)
        if self.show_history:
            self._draw_header(draw, gpio_available, statuses)
            self._draw_history(draw, history_lines or [])
        else:
            self._draw_header(draw, gpio_available, statuses)
            self._draw_tasks(draw, statuses, selected_index)
        self._draw_button_legend(draw)
        self._write_frame(image)

    def poll_events(self) -> list[str]:
        return []

    def tick(self, fps: int = 12) -> None:
        time.sleep(1 / fps)

    def close(self) -> None:
        return

    def _draw_header(self, draw, gpio_available: bool, statuses: list[TaskStatus]) -> None:
        draw.rectangle((0, 0, self.width, HEADER_H), fill=SURFACE)
        draw.line((0, HEADER_H - 1, self.width, HEADER_H - 1), fill=LINE, width=1)
        draw.text((10, 8), "Hygiene", fill=TEXT, font=self.font_title)

        date_text = _format_display_date(date.today())
        date_w = _text_width(draw, date_text, self.font_today)
        pill_pad_x = 12
        pill = (self.width - date_w - pill_pad_x * 2 - 8, 8, self.width - 8, 29)
        _rounded_rectangle(draw, pill, radius=7, fill=(31, 45, 47), outline=LINE)
        if statuses and all(status.due_state in (DueState.OK, DueState.DUE_SOON) for status in statuses):
            _rounded_rectangle(draw, pill, radius=7, fill=None, outline=CLEAN_BAR)
        draw.text((pill[0] + pill_pad_x, 11), date_text, fill=TEXT_SOFT, font=self.font_today)
        if not gpio_available:
            draw.text((116, 20), "GPIO", fill=WARNING, font=self.font_small)

    def _draw_tasks(self, draw, statuses: list[TaskStatus], selected_index: int | None) -> None:
        if not statuses:
            rect = (12, 62, self.width - 12, 126)
            _rounded_rectangle(draw, rect, radius=8, fill=SURFACE_RAISED, outline=LINE)
            draw.text((27, 80), "No active tasks", fill=TEXT, font=self.font_task)
            draw.text((27, 102), "Add tasks in the web UI", fill=TEXT_MUTED, font=self.font_small)
            return

        top = HEADER_H + CONTENT_PAD
        bottom = self.height - FOOTER_H - CONTENT_PAD
        available_h = bottom - top
        row_height = max(32, (available_h - ROW_GAP * (len(statuses) - 1)) // max(1, len(statuses)))
        for index, status in enumerate(statuses):
            y = top + index * (row_height + ROW_GAP)
            self._draw_task_row(draw, status, y, row_height, index == selected_index)

    def _draw_task_row(self, draw, status: TaskStatus, y: int, height: int, selected: bool) -> None:
        color = _status_color(status)
        bg = SURFACE_SELECTED if selected else SURFACE_RAISED
        border = color if selected else LINE
        rect = (CONTENT_PAD, y, self.width - CONTENT_PAD, y + height)
        _rounded_rectangle(draw, (rect[0] + 1, rect[1] + 2, rect[2] + 1, rect[3] + 2), radius=7, fill=(1, 4, 5))
        if status.due_state == DueState.DUE_TODAY:
            _rounded_rectangle(draw, (rect[0] + 2, rect[1] + 4, rect[0] + 10, rect[3] - 4), radius=4, fill=_mix(color, BG, 0.52))
        _rounded_rectangle(draw, rect, radius=7, fill=bg, outline=border, width=2 if selected else 1)
        draw.line(
            (rect[0] + 16, rect[1] + 1, rect[2] - 16, rect[1] + 1),
            fill=PLATE_HIGHLIGHT if not selected else _mix(color, TEXT, 0.45),
            width=1,
        )
        _rounded_rectangle(draw, (rect[0] + 8, rect[1] + 9, rect[0] + 12, rect[3] - 9), radius=2, fill=color)

        dot_x, dot_y = 32, y + height // 2
        self._draw_task_mark(draw, (dot_x, dot_y), status.task.name, color, large=False)
        if selected:
            draw.ellipse((dot_x - 2, dot_y - 2, dot_x + 2, dot_y + 2), fill=(232, 246, 243))

        right_x = self.width - RIGHT_W - RIGHT_PAD - 4
        name_width = right_x - TASK_X - 6
        task_font = self.font_task if height >= 48 else self.font_task_small
        lines = _wrap_to_width(draw, status.task.name, task_font, name_width)[:2]
        line_step = 18 if task_font == self.font_task else 16
        progress_gap = 4
        progress_h = 6
        text_group_h = len(lines) * line_step + progress_gap + progress_h
        name_y = y + max(5, (height - text_group_h) // 2)
        for offset, line in enumerate(lines):
            draw.text((TASK_X, name_y + offset * line_step), line, fill=TEXT, font=task_font)

        progress_y = name_y + len(lines) * line_step + progress_gap
        progress_w = max(38, name_width)
        gauge = (TASK_X, progress_y, TASK_X + progress_w, progress_y + progress_h)
        _rounded_rectangle(draw, gauge, radius=4, fill=CLEAN_BAR)
        used_color = USED_BAR
        used_w = progress_w if status.due_state in (DueState.DUE_TODAY, DueState.OVERDUE) else max(4, int(progress_w * _progress(status)))
        _rounded_rectangle(
            draw,
            (TASK_X, progress_y, TASK_X + used_w, progress_y + progress_h),
            radius=4,
            fill=used_color,
        )
        _rounded_rectangle(draw, gauge, radius=4, fill=None, outline=(20, 30, 31))
        if 5 < used_w < progress_w - 2:
            tick_x = TASK_X + used_w
            draw.line((tick_x, progress_y - 1, tick_x, progress_y + progress_h), fill=(236, 247, 232), width=1)

        due_date = _format_due_date(status.next_due)
        status_label, status_detail = _status_text(status)
        meta = f"{status_label} {status_detail}".strip()
        due_x = right_x + RIGHT_W - _text_width(draw, due_date, self.font_date)
        meta_x = right_x + RIGHT_W - _text_width(draw, meta, self.font_meta)
        due_y = y + max(4, (height - 30) // 2)
        draw.text((due_x, due_y), due_date, fill=TEXT, font=self.font_date)
        draw.text((meta_x, due_y + 17), meta, fill=color, font=self.font_meta)

    def _draw_task_mark(self, draw, center: tuple[int, int], task_name: str, color: tuple[int, int, int], *, large: bool) -> None:
        x, y = center
        name = task_name.lower()
        radius = 13 if large else 7
        glyph = BG
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

        if "fountain" in name or "water" in name:
            draw.ellipse((x - 4, y - 6, x + 4, y + 2), fill=glyph)
            draw.line((x - 5, y + 6, x + 5, y + 6), fill=glyph, width=2)
            return
        if "air" in name or "filter" in name or "conditioner" in name:
            for offset, width in ((-4, 5), (0, 7), (4, 5)):
                draw.line((x - width, y + offset, x + width, y + offset), fill=glyph, width=2)
            return
        if "floor" in name or "steam" in name or "clean" in name:
            draw.line((x - 5, y + 5, x + 5, y - 5), fill=glyph, width=2)
            draw.line((x + 1, y - 6, x + 6, y - 6), fill=glyph, width=2)
            return
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline=glyph, width=2)

    def _draw_button_legend(self, draw) -> None:
        label_y = self.height - 21
        line_y = self.height - 1
        draw.rectangle((0, self.height - FOOTER_H, self.width, self.height), fill=SURFACE)
        draw.line((0, self.height - FOOTER_H, self.width, self.height - FOOTER_H), fill=LINE, width=1)
        if time.monotonic() < self.flash_until:
            text = _fit_text(draw, self.flash_message, self.font_small, self.width - 14)
            draw.text((7, label_y), text, fill=TEXT, font=self.font_small)
            return

        labels = ["UP", "DOWN", "DONE", "LOG"]
        for text, center_x in zip(labels, self.button_label_centers):
            color = TEXT if text == "DONE" else TEXT_SOFT
            text_x = center_x - _text_width(draw, text, self.font_button) // 2
            draw.text((text_x, label_y), text, fill=color, font=self.font_button)
            draw.line((center_x - 22, line_y, center_x + 22, line_y), fill=color, width=2)

    def _draw_history(self, draw, history_lines: list[str]) -> None:
        draw.text((10, HEADER_H + 12), "Log", fill=TEXT, font=self.font_task)
        y = HEADER_H + 38
        for line in history_lines[:8]:
            draw.text((12, y), line, fill=TEXT_SOFT, font=self.font_small)
            y += 21
        if not history_lines:
            draw.text((12, y), "No completions yet", fill=TEXT_MUTED, font=self.font_small)

    def _write_frame(self, image) -> None:
        if self.bpp == 16:
            payload = _rgb565(image)
        elif self.bpp == 32:
            payload = image.tobytes("raw", "BGRX")
        else:
            raise RuntimeError(f"Unsupported framebuffer depth: {self.bpp} bpp")

        with self.framebuffer.open("r+b", buffering=0) as fb:
            if self.line_length == self.width * (self.bpp // 8):
                fb.write(payload)
            else:
                row_bytes = self.width * (self.bpp // 8)
                for y in range(self.height):
                    fb.seek(y * self.line_length)
                    fb.write(payload[y * row_bytes : (y + 1) * row_bytes])


def _default_framebuffer() -> str:
    candidates = sorted(Path("/sys/class/graphics").glob("fb*"))
    for candidate in candidates:
        name = (candidate / "name").read_text(encoding="utf-8", errors="ignore").strip().lower()
        if any(token in name for token in ("tft", "ili", "9341", "spi", "fb_")):
            return f"/dev/{candidate.name}"
    if Path("/dev/fb1").exists():
        return "/dev/fb1"
    return "/dev/fb0"


def _fb_sys_value(framebuffer: Path, name: str, default: str) -> str:
    sys_path = Path("/sys/class/graphics") / framebuffer.name / name
    if sys_path.exists():
        return sys_path.read_text(encoding="utf-8", errors="ignore").strip()
    return default


def _font(ImageFont, size: int, bold: bool = False, condensed: bool = False):
    if condensed:
        names = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSansNarrow-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSansNarrow-Regular.ttf",
        ]
        for name in names:
            if os.path.exists(name):
                return ImageFont.truetype(name, size)

    names = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for name in names:
        if os.path.exists(name):
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


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


def _progress(status: TaskStatus) -> float:
    if status.due_state in (DueState.DUE_TODAY, DueState.OVERDUE):
        return 1.0
    days_left = max(0, (status.next_due - date.today()).days)
    total = _estimated_interval_days(status)
    if total <= 0:
        return 0.0
    return max(0.08, min(1.0, 1.0 - (days_left / total)))


def _format_display_date(value: date) -> str:
    return value.strftime("%a %b %-d") if os.name != "nt" else value.strftime("%a %b %#d")


def _format_due_date(value: date) -> str:
    return value.strftime("%b %-d") if os.name != "nt" else value.strftime("%b %#d")


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


def _text_width(draw, text: str, font) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _wrap_to_width(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = _fit_text(draw, word, font, max_width)
    if current:
        lines.append(current)
    return lines or [text]


def _fit_text(draw, text: str, font, max_width: int) -> str:
    if _text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and _text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return trimmed + ellipsis if trimmed else ellipsis


def _rounded_rectangle(draw, xy, radius: int, fill, outline=None, width: int = 1) -> None:
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
        return
    draw.rectangle(xy, fill=fill, outline=outline, width=width)


def _rgb565(image) -> bytes:
    pixels = image.tobytes()
    out = bytearray((len(pixels) // 3) * 2)
    j = 0
    for i in range(0, len(pixels), 3):
        r, g, b = pixels[i], pixels[i + 1], pixels[i + 2]
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = value & 0xFF
        out[j + 1] = value >> 8
        j += 2
    return bytes(out)
