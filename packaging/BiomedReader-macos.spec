from pathlib import Path
import platform
from PyInstaller.utils.hooks import collect_data_files
root=Path(SPECPATH).resolve().parent
assets=[(str(root/'app/assets'),'assets')]
assets+=collect_data_files('citeproc')+collect_data_files('pdfminer')+collect_data_files('openpyxl')+collect_data_files('docx')
assets += [(str(root/'app/README.txt'),'.'),(str(root/'app/THIRD_PARTY_NOTICES.txt'),'.')]
assets += [(str(root/'build/codex-runtime/manifest.json'),'codex-runtime')]
assets += [(str(root/'app'/name),'figure_scripts') for name in
           ('figure_render.py','figure_canvas.py','figure_data.py','figure_table_charts.py','wb_density.py')]
a=Analysis([str(root/'app/main.py')],pathex=[str(root/'app')],binaries=[(str(root/'build/codex-runtime/codex'),'codex-runtime')],datas=assets,
    hiddenimports=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','citeproc.formatter.html','citeproc.formatter.plain','keyring.backends.macOS'],hookspath=[],hooksconfig={'matplotlib': {'backends': ['Agg','PDF','SVG']}},runtime_hooks=[],excludes=['tkinter'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='BiomedReader',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,argv_emulation=False,target_arch=platform.machine(),
    codesign_identity='-',entitlements_file=str(root/'packaging/entitlements.plist'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='BiomedReader')
app=BUNDLE(coll,name='Biomed Reader.app',icon=str(root/'build/BiomedReader.icns'),
    bundle_identifier='org.orangereader.biomedliterature',
    info_plist={'CFBundleDisplayName':'刊禾 · Biomed Reader','CFBundleShortVersionString':'2.2.2',
        'CFBundleVersion':'2.2.2','LSMinimumSystemVersion':'12.0','NSHighResolutionCapable':True,
        'NSRequiresAquaSystemAppearance':True})
