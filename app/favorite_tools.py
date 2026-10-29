"""Local collection filters and a single confirmation policy for unfavoriting."""
import re,unicodedata
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QListWidget,QListWidgetItem,
    QPushButton,QLineEdit,QDialogButtonBox,QMessageBox)

def normalized(value):
    return unicodedata.normalize('NFKC',str(value or '')).casefold()

def search_terms(query):
    query=query.strip().replace('“','"').replace('”','"')
    if not query:raise ValueError('请输入需要搜索的关键词。')
    if query.count('"')%2:raise ValueError('短语的双引号需要成对输入。')
    terms=[normalized(phrase or word) for phrase,word in re.findall(r'"([^"]+)"|(\S+)',query)]
    if not terms:raise ValueError('请输入有效关键词。')
    return terms

def filter_grouped_favorites(database,grouped,selection):
    if selection is None:return grouped
    chosen=set(selection['group_ids']);terms=search_terms(selection['query'])
    names={group['name']:str(group['id']) for group in database.list_custom_groups()}
    cache={};result={}
    for name,rows in grouped.items():
        if names.get(name,'__ungrouped__') not in chosen:continue
        matched=[]
        for article in rows:
            key=article['article_key']
            if key not in cache:
                text=normalized('\n'.join(str(article.get(field) or '') for field in
                    ('title','abstract','authors','journal_title','doi','pmid','tags','personal_note')))
                cache[key]=all(term in text for term in terms)
            if cache[key]:matched.append(article)
        if matched:result[name]=matched
    return result

def confirm_unfavorite(parent,articles,plan=None):
    rows=list({article['article_key']:article for article in articles}.values())
    if not rows:return False
    if plan is not None:
        final=set(plan['final_keys'])
        if not final:return True
        rows=[article for article in rows if article['article_key'] in final]
    names='\n'.join('• '+str(article.get('title') or article['article_key']) for article in rows[:5])
    if len(rows)>5:names+=f'\n……另 {len(rows)-5} 篇'
    message=(f'以下 {len(rows)} 篇文章取消后将不再属于任何收藏分组。确定继续吗？\n\n{names}\n\n'
        '将删除其收藏记录、标签笔记、已保存AI总结及关联信息；解除本地PDF链接，并删除 paper 中不再被其它文章使用的PDF副本。\n'
        '不再被使用的文章缓存也会清理。仍被关键词方案或已保存AI对话使用的检索数据，以及 paper 之外的原始PDF会保留。'
        +('\n\n本批其它文章仅移出选定分组，在其它分组的收藏、PDF和AI总结保留。' if plan and plan.get('retained_keys') else ''))
    dialog=QMessageBox(QMessageBox.Icon.Warning,'确认取消收藏',message,
        QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,parent)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    dialog.button(QMessageBox.StandardButton.Ok).setText('确定取消收藏')
    dialog.button(QMessageBox.StandardButton.Cancel).setText('保留收藏')
    return dialog.exec()==QMessageBox.StandardButton.Ok

class FavoriteSearchDialog(QDialog):
    def __init__(self,database,previous=None,current_group=None,parent=None):
        super().__init__(parent);self.selection=None;self.setWindowTitle('搜索已收藏文章');self.resize(650,520)
        layout=QVBoxLayout(self)
        note=QLabel('选择一个或多个收藏分组。搜索标题、摘要、作者、期刊、DOI、标签和笔记；不读取 PDF 全文。')
        note.setWordWrap(True);layout.addWidget(note)
        self.groups=QListWidget();grouped=database.grouped_favorites()
        identifiers={group['name']:str(group['id']) for group in database.list_custom_groups()}
        selected=set(previous['group_ids']) if previous else ({identifiers.get(current_group,'__ungrouped__')} if current_group else set(identifiers.values())|{'__ungrouped__'})
        for name,articles in grouped.items():
            item=QListWidgetItem(f'{name} · {len(articles)} 篇');key=identifiers.get(name,'__ungrouped__')
            item.setData(Qt.ItemDataRole.UserRole,key);item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if key in selected else Qt.CheckState.Unchecked);self.groups.addItem(item)
        layout.addWidget(self.groups,1)
        controls=QHBoxLayout()
        for text,value in (('全选分组',True),('全不选分组',False)):
            button=QPushButton(text);button.clicked.connect(lambda checked=False,value=value:self.select_groups(value));controls.addWidget(button)
        controls.addStretch();layout.addLayout(controls)
        layout.addWidget(QLabel('关键词（多个词用空格分隔，需全部匹配；双引号表示完整短语）：'))
        self.query=QLineEdit();self.query.setPlaceholderText('示例：ATM repair；短语示例："DNA replication"；不使用 AND / OR 语法')
        if previous:self.query.setText(previous['query'])
        layout.addWidget(self.query)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('搜索');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.validate);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def select_groups(self,value):
        for index in range(self.groups.count()):self.groups.item(index).setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
    def validate(self):
        identifiers=[self.groups.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.groups.count()) if self.groups.item(i).checkState()==Qt.CheckState.Checked]
        try:
            if not identifiers:raise ValueError('请至少选择一个收藏分组。')
            search_terms(self.query.text())
        except ValueError as error:QMessageBox.warning(self,'无法搜索',str(error));return
        self.selection=dict(group_ids=identifiers,query=self.query.text().strip());self.accept()
