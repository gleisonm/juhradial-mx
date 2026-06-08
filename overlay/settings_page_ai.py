#!/usr/bin/env python3
"""
JuhRadial MX - AI Prompt Builder settings page

Configure the AI Prompt Builder backend (Claude Code CLI by default, or the
Anthropic / OpenAI API), models, API keys, and capture behavior.

SPDX-License-Identifier: GPL-3.0
"""

import logging
import shlex

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk  # noqa: E402

from i18n import _  # noqa: E402
from settings_config import config  # noqa: E402
from settings_widgets import PageHeader, SettingRow, SettingsCard  # noqa: E402

logger = logging.getLogger(__name__)

def _g(*keys, default=None):
    return config.get("ai", *keys, default=default)


def _s(*keys_and_value):
    config.set("ai", *keys_and_value, auto_save=True)


class AIPage(Gtk.ScrolledWindow):
    """AI Prompt Builder configuration page."""

    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_vexpand(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        root.set_margin_top(16)
        root.set_margin_bottom(24)
        root.set_margin_start(24)
        root.set_margin_end(24)
        root.set_halign(Gtk.Align.CENTER)
        root.set_size_request(560, -1)
        self.set_child(root)

        root.append(
            PageHeader(
                "applications-science-symbolic",
                _("AI Prompt Builder"),
                _("Select text, open the Action Ring, and transform it with AI."),
            )
        )

        root.append(self._default_model_card())
        self._cli_card_w = self._cli_card()
        self._openai_card_w = self._api_card(
            "openai", _("OpenAI API (ChatGPT)"), "gpt-4o", "OPENAI_API_KEY"
        )
        root.append(self._cli_card_w)
        root.append(self._openai_card_w)
        root.append(self._behavior_card())
        root.append(self._recipes_card())

        self._update_engine_visibility(_g("default_engine", default="claude"))

    # -- Default model (engine) selector ----------------------------------

    def _default_model_card(self):
        from ai_config import ENGINES

        card = SettingsCard(_("Default model"))

        # Engine (provider) the builder opens with
        erow = SettingRow(
            _("Provider"),
            _("Which AI the Prompt Builder opens with. You can switch per-use in the builder."),
        )
        self._engine_codes = [code for code, _label in ENGINES]
        edd = Gtk.DropDown(model=Gtk.StringList.new([label for _code, label in ENGINES]))
        current = str(_g("default_engine", default="claude"))
        edd.set_selected(self._engine_codes.index(current) if current in self._engine_codes else 0)
        edd.set_valign(Gtk.Align.CENTER)
        edd.connect("notify::selected", self._on_default_engine_changed)
        erow.set_control(edd)
        card.append(erow)

        # Default model variant for that provider (e.g. Opus / Sonnet)
        mrow = SettingRow(_("Model"), _("Default variant for that provider."))
        self._model_dd = Gtk.DropDown()
        self._model_dd.set_valign(Gtk.Align.CENTER)
        self._populate_model_dd(current)
        self._model_dd.connect("notify::selected", self._on_default_model_changed)
        mrow.set_control(self._model_dd)
        card.append(mrow)
        return card

    def _populate_model_dd(self, engine):
        from ai_config import current_engine_model, engine_model_options

        self._model_engine = engine
        opts = engine_model_options({}, engine)
        self._model_values = [v for v, _label in opts]
        self._model_dd.set_model(Gtk.StringList.new([label for _v, label in opts]))
        cur = current_engine_model({"engine_model": _g("engine_model", default={})}, engine)
        if cur in self._model_values:
            self._model_dd.set_selected(self._model_values.index(cur))

    def _on_default_engine_changed(self, dropdown, _param):
        from ai_config import CLI_PRESETS

        idx = dropdown.get_selected()
        engine = self._engine_codes[idx] if 0 <= idx < len(self._engine_codes) else "claude"
        _s("default_engine", engine)
        if engine in ("claude", "gemini"):
            _s("backend", "cli")
            _s("cli", "provider", engine)
            _s("cli", "command", list(CLI_PRESETS[engine]["command"]))
            if hasattr(self, "_cli_cmd_entry"):
                self._cli_cmd_entry.set_text(shlex.join(CLI_PRESETS[engine]["command"]))
        elif engine == "chatgpt":
            _s("backend", "openai")
        self._populate_model_dd(engine)
        self._update_engine_visibility(engine)

    def _on_default_model_changed(self, dropdown, _param):
        idx = dropdown.get_selected()
        if not (0 <= idx < len(self._model_values)):
            return
        em = dict(_g("engine_model", default={}) or {})
        em[self._model_engine] = self._model_values[idx]
        _s("engine_model", em)

    def _update_engine_visibility(self, engine):
        is_cli = engine in ("claude", "gemini")
        self._cli_card_w.set_visible(is_cli)
        self._openai_card_w.set_visible(engine == "chatgpt")

    def _on_language_changed(self, dropdown, _param):
        idx = dropdown.get_selected()
        code = self._lang_codes[idx] if 0 <= idx < len(self._lang_codes) else "auto"
        _s("output_language", code)

    # -- CLI card ----------------------------------------------------------

    def _cli_card(self):
        from ai_config import CLI_PRESETS

        card = SettingsCard(_("Command-line backend (advanced)"))

        cmd_list = _g("cli", "command", default=CLI_PRESETS["claude"]["command"])
        # shlex.join preserves the empty `--allowedTools ""` argument (shown as '')
        # so the round-trip through the entry is lossless.
        cmd_str = shlex.join(cmd_list) if isinstance(cmd_list, list) else str(cmd_list)

        cmd_row = SettingRow(
            _("Command"),
            _("{prompt} = instruction, {model} = model. Selection is piped to stdin."),
        )
        self._cli_cmd_entry = Gtk.Entry()
        self._cli_cmd_entry.set_text(cmd_str)
        self._cli_cmd_entry.set_hexpand(True)
        self._cli_cmd_entry.set_width_chars(28)
        self._cli_cmd_entry.connect("changed", self._on_cli_command_changed)
        cmd_row.set_control(self._cli_cmd_entry)
        card.append(cmd_row)
        return card

    def _on_cli_command_changed(self, entry):
        text = entry.get_text().strip()
        # shlex.split preserves quoted empty args, e.g. --allowedTools "".
        try:
            parts = shlex.split(text) if text else []
        except ValueError:
            return  # unbalanced quotes mid-edit; ignore until valid
        _s("cli", "command", parts)

    # -- API card (anthropic / openai) ------------------------------------

    def _api_card(self, backend, title, default_model, env_name):
        card = SettingsCard(title)

        key_row = SettingRow(
            _("API key"),
            _("Stored in config.json. Leave empty to use the {env} variable.").format(env=env_name),
        )
        key_entry = Gtk.PasswordEntry()
        key_entry.set_show_peek_icon(True)
        key_entry.set_text(str(_g(backend, "api_key", default="")))
        key_entry.set_hexpand(True)
        key_entry.connect("changed", lambda e, b=backend: _s(b, "api_key", e.get_text()))
        key_row.set_control(key_entry)
        card.append(key_row)
        return card

    # -- Behavior card -----------------------------------------------------

    def _behavior_card(self):
        from ai_config import OUTPUT_LANGUAGES

        card = SettingsCard(_("Behavior"))

        lang_row = SettingRow(
            _("Output language"),
            _("Force the AI to answer in this language (Auto keeps the input language)."),
        )
        self._lang_codes = [code for code, _label in OUTPUT_LANGUAGES]
        lang_model = Gtk.StringList.new([label for _code, label in OUTPUT_LANGUAGES])
        lang_dd = Gtk.DropDown(model=lang_model)
        current_lang = str(_g("output_language", default="auto"))
        sel = self._lang_codes.index(current_lang) if current_lang in self._lang_codes else 0
        lang_dd.set_selected(sel)
        lang_dd.set_valign(Gtk.Align.CENTER)
        lang_dd.connect("notify::selected", self._on_language_changed)
        lang_row.set_control(lang_dd)
        card.append(lang_row)

        preserve_row = SettingRow(
            _("Preserve clipboard"),
            _("Restore your clipboard after capturing the selection and pasting."),
        )
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(_g("preserve_clipboard", default=True)))
        sw.connect("state-set", lambda s, st: (_s("preserve_clipboard", bool(st)), False)[1])
        preserve_row.set_control(sw)
        card.append(preserve_row)

        delay_row = SettingRow(
            _("Capture delay (ms)"),
            _("Wait after Ctrl+C before reading the clipboard. Raise if capture is flaky."),
        )
        adj = Gtk.Adjustment(
            value=int(_g("capture_delay_ms", default=120)),
            lower=0, upper=1000, step_increment=10, page_increment=50,
        )
        spin = Gtk.SpinButton(adjustment=adj)
        spin.set_valign(Gtk.Align.CENTER)
        spin.connect("value-changed", lambda s: _s("capture_delay_ms", int(s.get_value())))
        delay_row.set_control(spin)
        card.append(delay_row)
        return card

    # -- Recipes (editable) -----------------------------------------------

    def _recipes_card(self):
        import copy

        from ai_config import DEFAULT_RECIPES

        self._recipes = copy.deepcopy(_g("recipes", default=DEFAULT_RECIPES))
        if not isinstance(self._recipes, list):
            self._recipes = copy.deepcopy(DEFAULT_RECIPES)

        card = SettingsCard(_("Recipes"))
        hint = Gtk.Label()
        hint.set_markup(
            f'<span size="small">{_("One-click prompts shown in the builder. Edit the name and the instruction sent to the AI.")}</span>'
        )
        hint.set_halign(Gtk.Align.START)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        hint.set_margin_bottom(8)
        card.append(hint)

        self._recipes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.append(self._recipes_box)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_margin_top(8)
        add_btn = Gtk.Button(label=_("Add recipe"))
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.connect("clicked", self._on_add_recipe)
        reset_btn = Gtk.Button(label=_("Reset to defaults"))
        reset_btn.add_css_class("flat")
        reset_btn.connect("clicked", self._on_reset_recipes)
        btn_row.append(add_btn)
        btn_row.append(reset_btn)
        card.append(btn_row)

        self._rebuild_recipes()
        return card

    def _rebuild_recipes(self):
        child = self._recipes_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._recipes_box.remove(child)
            child = nxt

        for i, recipe in enumerate(self._recipes):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

            label_entry = Gtk.Entry()
            label_entry.set_text(recipe.get("label", ""))
            label_entry.set_width_chars(12)
            label_entry.set_placeholder_text(_("Name"))
            label_entry.connect(
                "changed", lambda e, idx=i: self._update_recipe(idx, "label", e.get_text())
            )
            row.append(label_entry)

            prompt_entry = Gtk.Entry()
            prompt_entry.set_text(recipe.get("prompt", ""))
            prompt_entry.set_hexpand(True)
            prompt_entry.set_placeholder_text(_("Instruction sent to the AI"))
            prompt_entry.connect(
                "changed", lambda e, idx=i: self._update_recipe(idx, "prompt", e.get_text())
            )
            row.append(prompt_entry)

            del_btn = Gtk.Button(icon_name="user-trash-symbolic")
            del_btn.add_css_class("flat")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text(_("Remove recipe"))
            del_btn.connect("clicked", lambda b, idx=i: self._remove_recipe(idx))
            row.append(del_btn)

            self._recipes_box.append(row)

    @staticmethod
    def _slug(text):
        keep = [c.lower() if c.isalnum() else "_" for c in (text or "").strip()]
        slug = "".join(keep).strip("_") or "recipe"
        return slug

    def _update_recipe(self, idx, key, value):
        if not (0 <= idx < len(self._recipes)):
            return
        self._recipes[idx][key] = value
        if key == "label" and not self._recipes[idx].get("id"):
            self._recipes[idx]["id"] = self._slug(value)
        _s("recipes", self._recipes)

    def _on_add_recipe(self, _btn):
        self._recipes.append(
            {"id": self._slug(f"custom {len(self._recipes) + 1}"),
             "label": _("New recipe"), "prompt": ""}
        )
        _s("recipes", self._recipes)
        self._rebuild_recipes()

    def _remove_recipe(self, idx):
        if 0 <= idx < len(self._recipes):
            self._recipes.pop(idx)
            _s("recipes", self._recipes)
            self._rebuild_recipes()

    def _on_reset_recipes(self, _btn):
        import copy

        from ai_config import DEFAULT_RECIPES

        self._recipes = copy.deepcopy(DEFAULT_RECIPES)
        _s("recipes", self._recipes)
        self._rebuild_recipes()
