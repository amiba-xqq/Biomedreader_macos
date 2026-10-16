from __future__ import annotations

import csv
import html
import json
import math
import re
import shutil
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSlider,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from catalogue import NLM_URL, parse_nlm
from favorite_bundle import export_bundle, import_bundle
from refresh import refresh_search
from keyword_cards import KeywordBoard, KeywordCard
from data_backup import backup_data_directory
from path_dialog import QFileDialog
from interface_scale import scaled,InterfaceScale
from card_board import CardBoard
from journal_bundle import export_journal_bundle, import_journal_bundle
from source_modes import cache_mode,profile_settings
from website_browser import WebsiteBrowser
from paged_table import PagedArticleTable,PAGE_SIZE
from article_types import ARTICLE_TYPES, matches_type, normalize_types


class CandidateInput(QLineEdit):
    """Text input avoids QSpinBox's fixed 32-bit maximum; zero means fetch all pages."""
    def __init__(self):
        super().__init__()
        self.setPlaceholderText("正整数；0 = 不设上限（全部分页获取）")

    def setValue(self, value):
        self.setText(str(value))

    def value(self):
        text = self.text().strip()
        if not re.fullmatch(r"\d+", text):
            raise ValueError("候选数请输入非负整数，0 表示不限。")
        return int(text)


class BackgroundOperation(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = SearchTaskSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.operation(self.signals.progress.emit))
        except Exception as error:
            self.signals.failed.emit(str(error))


class NumberItem(QTableWidgetItem):
    def __lt__(self, other):
        try:
            left = float(self.text()) if self.text().strip() else float('-inf')
            right = float(other.text()) if other.text().strip() else float('-inf')
        except ValueError:
            return self.text() < other.text()
        return left < right

from database import Database
from literature import LiteratureClient, SearchReport


ORANGE = "#F28C28"
DEEP_ORANGE = "#C96812"
PALE_ORANGE = "#FFF3E6"
INK = "#30251D"
MUTED = "#75685F"


APP_STYLE = f"""
QWidget {{
    color: {INK};
    font-family: "Segoe UI", "Microsoft YaHei UI", "Helvetica Neue", "PingFang SC", sans-serif;
    font-size: 10pt;
}}
QMainWindow, QWidget#RootWidget {{ background: #FFFDFC; }}
QFrame#Sidebar {{ background: #2F241D; border: none; }}
QLabel#Brand {{ color: white; font-size: 17pt; font-weight: 700; padding: 18px 12px; }}
QPushButton#NavButton {{
    color: #F8EBDD; background: transparent; border: none; border-radius: 8px;
    text-align: left; padding: 12px 16px; font-size: 11pt;
}}
QPushButton#NavButton:hover {{ background: #4B382C; }}
QPushButton#NavButton:checked {{ background: {ORANGE}; color: white; font-weight: 650; }}
QPushButton {{
    background: {ORANGE}; color: white; border: none; border-radius: 7px;
    padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {DEEP_ORANGE}; }}
QPushButton:disabled {{ background: #D7C8BC; color: #F7F1EC; }}
QPushButton[secondary="true"] {{
    color: {DEEP_ORANGE}; background: {PALE_ORANGE}; border: 1px solid #F2BE88;
}}
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget {{
    background: white; border: 1px solid #DDCFC3; border-radius: 6px; padding: 5px;
    selection-background-color: #F6B36B;
}}
QTableWidget {{ gridline-color: #EFE3D8; }}
QHeaderView::section {{
    background: {PALE_ORANGE}; color: {INK}; border: none; border-bottom: 1px solid #E6C5A4;
    padding: 8px; font-weight: 650;
}}
QFrame#Card {{
    background: white; border: 1px solid #EAD6C3; border-radius: 12px;
}}
QFrame#Card:hover {{ border: 2px solid {ORANGE}; }}
QLabel#PageTitle {{ font-size: 22pt; font-weight: 720; color: {INK}; }}
QLabel#SectionTitle {{ font-size: 14pt; font-weight: 700; color: {INK}; }}
QLabel#Muted {{ color: {MUTED}; }}
QGroupBox {{
    border: 1px solid #EAD6C3; border-radius: 10px; margin-top: 12px; padding-top: 12px;
    font-weight: 650;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; }}
QScrollArea {{ border: none; background: transparent; }}
"""


def parse_iso_date(value: str | None) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime(1900, 1, 1, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        try:
            return datetime(int(text[:4]), 1, 1, tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime(1900, 1, 1, tzinfo=timezone.utc)


def sort_articles(articles: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Apply the same offline ordering rules used by every article-list page."""
    if not articles:
        return []
    maximum_relevance = max(float(item.get("relevance_score") or 0) for item in articles) or 1.0
    now = datetime.now(timezone.utc)
    enriched = []
    for article in articles:
        copied = dict(article)
        date = parse_iso_date(copied.get("publication_date"))
        age_days = max(0.0, (now - date).total_seconds() / 86400)
        copied["_date"] = date
        copied["_combined"] = (
            0.72 * (float(copied.get("relevance_score") or 0) / maximum_relevance)
            + 0.28 * (0.5 ** (age_days / 180.0))
        )
        enriched.append(copied)
    if mode == "最新发表":
        return sorted(enriched, key=lambda item: (item["_date"], -int(item['website_rank']) if item.get('website_rank') is not None else item.get("relevance_score", 0)), reverse=True)
    if mode == "最相关":
        return sorted(enriched, key=lambda item: (item.get("relevance_score", 0), item["_date"]), reverse=True)
    if mode == "收藏时间":
        return sorted(enriched, key=lambda item: str(item.get("favorite_saved_at") or ""), reverse=True)
    return sorted(enriched, key=lambda item: (item["_combined"], item["_date"]), reverse=True)


def local_refresh_time(value):
    """Keep UTC storage and display the computer's current local timezone."""
    if not value:
        return "从未刷新"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)


class ClickableCard(QFrame):
    clicked = Signal(object)

    def __init__(self, payload: Any, title: str, subtitle: str, accent: str = ORANGE):
        super().__init__()
        self.payload = payload
        self.setObjectName("Card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(250, 150)
        self.setMaximumHeight(190)
        layout = QVBoxLayout(self)
        stripe = QFrame()
        stripe.setFixedHeight(6)
        stripe.setStyleSheet(f"background:{accent}; border-radius:3px;")
        title_label = QLabel(title)
        title_label.setWordWrap(True)
        title_label.setStyleSheet("font-size:14pt; font-weight:700; border:none;")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setWordWrap(True)
        subtitle_label.setObjectName("Muted")
        subtitle_label.setStyleSheet("border:none;")
        layout.addWidget(stripe)
        layout.addWidget(title_label)
        layout.addStretch()
        layout.addWidget(subtitle_label)

    def mousePressEvent(self, event):  # noqa: N802 - Qt naming convention
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.payload)
        super().mousePressEvent(event)


class ArticleTable(QTableWidget):
    article_activated = Signal(object)

    def __init__(self):
        super().__init__(0, 7)
        self.articles_by_key: dict[str, dict[str, Any]] = {}
        self.setHorizontalHeaderLabels(["收藏", "标题", "期刊", "发表日期", "数据源", "DOI", "JIF"])
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(36)
        for column, width in enumerate((46, 500, 155, 100, 100, 125, 65)):
            self.setColumnWidth(column, width)
        self.setWordWrap(False)
        self.doubleClicked.connect(self._emit_current)

    def set_articles(self, articles: list[dict[str, Any]]) -> None:
        self.setRowCount(0)
        self.articles_by_key = {article["article_key"]: article for article in articles}
        for article in articles:
            row = self.rowCount()
            self.insertRow(row)
            favorite_item = QTableWidgetItem("★" if article.get("is_favorite_in_profile", article.get("is_favorite")) else "")
            favorite_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            title_item = QTableWidgetItem(str(article.get("title") or "无标题"))
            title_item.setData(Qt.ItemDataRole.UserRole, article["article_key"])
            values = [
                favorite_item,
                title_item,
                QTableWidgetItem(str(article.get("journal_title") or "")),
                QTableWidgetItem(str(article.get("publication_date") or "")),
                QTableWidgetItem(str(article.get("source_name") or "")),
                QTableWidgetItem(str(article.get("doi") or "")),
                QTableWidgetItem(f"{article['jif']:g}" if article.get("jif") is not None else ""),
            ]
            for column, item in enumerate(values):
                item.setToolTip(item.text())
                if column == 6 and article.get("jif") is not None:
                    item.setToolTip(str(article.get("jif_source") or "") + "\n指标年份：" + str(article.get("jif_year") or "未核验 / 未提供"))
                self.setItem(row, column, item)
        if articles:
            self.selectRow(0)

    def current_article(self) -> dict[str, Any] | None:
        row = self.currentRow()
        if row < 0 or not self.item(row, 1):
            return None
        key = self.item(row, 1).data(Qt.ItemDataRole.UserRole)
        return self.articles_by_key.get(key)

    def _emit_current(self) -> None:
        article = self.current_article()
        if article:
            self.article_activated.emit(article)


class ArticleDialog(QDialog):
    favorite_changed = Signal()
    hidden_changed = Signal()
    doi_visited = Signal()

    def __init__(self, database: Database, article: dict[str, Any], parent=None, *, profile_id=None, allow_pdf=False):
        super().__init__(parent)
        self.database = database
        self.article = article
        self.profile_id = profile_id
        self.allow_pdf = allow_pdf
        self.source_mode = database.profile_cache_mode(profile_id) if profile_id is not None else 'both'
        self.cache_generation = database.cache_refreshed_at(profile_id) if profile_id is not None else ""
        self.setWindowTitle("文章详情")
        self.resize(900, 720)

        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        # Handle the link ourselves. QTextBrowser's default openLinks would also navigate
        # the embedded document to the external URL after anchorClicked, blanking it.
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.anchorClicked.connect(self.open_article_link)
        self.browser=browser
        layout.addWidget(browser, 1)

        if allow_pdf:
            pdf_row = QHBoxLayout()
            self.link_pdf_button = QPushButton("链接文章 · 浏览文件夹")
            self.link_pdf_button.setToolTip("选择下载好的 PDF，复制到本地数据目录的 paper 文件夹，并以文章标题命名。")
            self.open_pdf_button = QPushButton("查看原文")
            self.pdf_status = QLabel()
            self.pdf_status.setWordWrap(True)
            pdf_row.addWidget(self.link_pdf_button)
            pdf_row.addWidget(self.open_pdf_button)
            pdf_row.addStretch()
            layout.addLayout(pdf_row)
            layout.addWidget(self.pdf_status)
            self.link_pdf_button.clicked.connect(self.link_local_pdf)
            self.open_pdf_button.clicked.connect(self.open_local_pdf)
            self.update_pdf_state()

        self.tags = QLineEdit(str(article.get("tags") or ""))
        self.tags.setPlaceholderText("标签，多个标签使用逗号分隔")
        self.note = QPlainTextEdit(str(article.get("personal_note") or ""))
        self.note.setPlaceholderText("个人笔记")
        self.note.setMaximumHeight(100)
        layout.addWidget(self.tags)
        layout.addWidget(self.note)

        button_row = QHBoxLayout()
        self.favorite_button = QPushButton(
            "取消本组收藏" if self.is_saved_here() and profile_id is not None else
            "取消收藏" if self.is_saved_here() else "加入本组收藏" if profile_id is not None else "加入收藏"
        )
        save_button = QPushButton("保存标签和笔记")
        close_button = QPushButton("关闭")
        close_button.setProperty("secondary", True)
        button_row.addWidget(self.favorite_button)
        button_row.addWidget(save_button)
        if profile_id is not None:
            self.hidden_button=QPushButton('取消隐藏' if database.matches_hidden_article(article,database.hidden_article_tokens(profile_id)) else '隐藏文章')
            self.hidden_button.clicked.connect(self.toggle_hidden)
            button_row.addWidget(self.hidden_button)
        button_row.addStretch()
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
        self.favorite_button.clicked.connect(self._toggle_favorite)
        save_button.clicked.connect(self._save_notes)
        close_button.clicked.connect(self.accept)
        self.render_article()
        self.abstract_task=None
        self.abstract_status=QLabel()
        self.abstract_status.setWordWrap(True)
        layout.addWidget(self.abstract_status)
        if any(source in str(article.get('source_name') or '') for source in ('bioRxiv','medRxiv')) and not article.get('abstract') and article.get('doi'):
            QTimer.singleShot(0,self.load_website_abstract)

    def render_article(self):
        article=self.article
        title = html.escape(str(article.get("title") or "无标题"))
        abstract = html.escape(str(article.get("abstract") or "数据源未提供摘要。")).replace("\n", "<br>")
        authors = html.escape(str(article.get("authors") or "作者信息缺失"))
        journal = html.escape(str(article.get("journal_title") or "期刊信息缺失"))
        date = html.escape(str(article.get("publication_date") or "日期未知"))
        source = html.escape(str(article.get("source_name") or "来源未知"))
        doi = html.escape(str(article.get("doi") or "无"))
        url = html.escape(str(article.get("landing_page_url") or ""), quote=True)
        jif = article.get("jif")
        jif_text = f" · JIF {jif:g} ({article.get('jif_year') or '年份未知'})" if jif is not None else ""
        link_block = f'<a href="{url}">打开 DOI / 文章页面</a>' if url else ""
        self.browser.setHtml(
            f"""
            <style>body{{font-family:'Segoe UI','Microsoft YaHei UI','Helvetica Neue','PingFang SC',sans-serif;line-height:1.6;color:#30251D}}
            h1{{color:#C96812}} .meta{{color:#6F6259}} a{{color:#C96812}}</style>
            <h1>{title}</h1><p class="meta">{authors}</p>
            <p><b>{journal}</b> · {date}{jif_text}</p>
            <p>DOI: {doi} · 数据源：{source} · {link_block}</p>
            <h3>摘要</h3><p>{abstract}</p>
            """
        )

    def toggle_hidden(self):
        hidden=self.database.matches_hidden_article(self.article,self.database.hidden_article_tokens(self.profile_id))
        self.database.set_article_hidden(self.profile_id,self.article,not hidden)
        self.hidden_button.setText('隐藏文章' if hidden else '取消隐藏')
        self.hidden_changed.emit()

    def load_website_abstract(self):
        from biorxiv_website import fetch_detail
        article=dict(self.article)
        def retrieve(progress):
            client=LiteratureClient('')
            try:
                raw=fetch_detail(client.session,article['doi'],article.get('preprint_version',''),article.get('preprint_server','medrxiv' if 'medRxiv' in article.get('source_name','') else 'biorxiv'))
                detail,_=client.normalize_biorxiv(raw,'')
                self.database.update_biorxiv_detail(article['article_key'],detail)
                return detail
            finally:client.session.close()
        self.abstract_task=BackgroundOperation(retrieve)
        self.abstract_status.setText('正在获取摘要……')
        self.abstract_task.signals.finished.connect(self.abstract_loaded)
        self.abstract_task.signals.failed.connect(self.abstract_failed)
        QThreadPool.globalInstance().start(self.abstract_task)

    def abstract_loaded(self, detail):
        self.article.update({key:detail.get(key) for key in ('abstract','authors','publication_date','publication_year','preprint_version')})
        self.render_article()
        self.abstract_status.setText('摘要已保存，可离线查看。')
        self.abstract_task=None
        self.favorite_changed.emit()

    def abstract_failed(self, message):
        self.abstract_status.setText('暂时无法获取摘要，可通过 DOI 查看原文。')
        self.abstract_status.setToolTip(message)
        self.abstract_task=None

    def open_article_link(self, url):
        if url.scheme() not in ("https", "http"):
            return
        if QDesktopServices.openUrl(url):
            if self.profile_id is not None and self.article.get("doi") and url.host().casefold() in ("doi.org", "dx.doi.org", "www.doi.org"):
                if self.database.mark_doi_visited(self.profile_id, self.source_mode,
                                                 self.article["article_key"], self.cache_generation):
                    self.doi_visited.emit()
        else:
            QMessageBox.warning(self, "无法打开链接", "请检查系统默认浏览器设置。")

    def is_saved_here(self):
        if self.profile_id is not None:
            return self.article["article_key"] in self.database.favorite_keys_for_profile(self.profile_id)
        return bool(self.article.get("is_favorite"))

    def update_pdf_state(self):
        path = self.database.linked_pdf(self.article["article_key"])
        self.open_pdf_button.setEnabled(path is not None)
        self.pdf_status.setText(f"已链接：{path.name}" if path else "尚未链接本地 PDF；请选择已经下载好的文章文件。")
        self.pdf_status.setToolTip(str(path) if path else str(self.database.paper_directory))

    def link_local_pdf(self):
        source, _ = QFileDialog.getOpenFileName(self, "浏览文件夹 · 选择下载好的文章 PDF", "", "PDF 文件 (*.pdf)")
        if not source:
            return
        try:
            self.database.link_pdf(self.article["article_key"], Path(source))
            self.update_pdf_state()
        except (OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "链接失败", str(error))

    def open_local_pdf(self):
        path = self.database.linked_pdf(self.article["article_key"])
        self.update_pdf_state()
        if path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            QMessageBox.warning(self, "无法打开", "请检查 Windows 的默认 PDF 阅读器设置。")

    def _toggle_favorite(self) -> None:
        favorite = not self.is_saved_here()
        try:
            self.database.set_favorite(
                self.article["article_key"], favorite, self.tags.text().strip(), self.note.toPlainText().strip(), profile_id=self.profile_id
            )
        except (OSError,sqlite3.Error) as error:
            QMessageBox.warning(self,'取消收藏失败','关联 PDF 可能正在被占用，请关闭阅读器后重试。\n'+str(error))
            return
        self.article["is_favorite"] = 1 if favorite else 0
        self.favorite_button.setText(("取消本组收藏" if favorite else "加入本组收藏") if self.profile_id is not None else ("取消收藏" if favorite else "加入收藏"))
        if self.allow_pdf:self.update_pdf_state()
        self.favorite_changed.emit()

    def _save_notes(self) -> None:
        self.database.save_favorite_notes(
            self.article["article_key"], self.tags.text().strip(), self.note.toPlainText().strip()
        )
        self.article["tags"] = self.tags.text().strip()
        self.article["personal_note"] = self.note.toPlainText().strip()
        QMessageBox.information(self, "已保存", "标签和笔记已保存到本地数据库。")
        self.favorite_changed.emit()


class YearComboBox(QComboBox):
    """Editable year dropdown bounded by the computer's current calendar year."""
    def __init__(self, value=None):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.addItem("不限", None)
        for year in range(datetime.now().year, 1799, -1):
            self.addItem(str(year), year)
        if value is not None:
            self.setEditText(str(value))
        self.setMaxVisibleItems(12)
        self.setToolTip("可下拉选择或手动输入年份；留空或‘不限’表示不限制。最大为电脑当前年份。")

    def currentData(self, role=Qt.ItemDataRole.UserRole):
        if role != Qt.ItemDataRole.UserRole:
            return super().currentData(role)
        text = self.currentText().strip()
        if text in ("", "不限"):
            return None
        if not re.fullmatch(r"[0-9]{4}", text) or not 1000 <= int(text) <= datetime.now().year:
            raise ValueError(f"请输入 1000—{datetime.now().year} 之间的四位年份，或选择‘不限’。")
        return int(text)

    def showPopup(self):
        text = self.currentText()
        current = datetime.now().year
        if self.itemData(1) != current:
            self.clear()
            self.addItem("不限", None)
            for year in range(current, 1799, -1):
                self.addItem(str(year), year)
        self.setEditText(text)
        super().showPopup()


class ArticleTypeSelector(QListWidget):
    """Visible checkboxes make multi-selection clear; 'all' excludes specific filters."""
    def __init__(self, selected="all"):
        super().__init__()
        self.setMaximumHeight(110)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setToolTip("可以勾选一种或多种，检索任一所选类型；‘全部’表示不限制类型。")
        chosen = normalize_types(selected)
        for label, value in ARTICLE_TYPES:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if value in chosen else Qt.CheckState.Unchecked)
            self.addItem(item)
        self.itemChanged.connect(self._changed)

    def _changed(self, item):
        self.blockSignals(True)
        if item.checkState() == Qt.CheckState.Checked:
            if item.data(Qt.ItemDataRole.UserRole) == "all":
                for index in range(1, self.count()):
                    self.item(index).setCheckState(Qt.CheckState.Unchecked)
            else:
                self.item(0).setCheckState(Qt.CheckState.Unchecked)
        if not any(self.item(i).checkState() == Qt.CheckState.Checked for i in range(self.count())):
            self.item(0).setCheckState(Qt.CheckState.Checked)
        self.blockSignals(False)

    def selected_types(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())
                if self.item(i).checkState() == Qt.CheckState.Checked]

    def currentData(self):
        values = self.selected_types()
        return values[0] if len(values) == 1 else values

    def findData(self, value):
        return next((i for i in range(self.count()) if self.item(i).data(Qt.ItemDataRole.UserRole) == value), -1)

    def setCurrentIndex(self, index):
        self.blockSignals(True)
        for i in range(self.count()):
            self.item(i).setCheckState(Qt.CheckState.Checked if i == index else Qt.CheckState.Unchecked)
        self.blockSignals(False)


