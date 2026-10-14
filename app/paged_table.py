"""A virtual result table: paint visible rows and retain at most six metadata pages."""
from collections import OrderedDict
from PySide6.QtCore import Qt,QAbstractTableModel,QModelIndex,Signal,QTimer
from PySide6.QtWidgets import QTableView,QAbstractItemView,QHeaderView,QTableWidgetItem

PAGE_SIZE=100

class ArticlePageModel(QAbstractTableModel):
    page_requested=Signal(int)
    headers=['收藏','标题','期刊','发表日期','数据源','DOI','JIF']
    def __init__(self,parent=None):
        super().__init__(parent)
        self.keys=[];self.pages=OrderedDict();self.pending=set();self.generation=0
    def rowCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.keys)
    def columnCount(self,parent=QModelIndex()):
        return 0 if parent.isValid() else 7
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole and orientation==Qt.Orientation.Horizontal:return self.headers[section]
    def set_keys(self,keys):
        self.generation+=1
        self.beginResetModel();self.keys=keys;self.pages.clear();self.pending.clear();self.endResetModel()
    def request_page(self,page):
        if page not in self.pending and page not in self.pages and page*PAGE_SIZE<len(self.keys):
            self.pending.add(page)
            generation=self.generation
            QTimer.singleShot(0,lambda:self.page_requested.emit(page) if generation==self.generation and page in self.pending else None)
    def install_page(self,page,articles):
        self.pending.discard(page)
        self.pages[page]={article['article_key']:article for article in articles}
        self.pages.move_to_end(page)
        while len(self.pages)>6:self.pages.popitem(last=False)
        start=page*PAGE_SIZE;end=min(len(self.keys),start+PAGE_SIZE)-1
        if end>=start:self.dataChanged.emit(self.index(start,0),self.index(end,6))
    def article_at(self,row):
        if not 0<=row<len(self.keys):return None
        page=row//PAGE_SIZE
        if page not in self.pages:
            self.request_page(page);return None
        self.pages.move_to_end(page)
        return self.pages[page].get(self.keys[row])
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0<=index.row()<len(self.keys):return None
        if role==Qt.ItemDataRole.UserRole:return self.keys[index.row()]
        if role not in (Qt.ItemDataRole.DisplayRole,Qt.ItemDataRole.ToolTipRole,Qt.ItemDataRole.TextAlignmentRole):return None
        if role==Qt.ItemDataRole.TextAlignmentRole and index.column()==0:return Qt.AlignmentFlag.AlignCenter
        article=self.article_at(index.row())
        if article is None:return '加载中…' if index.column()==1 and role==Qt.ItemDataRole.DisplayRole else None
        values=['★' if article.get('is_favorite_in_profile',article.get('is_favorite')) else '',
                ('[已隐藏] ' if article.get('is_hidden') else '')+(article.get('title') or '无标题'),article.get('journal_title') or '',article.get('publication_date') or '',
                article.get('source_name') or '',article.get('doi') or '',
                f"{article['jif']:g}" if article.get('jif') is not None else '']
        if role==Qt.ItemDataRole.ToolTipRole and index.column()==6 and article.get('jif') is not None:
            return str(article.get('jif_source') or '')+'\n指标年份：'+str(article.get('jif_year') or '')
        return values[index.column()]

class PagedArticleTable(QTableView):
    article_activated=Signal(object)
    def __init__(self):
        super().__init__()
        self.page_model=ArticlePageModel(self);self.setModel(self.page_model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True);self.setWordWrap(False)
        self.verticalHeader().hide();self.verticalHeader().setDefaultSectionSize(30)
        header=self.horizontalHeader();header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False);header.setMinimumSectionSize(36)
        for column,width in enumerate((46,500,155,100,100,125,65)):self.setColumnWidth(column,width)
        self.doubleClicked.connect(lambda index:self.article_activated.emit(self.current_article()) if self.current_article() else None)
    def current_article(self):
        return self.page_model.article_at(self.currentIndex().row())
    def rowCount(self):return self.page_model.rowCount()
    def item(self,row,column):
        if not 0<=row<self.rowCount():return None
        index=self.page_model.index(row,column)
        item=QTableWidgetItem(str(self.page_model.data(index) or ''))
        item.setData(Qt.ItemDataRole.UserRole,self.page_model.keys[row]);return item
    @property
    def articles_by_key(self):
        return {key:article for page in self.page_model.pages.values() for key,article in page.items()}
