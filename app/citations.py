"""Offline CSL rendering. Unsupported journal styles are never silently guessed."""
import json
import re
import sys
from pathlib import Path
from citeproc import CitationStylesStyle, CitationStylesBibliography, Citation, CitationItem, formatter
from citeproc.source.json import CiteProcJSON

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets' / 'csl'


def journal_styles():
    return json.loads((ROOT / 'catalogue.json').read_text(encoding='utf8'))


def article_item(article):
    """Keep absent fields absent; never invent volume, pages, or author initials."""
    result = {'id': str(article.get('article_key') or 'article'), 'type': 'article-journal',
              'title': article.get('title') or '', 'container-title': article.get('journal_title') or ''}
    source = str(article.get('source_name') or '')
    if any(server in source for server in ('bioRxiv', 'medRxiv')) and (
            'PubMed' not in source or str(article.get('doi') or '').startswith('10.1101/')):
        result['type'] = 'article'
        result['genre'] = 'Preprint'
        result['archive'] = 'medRxiv' if 'medRxiv' in source else 'bioRxiv'
        result['container-title'] = result['container-title'] or result['archive']
    for field, key in [('doi', 'DOI'), ('volume', 'volume'), ('issue', 'issue'), ('pages', 'page'), ('landing_page_url', 'URL')]:
        if article.get(field):
            result[key] = str(article[field])
    date = str(article.get('publication_date') or '')
    if re.match(r'^\d{4}', date):
        result['issued'] = {'date-parts': [[int(date[:4])]]}
    authors = article.get('authors') or ''
    if isinstance(authors, str):
        # Existing caches retain display names. Literal names preserve them without
        # making an unreliable surname/given-name split for international names.
        result['author'] = [{'literal': name.strip()} for name in re.split(r';|\n', authors) if name.strip()]
    return result


def render(articles, journal='Cell', rich=False):
    catalogue = journal_styles()
    style_id = catalogue.get(journal, {}).get('style')
    if not style_id:
        raise ValueError('该期刊尚未匹配并核实正式 CSL 样式，不能生成声称符合该期刊标准的引用。')
    style = CitationStylesStyle(str(ROOT / (style_id + '.csl')), validate=False)
    bibliography = CitationStylesBibliography(style, CiteProcJSON([article_item(article) for article in articles]),
                                             formatter.html if rich else formatter.plain)
    citations = [Citation([CitationItem(str(article.get('article_key') or 'article'))]) for article in articles]
    for citation in citations:
        bibliography.register(citation)
    markers = [str(bibliography.cite(citation, lambda item: None)) for citation in citations]
    references = [str(item) for item in bibliography.bibliography()]
    if style.root.bibliography.get('second-field-align'):
        # The plain/HTML formatter has no layout columns. Preserve the separation
        # CSL requests between its number column and reference text.
        references = [re.sub(r'^(\[?\d+\]?[.)]?)(?=[^\s\d])', r'\1 ', text) for text in references]
    return {'markers': markers, 'references': references}


def choose_style(database, parent):
    from PySide6.QtWidgets import QComboBox, QCompleter
    from PySide6.QtCore import Qt
    selector = QComboBox(parent)
    selector.setEditable(True)
    selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    for title, metadata in sorted(journal_styles().items(), key=lambda pair: pair[0].casefold()):
        label = title + ('（' + metadata.get('fallback_label', '通用后备样式') + '）' if metadata.get('fallback') else '')
        selector.addItem(label if metadata['style'] else title + '（样式待核实）', title)
        if not metadata['style']:
            selector.model().item(selector.count() - 1).setEnabled(False)
    selector.completer().setFilterMode(Qt.MatchFlag.MatchContains)
    selector.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    selector.setCurrentIndex(max(0, selector.findData(database.get_settings().get('citation_journal', 'Cell'))))
    selector.activated.connect(lambda index: database.save_settings({'citation_journal': selector.itemData(index)}))
    return selector


def show_citation(database, article, parent):
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QPushButton, QApplication, QMessageBox
    article=dict(article)
    if hasattr(database,'article_record'):
        cached=database.article_record(article['article_key'])
        for field in ('pmid','doi','volume','issue','pages'):
            if cached.get(field):article[field]=cached[field]
    dialog = QDialog(parent)
    dialog.setWindowTitle('复制文章引用')
    dialog.resize(760, 380)
    layout = QVBoxLayout(dialog)
    selector = choose_style(database, dialog)
    layout.addWidget(selector)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    layout.addWidget(text)
    notice=QLabel('引用依据本地缓存生成；可联网补全卷号、期号和页码，已补全内容可离线使用。')
    notice.setWordWrap(True);layout.addWidget(notice)
    copy = QPushButton('复制引用')
    def copy_citation():
        QApplication.clipboard().setText(text.toPlainText())
        dialog.accept()
    copy.clicked.connect(copy_citation)
    layout.addWidget(copy)
    def update():
        try:
            text.setPlainText('\n'.join(render([article], selector.currentData())['references']))
            copy.setEnabled(True)
        except Exception as error:
            text.setPlainText(str(error))
            copy.setEnabled(False)
    selector.activated.connect(update)
    update()
    active=[True]
    if hasattr(database,'article_record') and (article.get('pmid') or article.get('doi')) and not (article.get('volume') and article.get('pages')):
        from PySide6.QtCore import QObject,Slot,QThreadPool
        from ui import BackgroundOperation
        from bibliography import enrich_articles
        copy.setEnabled(False);notice.setText('正在通过 PubMed 补全卷号和页码……')
        class Receiver(QObject):
            @Slot(object)
            def finished(self,result):
                if not active[0]:return
                rows,warnings=result;article.update(rows[0]);update()
                notice.setText('卷号、期号和页码已保存，可离线复制引用。' if article.get('volume') and article.get('pages') else
                    '未能补齐全部卷页：'+('；'.join(warnings) if warnings else '数据源可能尚未分配卷号或页码（在线优先发表）。')+' 可复制已有引用。')
            @Slot(str)
            def failed(self,message):
                if active[0]:notice.setText('暂未补全：'+message+'。可复制已有引用。');update()
        receiver=Receiver(dialog)
        task=BackgroundOperation(lambda progress:enrich_articles(database,[dict(article)],force_missing=True))
        task.signals.finished.connect(receiver.finished);task.signals.failed.connect(receiver.failed)
        dialog._bibliography_task=task;dialog._bibliography_receiver=receiver
        QThreadPool.globalInstance().start(task)
    accepted=dialog.exec()==QDialog.DialogCode.Accepted
    active[0]=False
    if accepted:
        QMessageBox.information(parent, '复制成功', '复制成功，可以粘贴')
