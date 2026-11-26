"""Reusable draggable cards using the same gesture and ordering behavior as search cards."""
from PySide6.QtCore import QByteArray,QPoint,QRectF,Qt,Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame,QVBoxLayout,QLabel,QWidget,QProgressBar
from keyword_cards import KeywordBoard,KeywordCard
from interface_scale import apply_scaled_style
from pathlib import Path
from skin_artwork import paint_card_artwork

FEATURE_CARD_ICONS = {
    "knowledge": "M3 5h5a4 4 0 0 1 4 4v12a5 5 0 0 0-4-2H3z M21 5h-5a4 4 0 0 0-4 4v12a5 5 0 0 1 4-2h5z M6 9h2m8 0h2",
    "questions": "M8 17c0-2-1-3-2-4a7 7 0 1 1 12 0c-1 1-2 2-2 4z M9 20h6m-5 2h4 M12 9v4m0-7v.2",
    "reviewer": "M14 3H5v18h14V8l-5-5v5h5 M8 13h5m-5 3h3 M14 16l1.5 1.5L19 14",
    "finder": "M4 3h9l3 3v4m-4 11H4V3 M7 8h5m-5 4h3 M20 17l3 4 M21 13a4 4 0 1 1-8 0a4 4 0 1 1 8 0",
    "figure": "M3 3v18h18 M7 17v-4m5 4V9m5 8V6 M6 10l5-4 3 1 5-4",
    "protocol": "M8 5H5v16h14V5h-3 M8 3h8v5H8z M8 12l1 1 2-2m2 1h3 M8 17l1 1 2-2m2 1h3",
    "lif": "M7 3h8l4 4v13H7z M15 3v4h4 M3 7v15h13 M9 16l3-3 2 2 2-3 2 4",
    "foci": "M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0 M8 9h.1m5-2h.1m4 5h.1m-8 4h.1m5 0h.1",
    "intensity": "M12 2v3m0 14v3 M2 12h3m14 0h3 M5 5l2 2m10 10 2 2 M19 5l-2 2M7 17l-2 2 M12 7a5 5 0 1 1 0 10a5 5 0 0 1 0-10z",
    "fiber": "M5 3c0 7 14 11 14 18 M19 3c0 7-14 11-14 18 M7 5h10 M10 9h4 M10 15h4 M7 19h10",
    "colony": "M3 9c0-3 4-5 9-5s9 2 9 5v6c0 3-4 5-9 5s-9-2-9-5z M3 9c0 3 4 5 9 5s9-2 9-5 M8 9h.1m4-1h.1m3 3h.1",
    "journal": "M3 5h7l2 2h9v13H3z M7 11h10m-10 4h8",
    "impact": "M3 3v18h18 M7 17v-3m5 3v-6m5 6V7 M6 9l5-3 3 1 5-4",
    "curated": "M6 3h13v18H6z M3 6v13 M10 11l1.5 1.5L15 9 M10 16h5",
    "custom": "M3 6h7l2 2h9v12H3z M12 11v6m-3-3h6",
    "mail": "M3 5h18v14H3z M3 6l9 7 9-7 M15 15l-3 2",
    "issue": "M3 4h18v12H9l-5 4v-4H3z M12 7v4m0 2v.1",
    "lab": "M9 3h6m-4 0v7l-6 9v2h14v-2l-6-9V3 M8 16h8",
    "student": "M2 9l10-5 10 5-10 5z M6 11v6c3 3 9 3 12 0v-6 M21 10v7",
    "cloud": "M6 18a4 4 0 0 1-1-8 7 7 0 0 1 13 0 4 4 0 0 1 0 8z M9 14h6",
    "terminal": "M3 5h18v14H3z M7 9l3 3-3 3 M13 15h4",
    "network": "M12 12L5 5m7 7 7-7m-7 7v7 M10 10h4v4h-4z M3 3h4v4H3z M17 3h4v4h-4z M10 17h4v4h-4z",
    "moon": "M19 15A8 8 0 0 1 9 5a8 8 0 1 0 10 10z M18 4v5m-2.5-2.5h5",
    "spark": "M12 2l2 7 7 3-7 2-2 7-2-7-7-2 7-3z M20 2v4m-2-2h4",
    "image": "M3 4h18v16H3z M3 17l6-6 4 4 3-3 5 5 M16 8h.1",
    "layers": "M2 11l10-6 10 6-10 6z M2 15l10 6 10-6",
}

DEPLOYMENT_PALETTES = {
    "deepseek": ("#E4EEF8", "#D2E0ED", "#517CA5", "cloud"),
    "codex": ("#E5EFEC", "#CEE0DA", "#4B7D70", "terminal"),
    "openai": ("#E8F1E6", "#D5E4D0", "#58845D", "network"),
    "kimi": ("#EFE7F6", "#E0D5EB", "#82669C", "moon"),
    "glm": ("#FAEBDF", "#EBD8C7", "#AB7050", "spark"),
    "ark": ("#F8E7E8", "#E9D2D6", "#AC657A", "image"),
    "bailian": ("#F9EFD8", "#EDDFC5", "#A67B3D", "layers"),
    "pi": ("#F9ECD9", "#E9D8BE", "#A77139", "lab"),
    "student": ("#E7F2F0", "#D2E4DF", "#548679", "student"),
}

