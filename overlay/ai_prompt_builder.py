#!/usr/bin/env python3
"""
JuhRadial MX - AI Prompt Builder window

The desktop equivalent of Logitech Options+ "Logi AI Prompt Builder": grabs the
current text selection, offers one-click recipes (rephrase, summarize, fix
grammar, translate, ...) plus a free-form prompt, runs it through the configured
backend (Claude Code CLI by default, or the Anthropic/OpenAI API), and shows the
result with Copy / Insert / Regenerate.

Launch standalone for testing:
    python3 ai_prompt_builder.py                 # self-captures the selection
    echo "some text" | python3 ai_prompt_builder.py --stdin

SPDX-License-Identifier: GPL-3.0
"""

import sys

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import ai_selection
from ai_backend import AIBackendError, generate
from ai_config import (
    OUTPUT_LANGUAGES,
    current_engine_model,
    engine_model_options,
    load_ai_config,
    patch_ai_config,
)

# Catppuccin Mocha-ish palette (matches the project's default theme feel).
COL_BASE = "#1e1e2e"
COL_SURFACE = "#313244"
COL_SURFACE2 = "#45475a"
COL_TEXT = "#cdd6f4"
COL_SUBTEXT = "#a6adc8"
COL_ACCENT = "#89b4fa"
COL_ACCENT_HOVER = "#74a0f0"
COL_RED = "#f38ba8"

STYLESHEET = f"""
QWidget#root {{
    background: {COL_BASE};
    border: 1px solid {COL_SURFACE2};
    border-radius: 14px;
}}
QLabel {{ color: {COL_TEXT}; }}
QLabel#title {{ font-size: 16px; font-weight: 600; }}
QLabel#subtitle {{ color: {COL_SUBTEXT}; font-size: 11px; }}
QLabel#status {{ color: {COL_SUBTEXT}; font-size: 12px; }}
QLabel#error {{ color: {COL_RED}; font-size: 12px; }}
QTextEdit, QLineEdit {{
    background: {COL_SURFACE};
    color: {COL_TEXT};
    border: 1px solid {COL_SURFACE2};
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {COL_ACCENT};
}}
QPushButton#recipe {{
    background: {COL_SURFACE};
    color: {COL_TEXT};
    border: 1px solid {COL_SURFACE2};
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13px;
}}
QPushButton#recipe:hover {{ background: {COL_SURFACE2}; border-color: {COL_ACCENT}; }}
QPushButton#primary {{
    background: {COL_ACCENT};
    color: {COL_BASE};
    border: none;
    border-radius: 8px;
    padding: 9px 16px;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {COL_ACCENT_HOVER}; }}
QPushButton#ghost {{
    background: transparent;
    color: {COL_SUBTEXT};
    border: 1px solid {COL_SURFACE2};
    border-radius: 8px;
    padding: 9px 16px;
}}
QPushButton#ghost:hover {{ color: {COL_TEXT}; border-color: {COL_ACCENT}; }}
QFrame#card {{ background: {COL_SURFACE}; border-radius: 10px; }}
QFrame#segbar {{ background: {COL_SURFACE}; border-radius: 9px; }}
QPushButton#seg {{
    background: transparent;
    color: {COL_SUBTEXT};
    border: none;
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#seg:hover {{ color: {COL_TEXT}; }}
QPushButton#seg:checked {{ background: {COL_ACCENT}; color: {COL_BASE}; }}
"""


class _Worker(QThread):
    """Runs a backend call off the UI thread."""

    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, recipe_prompt, selected_text, ai_cfg):
        super().__init__()
        self._recipe_prompt = recipe_prompt
        self._selected_text = selected_text
        self._ai_cfg = ai_cfg

    def run(self):
        try:
            result = generate(self._recipe_prompt, self._selected_text, self._ai_cfg)
            self.done.emit(result)
        except AIBackendError as e:
            self.failed.emit(str(e))
        except Exception as e:  # noqa: BLE001 - surface anything to the user
            self.failed.emit(f"Unexpected error: {e}")


