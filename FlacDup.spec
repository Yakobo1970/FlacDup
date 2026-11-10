# -*- mode: python ; coding: utf-8 -*-
# Build: <flacdup-env>\python.exe -m PyInstaller --noconfirm FlacDup.spec  ->  dist\FlacDup\
# Pruefen: dist\FlacDup\FlacDup.exe --selftest <Audiodatei> <ausgabe.txt>

import os
import sys

# Conda legt die DLLs der Python-Standardmodule in Libraryin ab; ohne aktivierte Umgebung
# findet PyInstaller sie nicht (ctypes, sqlite3 usw. scheitern dann in der .exe)
_LIBBIN = os.path.join(sys.prefix, "Library", "bin")
CONDA_DLLS = [(os.path.join(_LIBBIN, n), ".") for n in
              ("ffi-8.dll", "sqlite3.dll", "libbz2.dll", "liblzma.dll", "libexpat.dll")
              if os.path.exists(os.path.join(_LIBBIN, n))]

# Grosse, nicht benutzte Qt-/Python-Teile weglassen (WebEngine allein waere ~200 MB)
EXCLUDES = [
    "tkinter", "PIL", "matplotlib", "IPython", "jupyter", "pytest", "pandas",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets", "PySide6.QtNetworkAuth", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
    "PySide6.QtGraphs", "PySide6.QtGraphsWidgets",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=CONDA_DLLS,
    datas=[("assets", "assets")],
    hiddenimports=["selftest"],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)
# Software-OpenGL-Ersatz (20 MB) wird von FlacDup nicht gebraucht
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlacDup",
    console=False,
    icon="assets/icon.ico",
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FlacDup")