JOURNAL_PALETTES = {
    "builtin_if_6_10": ("#F9EDD8", "#EBDBC2", "#A77740", "impact"),
    "builtin_if_10_20": ("#E6F0EB", "#D0E3D9", "#4B8671", "impact"),
    "builtin_if_20_500": ("#E8E9F8", "#D8D9EF", "#736BA0", "impact"),
    "builtin_nc_selected": ("#F8E8E6", "#ECD6D2", "#AC6C65", "curated"),
    "builtin_nc_extended": ("#E6EFF8", "#D4E2EF", "#597FA4", "journal"),
}
CUSTOM_JOURNAL_PALETTES = (
    ("#F7EAF4", "#E8D8E4", "#95698A", "custom"),
    ("#F5EBD9", "#E9DDC7", "#97743D", "custom"),
    ("#E9F0EE", "#D7E4DD", "#638A78", "custom"),
)


class AccentCard(QFrame):
    feature_icon = None
    brand_icon = None
    brand_anchor = None

    def apply_appearance(self, tint, border, accent, icon, *, active=False, padding=0):
        from PySide6.QtSvg import QSvgRenderer

        if self.feature_icon is None:
            path = FEATURE_CARD_ICONS[icon]
            svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
                   f'<path d="{path}" fill="none" stroke="{accent}" stroke-width="1.45" '
                   'stroke-linecap="round" stroke-linejoin="round"/></svg>')
            self.feature_icon = QSvgRenderer(QByteArray(svg.encode("ascii")), self)
        selector = f"QFrame#{self.objectName()}"
        self.setStyleSheet(f"""
            {selector} {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #FFFFFF, stop:0.46 #FFFFFF, stop:1 {tint});
                border: {"2px solid " + accent if active else "1px solid " + border};
                border-bottom: 3px solid {accent};
                border-radius: 14px; padding:{padding}px;
            }}
            {selector}:hover, {selector}:focus {{ border-color:{accent}; }}
            {selector} QLabel {{ background:transparent; }}
        """)

    def set_deployment_appearance(self, provider, enabled=False):
        self.apply_appearance(*DEPLOYMENT_PALETTES[provider], active=enabled, padding=8)
        if self.brand_icon is None and provider not in ("pi", "student"):
            from PySide6.QtGui import QPixmap

            icon = QPixmap(str(Path(__file__).resolve().parent / "assets" / "model_icons" / f"{provider}.png"))
            self.brand_icon = icon if not icon.isNull() else False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        paint_card_artwork(self,painter)
        if self.feature_icon is None:
            painter.end()
            return
        painter.setOpacity(0.18)
        size = min(70, self.width() // 3, self.height() - 20)
        if size > 0:
            if self.brand_icon:
                painter.setOpacity(1)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                bottom = self.height() - 13
                if self.brand_anchor is not None:
                    bottom = self.brand_anchor.mapTo(self, QPoint(0, self.brand_anchor.height())).y()
                box = self.brand_icon.size().scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio)
                painter.drawPixmap(QRectF(self.width() - box.width() - 18, bottom - box.height(),
                                         box.width(), box.height()), self.brand_icon, QRectF(self.brand_icon.rect()))
            else:
                self.feature_icon.render(painter, QRectF(self.width() - size - 13, 12, size, size))
        painter.end()


class DirectoryCard(AccentCard):
    clicked=Signal(object)
    mousePressEvent=KeywordCard.mousePressEvent
    mouseMoveEvent=KeywordCard.mouseMoveEvent
    mouseReleaseEvent=KeywordCard.mouseReleaseEvent
    def __init__(self,board,identifier,title,subtitle):
        super().__init__(board)
        self.board=board;self.identifier=identifier;self.custom_width=0;self.busy=False;self.press_position=None
        self.feature_icon = None
        self.setObjectName('Card');self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip('单击打开；按住拖动调整卡片顺序。')
        layout=QVBoxLayout(self);layout.setContentsMargins(14,14,14,14)
        heading=self.title_label=QLabel(title);heading.setWordWrap(True);heading.setTextFormat(Qt.TextFormat.PlainText)
        apply_scaled_style(heading,'font-size:13pt;font-weight:700;border:none;')
        note=self.subtitle_label=QLabel(subtitle);note.setWordWrap(True);note.setStyleSheet('color:#8B7768;border:none;')
        layout.addWidget(heading);layout.addStretch();layout.addWidget(note)
        self.import_status=QLabel();self.import_status.setWordWrap(True);self.import_status.setStyleSheet('font-size:9pt;color:#C96812;border:none;')
        self.import_progress=QProgressBar();self.import_progress.setFixedHeight(8);self.import_progress.setTextVisible(False)
        self.import_progress.setStyleSheet('QProgressBar {background:#FFF3E0;border:none;} QProgressBar::chunk {background:#F28C20;}')
        layout.addWidget(self.import_status);layout.addWidget(self.import_progress)
        self.import_status.hide();self.import_progress.hide()
        for child in self.findChildren(QWidget):child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)

    def set_palette(self, tint, border, accent, icon=None):
        self.setAccessibleName(self.title_label.text())
        self.setAccessibleDescription(self.subtitle_label.text())
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.subtitle_label.setStyleSheet('color:#514940;background:transparent;border:none;')
        self.apply_appearance(tint, border, accent, icon or self.identifier)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.identifier)
            event.accept()
        else:
            super().keyPressEvent(event)

    def set_import_progress(self,completed,total,text):
        self.busy=True;self.import_progress.setRange(0,max(1,total));self.import_progress.setValue(completed)
        self.import_status.setText(text);self.import_status.show();self.import_progress.show()

class CardBoard(KeywordBoard):
    def __init__(self,columns=4):
        super().__init__(columns=columns,card_height=180)
        self.empty.setText('暂无卡片，可添加一个分组或目录。')
    def count(self):return len(self.cards)
    def add_card(self,identifier,title,subtitle,clicked,*,palette=None):
        card=DirectoryCard(self,identifier,title,subtitle)
        if palette is not None:
            card.set_palette(*palette)
        card.clicked.connect(clicked);self.cards.append(card)
        self.schedule_reflow();return card
