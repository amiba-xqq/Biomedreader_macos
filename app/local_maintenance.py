"""Explicit local maintenance using normal Windows Shell operations."""
import ctypes,os,sys,subprocess
from pathlib import Path
from PySide6.QtWidgets import QApplication,QMessageBox

DATA_WARNING=('这会清除当前本地数据目录中的关键词方案、检索缓存、收藏文章和分组、标签笔记、自定义期刊目录、AI配置与历史记录、AI总结与报告，以及 paper 内已链接的 PDF 副本。\n'
 '原始下载位置的 PDF 不受影响。软件将退出，下次打开会建立新的默认资料库。\n请先备份：此操作无法在软件内撤销。')

def recycle_path(path):
    """Recycle an explicitly validated application-owned item; never bypass protection."""
    path=Path(path)
    if not path.exists():return
    if path.is_symlink() or path.is_junction():raise ValueError('拒绝处理链接或重解析点。')
    if sys.platform=='darwin':
        from PySide6.QtCore import QFile
        if not QFile.moveToTrash(str(path)):raise OSError('无法移入废纸篓，原文件仍保留。')
        return
    if os.name!='nt':
        raise OSError('此版本的数据清理仅支持 Windows 标准回收站。')
    from ctypes import wintypes
    import uuid
    class GUID(ctypes.Structure):_fields_=[('bytes',ctypes.c_ubyte*16)]
    def guid(value):return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)
    ole=ctypes.OleDLL('ole32');shell=ctypes.OleDLL('shell32');pointer=ctypes.c_void_p
    ole.CoInitializeEx.argtypes=[pointer,wintypes.DWORD];ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoCreateInstance.argtypes=[ctypes.POINTER(GUID),pointer,wintypes.DWORD,ctypes.POINTER(GUID),ctypes.POINTER(pointer)]
    ole.CoCreateInstance.restype=ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes=[wintypes.LPCWSTR,pointer,ctypes.POINTER(GUID),ctypes.POINTER(pointer)]
    shell.SHCreateItemFromParsingName.restype=ctypes.c_long
    def check(result):
        if result<0:raise OSError(f'Windows 回收站操作失败（0x{result&0xffffffff:08X}），请检查文件占用、防护提示或磁盘回收站。')
    def call(obj,index,*types):
        table=ctypes.cast(obj,ctypes.POINTER(ctypes.POINTER(pointer))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long,pointer,*types)(table[index])
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:check(initialized)
    operation=pointer();item=pointer()
    try:
        clsid=guid('3ad05575-8857-4850-9277-11b85bdb8e09');iid=guid('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
        check(ole.CoCreateInstance(ctypes.byref(clsid),None,1,ctypes.byref(iid),ctypes.byref(operation)))
        item_iid=guid('43826d1e-e718-42ee-bc55-a1e261c37bfe')
        check(shell.SHCreateItemFromParsingName(str(path.absolute()),None,ctypes.byref(item_iid),ctypes.byref(item)))
        # Standard Windows IFileOperation. Recycle-on-delete, undo record and
        # early failure: no raw unlink, no helper shell, no elevation or AV changes.
        flags=0x00080000|0x20000000|0x00100000|0x0010|0x0400|0x0004|0x4000
        check(call(operation,5,wintypes.DWORD)(operation,flags))
        check(call(operation,18,pointer,pointer)(operation,item,None))
        check(call(operation,21)(operation))
        aborted=wintypes.BOOL();check(call(operation,22,ctypes.POINTER(wintypes.BOOL))(operation,ctypes.byref(aborted)))
        if aborted.value or path.exists():raise OSError('Windows 未完成回收操作，文件仍保留。请检查文件占用或安全软件提示。')
    finally:
        if item.value:call(item,2)(item)
        if operation.value:call(operation,2)(operation)
        if initialized>=0:ole.CoUninitialize()

def validate_clear_directory(path):
    from data_location import validate_data_directory
    root=Path(path)
    if root.is_symlink() or root.is_junction():raise ValueError('数据根目录不能是链接。')
    root=validate_data_directory(root)
    # Shell must not follow junctions out of the validated library.
    for current,folders,files in os.walk(root,followlinks=False):
        for name in folders+files:
            child=Path(current)/name
            if child.is_symlink() or child.is_junction():raise ValueError('数据目录含链接，请先移出后重试。')
    return root

def clear_directory(path):
    root=validate_clear_directory(path);recycle_path(root)
    from PySide6.QtCore import QSettings
    settings=QSettings('OrangeReader','BiomedLiterature');settings.setValue('cleared_data_directory',str(root));settings.sync()

def busy(window):
    return bool(getattr(getattr(window,'ai_manager',None),'jobs',None) or getattr(window.settings_page,'cleanup_task',None) or getattr(window.results,'current_task',None) or getattr(window.favorites,'pdf_task',None) or
       getattr(window.favorites,'cancel_task',None) or getattr(window.favorites,'bundle_task',None) or
       getattr(window.favorites,'pdf_import_dialog',None) or getattr(window.settings_page,'move_task',None) or
       getattr(window.settings_page.software_update,'task',None) or getattr(window,'batch_refresh',None) and window.batch_refresh.active)

def request_clear(page):
    window=page.window()
    if busy(window):QMessageBox.information(page,'暂不能删除','请先结束联网搜索、导入、分享、下载或数据迁移，并关闭 PDF 选择窗口。');return
    first=QMessageBox.warning(page,'删除本地数据',DATA_WARNING,QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
    if first!=QMessageBox.StandardButton.Ok:return
    second=QMessageBox.warning(page,'再次确认删除',str(page.database.path.parent)+'\n\n'+DATA_WARNING+'\n\n确定删除并退出？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
    if second!=QMessageBox.StandardButton.Yes:return
    try:root=validate_clear_directory(page.database.path.parent)
    except Exception as error:QMessageBox.warning(page,'未删除',str(error));return
    app=QApplication.instance();app.pending_clear_data=str(root);window.close()

def uninstaller_path():
    return Path(sys.executable).resolve().parent/'unins000.exe' if getattr(sys,'frozen',False) else None

def request_uninstall(page):
    if sys.platform=='darwin':
        QMessageBox.information(page,'卸载软件','请退出 Biomed Reader，然后在访达的“应用程序”中将 Biomed Reader 移至废纸篓。\n本地数据会保留；如需删除，请先使用本页“删除本地数据”。')
        return
    path=uninstaller_path()
    if path is None or not path.is_file():QMessageBox.information(page,'未找到卸载程序','当前目录没有 unins000.exe。完整目录版可在关闭软件后删除程序文件夹；本地数据可用本页“删除本地数据”单独处理。');return
    if busy(page.window()):QMessageBox.information(page,'暂不能卸载','请先结束当前后台操作。');return
    if QMessageBox.question(page,'卸载软件','即将退出并打开官方安装包自带的卸载程序。卸载时可以选择保留或删除本地数据。',QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)!=QMessageBox.StandardButton.Ok:return
    QApplication.instance().pending_uninstall=str(path);page.window().close()
