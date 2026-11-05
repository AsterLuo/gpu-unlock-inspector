# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包脚本。

目标：单文件、免安装、冷启动尽量快。
- 只带 tkinter 与本项目自身，不引入第三方运行时依赖；
- 排除 numpy / matplotlib / PIL 之外的常见大包，压体积；
- gpu_db.json 作为数据文件打进 exe（同时支持 exe 同目录的外部覆盖）。
"""
import os

block_cipher = None
here = os.path.abspath(os.path.join(SPECPATH))

a = Analysis(
    [os.path.join(here, 'src', 'main.py')],
    pathex=[os.path.join(here, 'src')],
    binaries=[],
    datas=[
        (os.path.join(here, 'assets', 'gpu_db.json'), '.'),
    ],
    hiddenimports=[
        'core.nvml', 'core.smi', 'core.wmi', 'core.collector',
        'core.modeldb', 'core.storage', 'core.theme', 'core.unlock',
        'core.bench_ocl', 'core.runner', 'core.ssdiag',
        'core.hwinfo', 'core.archive', 'core.tflops', 'core.usb_boot', 'core.usb_maker', 'core.deps', 'core.gpu_family', 'core.assets_fetch', 'core.envscan', 'core.single_instance', 'core.python_boot', 'ui.anim', 'core.log',
        'i18n.strings',
        'ui.base', 'ui.busy', 'ui.dialogs', 'ui.scroll', 'ui.panels', 'ui.app',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy', 'scipy', 'pandas', 'matplotlib', 'pytest', 'setuptools',
        'pip', 'wheel', 'IPython', 'notebook', 'distutils', 'lib2to3',
        'sqlite3', 'unittest', 'pydoc_data', 'test',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='GPUUnlockInspector',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)