class PromptBuilder(QWidget):
    def __init__(self, selected_text: str, saved_clipboard, ai_cfg: dict, engine=None):
        super().__init__()
        from ai_config import apply_engine

        self.selected_text = selected_text or ""
        self.saved_clipboard = saved_clipboard
        # Keep the un-applied config so the engine toggle can re-resolve live.
        self._base_cfg = ai_cfg
        self._engine = (engine or ai_cfg.get("default_engine") or "claude").lower()
        self.ai_cfg = apply_engine(ai_cfg, self._engine)
        self.ai_cfg["_engine_label"] = self._engine
        self.worker = None
        self.last_prompt = None
        self.result_text = ""

        self.setWindowTitle("AI Prompt Builder")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(760)
        self.setMinimumHeight(300)

        self._build_ui()
        QShortcut(QKeySequence("Escape"), self, activated=self.close)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        root = QWidget(objectName="root")
        outer.addWidget(root)
        self.v = QVBoxLayout(root)
        self.v.setContentsMargins(18, 16, 18, 16)
        self.v.setSpacing(12)

        # Header: title/subtitle on the left, engine + output-language toggles right
        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("AI Prompt Builder", objectName="title")
        self.sub = QLabel(self._subtitle_text(), objectName="subtitle")
        title_col.addWidget(title)
        title_col.addWidget(self.sub)
        header.addLayout(title_col)
        header.addStretch(1)

        # Model segmented toggle for the active engine (e.g. Claude: Opus/Sonnet)
        self.model_group = QButtonGroup(self)
        model_items = engine_model_options(self._base_cfg, self._engine)
        current_model = current_engine_model(self._base_cfg, self._engine)
        engine_name = {"claude": "Claude", "chatgpt": "ChatGPT", "gemini": "Gemini"}.get(
            self._engine, self._engine.capitalize()
        )
        model_seg = self._segmented(
            engine_name, model_items, current_model, self.model_group,
            "model_value", self._on_model_changed,
        )
        header.addLayout(model_seg)

        # Output-language segmented toggle: Auto / EN / PT-BR
        self.lang_group = QButtonGroup(self)
        short_labels = {"auto": "Auto", "en": "EN", "pt-BR": "PT-BR"}
        lang_items = [(code, short_labels.get(code, code)) for code, _full in OUTPUT_LANGUAGES]
        current_lang = (self.ai_cfg.get("output_language") or "auto").lower()
        lang_seg = self._segmented(
            "Output", lang_items, current_lang, self.lang_group,
            "lang_code", self._on_language_changed,
        )
        header.addLayout(lang_seg)
        self.v.addLayout(header)

        # Two columns: recipe options on the LEFT, text boxes on the RIGHT
        content = QHBoxLayout()
        content.setSpacing(14)

        # -- LEFT: recipe options in a 2-column grid (no scroll) --
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        left_col.addWidget(QLabel("Actions", objectName="subtitle"))

        recipes_grid = QGridLayout()
        recipes_grid.setContentsMargins(0, 0, 0, 0)
        recipes_grid.setSpacing(6)
        cols = 2
        for i, recipe in enumerate(self.ai_cfg.get("recipes", [])):
            # Escape '&' so Qt doesn't treat it as a mnemonic (e.g. "Fix … & grammar")
            label = recipe.get("label", recipe.get("id", "Recipe")).replace("&", "&&")
            btn = QPushButton(label, objectName="recipe")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(
                lambda _checked, r=recipe: self._run_recipe(r.get("prompt", ""), r.get("label", ""))
            )
            recipes_grid.addWidget(btn, i // cols, i % cols)
        for c in range(cols):
            recipes_grid.setColumnStretch(c, 1)
        left_col.addLayout(recipes_grid)
        left_col.addStretch(1)

        left_w = QWidget()
        left_w.setLayout(left_col)
        left_w.setMaximumWidth(360)
        content.addWidget(left_w, 0)

        # -- RIGHT: selected text, prompt, result --
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        if self.selected_text.strip():
            card = QFrame(objectName="card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(12, 10, 12, 10)
            cl.addWidget(QLabel("Selected text", objectName="subtitle"))
            preview = self.selected_text.strip().replace("\n", " ")
            if len(preview) > 240:
                preview = preview[:240] + "…"
            txt = QLabel(preview)
            txt.setWordWrap(True)
            txt.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            cl.addWidget(txt)
            cl.addStretch(1)  # keep label+text at the top; card fills the space below
            right_col.addWidget(card)
        else:
            note = QLabel(
                "No text selected — pick an action or type a prompt to start fresh.",
                objectName="subtitle",
            )
            note.setWordWrap(True)
            note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            right_col.addWidget(note)

        prompt_row = QHBoxLayout()
        self.prompt_input = QLineEdit()
        self.prompt_input.setPlaceholderText("Ask anything about the selection…")
        self.prompt_input.returnPressed.connect(self._run_custom)
        send = QPushButton("Send", objectName="primary")
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.clicked.connect(self._run_custom)
        prompt_row.addWidget(self.prompt_input)
        prompt_row.addWidget(send)
        right_col.addLayout(prompt_row)

        self.status = QLabel("", objectName="status")
        self.status.setWordWrap(True)
        self.status.hide()
        right_col.addWidget(self.status)

        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMinimumHeight(120)
        self.result_view.hide()
        right_col.addWidget(self.result_view, 1)

        self.actions_row = QHBoxLayout()
        self.btn_regen = QPushButton("Regenerate", objectName="ghost")
        self.btn_copy = QPushButton("Copy", objectName="ghost")
        self.btn_insert = QPushButton("Insert", objectName="primary")
        for b in (self.btn_regen, self.btn_copy, self.btn_insert):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.hide()
        self.btn_regen.clicked.connect(self._regenerate)
        self.btn_copy.clicked.connect(self._copy_result)
        self.btn_insert.clicked.connect(self._insert_result)
        self.actions_row.addStretch(1)
        self.actions_row.addWidget(self.btn_regen)
        self.actions_row.addWidget(self.btn_copy)
        self.actions_row.addWidget(self.btn_insert)
        right_col.addLayout(self.actions_row)

        right_w = QWidget()
        right_w.setLayout(right_col)
        content.addWidget(right_w, 1)

        self.v.addLayout(content)

    def _segmented(self, label_text, items, current, group, prop_name, on_click):
        """Build a labelled segmented toggle (column) for the header."""
        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel(label_text, objectName="subtitle")
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        col.addWidget(lbl)

        bar = QFrame(objectName="segbar")
        bar_l = QHBoxLayout(bar)
        bar_l.setContentsMargins(3, 3, 3, 3)
        bar_l.setSpacing(3)
        group.setExclusive(True)
        cur = str(current).lower()
        for code, text in items:
            btn = QPushButton(text, objectName="seg")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty(prop_name, code)
            if str(code).lower() == cur:
                btn.setChecked(True)
            group.addButton(btn)
            bar_l.addWidget(btn)
        group.buttonClicked.connect(on_click)
        col.addWidget(bar, alignment=Qt.AlignmentFlag.AlignRight)
        return col

    def _subtitle_text(self):
        label = self.ai_cfg.get("_engine_label") or "claude"
        return f"{label} · {self._active_model()}"

    def _active_model(self) -> str:
        if (self.ai_cfg.get("backend") or "cli").lower() == "openai":
            return self.ai_cfg.get("openai", {}).get("model", "?")
        return self.ai_cfg.get("cli", {}).get("model", "?")

    def _on_model_changed(self, button):
        model = button.property("model_value")
        if not model:
            return
        # Remember this model for the current engine, and apply it live.
        em = dict(self._base_cfg.get("engine_model") or {})
        em[self._engine] = model
        self._base_cfg["engine_model"] = em
        patch_ai_config({"engine_model": em})
        if (self.ai_cfg.get("backend") or "cli").lower() == "openai":
            self.ai_cfg.setdefault("openai", {})["model"] = model
        else:
            self.ai_cfg.setdefault("cli", {})["model"] = model
        self.sub.setText(self._subtitle_text())

    def _on_language_changed(self, button):
        code = button.property("lang_code")
        if not code:
            return
        self.ai_cfg["output_language"] = code  # applies to the next run immediately
        patch_ai_config({"output_language": code})  # remember as the default

    # -- Running -----------------------------------------------------------

    def _run_recipe(self, prompt: str, label: str):
        if prompt:
            self._start(prompt, label)

    def _run_custom(self):
        text = self.prompt_input.text().strip()
        if text:
            self._start(text, "Custom prompt")

    def _regenerate(self):
        if self.last_prompt is not None:
            self._start(self.last_prompt[0], self.last_prompt[1])

    def _start(self, prompt: str, label: str):
        if self.worker and self.worker.isRunning():
            return
        self.last_prompt = (prompt, label)
        self._set_busy(label)
        self.worker = _Worker(prompt, self.selected_text, self.ai_cfg)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _set_busy(self, label: str):
        self.status.setObjectName("status")
        self.status.setStyleSheet("")
        self.status.setText(f"Running “{label}”…")
        self.status.show()
        self.result_view.hide()
        for b in (self.btn_regen, self.btn_copy, self.btn_insert):
            b.hide()
        self.prompt_input.setEnabled(False)
        self.adjustSize()

    def _on_done(self, result: str):
        self.result_text = result
        self.status.hide()
        self.result_view.setPlainText(result)
        self.result_view.show()
        for b in (self.btn_regen, self.btn_copy, self.btn_insert):
            b.show()
        self.prompt_input.setEnabled(True)
        self.adjustSize()

    def _on_failed(self, message: str):
        self.status.setObjectName("error")
        self.status.setStyleSheet(f"color: {COL_RED};")
        self.status.setText(message)
        self.status.show()
        self.result_view.hide()
        self.btn_regen.show()
        self.prompt_input.setEnabled(True)
        self.adjustSize()

    # -- Result actions ----------------------------------------------------

    def _copy_result(self):
        if self.result_text:
            ai_selection.set_clipboard_text(self.result_text)
            self.btn_copy.setText("Copied ✓")

    def _insert_result(self):
        if not self.result_text:
            return
        text = self.result_text
        saved = self.saved_clipboard
        if ai_selection.injector_available():
            self.hide()
            QApplication.processEvents()
            # Let focus return to the previously active app before pasting.
            QThread.msleep(120)
            ai_selection.paste_text(text, restore=saved)
            self.close()
        else:
            # No key injector (no xdotool / running ydotoold): fall back to a
            # manual paste so the result still reaches the user.
            ai_selection.set_clipboard_text(text)
            self.status.setObjectName("status")
            self.status.setStyleSheet("")
            self.status.setText(
                "Copied to clipboard — switch to your app and press Ctrl+V. "
                "Install xdotool (X11) or run ydotoold for automatic insert."
            )
            self.status.show()
            self.btn_insert.setText("Copied ✓")
            self.adjustSize()

    def closeEvent(self, event):
        # Best-effort clipboard restore if the user closes without inserting.
        if self.saved_clipboard is not None:
            ai_selection.restore_clipboard(self.saved_clipboard)
        super().closeEvent(event)

    def center_on_cursor(self):
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen().geometry().center())
        screen = screen or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.adjustSize()
        x = geo.center().x() - self.width() // 2
        y = geo.center().y() - self.height() // 2
        self.move(max(geo.left(), x), max(geo.top(), y))


def _arg_value(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def main():
    use_stdin = "--stdin" in sys.argv
    engine = _arg_value("--engine")
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # Base (un-applied) config; PromptBuilder resolves the engine itself so the
    # in-window toggle can switch engines live. --engine overrides the default.
    ai_cfg = load_ai_config()

    if use_stdin:
        selected = sys.stdin.read()
        saved = None
    else:
        selected, saved = ai_selection.capture_selection(
            delay_ms=int(ai_cfg.get("capture_delay_ms", 120)),
            preserve_clipboard=bool(ai_cfg.get("preserve_clipboard", True)),
        )

    win = PromptBuilder(selected, saved, ai_cfg, engine=engine)
    win.show()
    win.center_on_cursor()
    win.raise_()
    win.activateWindow()
    win.prompt_input.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
