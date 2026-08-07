"""
windows/kivy_theme.py

Gemeinsames dunkles Kivy-Theme fuer die beiden eigenstaendigen Windows-Tools
(windows/source/gamification_installer.py und
windows/source2/plugin_packager.py). Farbgebung orientiert sich am dunklen
"Bollisoft"-Look der echten Webshop-Startseite (webshop/static/css/style.css,
.company-page: #080b0d Hintergrund, #58f0a8 Gruenakzent, Cascadia
Code/Consolas fuer Labels) - der Webshop wurde bewusst auf denselben Look
umgestellt, damit Installer/Packager/Webshop als EIN zusammenhaengendes
Produkt wirken. Struktur (Schritt-Karten, Statusfarben Gruen/Orange/Rot fuer
Erfolg/Warnung/Fehler) ist an der Pico-Firmware-Weboberflaeche selbst
(source/index.html) angelehnt.

Beide Tools importieren dieses Modul ueber einen sys.path-Eintrag auf
WINDOWS_DIR (gleiches Muster wie plugin_packager.py's bestehender Import von
tools/build_firmware.py) - windows/build_exe.py macht das Modul zusaetzlich
per "--paths WINDOWS_DIR" fuer PyInstallers statische Import-Analyse
sichtbar, da die reine Laufzeit-sys.path.insert() dafuer nicht ausreicht.
"""
import os

from kivy.clock import mainthread  # noqa: F401 (Re-Export fuer Aufrufer)
from kivy.core.clipboard import Clipboard  # noqa: F401 (Re-Export fuer Aufrufer)
from kivy.core.window import Window
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

# ==================== Farbpalette (siehe webshop/static/css/style.css) ====================

COLOR_BG = (8 / 255, 11 / 255, 13 / 255, 1)            # --company-bg
COLOR_SURFACE = (16 / 255, 21 / 255, 24 / 255, 1)       # --company-surface
COLOR_SURFACE_ALT = (18 / 255, 25 / 255, 29 / 255, 1)   # etwas helleres Karten-Innenleben
COLOR_LINE = (38 / 255, 49 / 255, 55 / 255, 1)          # --company-line
COLOR_TEXT = (240 / 255, 244 / 255, 242 / 255, 1)       # --company-text
COLOR_MUTED = (154 / 255, 171 / 255, 165 / 255, 1)      # --company-muted
COLOR_ACCENT = (88 / 255, 240 / 255, 168 / 255, 1)      # --company-accent (Gruen)
COLOR_ACCENT_PRESSED = (61 / 255, 201 / 255, 138 / 255, 1)
COLOR_ACCENT_DIM = (88 / 255, 240 / 255, 168 / 255, 0.12)
COLOR_WARNING = (243 / 255, 156 / 255, 18 / 255, 1)     # Pico-Arcade-Akzent (Orange)
COLOR_WARNING_PRESSED = (206 / 255, 129 / 255, 10 / 255, 1)
COLOR_DANGER = (224 / 255, 92 / 255, 76 / 255, 1)
COLOR_DANGER_PRESSED = (192 / 255, 57 / 255, 43 / 255, 1)

def _resolve_windows_font(filename):
    """Kivy's font_name braucht einen Dateipfad, keinen Font-Familiennamen
    (anders als CSS' "font-family: Consolas") - loest hier gezielt die auf
    jedem Windows (das einzige Zielsystem beider Tools, siehe
    windows/build_exe.py) mitgelieferte Consolas-TTF auf. Faellt auf None
    (= Kivys Standardschrift Roboto) zurueck, falls die Datei ausnahmsweise
    fehlt, statt beim Start abzustuerzen."""
    path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", filename)
    return path if os.path.isfile(path) else None


FONT_MONO = _resolve_windows_font("consola.ttf")
RADIUS = dp(10)


def apply_window_theme(title, size=(680, 780), min_size=(600, 620)):
    """Grundeinrichtung des Kivy-Fensters: dunkler Hintergrund + Mindestgroesse
    (Window.minimum_width/height existiert seit Kivy 2.1) statt tkinter's
    self.minsize()."""
    Window.clearcolor = COLOR_BG
    Window.title = title
    Window.size = size
    Window.minimum_width = min_size[0]
    Window.minimum_height = min_size[1]


