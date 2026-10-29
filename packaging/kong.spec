# PyInstaller spec: pyinstaller packaging/kong.spec --noconfirm   (run from the repo root)
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = SPECPATH  # noqa: F821 - provided by PyInstaller

datas, binaries, hiddenimports = [], [], []
for pkg in ("sherpa_onnx", "opencc", "ctranslate2", "faster_whisper", "tokenizers", "av", "sounddevice", "_sounddevice_data"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("kong")
hiddenimports += ["pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32"]

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[os.path.join(HERE, "..")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # The onnxruntime Python package ships its own onnxruntime.dll, which would clash with the
    # one sherpa-onnx loads; faster-whisper only needs it for VAD, which Kong does not use.
    excludes=["onnxruntime", "torch", "transformers", "matplotlib", "scipy", "pandas", "IPython", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kong",
    console=False,
    icon=os.path.join(HERE, "kong.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Kong", upx=False)
