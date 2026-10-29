# PyInstaller spec: pyinstaller packaging/kong.spec --noconfirm   (run from the repo root)
import glob
import importlib.util
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = SPECPATH  # noqa: F821 - provided by PyInstaller


def delvewheel_libs(pkg):
    """Wheels repaired by delvewheel keep their DLLs in a sibling '<pkg>.libs' folder, which
    the package adds to the DLL search path at import. collect_all() does not see it."""
    spec = importlib.util.find_spec(pkg)
    if spec is None or not spec.origin:
        return []
    libs = os.path.join(os.path.dirname(os.path.dirname(spec.origin)), pkg + ".libs")
    return [(f, pkg + ".libs") for f in glob.glob(os.path.join(libs, "*.dll"))]

datas, binaries, hiddenimports = [], [], []
for pkg in ("sherpa_onnx", "opencc", "ctranslate2", "faster_whisper", "tokenizers", "av", "sounddevice", "_sounddevice_data", "sv_ttk"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
for pkg in ("opencc", "sherpa_onnx", "ctranslate2", "av", "tokenizers", "numpy"):
    binaries += delvewheel_libs(pkg)
hiddenimports += collect_submodules("kong")
hiddenimports += ["pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32", "PIL.ImageTk"]

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