# ==================== Grundbausteine ====================

class Kicker(Label):
    """Kleines, gross geschriebenes Monospace-Label fuer Ueberschriften-Praefixe,
    entspricht den ".company-kicker"/".company-index"-Labeln im Webshop-CSS."""

    def __init__(self, text="", **kwargs):
        kwargs.setdefault("color", COLOR_ACCENT)
        if FONT_MONO:
            kwargs.setdefault("font_name", FONT_MONO)
        kwargs.setdefault("font_size", sp(12))
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(18))
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "middle")
        super().__init__(text=text.upper(), **kwargs)
        self.bind(size=self._sync_text_size)

    def _sync_text_size(self, *_args):
        self.text_size = self.size


class BodyLabel(Label):
    """Mehrzeiliger, linksbuendiger Fließtext in der gedaempften Textfarbe
    (--company-muted) - fuer Beschreibungstexte innerhalb einer SectionCard."""

    def __init__(self, text="", muted=True, **kwargs):
        kwargs.setdefault("color", COLOR_MUTED if muted else COLOR_TEXT)
        kwargs.setdefault("font_size", sp(13.5))
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "top")
        kwargs.setdefault("size_hint_y", None)
        super().__init__(text=text, **kwargs)
        self.bind(width=self._sync_text_size, texture_size=self._sync_height)

    def _sync_text_size(self, *_args):
        self.text_size = (self.width, None)

    def _sync_height(self, *_args):
        self.height = self.texture_size[1]


class HeadingLabel(Label):
    """Fettere Ueberschrift innerhalb einer Karte (z.B. "1. Pico verbinden")."""

    def __init__(self, text="", **kwargs):
        kwargs.setdefault("color", COLOR_TEXT)
        kwargs.setdefault("font_size", sp(16))
        kwargs.setdefault("bold", True)
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "middle")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(26))
        super().__init__(text=text, **kwargs)
        self.bind(size=self._sync_text_size)

    def _sync_text_size(self, *_args):
        self.text_size = self.size


class StatusLabel(Label):
    """Einzeilige Statuszeile (ersetzt tk.Label(textvariable=...)) - Text wird
    per .text direkt aktualisiert, halign/valign sind bereits korrekt
    konfiguriert (Kivy braucht dafuer explizit gesetztes text_size)."""

    def __init__(self, text="", color=None, bold=False, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(20))
        kwargs.setdefault("font_size", sp(13))
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "middle")
        kwargs.setdefault("shorten", False)
        super().__init__(text=text, color=(color or COLOR_MUTED), bold=bold, **kwargs)
        self.bind(size=self._sync_text_size)

    def _sync_text_size(self, *_args):
        self.text_size = self.size


class _RoundedCanvasMixin:
    """Zeichnet eine abgerundete Flaeche (+ optionale Umrandung) im canvas.before
    einer BoxLayout-Unterklasse und haelt sie bei Groessen-/Positionsaenderung
    synchron - Kivy-Widgets haben keine eingebaute "background-color"/"border-
    radius"-CSS-Entsprechung, daher dieser kleine Helfer fuer alle Karten/
    Buttons/Eingabefelder unten."""

    def _setup_rounded_bg(self, bg_color, line_color=None, radius=RADIUS):
        # Radius separat als eigenes Attribut merken statt spaeter aus
        # self._bg_rect.radius[0] zurueckzulesen: Kivy normalisiert den beim
        # RoundedRectangle uebergebenen Wert intern zu (rx, ry)-Tupeln pro
        # Ecke, Line(rounded_rectangle=...) erwartet an dieser Stelle aber
        # eine einzelne Zahl - sonst "TypeError: must be real number, not
        # tuple" beim ersten Resize.
        self._radius = radius
        with self.canvas.before:
            self._bg_color_instr = Color(*bg_color)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[radius])
            if line_color is not None:
                self._line_color_instr = Color(*line_color)
                self._line_instr = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, radius), width=dp(1.1))
            else:
                self._line_instr = None
        self.bind(pos=self._update_rounded_bg, size=self._update_rounded_bg)

    def _update_rounded_bg(self, *_args):
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        if self._line_instr is not None:
            self._line_instr.rounded_rectangle = (self.x, self.y, self.width, self.height, self._radius)

    def set_bg_color(self, color):
        self._bg_color_instr.rgba = color