class ProfileDialog(QDialog):
    def __init__(self, database: Database, profile: dict[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.database = database
        self.profile = profile or {}
        self.setWindowTitle("编辑关键词方案" if profile else "添加关键词方案")
        self.resize(760, 680)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(str(self.profile.get("name") or ""))
        self.query = QPlainTextEdit(str(self.profile.get("query_expression") or ""))
        self.query.setPlaceholderText('例如：(R-loop OR "RNA-DNA hybrid") AND replication')
        self.query.setToolTip("bioRxiv和medRxiv使用各自官网默认关键词搜索，可能命中正文和参考文献。不额外添加match-all。支持AND/OR/NOT、括号、引号及词尾*，不支持PubMed字段标签。")
        self.query.setMaximumHeight(100)
        self.minimum_jif = QDoubleSpinBox()
        self.minimum_jif.setRange(0, 200)
        self.minimum_jif.setDecimals(2)
        self.minimum_jif.setValue(float(self.profile.get("minimum_jif") or 0))
        self.maximum_results = CandidateInput()
        self.biorxiv_maximum_results = CandidateInput()
        settings = database.get_settings()
        self.maximum_results.setValue(
            int(self.profile.get('pubmed_maximum_results') if self.profile.get('pubmed_maximum_results') is not None else self.profile.get("maximum_results",0))
        )
        self.biorxiv_maximum_results.setValue(int(self.profile.get('biorxiv_maximum_results') if self.profile.get('biorxiv_maximum_results') is not None else self.profile.get('maximum_results',0)))
        self.biorxiv_maximum_results.setToolTip('每个预印本来源各用此候选数，例如20表示bioRxiv最多20篇、medRxiv最多20篇。0=不限；增量不截断。')
        self.search_sources=QComboBox()
        self.search_sources.addItem('PubMed + 预印本官网','both')
        self.search_sources.addItem('仅 PubMed','pubmed')
        self.search_sources.addItem('仅预印本官网（bioRxiv / medRxiv）','biorxiv')
        self.search_sources.setCurrentIndex(max(0,self.search_sources.findData(self.profile.get('search_sources','both'))))
        self.sort_mode = QComboBox()
        self.sort_mode.addItems(["综合最佳", "最新发表", "最相关"])
        self.sort_mode.setCurrentText(str(self.profile.get("sort_mode") or "最新发表"))
        form.addRow("方案名称", self.name)
        form.addRow("关键词或布尔表达式", self.query)
        form.addRow("最低 JIF", self.minimum_jif)
        form.addRow('联网检索数据源（仅此方案）',self.search_sources)
        form.addRow("PubMed 候选数（0＝不限）", self.maximum_results)
        form.addRow("bioRxiv / medRxiv 候选数（0＝不限）", self.biorxiv_maximum_results)
        form.addRow("默认排序", self.sort_mode)
        self.article_type = ArticleTypeSelector(self.profile.get("article_type", "all"))
        form.addRow("PubMed 文章类型（可多选）", self.article_type)
        self.article_type.setToolTip("bioRxiv 为预印本，不提供相同的文章类型标签，文章类型筛选仅应用于 PubMed。")
        self.year_start = YearComboBox(self.profile.get("year_from"))
        self.year_end = YearComboBox(self.profile.get("year_to"))
        form.addRow("发表年份下限（含）", self.year_start)
        form.addRow("发表年份上限（含）", self.year_end)
        layout.addLayout(form)
        layout.addWidget(QLabel("选择期刊目录（可多选取并集；目录及最低 JIF 仅限制 PubMed）"))

        self.journal_list = QListWidget()
        journal_controls = QHBoxLayout()
        self.journal_search = QLineEdit()
        self.journal_search.setPlaceholderText("输入目录名称筛选；可在“期刊目录”页面创建或编辑目录")
        self.journal_search.textChanged.connect(self.filter_profile_journals)
        journal_controls.addWidget(self.journal_search)
        for title, state in (("全选",Qt.CheckState.Checked),("全不选",Qt.CheckState.Unchecked)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, state=state: self.select_profile_journals(state))
            journal_controls.addWidget(button)
        layout.addLayout(journal_controls)
        selected = set(json.loads(self.profile.get("scope_ids_json") or "[]"))
        for scope in database.list_scopes():
            item = QListWidgetItem(
                f"{scope['name']} · {len(json.loads(scope['issns_json']))} 个 ISSN"
                + (" · 内置固定目录" if scope['is_builtin'] else " · 自定义目录")
            )
            item.setData(Qt.ItemDataRole.UserRole, scope["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if scope["id"] in selected
                else Qt.CheckState.Unchecked
            )
            self.journal_list.addItem(item)
        layout.addWidget(self.journal_list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(buttons)

    def filter_profile_journals(self):
        query = self.journal_search.text().casefold()
        for index in range(self.journal_list.count()):
            item = self.journal_list.item(index)
            item.setHidden(query not in item.text().casefold())

    def select_profile_journals(self, state):
        for index in range(self.journal_list.count()):
            item = self.journal_list.item(index)
            if not item.isHidden():
                item.setCheckState(state)

    def _validate_and_accept(self) -> None:
        try:
            self.maximum_results.value()
            self.biorxiv_maximum_results.value()
        except ValueError as error:
            QMessageBox.warning(self,"候选数无效",str(error))
            return
        if not self.name.text().strip() or not self.query.toPlainText().strip():
            QMessageBox.warning(self, "信息不完整", "请填写方案名称和关键词表达式。")
            return
        try:
            start, end = self.year_start.currentData(), self.year_end.currentData()
        except ValueError as error:
            QMessageBox.warning(self, "年份范围无效", str(error))
            return
        if start is not None and end is not None and start > end:
            QMessageBox.warning(self, "年份范围无效", "发表年份下限不能大于上限。")
            return
        if not self.selected_issns() and self.search_sources.currentData() == "pubmed":
            QMessageBox.warning(self, "未选择目录", "请至少选择一个期刊目录。")
            return
        self.accept()

    def selected_scope_ids(self) -> list[str]:
        return [
            self.journal_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.journal_list.count())
            if self.journal_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def selected_issns(self) -> list[str]:
        return self.database.scope_union(self.selected_scope_ids())

    def values(self) -> dict[str, Any]:
        return {
            "id": self.profile.get("id"),
            "name": self.name.text().strip(),
            "query_expression": self.query.toPlainText().strip(),
            "journal_issns": self.selected_issns(),
            "scope_ids": self.selected_scope_ids(),
            "minimum_jif": self.minimum_jif.value(),
            "maximum_results": self.maximum_results.value(),
            'pubmed_maximum_results':self.maximum_results.value(),
            'biorxiv_maximum_results':self.biorxiv_maximum_results.value(),
            'search_sources':self.search_sources.currentData(),
            "sort_mode": self.sort_mode.currentText(),
            "year_from": self.year_start.currentData(),
            "year_to": self.year_end.currentData(),
            "article_type": self.article_type.selected_types(),
        }


class SearchTaskSignals(QObject):
    progress = Signal(str)
    cached = Signal(int)
    finished = Signal(object)
    failed = Signal(str)


class SearchTask(QRunnable):
    def __init__(self, database: Database, profile: dict[str, Any], force_full=False,website_fetch=None):
        super().__init__()
        self.database = database
        self.profile = profile
        self.force_full = force_full
        self.website_fetch=website_fetch
        self.signals = SearchTaskSignals()
        self.cancelled = False
        self.settings = profile_settings(profile,database.get_settings())
        self.source_mode = self.settings.get("search_sources", "both")

    def run(self) -> None:
        try:
            selected_issns = self.database.profile_issns(self.profile)
            if not selected_issns and self.source_mode == "pubmed":
                raise ValueError("所选期刊目录为空，请先编辑期刊目录。")
            minimum_jif = float(self.profile.get("minimum_jif") or 0)
            if minimum_jif > 0 and self.source_mode != "biorxiv":
                eligible = {
                    alias
                    for journal in self.database.list_journals()
                    if journal.get("jif") is not None and float(journal["jif"]) >= minimum_jif
                    for alias in {journal["issn"], *(journal.get("aliases") or "").split(";")}
                }
                selected_issns = [issn for issn in selected_issns if issn in eligible]
                if not selected_issns and self.source_mode == "pubmed":
                    raise ValueError("应用最低 JIF 条件后没有期刊。请在期刊目录页管理全部期刊并导入 JIF，或将最低 JIF 设为 0。")
            settings = self.settings
            aliases = {alias: sorted({j["issn"], *filter(None, (j.get("aliases") or "").split(";"))})
                       for j in self.database.list_journals()
                       for alias in {j["issn"], *filter(None, (j.get("aliases") or "").split(";"))}}
            selected_issns = list(dict.fromkeys(alias for issn in selected_issns
                                               for alias in aliases.get(issn,[issn])))
            client = LiteratureClient(
                "", "",
                biorxiv_cache_path=self.database.path.parent / "biorxiv_metadata.sqlite3"
            )
            try:
                def save_batch(candidates):
                    if not candidates:return
                    self.database.store_refresh_results(int(self.profile['id']),candidates,cache_mode(settings),partial=True)
                    self.signals.cached.emit(int(self.profile['id']))
                report, checkpoint = refresh_search(self.database, client, self.profile, settings,
                    selected_issns, self.signals.progress.emit, lambda: self.cancelled, self.force_full,
                    website_fetch=(lambda url:self.website_fetch(url,lambda:self.cancelled)) if self.website_fetch else None,
                    candidate_callback=save_batch)
            finally:
                client.session.close()
            if self.cancelled:
                raise InterruptedError("检索已取消，保留上次缓存。")
            if not report.candidates and report.source_errors:
                raise ValueError("；".join(report.source_errors))
            # Use the local catalogue for missing PubMed journal names.
            catalogue = {alias:j["title"] for j in self.database.list_journals()
                         for alias in (j.get("aliases") or j["issn"]).split(";")}
            for article, _ in report.candidates:
                if not article.get("journal_title"):
                    article["journal_title"] = catalogue.get(article.get("issn"),"")
            self.database.store_refresh_results(int(self.profile["id"]), report.candidates, cache_mode(settings), checkpoint,
                                                partial=bool(report.source_errors))
            # Only discard temporary pages after the entire refresh is committed,
            # including both preprint hosts. A later full refresh must read anew.
            for job in client.completed_website_jobs:
                try:job.discard()
                except (OSError,sqlite3.Error):pass
            self.signals.finished.emit(report)
        except Exception as error:  # Background boundary: report any source/DB failure to the GUI.
            self.signals.failed.emit(str(error))


class HomePage(QWidget):
    open_profile = Signal(int)
    profiles_changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        outer = QVBoxLayout(self)
        self.refresh_states = {}
        self.profile_cards = {}
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("关键词方案")
        title.setObjectName("PageTitle")
        subtitle = QLabel("单击查看缓存；拖动卡片调整顺序，拖动右边缘调整宽度。默认每行 5 张。")
        subtitle.setWordWrap(True)
        subtitle.setObjectName("Muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        add_button = QPushButton("＋ 添加关键词组")
        add_button.clicked.connect(self.add_profile)
        header.addLayout(title_box,1)
        reset_widths = QPushButton("恢复默认宽度")
        reset_widths.setProperty('secondary',True)
        reset_widths.clicked.connect(self.reset_widths)
        header.addWidget(reset_widths)
        header.addWidget(add_button)
        outer.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = KeywordBoard()
        self.container.order_changed.connect(self.persist_order)
        self.container.width_changed.connect(self.database.set_profile_card_width)
        self.scroll.setWidget(self.container)
        self.container.scroll_area = self.scroll
        outer.addWidget(self.scroll, 1)
        self.reload()

    def reload(self) -> None:
        self.profile_cards = {}
        self.container.clear()
        profiles = self.database.list_profiles()
        for profile in profiles:
            refreshed = local_refresh_time(profile.get("last_refreshed_at"))
            card = KeywordCard(self.container, profile, refreshed)
            self.container.cards.append(card)
            self.profile_cards[int(profile['id'])] = card
            self.set_refresh_state(int(profile['id']), self.refresh_states.get(int(profile['id']), ''))
            card.clicked.connect(self.open_profile.emit)
        self.container.reflow()

    def persist_order(self, identifiers):
        try:
            self.database.reorder_profiles(identifiers)
        except ValueError as error:
            QTimer.singleShot(0,self.reload)
            QMessageBox.warning(self,'无法排序',str(error))

    def reset_widths(self):
        self.database.reset_profile_card_widths()
        for card in self.container.cards:
            card.custom_width = 0
        self.container.reflow()

    def set_refresh_state(self, profile_id, message):
        if message:
            self.refresh_states[profile_id] = message
        else:
            self.refresh_states.pop(profile_id, None)
        card = self.profile_cards.get(profile_id)
        if card:
            card.set_busy(message)

    def add_profile(self) -> None:
        dialog = ProfileDialog(self.database, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                profile_id = self.database.save_profile(dialog.values())
                self.reload()
                self.profiles_changed.emit()
                self.open_profile.emit(profile_id)
            except sqlite3.IntegrityError:  # imported locally below to keep UI error helpful
                QMessageBox.warning(self, "名称重复", "已经存在同名关键词方案，请换一个名称。")


class ArticleCard(QFrame):
    open_requested = Signal(object)
    favorite_requested = Signal(object)
    hidden_requested = Signal(object)

    def __init__(self, article, density='loose'):
        super().__init__()
        self.article = article
        self.setObjectName("ArticleCard")
        preprint = any(source in str(article.get("source_name") or "") for source in ('bioRxiv','medRxiv'))
        visited = bool(article.get("doi_visited"))
        background = "#F1ECE6" if visited else "#FFFFFF"
        foreground = "#82776F" if visited else "#30251D"
        self.setStyleSheet(f"QFrame#ArticleCard {{background:{background};border:1px solid #E8D5C4;border-radius:12px;}} QLabel {{background:transparent;color:{foreground};}}")
        compact=density=='compact'
        self._base_card_height=280 if compact else 360
        self.setFixedHeight(scaled(self._base_card_height))
        self.setMinimumWidth(scaled(240 if compact else 300))
        layout = QVBoxLayout(self)
        if compact:layout.setContentsMargins(8,8,8,8);layout.setSpacing(4)
        journal = QLabel(str(article.get("journal_title") or "期刊名称缺失"))
        journal.setStyleSheet("color:#C96812;font-weight:700;border:none;")
        journal.setWordWrap(True)
        journal._base_card_height=28 if compact else 36
        journal.setFixedHeight(scaled(journal._base_card_height))
        title = QLabel(str(article.get("title") or "无标题"))
        title.setWordWrap(True)
        title._base_card_height=54 if compact else 75
        title.setFixedHeight(scaled(title._base_card_height))
        title_foreground = "#D97983" if preprint else foreground
        title_weight = 700 if preprint else (400 if visited else 600)
        title.setStyleSheet(f"font-size:{11 if compact else 12}pt;font-weight:{title_weight};color:{title_foreground};border:none;")
        self.title_label = title
        title.setToolTip(title.text())
        self.abstract = QTextBrowser()
        self.abstract.setStyleSheet(f"border:none;background:transparent;color:{foreground};")
        self.abstract.setOpenExternalLinks(False)
        self.full_abstract = str(article.get("abstract") or ("正在自动读取摘要……" if article.get("biorxiv_search_method")=="website" else "数据源未提供摘要。"))
        self.expanded = False
        self.expand = QPushButton("展开摘要")
        self.expand.setProperty("secondary", True)
        self.expand.clicked.connect(self.toggle_abstract)
        self.update_abstract()
        layout.addWidget(journal)
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        if article.get("jif") is not None:
            self.jif_label = QLabel(f"JIF {article['jif']:g}")
            self.jif_label.setStyleSheet("color:#C96812;font-weight:700;border:none;")
            self.jif_label.setToolTip(str(article.get("jif_source") or "") + "\n年份标签：" + str(article.get("jif_year") or "未知"))
            title_row.addWidget(self.jif_label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)
        layout.addWidget(self.abstract, 1)
        metadata = QLabel(f"{article.get('publication_date') or '日期未知'} · {article.get('source_name') or ''}")
        if visited:
            metadata.setText(metadata.text() + " · 已访问 DOI")
        if article.get("is_hidden"):metadata.setText(metadata.text()+" · 已隐藏")
        metadata.setObjectName("Muted")
        layout.addWidget(metadata)
        controls = self.controls = QHBoxLayout()
        details = QPushButton("文章详情")
        details.clicked.connect(lambda: self.open_requested.emit(self.article))
        favorite = self.favorite_button = QPushButton("已收藏♥" if article.get("is_favorite_in_profile", article.get("is_favorite")) else "收藏♡")
        favorite.setFont(QFont('Segoe UI Symbol',10))
        favorite.setStyleSheet('font-family:"Segoe UI Symbol","Microsoft YaHei UI","Apple Symbols","PingFang SC";')
        favorite.setToolTip('取消本组收藏' if article.get('is_favorite_in_profile',article.get('is_favorite')) else '收藏到当前关键词组')
        favorite.clicked.connect(lambda: self.favorite_requested.emit(self.article))
        controls.addWidget(self.expand)
        controls.addStretch()
        controls.addWidget(details)
        controls.addWidget(favorite)
        self.hidden_button=QPushButton('取消隐藏' if article.get('is_hidden') else '隐藏')
        self.hidden_button.setProperty('secondary',True)
        self.hidden_button.clicked.connect(lambda:self.hidden_requested.emit(self.article))
        journal_row=QHBoxLayout();journal_row.addWidget(journal,1);journal_row.addWidget(self.hidden_button)
        layout.removeWidget(journal);layout.insertLayout(0,journal_row)
        layout.addLayout(controls)

    def toggle_abstract(self):
        self.expanded = not self.expanded
        self.update_abstract()

    def update_abstract(self):
        shortened = self.full_abstract[:430] + ("…" if len(self.full_abstract) > 430 else "")
        self.abstract.setPlainText(self.full_abstract if self.expanded else shortened)
        self.abstract.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if self.expanded else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.expand.setText("收起摘要" if self.expanded else "展开摘要")


class ResultsPage(QWidget):
    back_requested = Signal()
    cache_changed = Signal()
    refresh_state = Signal(int, str)

    def __init__(self, database: Database, thread_pool: QThreadPool):
        super().__init__()
        self.database = database
        self.thread_pool = thread_pool
        self.website=WebsiteBrowser(database.path.parent,self)
        self.profile_id: int | None = None
        self.current_task: SearchTask | None = None
        self._result_token=0
        self._result_keys=[]
        self._read_tasks={}
        self._card_pages=OrderedDict()
        self._pending_reads=set()
        self._small_cache=True
        self._index_ready=False
        self.read_pool=QThreadPool(self)
        self.abstract_pool=QThreadPool(self);self.abstract_pool.setMaxThreadCount(2)
        self._abstract_tasks={};self._abstract_attempts=set()
        self.read_pool.setMaxThreadCount(2)
        self.displayed_articles = []
        self.card_page_number = 0
        self.position_source_mode = cache_mode(database.get_settings())
        self._restoring_position = False
        self._position_token = 0
        self.position_timer = QTimer(self)
        self.position_timer.setSingleShot(True)
        self.position_timer.setInterval(250)
        self.position_timer.timeout.connect(self.save_position)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        back = QPushButton("← 返回关键词组")
        back.setProperty("secondary", True)
        back.clicked.connect(self.back_requested.emit)
        self.title = QLabel("检索结果")
        self.title.setObjectName("PageTitle")
        self.sort_mode = QComboBox()
        self.sort_mode.addItems(["综合最佳", "最新发表", "最相关"])
        self.sort_mode.currentTextChanged.connect(self.reload_cache)
        self.edit_button = QPushButton("编辑方案")
        self.edit_button.setProperty("secondary", True)
        self.edit_button.clicked.connect(self.edit_profile)
        self.delete_button = QPushButton("删除方案")
        self.delete_button.setProperty("secondary", True)
        self.delete_button.clicked.connect(self.delete_profile)
        self.refresh_button = QPushButton("联网刷新")
        self.refresh_button.clicked.connect(self.refresh_online)
        toolbar.addWidget(back)
        toolbar.addWidget(self.title, 1)
        toolbar.addWidget(QLabel("排序"))
        toolbar.addWidget(self.sort_mode)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.delete_button)
        toolbar.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("取消检索")
        self.cancel_button.clicked.connect(self.cancel_refresh)
        self.cancel_button.setEnabled(False)
        toolbar.addWidget(self.cancel_button)
        layout.addLayout(toolbar)
        self.status = QLabel("本页读取本地缓存。")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("展现形式"))
        self.view_mode = QComboBox()
        self.view_mode.addItem("表格", "table")
        self.view_mode.addItem("嵌入式卡片", "cards")
        self.view_mode.currentIndexChanged.connect(self.change_view)
        view_row.addWidget(self.view_mode)
        self.density_label=QLabel('卡片密度');self.card_density=QComboBox()
        self.card_density.addItem('宽松','loose');self.card_density.addItem('紧密','compact')
        self.card_density.setCurrentIndex(max(0,self.card_density.findData(database.get_settings().get('keyword_card_density','loose'))))
        self.card_density.currentIndexChanged.connect(self.change_card_density)
        view_row.addWidget(self.density_label);view_row.addWidget(self.card_density)
        self.visible_sources={'PubMed','bioRxiv','medRxiv'}
        view_row.addWidget(QLabel('检索源'))
        self.result_sources=QPushButton('全部')
        self.result_sources.setProperty('secondary',True)
        self.result_sources.setToolTip('选择要显示的已缓存来源，可多选；不改变联网检索方案。')
        self.result_sources.clicked.connect(self.choose_result_sources)
        view_row.addWidget(self.result_sources)
        self.show_hidden=False
        self.hidden_settings=QPushButton('隐藏设置')
        self.hidden_settings.setProperty('secondary',True)
        self.hidden_settings.clicked.connect(self.choose_hidden_settings)
        view_row.addWidget(self.hidden_settings)
        self.force_full = QCheckBox("完整检索")
        self.force_full.setToolTip("默认复用本方案缓存并补查新增/更新文章。勾选后重新完整检索并重建候选集，适合定期核对历史结果。")
        view_row.addWidget(self.force_full)
        view_row.addStretch()
        layout.addLayout(view_row)
        self.table = PagedArticleTable()
        self.table.page_model.page_requested.connect(self._table_page_requested)
        self.table.article_activated.connect(self.open_article)
        self.result_stack = QStackedWidget()
        self.result_stack.addWidget(self.table)
        card_page = QWidget()
        card_layout = QVBoxLayout(card_page)
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.verticalScrollBar().valueChanged.connect(self.queue_position_save)
        card_container = self.card_container = QWidget()
        self.card_grid = QGridLayout(card_container)
        self.card_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.card_grid.setVerticalSpacing(14)
        self.card_scroll.setWidget(card_container)
        card_layout.addWidget(self.card_scroll, 1)
        pager = QHBoxLayout()
        previous, following = QPushButton("上一页"), QPushButton("下一页")
        self.previous_card,self.next_card=previous,following
        previous.clicked.connect(lambda: self.turn_card_page(-1))
        following.clicked.connect(lambda: self.turn_card_page(1))
        self.card_page_label = QLabel()
        pager.addWidget(previous)
        pager.addWidget(self.card_page_label)
        pager.addWidget(following)
        pager.addStretch()
        card_layout.addLayout(pager)
        self.result_stack.addWidget(card_page)
        layout.addWidget(self.result_stack, 1)
        action_row = QHBoxLayout()
        detail_button = QPushButton("查看摘要")
        self.table_detail_button = detail_button
        detail_button.clicked.connect(lambda: self.open_article(self.table.current_article()))
        favorite_button = QPushButton("收藏 / 取消收藏")
        self.table_favorite_button = favorite_button
        favorite_button.clicked.connect(self.toggle_current_favorite)
        action_row.addWidget(detail_button)
        action_row.addWidget(favorite_button)
        self.table_hidden_button=QPushButton('隐藏 / 取消隐藏')
        self.table_hidden_button.clicked.connect(lambda:self.toggle_article_hidden(self.table.current_article()))
        action_row.addWidget(self.table_hidden_button)
        action_row.addStretch()
        layout.addLayout(action_row)

    def load_profile(self, profile_id: int) -> None:
        self.save_position()
        self._abstract_attempts.clear()
        self._restoring_position = True
        self.profile_id = profile_id
        self.show_hidden=self.database.get_settings().get(f'show_hidden_{profile_id}','0')=='1'
        try:values=json.loads(self.database.get_settings().get(f'result_sources_{profile_id}','["PubMed","bioRxiv","medRxiv"]'))
        except (ValueError,TypeError):values=['PubMed','bioRxiv','medRxiv']
        self.visible_sources=set(values)&{'PubMed','bioRxiv','medRxiv'}
        self.update_source_label()
        profile = self.database.get_profile(profile_id)
        if not profile:
            self.back_requested.emit()
            return
        self.title.setText(profile["name"])
        self.sort_mode.blockSignals(True)
        self.sort_mode.setCurrentText(profile["sort_mode"])
        self.sort_mode.blockSignals(False)
        self.view_mode.blockSignals(True)
        self.view_mode.setCurrentIndex(max(0, self.view_mode.findData(profile.get("view_mode", "table"))))
        self.view_mode.blockSignals(False)
        mode = self.database.profile_cache_mode(profile_id)
        saved = self.database.card_position(profile_id, mode)
        if saved.get("sort_mode"):
            self.sort_mode.setCurrentText(saved["sort_mode"])
        self.reload_cache()

    def reload_cache(self) -> None:
        if self.profile_id is None:
            return
        self.save_position()
        self.position_source_mode = self.database.profile_cache_mode(self.profile_id)
        saved = self.database.card_position(self.profile_id, self.position_source_mode)
        self._restoring_position = True
        self.card_page_number = saved["page"]
        profile = self.database.get_profile(self.profile_id)
        if not profile:return
        self._result_token+=1
        self._index_ready=False
        self._result_keys=[];self.displayed_articles=[]
        self._card_pages.clear();self._pending_reads.clear()
        self.table.page_model.set_keys([])
        self._clear_cards()
        self._saved_scroll=saved['scroll_y']
        identifier,mode=self.profile_id,self.position_source_mode
        self._cache_count=self.database.result_count(identifier,mode)
        self._small_cache=self._cache_count<=200
        ordering=self.sort_mode.currentText()
        visible_sources=set(self.visible_sources)
        show_hidden=self.show_hidden
        self.status.setText('正在读取本地排序索引……只按需加载当前页文章。')
        self.previous_card.setEnabled(False);self.next_card.setEnabled(False)
        self._show_active_view()
        def build_index():
            rows=self.database.result_metadata(identifier,mode)
            hidden_tokens=self.database.hidden_article_tokens(identifier)
            allowed=[]
            for row in rows:
                if not show_hidden and self.database.matches_hidden_article(row,hidden_tokens):continue
                origin=str(row.get('source_name') or '').casefold()
                names={name for name in ('PubMed','bioRxiv','medRxiv') if name.casefold() in origin}
                if not names.intersection(visible_sources):continue
                if not any(source in (row.get('source_name') or '') for source in ('bioRxiv','medRxiv')) and not matches_type(json.loads(row.get('types_json') or '[]'),profile.get('article_type','all')):
                    continue
                year=row.get('publication_year')
                if (profile.get('year_from') or profile.get('year_to')) and not (year and (profile.get('year_from') or 1000)<=year<=(profile.get('year_to') or 3000)):
                    if not (row.get('website_date_filtered') and not year):continue
                allowed.append(row)
            return [row['article_key'] for row in sort_articles(allowed,ordering)]
        self._read_result(build_index,self._index_loaded)

    def update_source_label(self):
        names=[name for name in ('PubMed','bioRxiv','medRxiv') if name in self.visible_sources]
        self.result_sources.setText('全部' if len(names)==3 else '、'.join(names) if names else '未选择')

    def choose_hidden_settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('隐藏设置');layout=QVBoxLayout(dialog)
        choice=QCheckBox('显示隐藏的文章（可点击“取消隐藏”恢复）');choice.setChecked(self.show_hidden)
        layout.addWidget(choice)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.set_show_hidden(choice.isChecked())

    def set_show_hidden(self,show):
        self.show_hidden=bool(show)
        if self.profile_id is None:return
        self.database.save_settings({f'show_hidden_{self.profile_id}':'1' if show else '0'})
        self.database.save_card_position(self.profile_id,self.position_source_mode,0,0,self.sort_mode.currentText())
        self.reload_cache()

    def toggle_article_hidden(self,article):
        if not article or self.profile_id is None:return
        tokens=self.database.hidden_article_tokens(self.profile_id)
        self.database.set_article_hidden(self.profile_id,article,not self.database.matches_hidden_article(article,tokens))
        self.reload_cache()

    def choose_result_sources(self):
        dialog=QDialog(self);dialog.setWindowTitle('选择显示的检索源')
        layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('仅筛选本方案已有缓存，可选择一个或多个来源。'))
        choices={}
        for name in ('PubMed','bioRxiv','medRxiv'):
            check=QCheckBox(name);check.setChecked(name in self.visible_sources)
            layout.addWidget(check);choices[name]=check
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.set_result_sources({name for name,check in choices.items() if check.isChecked()})

    def set_result_sources(self,sources):
        self.visible_sources=set(sources)&{'PubMed','bioRxiv','medRxiv'}
        self.update_source_label()
        if self.profile_id is None:return
        self.database.save_settings({f'result_sources_{self.profile_id}':json.dumps(sorted(self.visible_sources))})
        self._position_token+=1;self._restoring_position=True
        self.database.save_card_position(self.profile_id,self.position_source_mode,0,0,self.sort_mode.currentText())
        self.reload_cache()

    def _read_result(self,operation,ready):
        token=self._result_token
        if self._small_cache:
            ready(operation());return
        task=BackgroundOperation(lambda _progress: operation());key=id(task);self._read_tasks[key]=task
        def complete(result):
            self._read_tasks.pop(key,None)
            if token==self._result_token:ready(result)
        def failure(message):
            self._read_tasks.pop(key,None)
            if token==self._result_token:self.status.setText('本地缓存读取失败：'+message)
        task.signals.finished.connect(complete);task.signals.failed.connect(failure)
        self.read_pool.start(task)

    def cancel_local_reads(self):
        self._result_token+=1

    def _index_loaded(self,keys):
        self._index_ready=True
        self._result_keys=keys
        self.table.page_model.set_keys(keys)
        profile=self.database.get_profile(self.profile_id)
        source='当前本地缓存 · '+('PubMed' if self.position_source_mode=='pubmed' else 'PubMed + 预印本' if self.position_source_mode.startswith('both') else '预印本')
        self.status.setText(f"{source} · 当前显示 {len(keys)} 篇 / 缓存共 {self._cache_count} 篇 · 上次刷新：{local_refresh_time(profile.get('last_refreshed_at'))}")
        if self.current_task and self.current_task.profile['id']==self.profile_id:
            self.status.setText(f'正在联网搜索……当前已保存 {self._cache_count} 篇，显示 {len(keys)} 篇；新结果分批加入，可继续分页浏览。')
        if self.view_mode.currentData()=='cards':self.reload_cards()
        elif keys:self._table_page_requested(0)
        else:self._restoring_position=False

    def _show_active_view(self):
        self.density_label.setVisible(self.view_mode.currentData()=='cards')
        self.card_density.setVisible(self.view_mode.currentData()=='cards')
        self.result_stack.setCurrentIndex(1 if self.view_mode.currentData() == "cards" else 0)
        self.table_detail_button.setVisible(self.view_mode.currentData() == "table")
        self.table_favorite_button.setVisible(self.view_mode.currentData() == "table")
        self.table_hidden_button.setVisible(self.view_mode.currentData() == "table")

    def _get_result_page(self,start,count,ready):
        identifier,mode=self.profile_id,self.position_source_mode
        keys=self._result_keys[start:start+count]
        def read():
            rows=self.database.cached_articles(identifier,mode,keys)
            favorites=self.database.favorite_keys_for_profile(identifier)
            visited=self.database.visited_dois(identifier,mode)
            hidden_tokens=self.database.hidden_article_tokens(identifier)
            by_key={row['article_key']:row for row in rows}
            result=[]
            for key in keys:
                if key not in by_key:continue
                row=by_key[key]
                row['is_favorite_in_profile']=key in favorites;row['doi_visited']=key in visited
                row['is_hidden']=self.database.matches_hidden_article(row,hidden_tokens)
                result.append(row)
            return result
        self._read_result(read,ready)

    def _table_page_requested(self,page):
        if not self._index_ready or self.view_mode.currentData()!='table':
            self.table.page_model.pending.discard(page);return
        request=(self._result_token,page)
        if request in self._pending_reads:return
        self._pending_reads.add(request)
        def install(articles):
            self._pending_reads.discard(request)
            self.table.page_model.install_page(page,articles)
            self.displayed_articles=articles
            if page==0 and not self.table.currentIndex().isValid() and articles and self.table.rowAt(0)<=0:self.table.selectRow(0)
        self._get_result_page(page*PAGE_SIZE,PAGE_SIZE,install)

    def queue_position_save(self):
        if not self._restoring_position:
            self.position_timer.start()

    def save_position(self):
        self.position_timer.stop()
        if self.profile_id is not None and self.result_stack.currentIndex() == 1 and not self._restoring_position:
            self.database.save_card_position(self.profile_id, self.position_source_mode,
                                            self.card_page_number, self.card_scroll.verticalScrollBar().value(),
                                            self.sort_mode.currentText())

    def restore_position(self, scroll_y):
        self._position_token += 1
        token = self._position_token
        self._restoring_position = True
        def apply():
            if token != self._position_token:
                return
            self.card_scroll.verticalScrollBar().setValue(scroll_y)
            self._restoring_position = False
        # Defer until the page is shown and Qt has calculated the grid's scroll range.
        QTimer.singleShot(60, apply)

    def hideEvent(self, event):
        self.save_position()
        super().hideEvent(event)

    def change_view(self):
        self.save_position()
        if self.profile_id is not None:
            self.database.set_profile_view(self.profile_id, self.view_mode.currentData())
            self._show_active_view()
            if self.view_mode.currentData()=='cards':
                saved=self.database.card_position(self.profile_id,self.position_source_mode)
                self.card_page_number=saved['page'];self._saved_scroll=saved['scroll_y']
                self.reload_cards()
            else:
                self.table.viewport().update()
                top=self.table.rowAt(0)
                self._table_page_requested(max(0,top)//PAGE_SIZE)

    def turn_card_page(self, direction):
        self._position_token += 1
        self._restoring_position = False
        total_pages = max(1, math.ceil(len(self._result_keys) / 24))
        self.card_page_number = min(total_pages - 1, max(0, self.card_page_number + direction))
        self._saved_scroll=0
        self.reload_cards()
        self.card_scroll.verticalScrollBar().setValue(0)
        self.save_position()

    def reload_cards(self):
        if not self._index_ready:
            self.card_page_label.setText('正在整理排序索引……');return
        total_pages=max(1,math.ceil(len(self._result_keys)/24))
        self.card_page_number=min(self.card_page_number,total_pages-1)
        self.previous_card.setEnabled(self.card_page_number>0)
        self.next_card.setEnabled(self.card_page_number<total_pages-1)
        self.card_page_label.setText(f'第 {self.card_page_number+1} / {total_pages} 页 · 共 {len(self._result_keys)} 篇')
        page=self.card_page_number
        def install(articles):
            self._card_pages[page]=articles;self._card_pages.move_to_end(page)
            while len(self._card_pages)>6:self._card_pages.popitem(last=False)
            if self.view_mode.currentData()!='cards' or self.card_page_number!=page:return
            self.displayed_articles=articles
            self._render_card_articles(articles)
            self.restore_position(self._saved_scroll)
        if page in self._card_pages:install(self._card_pages[page])
        else:self._get_result_page(page*24,24,install)

    def _clear_cards(self):
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def _render_card_articles(self,visible):
        self._clear_cards()
        compact=self.card_density.currentData()=='compact'
        columns=min(3,max(1,self.card_scroll.viewport().width()//scaled(260))) if compact else (2 if self.card_scroll.viewport().width()>=scaled(650) else 1)
        self.card_container.setMinimumHeight(max(1, math.ceil(len(visible) / columns)) * scaled(294 if compact else 374) + 24)
        for column in range(3):self.card_grid.setColumnStretch(column,0)
        for index, article in enumerate(visible):
            card = ArticleCard(article,self.card_density.currentData())
            card.open_requested.connect(self.open_article)
            card.favorite_requested.connect(self.toggle_article_favorite)
            card.hidden_requested.connect(self.toggle_article_hidden)
            self.card_grid.addWidget(card, index // columns, index % columns)
            if article.get('biorxiv_search_method')=='website' and not article.get('abstract'):
                self.load_card_abstract(article)
        for column in range(columns):
            self.card_grid.setColumnStretch(column, 1)

    def change_card_density(self):
        self.database.save_settings({'keyword_card_density':self.card_density.currentData()})
        if self.view_mode.currentData()=='cards':
            self._render_card_articles(self.displayed_articles)
            self.restore_position(0)

    def load_card_abstract(self,article):
        """Automatically fill visible preprint cards without blocking local browsing."""
        key=article['article_key'];version=article.get('preprint_version','')
        identity=(key,version)
        if identity in self._abstract_tasks or identity in self._abstract_attempts:return
        self._abstract_attempts.add(identity)
        token=self._result_token
        def retrieve(_progress):
            if token!=self._result_token:return None
            from biorxiv_website import fetch_detail
            client=LiteratureClient('')
            try:
                raw=fetch_detail(client.session,article['doi'],version,article.get('preprint_server','medrxiv' if 'medRxiv' in article.get('source_name','') else 'biorxiv'))
                detail,_=client.normalize_biorxiv(raw,'')
                self.database.update_biorxiv_detail(key,detail)
                return detail
            finally:client.session.close()
        task=BackgroundOperation(retrieve);self._abstract_tasks[identity]=task
        def finish(detail):
            self._abstract_tasks.pop(identity,None)
            if detail is None:
                self._abstract_attempts.discard(identity)
                if self.isVisible() and self.view_mode.currentData()=='cards':
                    for index in range(self.card_grid.count()):
                        card=self.card_grid.itemAt(index).widget()
                        if card.article['article_key']==key:QTimer.singleShot(0,lambda row=card.article:self.load_card_abstract(row))
                return
            fields={name:detail.get(name) for name in ('abstract','authors','publication_date','publication_year')}
            for rows in self._card_pages.values():
                for row in rows:
                    if row['article_key']==key and row.get('preprint_version','')==version:row.update(fields)
            for index in range(self.card_grid.count()):
                card=self.card_grid.itemAt(index).widget()
                if card.article['article_key']==key and card.article.get('preprint_version','')==version:
                    card.article.update(fields);card.full_abstract=detail.get('abstract') or '数据源未提供摘要。';card.update_abstract()
        def failed(_message):
            self._abstract_tasks.pop(identity,None)
            for index in range(self.card_grid.count()):
                card=self.card_grid.itemAt(index).widget()
                if card.article['article_key']==key:
                    card.full_abstract='暂时无法获取摘要，可在联网后重新进入此方案重试。';card.update_abstract()
        task.signals.finished.connect(finish);task.signals.failed.connect(failed)
        self.abstract_pool.start(task)

    def reflow_cards(self):
        if not hasattr(self, "card_grid") or self.view_mode.currentData() != "cards":
            return
        compact=self.card_density.currentData()=='compact'
        columns=min(3,max(1,self.card_scroll.viewport().width()//scaled(260))) if compact else (2 if self.card_scroll.viewport().width()>=scaled(650) else 1)
        widgets = []
        while self.card_grid.count():
            item = self.card_grid.takeAt(0)
            if item.widget():
                widgets.append(item.widget())
        for index, card in enumerate(widgets):
            self.card_grid.addWidget(card, index // columns, index % columns)
        for column in range(3):self.card_grid.setColumnStretch(column,1 if column<columns else 0)
        self.card_container.setMinimumHeight(max(1, math.ceil(len(widgets) / columns)) * scaled(294 if compact else 374) + 24)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.reflow_cards)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.reflow_cards)

    def refresh_online(self) -> None:
        if self.profile_id is None or self.current_task is not None:
            return
        profile = self.database.get_profile(self.profile_id)
        if not profile:
            return
        self.refresh_button.setDisabled(True)
        self.cancel_button.setEnabled(True)
        self.status.setText("正在启动检索；已有缓存可继续浏览，新结果将分批保存并展示。")
        self.website.stopped=False
        task = SearchTask(self.database, profile, self.force_full.isChecked(),website_fetch=self.website.get)
        self.force_full.setChecked(False)
        self.current_task = task
        self.refresh_state.emit(profile['id'], "正在联网搜索……")
        task.signals.progress.connect(self._refresh_progress)
        task.signals.cached.connect(self._batch_cached)
        task.signals.finished.connect(self._refresh_finished)
        task.signals.failed.connect(self._refresh_failed)
        self.thread_pool.start(task)

    def _batch_cached(self,identifier):
        if identifier!=self.profile_id:return
        if not hasattr(self,'live_cache_timer'):
            self.live_cache_timer=QTimer(self);self.live_cache_timer.setSingleShot(True)
            self.live_cache_timer.timeout.connect(self.reload_cache)
        if not self.live_cache_timer.isActive():self.live_cache_timer.start(1000)

    def _refresh_progress(self, message):
        if self.current_task:
            identifier = self.current_task.profile['id']
            self.refresh_state.emit(identifier, message)
            if self.profile_id == identifier:
                self.status.setText(message)

    def _refresh_finished(self, report: SearchReport) -> None:
        self.website.end_search()
        identifier = self.current_task.profile['id'] if self.current_task else self.profile_id
        self.refresh_state.emit(identifier, "")
        self.cancel_button.setEnabled(False)
        self.current_task = None
        self.refresh_button.setDisabled(False)
        self.reload_cache()
        message = (
            f"刷新完成：合并 {len(report.candidates)} 篇；"
            + "，".join(f"{'预印本' if source=='bioRxiv' else source} 返回 {report.source_counts[source]} 条" for source in report.selected_sources) + "。"
        )
        if report.source_errors:
            message = message.replace('刷新完成：','部分刷新：')
            message += "\n部分请求失败，未更新整体成功刷新时间：" + "；".join(report.source_errors[:4])
        if report.refresh_modes:
            message+='\n'+'；'.join(f'{source}：{mode}' for source,mode in report.refresh_modes.items())
        if report.incremental:
            message += f"\n增量刷新：复用 {report.reused_count} 篇，新增 {report.new_count} 篇；已更新重叠记录。"
        if identifier == self.profile_id:
            self.status.setText(message)
        self.cache_changed.emit()

    def _refresh_failed(self, message: str) -> None:
        self.website.end_search()
        identifier = self.current_task.profile['id'] if self.current_task else self.profile_id
        self.refresh_state.emit(identifier, "")
        self.cancel_button.setEnabled(False)
        self.current_task = None
        self.refresh_button.setDisabled(False)
        if identifier == self.profile_id:
            self.reload_cache()
            self.status.setText("刷新未完成；已展示当前已保存的结果，包含本次检索取得的文章。上次成功刷新时间保持不变。")
        self.cache_changed.emit()
        if not message.startswith("检索已取消"):
            QMessageBox.warning(self, "刷新失败", message)

    def cancel_refresh(self):
        if self.current_task:
            self.current_task.cancelled = True
            self.status.setText("正在取消，等待当前请求结束；保留并展示本次已取得的结果。")
            self.cancel_button.setEnabled(False)

    def edit_profile(self) -> None:
        if self.profile_id is None:
            return
        profile = self.database.get_profile(self.profile_id)
        if not profile:
            return
        dialog = ProfileDialog(self.database, profile, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.database.save_profile(dialog.values())
                self.load_profile(self.profile_id)
                self.cache_changed.emit()
            except sqlite3.IntegrityError:
                QMessageBox.warning(self, "名称重复", "已经存在同名关键词方案。")

    def delete_profile(self) -> None:
        if self.profile_id is None:
            return
        answer = QMessageBox.question(
            self, "删除关键词方案", "确定删除这个关键词方案吗？文章和收藏仍会保留。"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.database.delete_profile(self.profile_id)
            self.profile_id = None
            self.cache_changed.emit()
            self.back_requested.emit()

    def open_article(self, article: dict[str, Any] | None) -> None:
        if not article:
            return
        dialog = ArticleDialog(self.database, article, self, profile_id=self.profile_id)
        dialog.favorite_changed.connect(self.reload_cache)
        dialog.favorite_changed.connect(self.cache_changed.emit)
        dialog.doi_visited.connect(self.reload_cache)
        dialog.hidden_changed.connect(self.reload_cache)
        dialog.exec()

    def toggle_current_favorite(self) -> None:
        article = self.table.current_article()
        self.toggle_article_favorite(article)

    def toggle_article_favorite(self, article) -> None:
        if not article:
            return
        new_value = article["article_key"] not in self.database.favorite_keys_for_profile(self.profile_id)
        try:
            self.database.set_favorite(article["article_key"], new_value, profile_id=self.profile_id)
        except (OSError,sqlite3.Error) as error:
            QMessageBox.warning(self,'取消收藏失败','关联 PDF 可能正在被占用，请关闭阅读器后重试。\n'+str(error))
            return
        self.reload_cache()
        self.cache_changed.emit()


class NewFavoriteGroupDialog(QDialog):
    def __init__(self,database,parent=None):
        super().__init__(parent);self.database=database;self.setWindowTitle('新增自定义收藏分组')
        layout=QVBoxLayout(self);form=QFormLayout()
        self.name=QLineEdit();self.keywords=QLineEdit();self.keywords.setPlaceholderText('相关关键词，多个可用逗号分隔')
        form.addRow('分组名称',self.name);form.addRow('相关关键词',self.keywords);layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def save(self):
        try:self.database.create_favorite_group(self.name.text(),self.keywords.text())
        except sqlite3.IntegrityError:
            if QMessageBox.question(self,'分组名称重复','已有同名分组。是否添加“_重复”后缀创建新分组？')!=QMessageBox.StandardButton.Yes:return
            self.database.create_favorite_group(self.name.text(),self.keywords.text(),True)
        except ValueError as error:QMessageBox.warning(self,'无法创建',str(error));return
        self.accept()


class TransferFavoritesDialog(QDialog):
    def __init__(self,database,keys,source_group_id=None,parent=None):
        super().__init__(parent);self.database=database;self.keys=keys;self.source_group_id=source_group_id
        self.setWindowTitle('复制或移动收藏文章');self.resize(520,480)
        layout=QVBoxLayout(self);note=QLabel(f'已选择 {len(keys)} 篇文章，可选择一个或多个目标分组。\n'+('移动仅移除当前组的归属，保留其它组。' if source_group_id is not None else '在全部收藏视图移动时，会移除原有各组归属。')+'\n文章信息、笔记和PDF链接保留。')
        note.setWordWrap(True);layout.addWidget(note)
        self.operation=QComboBox();self.operation.addItem('复制到目标分组',False);self.operation.addItem('移动到目标分组',True);layout.addWidget(self.operation)
        self.groups=QListWidget()
        for group in database.list_custom_groups():
            if group['id']==source_group_id:continue
            item=QListWidgetItem(group['name']);item.setData(Qt.ItemDataRole.UserRole,group['id']);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(Qt.CheckState.Unchecked);self.groups.addItem(item)
        layout.addWidget(self.groups)
        selection_row=QHBoxLayout()
        for text,state in (('全选目标组',Qt.CheckState.Checked),('全不选',Qt.CheckState.Unchecked)):
            button=QPushButton(text)
            button.clicked.connect(lambda checked=False,value=state:[self.groups.item(i).setCheckState(value) for i in range(self.groups.count())])
            selection_row.addWidget(button)
        layout.addLayout(selection_row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def save(self):
        targets=[self.groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.groups.count()) if self.groups.item(i).checkState()==Qt.CheckState.Checked]
        try:self.database.transfer_favorites(self.keys,targets,self.operation.currentData(),self.source_group_id)
        except ValueError as error:QMessageBox.warning(self,'无法完成',str(error));return
        self.accept()


class GroupsDialog(QDialog):
    def __init__(self, database: Database, parent=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("编辑收藏分组")
        self.resize(700, 460)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "支持关键词方案分组和自定义分组。方案分组改名请编辑来源方案；删除方案后分组仍保留。删除分组不会删除收藏文章。关键词供识别，不自动重新归类。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["分组名称", "关键词（逗号分隔）"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for group in database.list_custom_groups():
            self._append_row(group["name"], group["keywords"], group["id"])
            if group.get("profile_id") is not None and database.get_profile(group["profile_id"]):
                item = self.table.item(self.table.rowCount() - 1, 0)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip("名称跟随关键词方案；可删除此分组，文章仍保留。")
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("添加一行")
        remove = QPushButton("删除选中行")
        remove.setProperty("secondary", True)
        add.clicked.connect(lambda: self._append_row("", ""))
        remove.clicked.connect(self._remove_selected)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _append_row(self, name: str, keywords: str, group_id=None) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, group_id)
        self.table.setItem(row, 1, QTableWidgetItem(keywords))

    def _remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def _save(self) -> None:
        groups = []
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip() if self.table.item(row, 0) else ""
            keywords = self.table.item(row, 1).text().strip() if self.table.item(row, 1) else ""
            if name == "未分组":
                QMessageBox.warning(self, "名称不可用", "“未分组”为保留名称，请使用其他分组名称。")
                return
            if (name or keywords or self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) and not (name and keywords):
                QMessageBox.warning(self, "信息不完整", "请填写分组名称和关键词；删除分组请使用“删除选中行”。")
                return
            if name and keywords:
                groups.append({"name": name, "keywords": keywords, "id": self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)})
        try:
            occupied=set()
            for group in groups:
                if group['name'].casefold() in occupied:
                    if QMessageBox.question(self,'分组名称重复',f"分组“{group['name']}”重复，是否添加“_重复”后缀？")!=QMessageBox.StandardButton.Yes:return
                    base=group['name']+'_重复';name=base;number=2
                    while name.casefold() in occupied:name=f'{base}_{number}';number+=1
                    group['name']=name
                occupied.add(group['name'].casefold())
            self.database.replace_custom_groups(groups)
            self.accept()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "名称重复", "自定义分组名称不能重复。")
        except ValueError as error:
            QMessageBox.warning(self, "无法保存", str(error))


class FavoriteExportDialog(QDialog):
    """Groups choose the article pool; only individually checked articles are exported."""
    def __init__(self, database, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("打包分享收藏")
        self.resize(1050, 700)
        layout = QVBoxLayout(self)
        explanation = QLabel("勾选分组后，右侧仅展示这些分组的文章并自动勾选新增分组中的文章。最终只打包右侧勾选的文章；分组关系、标签和笔记一并分享。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        columns = QHBoxLayout()
        group_box = QVBoxLayout()
        group_box.addWidget(QLabel("选择分组"))
        self.groups = QListWidget()
        grouped = database.grouped_favorites()
        self.group_article_keys = {name:{article['article_key'] for article in rows} for name,rows in grouped.items()}
        self.active_groups = set()
        self.article_records = database.favorite_articles()
        for name, rows in sorted(grouped.items()):
            item = QListWidgetItem(f"{name} · {len(rows)} 篇")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.groups.addItem(item)
        group_box.addWidget(self.groups)
        columns.addLayout(group_box, 1)
        article_box = QVBoxLayout()
        article_box.addWidget(QLabel("选择文章（可在收藏列表中先按 Ctrl / Shift 多选）"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题、期刊或 DOI")
        self.search.textChanged.connect(self.filter_articles)
        article_box.addWidget(self.search)
        self.articles = QListWidget()
        for article in self.article_records:
            text = f"{article['title']}\n{article.get('journal_title') or ''} · {article.get('doi') or ''}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, article['article_key'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if article['article_key'] in selected else Qt.CheckState.Unchecked)
            self.articles.addItem(item)
        self.groups.itemChanged.connect(self.groups_changed)
        article_box.addWidget(self.articles)
        columns.addLayout(article_box, 3)
        layout.addLayout(columns, 1)
        controls = QHBoxLayout()
        for text, state in (("全选可见文章", Qt.CheckState.Checked), ("全不选可见文章", Qt.CheckState.Unchecked)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, state=state:self.select_articles(state))
            controls.addWidget(button)
        self.include_pdf = QCheckBox("包含已链接的 PDF")
        self.include_pdf.setChecked(True)
        controls.addWidget(self.include_pdf)
        controls.addStretch()
        layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存分享压缩包")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def groups_changed(self, item):
        names = set(self.checked(self.groups))
        added = names - self.active_groups
        newly_checked = set().union(*(self.group_article_keys[name] for name in added)) if added else set()
        allowed = set().union(*(self.group_article_keys[name] for name in names)) if names else None
        previous = set(self.checked(self.articles)) if names else set()
        self.active_groups = names
        self.articles.clear()
        for article in self.article_records:
            key = article['article_key']
            if allowed is not None and key not in allowed:
                continue
            text = f"{article['title']}\n{article.get('journal_title') or ''} · {article.get('doi') or ''}"
            entry = QListWidgetItem(text)
            entry.setToolTip(text)
            entry.setData(Qt.ItemDataRole.UserRole, key)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Checked if key in previous or key in newly_checked else Qt.CheckState.Unchecked)
            self.articles.addItem(entry)
        self.filter_articles()

    def filter_articles(self):
        query = self.search.text().strip().casefold()
        for index in range(self.articles.count()):
            item = self.articles.item(index)
            item.setHidden(query not in item.text().casefold())

    def select_articles(self, state):
        for index in range(self.articles.count()):
            item = self.articles.item(index)
            if not item.isHidden():item.setCheckState(state)

    def checked(self, widget):
        return [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())
                if widget.item(i).checkState() == Qt.CheckState.Checked]

    def validate(self):
        if not self.checked(self.articles):
            QMessageBox.warning(self, "尚未选择", "请在右侧至少勾选一篇文章。")
            return
        self.accept()


class FavoritesPage(QWidget):
    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.current_group: str | None = None
        self.all_articles: list[dict[str, Any]] = []
        self.visible_articles=[];self.favorite_page_number=0;self.selected_favorite_keys=set()
        self.bundle_task = None
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("收藏文章")
        title.setObjectName("PageTitle")
        self.view_mode = QComboBox()
        self.view_mode.addItems(["分组展示", "不分组展示"])
        self.sort_mode = QComboBox()
        self.sort_mode.addItems(["文章发表时间", "收藏时间"])
        group_settings = QPushButton("编辑分组 / 删除分组")
        group_settings.setProperty("secondary", True)
        group_settings.clicked.connect(self.manage_groups)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("显示"))
        header.addWidget(self.view_mode)
        header.addWidget(QLabel("排序"))
        header.addWidget(self.sort_mode)
        header.addWidget(group_settings)
        layout.addLayout(header)
        subtitle = QLabel("支持方案分组与自定义分组；文章可复制或移动到多个组。按住分组卡片拖动可调整顺序。")
        subtitle.setObjectName("Muted")
        layout.addWidget(subtitle)

        share_row = QHBoxLayout()
        self.export_bundle_button = QPushButton("打包分享…")
        self.import_bundle_button = QPushButton("读取分享压缩包…")
        self.export_bundle_button.clicked.connect(self.export_favorites_bundle)
        self.import_bundle_button.clicked.connect(self.import_favorites_bundle)
        self.bundle_status = QLabel("")
        self.bundle_status.setWordWrap(True)
        share_row.addWidget(self.export_bundle_button)
        share_row.addWidget(self.import_bundle_button)
        self.add_group_button=QPushButton('＋ 自定义分组')
        self.add_group_button.clicked.connect(self.add_custom_group)
        self.transfer_button=QPushButton('复制 / 移动所选文章…')
        self.transfer_button.clicked.connect(self.transfer_selected)
        share_row.addWidget(self.add_group_button);share_row.addWidget(self.transfer_button)
        share_row.addWidget(self.bundle_status, 1)
        layout.addLayout(share_row)
        self.stack = QStackedWidget()
        self.group_scroll = QScrollArea()
        self.group_scroll.setWidgetResizable(True)
        self.group_container = CardBoard(columns=5)
        self.group_container.scroll_area=self.group_scroll
        self.group_container.order_changed.connect(lambda order:self.database.save_card_order('favorite',order))
        self.group_scroll.setWidget(self.group_container)
        self.table_page = QWidget()
        table_layout = QVBoxLayout(self.table_page)
        self.group_back = QPushButton("← 返回分组")
        self.group_back.setProperty("secondary", True)
        self.group_back.clicked.connect(self._back_to_groups)
        self.group_label = QLabel("")
        self.group_label.setObjectName("SectionTitle")
        table_header = QHBoxLayout()
        table_header.addWidget(self.group_back)
        table_header.addWidget(self.group_label)
        table_header.addStretch()
        table_header.addWidget(QLabel('展现形式'))
        self.article_view=QComboBox();self.article_view.addItem('表格','table');self.article_view.addItem('嵌入式卡片','cards')
        self.article_view.setCurrentIndex(max(0,self.article_view.findData(database.get_settings().get('favorites_article_view','table'))))
        table_header.addWidget(self.article_view)
        self.favorite_density_label=QLabel('卡片密度');self.favorite_density=QComboBox()
        self.favorite_density.addItem('宽松','loose');self.favorite_density.addItem('紧密','compact')
        self.favorite_density.setCurrentIndex(max(0,self.favorite_density.findData(database.get_settings().get('favorites_card_density','loose'))))
        table_header.addWidget(self.favorite_density_label);table_header.addWidget(self.favorite_density)
        table_layout.addLayout(table_header)
        self.table = ArticleTable()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.article_activated.connect(self.open_article)
        self.article_stack=QStackedWidget();self.article_stack.addWidget(self.table)
        cards_page=QWidget();cards_layout=QVBoxLayout(cards_page)
        self.favorite_scroll=QScrollArea();self.favorite_scroll.setWidgetResizable(True)
        self.favorite_container=QWidget();self.favorite_grid=QGridLayout(self.favorite_container)
        self.favorite_grid.setAlignment(Qt.AlignmentFlag.AlignTop);self.favorite_grid.setVerticalSpacing(14)
        self.favorite_scroll.setWidget(self.favorite_container);cards_layout.addWidget(self.favorite_scroll,1)
        pager=QHBoxLayout();self.favorite_previous=QPushButton('上一页');self.favorite_next=QPushButton('下一页')
        self.favorite_page_label=QLabel()
        self.favorite_previous.clicked.connect(lambda:self.turn_favorite_page(-1));self.favorite_next.clicked.connect(lambda:self.turn_favorite_page(1))
        pager.addWidget(self.favorite_previous);pager.addWidget(self.favorite_page_label);pager.addWidget(self.favorite_next);pager.addStretch()
        cards_layout.addLayout(pager);self.article_stack.addWidget(cards_page);table_layout.addWidget(self.article_stack)
        self.stack.addWidget(self.group_scroll)
        self.stack.addWidget(self.table_page)
        layout.addWidget(self.stack, 1)
        self.view_mode.currentTextChanged.connect(self.reload)
        self.sort_mode.currentTextChanged.connect(self.reload)
        self.article_view.currentIndexChanged.connect(self.change_favorite_view)
        self.favorite_density.currentIndexChanged.connect(self.change_favorite_density)
        self.reload()

    def reload(self) -> None:
        self.all_articles = self.database.favorite_articles()
        if self.view_mode.currentText() == "不分组展示":
            self.current_group = None
            self.group_back.hide()
            self.group_label.setText(f"全部收藏 · {len(self.all_articles)} 篇")
            mode = "收藏时间" if self.sort_mode.currentText() == "收藏时间" else "最新发表"
            self.show_favorite_articles(sort_articles(self.all_articles, mode))
            self.stack.setCurrentWidget(self.table_page)
            return
        if self.current_group:
            self._show_group(self.current_group)
        else:
            self._show_group_cards()

    def _show_group_cards(self) -> None:
        self.group_container.clear()
        grouped = self.database.grouped_favorites()
        by_name={g['name']:g for g in self.database.list_custom_groups()}
        entries=[dict(id=str(by_name[name]['id']) if name in by_name else '__ungrouped__',name=name,articles=articles) for name,articles in grouped.items()]
        entries=self.database._ordered_cards(entries,'favorite_card_order')
        self.group_names={row['id']:row['name'] for row in entries}
        for row in entries:
            self.group_container.add_card(row['id'],row['name'],f"{len(row['articles'])} 篇收藏文章\n"+by_name.get(row['name'],{}).get('keywords','未指定分组'),lambda identifier:self._show_group(self.group_names[identifier]))
        self.group_container.reflow()
        self.stack.setCurrentWidget(self.group_scroll)

    def _show_group(self, group_name: str) -> None:
        if self.current_group!=group_name:self.favorite_page_number=0;self.selected_favorite_keys.clear()
        self.current_group = group_name
        articles = self.database.grouped_favorites().get(group_name, [])
        mode = "收藏时间" if self.sort_mode.currentText() == "收藏时间" else "最新发表"
        self.group_label.setText(f"{group_name} · {len(articles)} 篇")
        self.group_back.show()
        self.show_favorite_articles(sort_articles(articles, mode))
        self.stack.setCurrentWidget(self.table_page)

    def _back_to_groups(self) -> None:
        self.current_group = None
        self._show_group_cards()

    def selected_article_keys(self):
        if self.stack.currentWidget()!=self.table_page:return []
        if self.article_view.currentData()=='cards':return sorted(self.selected_favorite_keys)
        return [self.table.item(row,1).data(Qt.ItemDataRole.UserRole) for row in sorted({index.row() for index in self.table.selectedIndexes()})]

    def show_favorite_articles(self,articles):
        self.visible_articles=articles
        self.selected_favorite_keys.intersection_update(a['article_key'] for a in articles)
        cards=self.article_view.currentData()=='cards'
        self.favorite_density.setVisible(cards);self.favorite_density_label.setVisible(cards)
        self.article_stack.setCurrentIndex(1 if cards else 0)
        if cards:self.render_favorite_cards()
        else:
            self.table.set_articles(articles)
            if self.selected_favorite_keys:self.table.clearSelection()
            for row,article in enumerate(articles):
                if article['article_key'] in self.selected_favorite_keys:
                    for column in range(self.table.columnCount()):
                        if self.table.item(row,column):self.table.item(row,column).setSelected(True)

    def change_favorite_view(self):
        if self.article_view.currentData()=='cards':
            self.selected_favorite_keys=set(self.table.item(row,1).data(Qt.ItemDataRole.UserRole) for row in {index.row() for index in self.table.selectedIndexes()})
        self.database.save_settings({'favorites_article_view':self.article_view.currentData()})
        self.show_favorite_articles(self.visible_articles)

    def change_favorite_density(self):
        self.database.save_settings({'favorites_card_density':self.favorite_density.currentData()})
        if self.article_view.currentData()=='cards':self.render_favorite_cards()

    def render_favorite_cards(self):
        while self.favorite_grid.count():
            item=self.favorite_grid.takeAt(0)
            if item.widget():item.widget().hide();item.widget().deleteLater()
        pages=max(1,math.ceil(len(self.visible_articles)/24))
        self.favorite_page_number=min(self.favorite_page_number,pages-1)
        self.favorite_previous.setEnabled(self.favorite_page_number>0);self.favorite_next.setEnabled(self.favorite_page_number<pages-1)
        self.favorite_page_label.setText(f'第 {self.favorite_page_number+1} / {pages} 页 · 共 {len(self.visible_articles)} 篇')
        start=self.favorite_page_number*24
        for index,article in enumerate(self.visible_articles[start:start+24]):
            card=ArticleCard(article,self.favorite_density.currentData());card.favorite_button.hide();card.hidden_button.hide()
            if not article.get('abstract'):card.full_abstract='暂无缓存摘要，可打开文章详情查看。';card.update_abstract()
            card.open_requested.connect(self.open_article)
            check=QCheckBox('选择');key=article['article_key'];check.setChecked(key in self.selected_favorite_keys)
            check.toggled.connect(lambda selected,key=key:self.selected_favorite_keys.add(key) if selected else self.selected_favorite_keys.discard(key))
            card.controls.insertWidget(0,check)
            self.favorite_grid.addWidget(card,index//2,index%2)
        self.reflow_favorite_cards();QTimer.singleShot(0,self.reflow_favorite_cards)
        self.favorite_scroll.verticalScrollBar().setValue(0)

    def reflow_favorite_cards(self):
        if self.article_view.currentData()!='cards':return
        compact=self.favorite_density.currentData()=='compact'
        columns=min(3,max(1,self.favorite_scroll.viewport().width()//scaled(260))) if compact else (2 if self.favorite_scroll.viewport().width()>=scaled(650) else 1)
        widgets=[]
        while self.favorite_grid.count():
            item=self.favorite_grid.takeAt(0)
            if item.widget():widgets.append(item.widget())
        for index,card in enumerate(widgets):self.favorite_grid.addWidget(card,index//columns,index%columns)
        for column in range(3):self.favorite_grid.setColumnStretch(column,1 if column<columns else 0)
        self.favorite_container.setMinimumHeight(max(1,math.ceil(len(widgets)/columns))*scaled(294 if compact else 374)+24)

    def turn_favorite_page(self,direction):
        pages=max(1,math.ceil(len(self.visible_articles)/24))
        self.favorite_page_number=max(0,min(pages-1,self.favorite_page_number+direction));self.render_favorite_cards()

    def showEvent(self,event):
        super().showEvent(event);QTimer.singleShot(0,self.reflow_favorite_cards)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'favorite_grid'):QTimer.singleShot(0,self.reflow_favorite_cards)

    def export_favorites_bundle(self):
        selected = self.selected_article_keys()
        dialog = FavoriteExportDialog(self.database, selected, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存收藏分享压缩包", "BiomedReader_收藏分享.zip", "ZIP 压缩包 (*.zip)")
        if not path:return
        keys, include_pdf = dialog.checked(dialog.articles), dialog.include_pdf.isChecked()
        self.start_bundle_task(lambda progress: export_bundle(self.database, path, (), keys, include_pdf, progress), False)

    def import_favorites_bundle(self):
        path, _ = QFileDialog.getOpenFileName(self, "读取收藏分享压缩包", "", "ZIP 压缩包 (*.zip)")
        if path:self.start_bundle_task(lambda progress: import_bundle(self.database, path, progress), True)

    def start_bundle_task(self, operation, importing):
        if self.bundle_task:return
        self.export_bundle_button.setEnabled(False)
        self.import_bundle_button.setEnabled(False)
        self.bundle_task = BackgroundOperation(operation)
        self.bundle_task.signals.progress.connect(self.bundle_status.setText)
        self.bundle_task.signals.finished.connect(lambda report:self.bundle_finished(report, importing))
        self.bundle_task.signals.failed.connect(self.bundle_failed)
        QThreadPool.globalInstance().start(self.bundle_task)

    def bundle_finished(self, report, importing):
        self.bundle_task = None
        self.export_bundle_button.setEnabled(True)
        self.import_bundle_button.setEnabled(True)
        if importing:
            self.bundle_status.setText(f"导入完成：新增 {report['added']} 篇，合并 {report['merged']} 篇，新建 {report['groups']} 组，链接 {report['pdfs']} 个 PDF。")
            self.reload()
        else:
            self.bundle_status.setText(f"已打包 {report['articles']} 篇文章、{report['groups']} 个分组、{report['pdfs']} 个 PDF。")

    def bundle_failed(self, message):
        self.bundle_task = None
        self.export_bundle_button.setEnabled(True)
        self.import_bundle_button.setEnabled(True)
        self.bundle_status.setText("操作失败，未完成导入或导出。")
        QMessageBox.warning(self, "分享包操作失败", message)

    def manage_groups(self) -> None:
        dialog = GroupsDialog(self.database, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.current_group = None
            self.reload()

    def add_custom_group(self):
        dialog=NewFavoriteGroupDialog(self.database,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.current_group=None;self.reload()

    def transfer_selected(self):
        if self.stack.currentWidget()!=self.table_page:
            QMessageBox.information(self,'请选择文章','请先进入分组或不分组视图，选择需要复制或移动的文章。');return
        keys=self.selected_article_keys()
        if not keys:QMessageBox.information(self,'请选择文章','可使用Ctrl或Shift选择一篇或多篇文章。');return
        source=next((g['id'] for g in self.database.list_custom_groups() if g['name']==self.current_group),None)
        dialog=TransferFavoritesDialog(self.database,keys,source,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.reload()

    def open_article(self, article: dict[str, Any]) -> None:
        dialog = ArticleDialog(self.database, article, self, allow_pdf=True)
        dialog.favorite_changed.connect(self.reload)
        dialog.exec()


class JournalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加生物医学期刊")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit()
        self.issn = QLineEdit()
        self.issn.setPlaceholderText("例如 0028-4793")
        self.jif = QDoubleSpinBox()
        self.jif.setRange(0, 200)
        self.jif.setDecimals(3)
        self.jif_year = QSpinBox()
        self.jif_year.setRange(0, 2100)
        form.addRow("期刊名", self.title)
        form.addRow("ISSN", self.issn)
        form.addRow("JIF（可留 0）", self.jif)
        form.addRow("JIF 年份（可留 0）", self.jif_year)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self) -> None:
        if not self.title.text().strip() or not re.match(r"^\d{4}-[\dXx]{4}$", self.issn.text().strip()):
            QMessageBox.warning(self, "格式错误", "请填写期刊名，并使用 0028-4793 格式的 ISSN。")
            return
        self.accept()


class SettingsPage(QWidget):
    journals_changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.current_scope = None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        body=QWidget();scroll.setWidget(body);outer.addWidget(scroll)
        layout = QVBoxLayout(body)
        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        general = QGroupBox("网络与默认值")
        general_form = QFormLayout(general)
        settings = database.get_settings()
        appearance=QGroupBox('字体与布局大小');appearance_form=QFormLayout(appearance)
        self.auto_scale=QCheckBox('根据当前屏幕可用宽度自动调整')
        self.auto_scale.setChecked(settings.get('interface_auto_scale','1')=='1')
        self.scale_percent=QSpinBox();self.scale_percent.setRange(80,200);self.scale_percent.setSuffix(' %')
        self.scale_percent.setValue(int(settings.get('interface_scale_percent','100')))
        self.scale_percent.setToolTip('同时调整字体、间距和卡片大小；开启自动调整时，此值作为自动大小的倍率。')
        def save_appearance():
            database.save_settings({'interface_auto_scale':'1' if self.auto_scale.isChecked() else '0',
                'interface_scale_percent':str(self.scale_percent.value())})
            window=self.window()
            if hasattr(window,'interface_scale'):window.interface_scale.schedule()
        self.auto_scale.toggled.connect(save_appearance);self.scale_percent.valueChanged.connect(save_appearance)
        appearance_form.addRow(self.auto_scale);appearance_form.addRow('字体和布局缩放',self.scale_percent)
        layout.addWidget(appearance)
        self.biorxiv_years = QSpinBox()
        self.biorxiv_years.setRange(1, 1000)
        self.biorxiv_years.setValue(max(1, min(1000, int(settings.get('biorxiv_recent_years', '2')))))
        self.biorxiv_years.setSuffix(" 年")
        self.biorxiv_years.setToolTip("立即保存。按本地当天向前推算，并与方案年份范围取交集；仅限制 bioRxiv，PubMed 不受影响。")
        self.biorxiv_years.valueChanged.connect(lambda value: self.database.save_settings({'biorxiv_recent_years': str(value)}))
        general_form.addRow("预印本最近年限", self.biorxiv_years)
        self.preprint_servers=QComboBox()
        self.preprint_servers.addItem('仅 bioRxiv','biorxiv')
        self.preprint_servers.addItem('仅 medRxiv','medrxiv')
        self.preprint_servers.addItem('bioRxiv 和 medRxiv','both')
        self.preprint_servers.setCurrentIndex(max(0,self.preprint_servers.findData(settings.get('preprint_servers','biorxiv'))))
        self.preprint_servers.currentIndexChanged.connect(lambda:self.database.save_settings({'preprint_servers':self.preprint_servers.currentData()}))
        self.preprint_servers.setToolTip('使用官网关键词搜索。此项影响下一次联网检索，不改变已有缓存的显示。')
        general_form.addRow('预印本来源范围',self.preprint_servers)
        self.pubmed_scope=QComboBox()
        self.pubmed_scope.addItem('默认（不限定）','default')
        self.pubmed_scope.addItem('标题与摘要','title_abstract')
        self.pubmed_scope.setCurrentIndex(max(0,self.pubmed_scope.findData(settings.get('pubmed_search_scope','default'))))
        self.pubmed_scope.currentIndexChanged.connect(lambda:self.database.save_settings({'pubmed_search_scope':self.pubmed_scope.currentData()}))
        general_form.addRow('PubMed 搜索范围',self.pubmed_scope)
        self.preprint_scope=QComboBox()
        for text,value in (('默认（不限定）','default'),('标题与摘要','title_abstract'),('全文','fulltext'),('全文、标题与摘要','fulltext_title_abstract')):
            self.preprint_scope.addItem(text,value)
        self.preprint_scope.setCurrentIndex(max(0,self.preprint_scope.findData(settings.get('preprint_search_scope','default'))))
        self.preprint_scope.currentIndexChanged.connect(lambda:self.database.save_settings({'preprint_search_scope':self.preprint_scope.currentData()}))
        self.preprint_scope.setToolTip('全文包含标题和摘要，可能与默认或全文、标题与摘要返回相同结果。修改仅影响下次联网检索。')
        general_form.addRow('预印本搜索范围',self.preprint_scope)
        self.metric_year=QComboBox();self.metric_year.setEditable(True)
        self.metric_year.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        current_year=datetime.now().year
        self.metric_year.addItems([str(year) for year in range(current_year,2009,-1)])
        self.metric_year.setCurrentText(settings.get('jif_year',str(current_year)))
        self.metric_year.lineEdit().setValidator(QIntValidator(2010,current_year,self.metric_year))
        self.metric_year.activated.connect(self.apply_jif_year)
        self.metric_year.lineEdit().editingFinished.connect(self.apply_jif_year)
        self.metric_year.setToolTip('默认本机当前年份减1；只显示该年已录入的JIF，缺失留空。切换不会改写其它年份的指标。')
        general_form.addRow('JIF 年份',self.metric_year)
        layout.addWidget(general)

        journal_box = self.journal_box = QGroupBox("期刊目录（NLM 公开目录 / 本地 JIF）")
        journal_layout = QVBoxLayout(journal_box)
        filters = QHBoxLayout()
        self.journal_search = QLineEdit()
        self.journal_search.setPlaceholderText("搜索部分期刊名称、ISSN；点击匹配行选择期刊")
        self.category_filter = QComboBox()
        self.category_filter.addItem("全部领域")
        self.journal_sort = QComboBox()
        self.journal_sort.addItems(["期刊名称 A–Z","期刊名称 Z–A","JIF 降序","JIF 升序"])
        self.journal_search.textChanged.connect(self.filter_journals)
        self.category_filter.currentTextChanged.connect(self.filter_journals)
        self.journal_sort.currentIndexChanged.connect(self.sort_journals)
        filters.addWidget(self.journal_search,1)
        filters.addWidget(self.category_filter)
        filters.addWidget(self.journal_sort)
        journal_layout.addLayout(filters)
        range_row = QHBoxLayout()
        self.selected_only = QCheckBox("只展示已选择的期刊")
        self.jif_minimum, self.jif_maximum = QLineEdit(), QLineEdit()
        self.jif_minimum.setPlaceholderText("JIF 下限（留空不限）")
        self.jif_maximum.setPlaceholderText("JIF 上限（留空不限）")
        self.jif_minimum.setMaximumWidth(190)
        self.jif_maximum.setMaximumWidth(190)
        self.jif_filter_hint = QLabel()
        self.jif_filter_hint.setStyleSheet("color:#B33E30;")
        range_row.addWidget(self.selected_only)
        range_row.addWidget(self.jif_minimum)
        range_row.addWidget(QLabel("至"))
        range_row.addWidget(self.jif_maximum)
        range_row.addWidget(self.jif_filter_hint, 1)
        journal_layout.addLayout(range_row)
        self.selected_only.toggled.connect(self.filter_journals)
        self.jif_minimum.textChanged.connect(self.filter_journals)
        self.jif_maximum.textChanged.connect(self.filter_journals)
        self.journal_status = QLabel("NLM 包含历史刊名；SCI 收录状态未核实；JIF 缺失留空。")
        self.journal_status.setWordWrap(True)
        journal_layout.addWidget(self.journal_status)
        self.journal_table = QTableWidget(0, 8)
        self.journal_table.setHorizontalHeaderLabels(["启用", "期刊", "ISSN", "领域", "JIF", "JIF 年份","其他 ISSN","来源"])
        self.journal_table.setColumnHidden(3, True)
        self.journal_table.setColumnHidden(6, True)
        self.journal_table.setColumnHidden(7, True)
        self.category_filter.hide()
        self.journal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.journal_table.setSortingEnabled(True)
        header = self.journal_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(36)
        for column, width in ((0,46),(1,500),(2,135),(4,85),(5,100)):
            self.journal_table.setColumnWidth(column, width)
        self.journal_table.verticalHeader().setVisible(False)
        journal_layout.addWidget(self.journal_table)
        self.journal_table.itemChanged.connect(self.journal_item_changed)
        journal_buttons = QHBoxLayout()
        add_journal = QPushButton("添加期刊")
        import_csv = QPushButton("导入期刊/JIF CSV")
        save_journals = QPushButton("保存期刊目录")
        self.catalogue_edit_buttons = [add_journal, import_csv, save_journals]
        self.scope_bulk_buttons = []
        add_journal.clicked.connect(self.add_journal)
        import_csv.clicked.connect(self.import_csv)
        save_journals.clicked.connect(self.save_journals)
        journal_buttons.addWidget(add_journal)
        journal_buttons.addWidget(import_csv)
        journal_buttons.addWidget(save_journals)
        for label,state in (("全选",Qt.CheckState.Checked),("全不选",Qt.CheckState.Unchecked)):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False,state=state: self.bulk_enable(state))
            journal_buttons.addWidget(button)
            self.scope_bulk_buttons.append(button)
        self.catalogue_task = None
        self.sync_nlm = QPushButton("更新公开目录")
        self.sync_nlm.clicked.connect(self.sync_catalogue)
        journal_buttons.addStretch()
        journal_layout.addLayout(journal_buttons)
        sync_buttons=QHBoxLayout()
        sync_buttons.addWidget(self.sync_nlm)
        sync_buttons.addStretch()
        journal_layout.addLayout(sync_buttons)
        # The reusable catalogue panel is mounted on JournalScopesPage, not in Settings.
        journal_box.hide()

        data_box = QGroupBox("本地数据")
        data_layout = QHBoxLayout(data_box)
        path_label = QLabel(str(database.path.parent))
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_folder = QPushButton("打开数据目录")
        backup = self.backup_button = QPushButton("备份本地数据（ZIP）")
        self.backup_task=None
        export = QPushButton("导出收藏 CSV")
        open_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(database.path.parent)))
        )
        backup.clicked.connect(self.backup_database)
        export.clicked.connect(self.export_favorites)
        data_layout.addWidget(path_label, 1)
        data_layout.addWidget(open_folder)
        data_layout.addWidget(backup)
        data_layout.addWidget(export)
        self.move_data_button=QPushButton('移动本地数据目录')
        self.move_data_button.clicked.connect(self.move_data_directory)
        data_layout.addWidget(self.move_data_button)
        self.link_data_button=QPushButton('链接本地数据目录')
        self.link_data_button.clicked.connect(self.link_data_directory)
        data_layout.addWidget(self.link_data_button)
        layout.addWidget(data_box)
        self.backup_status=QLabel('');self.backup_status.setWordWrap(True)
        layout.addWidget(self.backup_status)

        paper_box = QGroupBox("本地文章 PDF")
        paper_layout = QHBoxLayout(paper_box)
        paper_path = QLabel(str(database.paper_directory))
        paper_path.setWordWrap(True)
        paper_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_paper = QPushButton("打开 PDF 文件夹")
        open_paper.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(database.paper_directory))))
        paper_layout.addWidget(paper_path, 1)
        paper_layout.addWidget(open_paper)
        layout.addWidget(paper_box)

        layout.addStretch()
        runtime = QLabel(f"刊禾 · Biomed Reader 1.9.19 · Python {sys.version.split()[0]} · 已内置 Python 和依赖库")
        runtime.setObjectName("Muted")
        layout.addWidget(runtime)

    def reload_journals(self) -> None:
        journals = self.database.list_journals()
        selected = set(json.loads(self.current_scope["issns_json"])) if self.current_scope else None
        fixed = bool(self.current_scope and self.current_scope.get("is_builtin"))
        if fixed:
            if str(self.current_scope.get('id','')).startswith('builtin_if_'):
                journals=[j for j in journals if j['issn'] in selected]
            else:
                journals = [j for j in journals if selected.intersection(
                    {j["issn"], *(j.get("aliases") or "").split(";")})]
        self.journal_table.blockSignals(True)
        self.journal_table.setSortingEnabled(False)
        self.journal_table.setUpdatesEnabled(False)
        category = self.category_filter.currentText()
        self.category_filter.blockSignals(True)
        self.category_filter.clear()
        self.category_filter.addItems(["全部领域"]+sorted({j["category"] for j in journals}))
        self.category_filter.setCurrentText(category)
        self.category_filter.blockSignals(False)
        self.journal_table.setRowCount(0)
        for journal in journals:
            row = self.journal_table.rowCount()
            self.journal_table.insertRow(row)
            enabled = QTableWidgetItem()
            enabled.setFlags(enabled.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            members = {journal["issn"], *(journal.get("aliases") or "").split(";")}
            checked = bool(selected.intersection(members)) if selected is not None else bool(journal["enabled"])
            enabled.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            enabled.setData(Qt.ItemDataRole.UserRole, journal["issn"])
            enabled.setData(Qt.ItemDataRole.UserRole + 1, sorted(members.intersection(selected or set())))
            self.journal_table.setItem(row, 0, enabled)
            self.journal_table.setItem(row, 1, QTableWidgetItem(journal["title"]))
            self.journal_table.setItem(row, 2, QTableWidgetItem(journal["issn"]))
            self.journal_table.setItem(row, 3, QTableWidgetItem(journal["category"]))
            self.journal_table.setItem(row, 4, NumberItem("" if journal["jif"] is None else str(journal["jif"])))
            self.journal_table.setItem(row, 5, NumberItem("" if journal["jif_year"] is None else str(journal["jif_year"])))
            self.journal_table.setItem(row,6,QTableWidgetItem(journal.get("aliases") or ""))
            self.journal_table.setItem(row,7,QTableWidgetItem(journal.get("source") or "用户导入"))
            if self.current_scope:
                for column in range(8):
                    item = self.journal_table.item(row, column)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if fixed:
                    enabled.setFlags(enabled.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.journal_table.setSortingEnabled(True)
        self.journal_table.setUpdatesEnabled(True)
        self.journal_table.blockSignals(False)
        self.sort_journals()
        self.filter_journals()
        for button in self.catalogue_edit_buttons + [self.sync_nlm]:
            button.setVisible(self.current_scope is None)
        for button in self.scope_bulk_buttons:
            button.setVisible(not fixed)
        self.journal_table.horizontalHeaderItem(0).setText("选择" if self.current_scope else "启用")
        if self.current_scope:
            if_range= str(self.current_scope.get('id','')).startswith('builtin_if_')
            self.journal_status.setText(
                f"{'内置固定目录，只读' if fixed else '勾选期刊后填写名称并保存'} · "
                f"已保存 {len(selected)} 个 ISSN。检索同时匹配纸质与电子 ISSN。\n"
                + ("按2025年JIF重新划分，缺失及范围外期刊不纳入；当前JIF列随设置年份显示。" if if_range else
                 "全选/全不选仅作用于当前筛选结果。范围名称来自用户文件，不代表已核实 JIF。"))
        else:
            self.journal_status.setText("NLM 包含历史刊名；SCI 收录状态未核实；JIF 缺失留空。")

    def filter_journals(self):
        if not hasattr(self,"journal_table"):
            return
        query = self.journal_search.text().casefold().strip()
        category = self.category_filter.currentText()
        try:
            lower = float(self.jif_minimum.text()) if self.jif_minimum.text().strip() else None
            upper = float(self.jif_maximum.text()) if self.jif_maximum.text().strip() else None
            if any(v is not None and (not math.isfinite(v) or v < 0) for v in (lower, upper)):
                raise ValueError()
            if lower is not None and upper is not None and lower > upper:
                raise ValueError()
            self.jif_filter_hint.setText("")
        except ValueError:
            self.jif_filter_hint.setText("请输入非负数，且下限 ≤ 上限")
            return
        for row in range(self.journal_table.rowCount()):
            matches = query in " ".join(self.journal_table.item(row,c).text() for c in (1,2,6)).casefold()
            matches = matches and (category in ("", "全部领域") or self.journal_table.item(row,3).text()==category)
            if self.selected_only.isChecked():
                matches = matches and self.journal_table.item(row,0).checkState() == Qt.CheckState.Checked
            value = self.journal_table.item(row,4).text().strip()
            if lower is not None or upper is not None:
                try:
                    number = float(value)
                    matches = matches and math.isfinite(number) and (lower is None or number >= lower) and (upper is None or number <= upper)
                except ValueError:
                    matches = False
            self.journal_table.setRowHidden(row,not matches)

    def sort_journals(self):
        mode = self.journal_sort.currentIndex()
        self.journal_table.sortItems(1 if mode<2 else 4,
                                    Qt.SortOrder.AscendingOrder if mode in (0,3) else Qt.SortOrder.DescendingOrder)
        self.filter_journals()

    def journal_item_changed(self, item):
        if item.column() in (0, 4):
            self.filter_journals()

    def bulk_enable(self, state):
        visible = [row for row in range(self.journal_table.rowCount()) if not self.journal_table.isRowHidden(row)]
        self.journal_table.blockSignals(True)
        for row in visible:
            self.journal_table.item(row,0).setCheckState(state)
        self.journal_table.blockSignals(False)
        self.filter_journals()

    def sync_catalogue(self):
        if self.catalogue_task:
            return
        def operation(progress):
            import requests
            progress("正在下载 NLM 官方公开目录……")
            response = requests.get(NLM_URL,timeout=(10,90))
            response.raise_for_status()
            rows = parse_nlm(response.text)
            # Public metadata refresh preserves metrics and individual selection choices.
            existing = {j["issn"]:j for j in self.database.list_journals()}
            for row in rows:
                old = existing.get(row["issn"])
                if old:
                    row["enabled"] = old["enabled"]
                    row.update(jif=old["jif"],jif_year=old["jif_year"],category=old["category"],source=old["source"])
            self.database.save_journals(rows)
            return len(rows)
        self.catalogue_task = BackgroundOperation(operation)
        self.catalogue_task.signals.progress.connect(self.journal_status.setText)
        self.catalogue_task.signals.finished.connect(self.catalogue_finished)
        self.catalogue_task.signals.failed.connect(self.catalogue_failed)
        self.sync_nlm.setEnabled(False)
        QThreadPool.globalInstance().start(self.catalogue_task)

    def catalogue_finished(self,count):
        self.catalogue_task = None
        self.sync_nlm.setEnabled(True)
        self.reload_journals()
        self.journal_status.setText(f"已同步 {count} 个期刊条目；已有启用状态保留。")
        self.journals_changed.emit()

    def catalogue_failed(self,message):
        self.catalogue_task = None
        self.sync_nlm.setEnabled(True)
        self.journal_status.setText(message)

    def save_general(self) -> None:
        self.database.save_settings(
            {
                "biorxiv_recent_years": str(self.biorxiv_years.value()),
                'preprint_servers':self.preprint_servers.currentData(),
            }
        )
        QMessageBox.information(self, "已保存", "网络设置已保存。")

    def apply_jif_year(self,*_):
        try:
            year=int(self.metric_year.currentText())
            if year==int(self.database.get_settings()['jif_year']):return
            self.database.select_jif_year(year)
        except (ValueError,TypeError):
            QMessageBox.warning(self,'年份无效',f'请输入2010年至{datetime.now().year}之间的年份。')
            self.metric_year.setCurrentText(self.database.get_settings()['jif_year']);return
        self.reload_journals();self.journals_changed.emit()

    def save_journals(self) -> None:
        for row in range(self.journal_table.rowCount()):
            try:
                jif = self.journal_table.item(row,4).text().strip()
                year = self.journal_table.item(row,5).text().strip()
                if jif:
                    float(jif)
                if year:
                    int(year)
            except ValueError:
                QMessageBox.warning(self,"无法保存",f"第 {row+1} 行的 JIF 或年份不是有效数字。")
                return
        journals = []
        for row in range(self.journal_table.rowCount()):
            jif_text = self.journal_table.item(row, 4).text().strip()
            year_text = self.journal_table.item(row, 5).text().strip()
            journals.append(
                {
                    "enabled": 1 if self.journal_table.item(row, 0).checkState() == Qt.CheckState.Checked else 0,
                    "title": self.journal_table.item(row, 1).text().strip(),
                    "issn": self.journal_table.item(row, 2).text().strip().upper(),
                    "category": self.journal_table.item(row, 3).text().strip() or "生物医学",
                    "aliases":self.journal_table.item(row,6).text().strip(),
                    "source":self.journal_table.item(row,7).text().strip(),
                    "jif": float(jif_text) if jif_text else None,
                    "jif_year": int(year_text) if year_text else None,
                }
            )
        try:
            self.database.save_journals(journals)
            self.reload_journals()
            self.journals_changed.emit()
            QMessageBox.information(self, "已保存", "期刊目录已更新。")
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存", str(error))

    def add_journal(self) -> None:
        dialog = JournalDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.database.save_journals(
                [{
                    "enabled": 1,
                    "title": dialog.title.text().strip(),
                    "issn": dialog.issn.text().strip().upper(),
                    "category": "",
                    "jif": dialog.jif.value() or None,
                    "jif_year": dialog.jif_year.value() or None,
                }]
            )
            self.reload_journals()
            self.journals_changed.emit()

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择期刊 CSV", "", "CSV 文件 (*.csv)")
        if not path:
            return
        try:
            rows = []
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if not row.get("title") or not row.get("issn"):
                        continue
                    rows.append(
                        {
                            "title": row["title"].strip(),
                            "issn": row["issn"].strip().upper(),
                            "category": "",
                            "jif": float(row["jif"]) if row.get("jif", "").strip() else None,
                            "jif_year": int(row["jif_year"]) if row.get("jif_year", "").strip() else None,
                            "enabled": 0 if str(row.get("enabled", "1")).strip() == "0" else 1,
                        }
                    )
            self.database.save_journals(rows)
            self.reload_journals()
            self.journals_changed.emit()
            QMessageBox.information(self, "导入完成", f"已导入或更新 {len(rows)} 本期刊。")
        except Exception as error:
            QMessageBox.warning(self, "导入失败", str(error))

    def _data_switch_ready(self):
        if self.backup_task or QThreadPool.globalInstance().activeThreadCount():
            QMessageBox.information(self,'请稍后操作','请等待联网检索、摘要读取、备份等后台任务结束后再操作。');return False
        return True

    def move_data_directory(self):
        if not self._data_switch_ready():return
        destination=QFileDialog.getExistingDirectory(self,'选择新的空数据文件夹（移动成功后重启）')
        if not destination:return
        from data_location import validate_move_paths
        self._start_data_switch(lambda progress:validate_move_paths(self.database.path.parent,destination))

    def link_data_directory(self):
        if not self._data_switch_ready():return
        destination=QFileDialog.getExistingDirectory(self,'选择已有的 Biomed Reader 本地数据目录')
        if not destination:return
        if Path(destination).resolve()==self.database.path.parent.resolve():
            QMessageBox.information(self,'当前目录','该文件夹已经是当前使用的数据目录。');return
        from data_location import validate_data_directory
        self._start_data_switch(lambda progress:dict(path=str(validate_data_directory(destination)),kind='link'))

    def _start_data_switch(self,operation):
        window=self.window()
        window.results.save_position();window.results.cancel_local_reads();window.results.website.shutdown()
        window.setEnabled(False)
        self.move_task=BackgroundOperation(operation)
        self.move_task.signals.progress.connect(self.backup_status.setText)
        def completed(report):
            window._data_switching=True
            QApplication.instance().pending_data_switch=report
            self.move_task=None
            QApplication.instance().quit()
        def failed(message):
            window.setEnabled(True);self.move_task=None
            QMessageBox.warning(self,'数据目录操作失败',message+'\n当前使用的数据目录未改变，原始数据未删除。')
        self.move_task.signals.finished.connect(completed);self.move_task.signals.failed.connect(failed)
        QThreadPool.globalInstance().start(self.move_task)

    def backup_database(self) -> None:
        if self.backup_task:return
        target, _ = QFileDialog.getSaveFileName(self, '备份整个本地数据目录',
            f'BiomedReader_本地数据备份_{datetime.now():%Y%m%d_%H%M%S}.zip','ZIP 压缩包 (*.zip)')
        if not target:return
        self.backup_button.setEnabled(False)
        self.backup_task=BackgroundOperation(lambda progress:backup_data_directory(self.database,target,progress))
        self.backup_task.signals.progress.connect(self.backup_status.setText)
        self.backup_task.signals.finished.connect(self.backup_finished)
        self.backup_task.signals.failed.connect(self.backup_failed)
        QThreadPool.globalInstance().start(self.backup_task)

    def backup_finished(self,report):
        self.backup_task=None;self.backup_button.setEnabled(True)
        self.backup_status.setText(f"备份完成：{report['files']} 个文件，包含数据库及paper等数据子目录。")
        QMessageBox.information(self,'备份完成',report['path'])

    def backup_failed(self,message):
        self.backup_task=None;self.backup_button.setEnabled(True)
        self.backup_status.setText('备份未完成，未替换已有备份。')
        QMessageBox.warning(self,'备份失败',message)

    def export_favorites(self) -> None:
        target, _ = QFileDialog.getSaveFileName(self, "导出收藏", "favorites.csv", "CSV (*.csv)")
        if not target:
            return
        fields = [
            "title", "authors", "journal_title", "publication_date", "doi", "abstract",
            "source_name", "tags", "personal_note", "favorite_saved_at", "landing_page_url",
        ]
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for article in self.database.favorite_articles():
                writer.writerow({field: article.get(field, "") for field in fields})
        QMessageBox.information(self, "导出完成", target)


class JournalExportDialog(QDialog):
    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.setWindowTitle('打包分享期刊目录')
        self.resize(640,520)
        layout = QVBoxLayout(self)
        note = QLabel('勾选一个或多个目录。分享包包含目录成员 ISSN、期刊名称、别名和参考 JIF。接收者导入后创建为自定义目录；已有非空 JIF 保留，缺失值补齐。')
        note.setWordWrap(True);layout.addWidget(note)
        self.scopes = QListWidget()
        for scope in database.list_scopes():
            item = QListWidgetItem(f"{scope['name']} · {len(json.loads(scope['issns_json']))} 个 ISSN" + (' · 内置' if scope['is_builtin'] else ''))
            item.setData(Qt.ItemDataRole.UserRole,scope['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scopes.addItem(item)
        layout.addWidget(self.scopes,1)
        controls = QHBoxLayout()
        for text,state in (('全选',Qt.CheckState.Checked),('全不选',Qt.CheckState.Unchecked)):
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False,state=state:self.select_all(state))
            controls.addWidget(button)
        controls.addStretch();layout.addLayout(controls)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存目录分享压缩包')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def select_all(self, state):
        for index in range(self.scopes.count()):
            self.scopes.item(index).setCheckState(state)

    def selected_ids(self):
        return [self.scopes.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.scopes.count())
                if self.scopes.item(index).checkState()==Qt.CheckState.Checked]

    def validate(self):
        if not self.selected_ids():
            QMessageBox.warning(self,'尚未选择','请至少勾选一个期刊目录卡片。')
            return
        self.accept()


class JournalScopesPage(QWidget):
    """Directory cards and a full-size editor share the existing searchable catalogue panel.

    Scope checkboxes describe membership only. They never change the global catalogue's enabled
    flags, so saving one directory cannot silently change the membership of another directory.
    """

    def __init__(self, database: Database, catalogue: SettingsPage):
        super().__init__()
        self.database, self.catalogue = database, catalogue
        self.bundle_task = None
        self.stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.addWidget(self.stack)
        cards_page = QWidget()
        cards_layout = QVBoxLayout(cards_page)
        header = QHBoxLayout()
        title = QLabel("期刊目录")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        self.export_bundle_button = QPushButton('打包分享…')
        self.import_bundle_button = QPushButton('读取分享压缩包…')
        self.export_bundle_button.clicked.connect(self.export_scopes)
        self.import_bundle_button.clicked.connect(self.import_scopes)
        header.addWidget(self.export_bundle_button)
        header.addWidget(self.import_bundle_button)
        manage = QPushButton("管理全部期刊 / 导入 JIF")
        manage.clicked.connect(lambda: self.open_scope("__all__"))
        add = QPushButton("＋ 自定义期刊目录")
        add.clicked.connect(lambda: self.open_scope(None))
        header.addWidget(manage)
        header.addWidget(add)
        cards_layout.addLayout(header)
        note = QLabel("单击目录查看或编辑，按住拖动调整顺序。默认一行4张；NC扩选包含原NC精选及扩展候选。")
        note.setWordWrap(True)
        cards_layout.addWidget(note)
        self.bundle_status = QLabel('目录分享可离线使用；重名卡片自动添加“_已重复”后缀，不覆盖原目录。')
        self.bundle_status.setWordWrap(True)
        self.bundle_status.setObjectName('Muted')
        cards_layout.addWidget(self.bundle_status)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = CardBoard(columns=4)
        self.directory_board=container;container.scroll_area=scroll
        self.grid=container
        container.order_changed.connect(lambda order:self.database.save_card_order('scope',order))
        scroll.setWidget(container)
        cards_layout.addWidget(scroll, 1)
        self.stack.addWidget(cards_page)

        editor_page = QWidget()
        editor_layout = QVBoxLayout(editor_page)
        toolbar = QHBoxLayout()
        back = QPushButton("← 目录卡片")
        back.clicked.connect(self.show_cards)
        self.scope_name = QLineEdit()
        self.scope_name.setPlaceholderText("输入自定义目录名称")
        self.save_button = QPushButton("保存自定义目录")
        self.save_button.clicked.connect(self.save_scope)
        self.copy_button = QPushButton("复制为自定义目录")
        self.copy_button.clicked.connect(self.copy_scope)
        self.delete_button = QPushButton("删除目录")
        self.delete_button.clicked.connect(self.delete_scope)
        for widget in (back, self.scope_name, self.save_button, self.copy_button, self.delete_button):
            toolbar.addWidget(widget)
        editor_layout.addLayout(toolbar)
        editor_layout.addWidget(catalogue.journal_box, 1)
        catalogue.journal_box.show()
        self.stack.addWidget(editor_page)
        catalogue.journals_changed.connect(self.reload)
        self.reload()

    def export_scopes(self):
        if self.bundle_task:
            return
        dialog = JournalExportDialog(self.database,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            return
        path,_ = QFileDialog.getSaveFileName(self,'保存期刊目录分享包','BiomedReader_期刊目录.zip','ZIP 压缩包 (*.zip)')
        if not path:
            return
        if not Path(path).suffix:
            path += '.zip'
        identifiers = dialog.selected_ids()
        self.start_bundle_task(lambda progress:export_journal_bundle(self.database,path,identifiers,progress),False)

    def import_scopes(self):
        if self.bundle_task:
            return
        path,_ = QFileDialog.getOpenFileName(self,'读取期刊目录分享包','','ZIP 压缩包 (*.zip)')
        if path:
            self.start_bundle_task(lambda progress:import_journal_bundle(self.database,path,progress),True)

    def start_bundle_task(self, operation, importing):
        if self.bundle_task:
            return
        self.export_bundle_button.setEnabled(False);self.import_bundle_button.setEnabled(False)
        self.bundle_task = BackgroundOperation(operation)
        self.bundle_task.signals.progress.connect(self.bundle_status.setText)
        self.bundle_task.signals.finished.connect(lambda result:self.bundle_finished(result,importing))
        self.bundle_task.signals.failed.connect(self.bundle_failed)
        QThreadPool.globalInstance().start(self.bundle_task)

    def bundle_finished(self, result, importing):
        self.bundle_task = None
        self.export_bundle_button.setEnabled(True);self.import_bundle_button.setEnabled(True)
        if importing:
            self.bundle_status.setText(f"导入完成：新增 {result['scopes']} 个目录，其中 {result['renamed']} 个重名目录已添加后缀；新增 {result['journals']} 本期刊。")
            self.catalogue.reload_journals()
            self.catalogue.journals_changed.emit()
            self.stack.setCurrentIndex(0)
        else:
            self.bundle_status.setText(f"导出完成：{result['scopes']} 个目录、{result['issns']} 个成员 ISSN。")

    def bundle_failed(self, message):
        self.bundle_task = None
        self.export_bundle_button.setEnabled(True);self.import_bundle_button.setEnabled(True)
        self.bundle_status.setText('目录分享未完成；原有目录和已保存数据保留。')
        QMessageBox.warning(self,'目录分享失败',message)

    def reload(self):
        self.directory_board.clear()
        for index, scope in enumerate(self.database.list_scopes()):
            card = self.directory_board.add_card(scope["id"], scope["name"],
                                 f"{len(json.loads(scope['issns_json']))} 个 ISSN\n"
                                 + ("内置固定目录 · 点击查看" if scope["is_builtin"] else "自定义目录 · 点击编辑"),self.open_scope)
        self.directory_board.reflow()

    def show_cards(self):
        if self.catalogue.catalogue_task:
            QMessageBox.information(self, "正在同步", "请等待期刊目录同步完成。")
            return
        self.reload()
        self.stack.setCurrentIndex(0)

    def open_scope(self, scope_id):
        if scope_id == "__all__":
            scope = None
        elif scope_id is None:
            scope = {"id": None, "name": "", "issns_json": "[]", "is_builtin": 0}
        else:
            scope = next((s for s in self.database.list_scopes() if s["id"] == scope_id), None)
            if scope is None:
                return
        self.catalogue.current_scope = scope
        self.scope_name.setText(scope["name"] if scope else "全部期刊 · 公共基础目录")
        fixed = bool(scope and scope["is_builtin"])
        self.scope_name.setReadOnly(scope is None or fixed)
        self.save_button.setVisible(scope is not None and not fixed)
        self.copy_button.setVisible(bool(scope and scope["id"]))
        self.delete_button.setVisible(bool(scope and scope["id"] and not fixed))
        self.catalogue.journal_search.clear()
        self.catalogue.category_filter.setCurrentIndex(0)
        self.catalogue.reload_journals()
        self.stack.setCurrentIndex(1)

    def selected_issns(self):
        selected = set()
        table = self.catalogue.journal_table
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item.checkState() == Qt.CheckState.Checked:
                # Keep original print/electronic identifiers when an existing row stays selected.
                selected.update(item.data(Qt.ItemDataRole.UserRole + 1) or [item.data(Qt.ItemDataRole.UserRole)])
        return sorted(selected)

    def save_scope(self):
        scope = self.catalogue.current_scope
        try:
            self.database.save_scope(self.scope_name.text(), self.selected_issns(), scope["id"])
        except (ValueError, sqlite3.IntegrityError) as error:
            QMessageBox.warning(self, "无法保存目录", "目录名称已存在，请换一个名称。" if isinstance(error, sqlite3.IntegrityError) else str(error))
            return
        self.show_cards()

    def copy_scope(self):
        scope = dict(self.catalogue.current_scope)
        scope.update(id=None, is_builtin=0, name=scope["name"] + " · 副本")
        self.open_scope(None)
        self.catalogue.current_scope = scope
        self.scope_name.setText(scope["name"])
        self.catalogue.reload_journals()

    def delete_scope(self):
        scope = self.catalogue.current_scope
        if QMessageBox.question(self, "删除自定义目录", f"确定删除“{scope['name']}”？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.database.delete_scope(scope["id"])
        except ValueError as error:
            QMessageBox.warning(self, "无法删除", str(error))
            return
        self.show_cards()


class MainWindow(QMainWindow):
    def __init__(self, database: Database, icon_path: Path | None = None):
        super().__init__()
        self.database = database
        self.thread_pool = QThreadPool.globalInstance()
        self.setWindowTitle("刊禾 · Biomed Reader")
        self.resize(1380, 860)
        if icon_path and icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget()
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self.sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        brand = QLabel("刊禾\nBiomed Reader")
        brand.setObjectName("Brand")
        sidebar_layout.addWidget(brand)
        self.nav_buttons = []
        for label, callback in (
            ("关键词方案", self.show_home),
            ("收藏文章", self.show_favorites),
            ("期刊目录", self.show_journal_scopes),
            ("设置", self.show_settings),
        ):
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(callback)
            sidebar_layout.addWidget(button)
            self.nav_buttons.append(button)
        sidebar_layout.addStretch()
        root_layout.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.pages.setContentsMargins(18, 18, 18, 18)
        self.home = HomePage(database)
        self.favorites = FavoritesPage(database)
        self.settings_page = SettingsPage(database)
        self.journal_scopes = JournalScopesPage(database, self.settings_page)
        self.results = ResultsPage(database, self.thread_pool)
        self.pages.addWidget(self.home)
        self.pages.addWidget(self.favorites)
        self.pages.addWidget(self.settings_page)
        self.pages.addWidget(self.journal_scopes)
        self.pages.addWidget(self.results)
        root_layout.addWidget(self.pages, 1)

        self.home.open_profile.connect(self.open_profile)
        self.home.profiles_changed.connect(self.home.reload)
        self.results.back_requested.connect(self.show_home)
        self.results.cache_changed.connect(self._reload_all)
        self.results.refresh_state.connect(self.home.set_refresh_state)
        self.settings_page.journals_changed.connect(self.home.reload)
        self.show_home()
        self.interface_scale=InterfaceScale(self,database,APP_STYLE)

        qt_settings = QSettings("OrangeReader", "BiomedLiterature")
        geometry = qt_settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _select_navigation(self, index: int | None) -> None:
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(index is not None and button_index == index)

    def show_home(self) -> None:
        self.home.reload()
        self.pages.setCurrentWidget(self.home)
        self._select_navigation(0)

    def show_favorites(self) -> None:
        self.favorites.current_group = None
        self.favorites.reload()
        self.pages.setCurrentWidget(self.favorites)
        self._select_navigation(1)

    def show_settings(self) -> None:
        self.pages.setCurrentWidget(self.settings_page)
        self._select_navigation(3)

    def show_journal_scopes(self) -> None:
        self.journal_scopes.show_cards()
        self.pages.setCurrentWidget(self.journal_scopes)
        self._select_navigation(2)

    def open_profile(self, profile_id: int) -> None:
        self.results.load_profile(profile_id)
        self.pages.setCurrentWidget(self.results)
        self._select_navigation(None)

    def _reload_all(self) -> None:
        self.home.reload()
        self.favorites.reload()

    def closeEvent(self, event):  # noqa: N802 - Qt naming convention
        if getattr(self.settings_page,'move_task',None):
            event.ignore();return
        self.interface_scale.stop()
        self.results.cancel_local_reads()
        self.results.website.shutdown()
        if not getattr(self,'_data_switching',False):self.results.save_position()
        if self.results.current_task:
            self.results.current_task.cancelled = True
        QSettings("OrangeReader", "BiomedLiterature").setValue("window_geometry", self.saveGeometry())
        super().closeEvent(event)
