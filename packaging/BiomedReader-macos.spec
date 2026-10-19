from pathlib import Path
import platform
root=Path(SPECPATH).resolve().parent
assets=[(str(p),'assets') for p in (root/'app/assets').iterdir() if p.is_file() and p.suffix!='.gz']
assets += [(str(root/'app/README.txt'),'.'),(str(root/'app/THIRD_PARTY_NOTICES.txt'),'.')]
a=Analysis([str(root/'app/main.py')],pathex=[str(root/'app')],binaries=[],datas=assets,
    hiddenimports=['PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets'],hookspath=[],runtime_hooks=[],excludes=['tkinter'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='BiomedReader',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=False,argv_emulation=False,target_arch=platform.machine(),
    codesign_identity='-',entitlements_file=str(root/'packaging/entitlements.plist'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='BiomedReader')
app=BUNDLE(coll,name='Biomed Reader.app',icon=str(root/'build/BiomedReader.icns'),
    bundle_identifier='org.orangereader.biomedliterature',
    info_plist={'CFBundleDisplayName':'刊禾 · Biomed Reader','CFBundleShortVersionString':'2.0',
        'CFBundleVersion':'2.0','LSMinimumSystemVersion':'12.0','NSHighResolutionCapable':True,
        'NSRequiresAquaSystemAppearance':True})