class SectionCard(_RoundedCanvasMixin, BoxLayout):
    """Eine Schritt-Karte (entspricht tkinter's tk.LabelFrame) mit Titelzeile
    und einem vertikalen Inhaltsbereich (self.body), an das Aufrufer beliebige
    Widgets anhaengen.

    expand=False (Default): Karte + Inhaltsbereich sizen sich selbst auf ihren
    Inhalt (minimum_height) - fuer die normalen, natuerlich hohen Schritt-
    Karten. expand=True: Karte behaelt das von aussen vorgegebene size_hint_y
    (z.B. 1, um restlichen Platz zu fuellen) und auch self.body bekommt
    size_hint_y=1 statt minimum_height - fuer die Protokoll-Karte, deren
    LogPanel den kompletten verbleibenden Platz ausfuellen und selbst scrollen
    soll."""

    def __init__(self, heading, expand=False, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        if not expand:
            kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("padding", (dp(16), dp(14)))
        kwargs.setdefault("spacing", dp(8))
        super().__init__(**kwargs)
        self._setup_rounded_bg(COLOR_SURFACE_ALT, COLOR_LINE)
        if not expand:
            self.bind(minimum_height=self.setter("height"))

        self.add_widget(HeadingLabel(text=heading))
        if expand:
            self.body = BoxLayout(orientation="vertical", spacing=dp(8))
        else:
            self.body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
            self.body.bind(minimum_height=self.body.setter("height"))
        self.add_widget(self.body)

    def add_body(self, widget):
        self.body.add_widget(widget)
        return widget


class ThemedButton(ButtonBehavior, _RoundedCanvasMixin, BoxLayout):
    """Flacher, abgerundeter Button in drei Varianten (primary/secondary/
    danger) statt tk.Button's OS-Standardlook. ButtonBehavior liefert
    on_press/on_release, die Aufrufer wie gewohnt per .bind(on_release=...)
    nutzen."""

    _VARIANTS = {
        "primary": (COLOR_ACCENT, COLOR_ACCENT_PRESSED, (7 / 255, 16 / 255, 11 / 255, 1)),
        "secondary": (COLOR_SURFACE, COLOR_LINE, COLOR_TEXT),
        "danger": (COLOR_DANGER, COLOR_DANGER_PRESSED, COLOR_TEXT),
        "warning": (COLOR_WARNING, COLOR_WARNING_PRESSED, (7 / 255, 16 / 255, 11 / 255, 1)),
    }

    disabled = BooleanProperty(False)

    def __init__(self, text="", variant="secondary", **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(38))
        kwargs.setdefault("padding", (dp(16), 0))
        super().__init__(**kwargs)
        self._normal_color, self._pressed_color, text_color = self._VARIANTS[variant]
        line_color = COLOR_LINE if variant == "secondary" else None
        self._setup_rounded_bg(self._normal_color, line_color)
        self._label = Label(
            text=text, color=text_color, bold=(variant != "secondary"),
            font_size=sp(13.5), halign="center", valign="middle",
        )
        self._label.bind(size=lambda *_a: setattr(self._label, "text_size", self._label.size))
        self.add_widget(self._label)
        self.bind(disabled=self._on_disabled_changed)

    @property
    def text(self):
        return self._label.text

    @text.setter
    def text(self, value):
        self._label.text = value

    def on_press(self):
        if not self.disabled:
            self.set_bg_color(self._pressed_color)

    def on_release(self):
        if not self.disabled:
            self.set_bg_color(self._normal_color)

    def _on_disabled_changed(self, _instance, disabled):
        self._label.opacity = 0.45 if disabled else 1.0
        if disabled:
            r, g, b, a = self._normal_color
            self.set_bg_color((r, g, b, a * 0.35))
        else:
            self.set_bg_color(self._normal_color)


class ThemedProgressBar(_RoundedCanvasMixin, Widget):
    """Duenner, abgerundeter Fortschrittsbalken in der Akzentfarbe statt
    ttk.Progressbar's OS-Standardlook. API-kompatibel genug zu ttk (`.value`
    von 0..max)."""

    value = NumericProperty(0)
    max = NumericProperty(100)

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(8))
        super().__init__(**kwargs)
        self._setup_rounded_bg(COLOR_SURFACE, None, radius=dp(4))
        with self.canvas.after:
            self._fill_color = Color(*COLOR_ACCENT)
            self._fill_rect = RoundedRectangle(pos=self.pos, size=(0, self.height), radius=[dp(4)])
        self.bind(pos=self._update_fill, size=self._update_fill, value=self._update_fill, max=self._update_fill)

    def _update_fill(self, *_args):
        ratio = 0 if not self.max else max(0.0, min(1.0, self.value / self.max))
        self._fill_rect.pos = self.pos
        self._fill_rect.size = (self.width * ratio, self.height)


