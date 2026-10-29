"""User-triggered cache collection followed by SQLite space reclamation."""
import shutil,sqlite3
from contextlib import closing
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QProgressBar,QMessageBox

def clean_database(database,progress=lambda value:None):
    before=database.path.stat().st_size
    # VACUUM may require twice the original file size in additional free space.
    if shutil.disk_usage(database.path.parent).free<before*2+64*1024*1024:raise ValueError('磁盘剩余空间不足。数据库压缩需要最多约原数据库两倍的额外空闲空间；未开始清理。')
    from cache_cleanup import collect_unused
    progress('正在检查文章、收藏、关键词方案及AI历史引用……')
    with database.connect() as c:
        c.execute('BEGIN IMMEDIATE');report=collect_unused(c)
        if c.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('数据库关联校验未通过，已撤销本次清理，请先备份。')
    progress('无用缓存已清理，正在压缩数据库并回收磁盘空间……')
    try:
        with closing(sqlite3.connect(database.path,timeout=30)) as c:
            c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.execute('VACUUM');c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('压缩后的数据库校验未通过，请保留资料库并联系开发者。')
    except sqlite3.Error:
        raise ValueError('无用缓存已清理，但数据库压缩未完成，文件可能被其它软件占用。请关闭其它Biomed Reader窗口后重试；收藏和有效缓存仍保留。') from None
    from preprint_cache_cleanup import clean_preprint_cache
    try:preprint=clean_preprint_cache(database,progress)
    except (ValueError,OSError,sqlite3.Error) as error:preprint=dict(exists=True,before=0,after=0,removed_entries=0,removed_pages=0,trimmed_pages=0,warnings=['主资料库清理已完成；预印本缓存未清理：'+str(error)])
    from website_cache_cleanup import clean_website_cache
    try:website=clean_website_cache(database,progress)
    except (ValueError,OSError,sqlite3.Error) as error:website=dict(exists=True,before=0,after=0,removed_pages=0,warnings=['其它清理已完成；官网进度缓存未清理：'+str(error)])
    return dict(**report,before=before,after=database.path.stat().st_size,preprint=preprint,website=website)

class CacheCleanupDialog(QDialog):
    def __init__(self,page):
        super().__init__(page.window());self.page=page;self.task=None;self.setWindowTitle('清理无用缓存');self.resize(640,230);self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint,False);layout=QVBoxLayout(self);self.status=QLabel('正在准备清理……');self.status.setWordWrap(True);layout.addWidget(self.status);bar=QProgressBar();bar.setRange(0,0);layout.addWidget(bar);layout.addWidget(QLabel('保留收藏、PDF、有效检索缓存和增量刷新记录。请等待完成。'))
        self.manager=page.window().ai_manager;self.channel='cache-maintenance';self.manager.progress.connect(self.progress)
    def start(self):
        self.show();self.task=self.manager.submit(self,lambda:clean_database(self.page.database,lambda text:self.manager.progress.emit(self.channel,text)),'completed','failed')
    def progress(self,channel,text):
        if channel==self.channel:self.status.setText(text)
    def completed(self,report):
        self.task=None;self.page.cleanup_task=None;self.accept()
        from local_metrics import start_size_scan
        start_size_scan(self.page)
        before=report['before']/1024**2;after=report['after']/1024**2
        preprint=report.get('preprint') or {};extra=''
        if preprint.get('exists') and preprint.get('before'):extra=f"\n预印本元数据缓存：{preprint['before']/1024**2:.1f} MB → {preprint['after']/1024**2:.1f} MB\n清理预印本缓存记录 {preprint['removed_entries']:,} 条（含分页副本）。"
        website=report.get('website') or {}
        if website.get('exists') and website.get('before'):extra+=f"\n官网检索进度缓存：{website['before']/1024**2:.1f} MB → {website['after']/1024**2:.1f} MB\n清理过期／无用网页 {website['removed_pages']:,} 页。"
        messages=(preprint.get('warnings') or [])+(website.get('warnings') or []);warnings='\n\n'+'\n'.join(messages) if messages else ''
        notify=QMessageBox.warning if warnings else QMessageBox.information
        notify(self.page,'清理完成（有提示）' if warnings else '清理完成',f"已清理无用文章 {report['articles']:,} 篇、无用AI总结 {report['summaries']:,} 项。\n主数据库：{before:.1f} MB → {after:.1f} MB\n释放主数据库空间：{max(0,before-after):.1f} MB"+extra+'\n\n收藏、PDF、现有方案及增量检索记录均保留。'+warnings)
    def failed(self,message):self.task=None;self.page.cleanup_task=None;self.accept();QMessageBox.warning(self.page,'清理未完成',message)
    def reject(self):
        if self.task:return
        super().reject()
    def closeEvent(self,event):
        if self.task:event.ignore()
        else:super().closeEvent(event)

def request_cleanup(page):
    from local_maintenance import busy
    if busy(page.window()) or page.backup_task or page.cleanup_task:QMessageBox.information(page,'暂不能清理','请先结束联网搜索、AI任务、备份、分享及数据迁移。');return
    text=('将清理主资料库、biorxiv_metadata.sqlite3 中不再使用的文章资料，以及 website_progress.sqlite3 中过期或属于已删除方案的临时网页，并分别压缩数据库。\n'
          '包括已删除“AI寻找文章”对话的遗留候选卡片、文章引用索引，以及已删除对话/续聊资料留下的数据库空闲空间。\n\n保留所有收藏文章、已链接PDF、有效方案缓存、仍存在的AI对话及其续聊资料、期刊目录和增量检索记录。不删除用户自行选择的原始上传文件。\n建议先使用“备份本地数据（ZIP）”。是否继续？')
    if QMessageBox.question(page,'清理无用缓存',text,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
    page.cleanup_task=CacheCleanupDialog(page);page.cleanup_task.start()
