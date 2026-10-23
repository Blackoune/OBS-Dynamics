"""Fenêtre Banque de mots des sous-titres.

Une ligne par mot : écriture correcte, façons dont le moteur l'entend
(séparées par des virgules), type. Chaque modification est appliquée aux
sous-titres suivants ; l'écriture sur disque attend une courte pause, comme
la fenêtre Style. Un champ d'essai montre tout de suite ce que la banque fait
d'une phrase.
"""
from __future__ import annotations

from typing import Callable, Optional

import subtitles_words as sw
from i18n import t
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER, COL_CARD,
                       COL_TEXT, COL_TEXT_MUTED, SmoothScroll, ctk, font)

SAVE_DELAY_MS = 400
_KIND_KEYS = {"name": "SUBS_WORDS_KIND_NAME", "game": "SUBS_WORDS_KIND_GAME",
              "emote": "SUBS_WORDS_KIND_EMOTE", "other": "SUBS_WORDS_KIND_OTHER"}


class _Row:
    def __init__(self, dialog: "WordBankDialog", parent, word: sw.Word) -> None:
        self.frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.frame.grid_columnconfigure((0, 1), weight=1)
        changed = dialog.changed
        self.text = ctk.CTkEntry(self.frame, height=32, font=font(12), fg_color=COL_BG,
                                 border_color=COL_BORDER, text_color=COL_TEXT)
        self.text.insert(0, word.text)
        self.text.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.heard = ctk.CTkEntry(self.frame, height=32, font=font(12), fg_color=COL_BG,
                                  border_color=COL_BORDER, text_color=COL_TEXT)
        self.heard.insert(0, ", ".join(word.heard))
        self.heard.grid(row=0, column=1, sticky="ew", padx=(0, 6))
        for entry in (self.text, self.heard):
            entry.bind("<KeyRelease>", lambda _e: changed())
        self._labels = {t(k): kind for kind, k in _KIND_KEYS.items()}
        self.kind = ctk.CTkOptionMenu(self.frame, values=list(self._labels), width=110,
                                      height=32, font=font(12), fg_color=COL_BG,
                                      button_color=COL_BORDER, text_color=COL_TEXT,
                                      dropdown_fg_color=COL_CARD,
                                      command=lambda _v: changed())
        self.kind.set(t(_KIND_KEYS[word.kind]))
        self.kind.grid(row=0, column=2, padx=(0, 6))
        ctk.CTkButton(self.frame, text=t("SUBS_WORDS_REMOVE"), width=80, height=32,
                      corner_radius=8, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER, hover_color=COL_BG,
                      text_color=COL_TEXT_MUTED, font=font(11),
                      command=lambda: dialog.remove(self)).grid(row=0, column=3)

    def word(self) -> dict:
        return {"text": self.text.get(),
                "heard": [h for h in self.heard.get().split(",")],
                "kind": self._labels.get(self.kind.get(), "other")}


class WordBankDialog(ctk.CTkToplevel):

    def __init__(self, master, words: list[sw.Word],
                 on_change: Callable[[list[sw.Word], bool], None]) -> None:
        """`on_change(words, persist)` : persist=False pendant la frappe."""
        super().__init__(master, fg_color=COL_BG)
        self.title(t("SUBS_WORDS_TITLE"))
        self.geometry("720x640")
        self.minsize(560, 420)
        self.transient(master.winfo_toplevel())
        self._on_change = on_change
        self._save_job: Optional[str] = None
        self._rows: list[_Row] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(self, text=t("SUBS_WORDS_HINT"), font=font(11), text_color=COL_TEXT_MUTED,
                     justify="left", wraplength=660, anchor="w").grid(
            row=0, column=0, sticky="ew", padx=24, pady=(18, 8))

        trial = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12,
                             border_width=1, border_color=COL_BORDER)
        trial.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 8))
        trial.grid_columnconfigure(0, weight=1)
        self._trial = ctk.CTkEntry(trial, height=32, font=font(12), fg_color=COL_BG,
                                   border_color=COL_BORDER, text_color=COL_TEXT,
                                   placeholder_text=t("SUBS_WORDS_TRY"))
        self._trial.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))
        self._trial.bind("<KeyRelease>", lambda _e: self._update_trial())
        self._result = ctk.CTkLabel(trial, text="", font=font(12), text_color=COL_TEXT,
                                    anchor="w", justify="left", wraplength=640)
        self._result.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=2, column=0, sticky="ew", padx=(30, 120))
        head.grid_columnconfigure((0, 1), weight=1)
        for col, key in enumerate(("SUBS_WORDS_COL_TEXT", "SUBS_WORDS_COL_HEARD",
                                   "SUBS_WORDS_COL_KIND")):
            ctk.CTkLabel(head, text=t(key), font=font(11, "bold"), text_color=COL_TEXT_MUTED,
                         anchor="w", width=110 if col == 2 else 0).grid(
                row=0, column=col, sticky="ew", padx=(0, 6))

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=3, column=0, sticky="nsew", padx=20)
        self._list.grid_columnconfigure(0, weight=1)
        self._scroller = SmoothScroll(self._list)
        for word in words:
            self._add_row(word)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=24, pady=14)
        self._add_btn = ctk.CTkButton(bar, text=t("SUBS_WORDS_ADD"), width=160, height=36,
                                      corner_radius=9, fg_color="transparent", border_width=1,
                                      border_color=COL_BORDER, hover_color=COL_CARD,
                                      text_color=COL_TEXT, font=font(12),
                                      command=self._add_empty)
        self._add_btn.pack(side="left")
        ctk.CTkButton(bar, text=t("SUBS_STYLE_CLOSE"), width=130, height=36, corner_radius=9,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                      font=font(12, "bold"), command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_add()
        self._lift_job = self.after(50, self.lift)

    # -- lignes ------------------------------------------------------------ #

    def _add_row(self, word: sw.Word) -> _Row:
        row = _Row(self, self._list, word)
        row.frame.grid(row=len(self._rows), column=0, sticky="ew", pady=3)
        self._rows.append(row)
        return row

    def _add_empty(self) -> None:
        if len(self._rows) >= sw.MAX_WORDS:
            return
        self._add_row(sw.Word("")).text.focus_set()
        self._refresh_add()

    def remove(self, row: _Row) -> None:
        self._rows.remove(row)
        row.frame.destroy()
        for i, r in enumerate(self._rows):
            r.frame.grid_configure(row=i)
        self._refresh_add()
        self.changed()

    def _refresh_add(self) -> None:
        self._add_btn.configure(state="normal" if len(self._rows) < sw.MAX_WORDS else "disabled")

    # -- valeurs ----------------------------------------------------------- #

    def words(self) -> list[sw.Word]:
        """Les lignes valides ; une ligne vide ou en cours d'écriture est ignorée."""
        return sw.from_dict([r.word() for r in self._rows])

    def changed(self) -> None:
        self._on_change(self.words(), False)
        self._update_trial()
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(SAVE_DELAY_MS, self._save)

    def _update_trial(self) -> None:
        sentence = self._trial.get()
        fixed = sw.Corrector(self.words()).apply(sentence) if sentence else ""
        self._result.configure(text=t("SUBS_WORDS_RESULT", text=fixed) if sentence else "")

    def _save(self) -> None:
        self._save_job = None
        self._on_change(self.words(), True)

    def close(self) -> None:
        self.after_cancel(self._lift_job)
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save()
        self.destroy()