class ThemedTextInput(TextInput):
    """Dunkel eingefaerbtes TextInput (Login-Felder, kopierbares
    Read-Only-UID-Feld) statt tk.Entry's helles OS-Standardfeld."""

    def __init__(self, **kwargs):
        kwargs.setdefault("multiline", False)
        kwargs.setdefault("background_color", COLOR_SURFACE)
        kwargs.setdefault("foreground_color", COLOR_TEXT)
        kwargs.setdefault("cursor_color", COLOR_ACCENT)
        kwargs.setdefault("selection_color", COLOR_ACCENT_DIM)
        kwargs.setdefault("hint_text_color", COLOR_MUTED)
        kwargs.setdefault("padding", (dp(10), dp(10), dp(10), dp(10)))
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(36))
        super().__init__(**kwargs)


class ThemedSpinnerOption(SpinnerOption):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_color = COLOR_SURFACE
        self.color = COLOR_TEXT


class ThemedSpinner(Spinner):
    """Dunkel eingefaerbtes Dropdown (Mod-Auswahl) statt ttk.Combobox."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(36))
        kwargs.setdefault("background_normal", "")
        kwargs.setdefault("background_color", COLOR_SURFACE)
        kwargs.setdefault("color", COLOR_TEXT)
        kwargs.setdefault("option_cls", ThemedSpinnerOption)
        super().__init__(**kwargs)


class LogPanel(BoxLayout):
    """Ersetzt tk.Text als scrollbares, monospace Protokollfenster."""

    def __init__(self, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        label_kwargs = dict(
            text="", color=COLOR_MUTED, font_size=sp(12),
            halign="left", valign="top", size_hint_y=None, padding=(dp(10), dp(8)),
        )
        if FONT_MONO:
            label_kwargs["font_name"] = FONT_MONO
        self._label = Label(**label_kwargs)
        self._label.bind(width=self._sync_text_size, texture_size=self._sync_height)
        self._scroll = ScrollView(do_scroll_x=False)
        self._scroll.add_widget(self._label)
        self.add_widget(self._scroll)

    def _sync_text_size(self, *_args):
        self._label.text_size = (self._label.width, None)

    def _sync_height(self, *_args):
        self._label.height = max(self._label.texture_size[1], self._scroll.height)

    def write(self, message):
        self._label.text = (self._label.text + "\n" + message) if self._label.text else message
        self._scroll.scroll_y = 0


class Divider(Widget):
    """Duenne horizontale Trennlinie (--company-line)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(1))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*COLOR_LINE)
            self._line = RoundedRectangle(pos=self.pos, size=self.size, radius=[0])
        self.bind(pos=self._update, size=self._update)

    def _update(self, *_args):
        self._line.pos = self.pos
        self._line.size = self.size


# ==================== Popups (ersetzen tkinter.messagebox) ====================

class _MessagePopup(Popup):
    def __init__(self, title, message, accent=COLOR_ACCENT, buttons=None, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(420), dp(220)))
        kwargs.setdefault("auto_dismiss", False)
        kwargs.setdefault("separator_color", accent)
        kwargs.setdefault("title_color", COLOR_TEXT)
        kwargs.setdefault("background", "")
        kwargs.setdefault("background_color", COLOR_SURFACE)
        super().__init__(title=title, **kwargs)

        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(14))
        body = BodyLabel(text=message, muted=False)
        body.bind(width=lambda *_a: setattr(body, "text_size", (body.width, None)))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(body)
        root.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        btn_row.add_widget(Widget())
        for text, variant, handler in (buttons or [("OK", "primary", self.dismiss)]):
            btn = ThemedButton(text=text, variant=variant, size_hint_x=None, width=dp(120))
            btn.bind(on_release=lambda *_a, h=handler: h())
            btn_row.add_widget(btn)
        root.add_widget(btn_row)
        self.content = root


