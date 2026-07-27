# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = [
    'aiosqlite', 'pypdfium2', 'tiktoken', 'uvicorn', 'fastapi', 'openai', 'httpx',
    # httpx lazy-imports socksio when SOCKS proxy is used; bundle it explicitly
    # so the standalone binary works on machines with proxy env vars.
    'socksio',
    'httpcore', 'httpcore._backends', 'httpcore._backends.anyio',
    'httpcore._sync', 'httpcore._async',
]
hiddenimports += collect_submodules('axiom_core')

# skills/ + templates/ 目录打 进包 (PyInstaller 解压到 _MEIPASS/)。
# 没这个, 打包后 load_builtin_skills / builtin_templates_root 找不到目录 → 内置 skill/模板全丢。
_proj_root = os.path.abspath('.')
datas = []
if os.path.isdir(os.path.join(_proj_root, 'skills')):
    datas.append(('skills', 'skills'))
if os.path.isdir(os.path.join(_proj_root, 'templates')):
    datas.append(('templates', 'templates'))


a = Analysis(
    ['backend_entry.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='axiom-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
