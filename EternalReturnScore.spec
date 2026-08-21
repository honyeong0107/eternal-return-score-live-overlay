# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


datas = [
    ("score_overlay/static", "score_overlay/static"),
    ("score_overlay/models", "score_overlay/models"),
    ("LICENSE", "."),
    ("THIRD_PARTY_NOTICES.md", "."),
] + collect_data_files("rapidocr_onnxruntime")

a = Analysis(
    ["score_overlay/portable.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("rapidocr_onnxruntime") + ["windows_capture"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "pandas",
        "PIL._avif",
        "tensorflow",
        "tkinter",
        "torch",
    ],
    noarchive=False,
    optimize=1,
)

# Frames come from Windows Graphics Capture or MSS; OpenCV video codecs are unused.
a.binaries = [
    item for item in a.binaries
    if "opencv_videoio_ffmpeg" not in item[0].casefold()
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="EternalReturnScore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