def show_info(title, message):
    _MessagePopup(title, message, accent=COLOR_ACCENT).open()


def show_error(title, message):
    _MessagePopup(title, message, accent=COLOR_DANGER).open()


def confirm(title, message, on_yes, yes_text="Ja", no_text="Abbrechen", danger=False):
    """Asynchrones Gegenstueck zu tkinter's blockierendem
    messagebox.askyesno() - ruft on_yes() erst auf, wenn der Nutzer bestaetigt
    hat, statt den Rueckgabewert synchron zurueckzugeben (Kivy-Popups sind
    nicht-blockierend)."""
    popup_ref = {}

    def _confirm():
        popup_ref["popup"].dismiss()
        on_yes()

    popup = _MessagePopup(
        title, message, accent=COLOR_DANGER if danger else COLOR_ACCENT,
        buttons=[
            (no_text, "secondary", lambda: popup_ref["popup"].dismiss()),
            (yes_text, "danger" if danger else "primary", _confirm),
        ],
    )
    popup_ref["popup"] = popup
    popup.open()


class ChoicePopup(Popup):
    """Modale Auswahlliste (ersetzt AssetChoiceDialog's Radiobutton-Liste) -
    ein Klick markiert eine Zeile, "Bestaetigen" ruft on_confirm(key) mit der
    zuletzt markierten Option auf."""

    def __init__(self, title, options, on_confirm, confirm_text="Auswaehlen", **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(460), dp(min(120 + 46 * len(options), 460))))
        kwargs.setdefault("auto_dismiss", False)
        kwargs.setdefault("separator_color", COLOR_ACCENT)
        kwargs.setdefault("title_color", COLOR_TEXT)
        kwargs.setdefault("background", "")
        kwargs.setdefault("background_color", COLOR_SURFACE)
        super().__init__(title=title, **kwargs)

        self._on_confirm = on_confirm
        self._selected_key = options[0][0] if options else None
        self._rows = {}

        root = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        options_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        options_box.bind(minimum_height=options_box.setter("height"))
        for key, label_text in options:
            row = _SelectableRow(key=key, text=label_text, selected=(key == self._selected_key))
            row.bind(on_release=self._on_row_selected)
            self._rows[key] = row
            options_box.add_widget(row)
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(options_box)
        root.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(10))
        btn_row.add_widget(Widget())
        cancel_btn = ThemedButton(text="Abbrechen", variant="secondary", size_hint_x=None, width=dp(120))
        cancel_btn.bind(on_release=lambda *_a: self.dismiss())
        confirm_btn = ThemedButton(text=confirm_text, variant="primary", size_hint_x=None, width=dp(150))
        confirm_btn.bind(on_release=self._on_confirm_pressed)
        btn_row.add_widget(cancel_btn)
        btn_row.add_widget(confirm_btn)
        root.add_widget(btn_row)
        self.content = root

    def _on_row_selected(self, row):
        self._selected_key = row.key
        for key, other in self._rows.items():
            other.selected = (key == row.key)

    def _on_confirm_pressed(self, *_args):
        self.dismiss()
        if self._selected_key is not None:
            self._on_confirm(self._selected_key)


class _SelectableRow(ButtonBehavior, _RoundedCanvasMixin, BoxLayout):
    key = StringProperty("")
    selected = BooleanProperty(False)

    def __init__(self, key, text, selected=False, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(40))
        kwargs.setdefault("padding", (dp(12), 0))
        super().__init__(key=key, selected=selected, **kwargs)
        self._setup_rounded_bg(COLOR_ACCENT_DIM if selected else COLOR_SURFACE, COLOR_LINE, radius=dp(6))
        self._label = Label(text=text, color=COLOR_TEXT, font_size=sp(13), halign="left", valign="middle")
        self._label.bind(size=lambda *_a: setattr(self._label, "text_size", self._label.size))
        self.add_widget(self._label)
        self.bind(selected=self._on_selected_changed)

    def _on_selected_changed(self, _instance, selected):
        self.set_bg_color(COLOR_ACCENT_DIM if selected else COLOR_SURFACE)
