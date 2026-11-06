#!/usr/bin/env python3
"""Exercise production content QML with isolated theme tokens and fictional PRs."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

root = Path(__file__).resolve().parent
qt6_runner = Path("/usr/lib/qt6/bin/qmltestrunner")
runner = str(qt6_runner) if qt6_runner.is_file() else shutil.which("qmltestrunner")
env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QPA_PLATFORMTHEME="generic")
with tempfile.TemporaryDirectory(prefix="pr-ui-") as temporary:
    imports = Path(temporary)
    (imports / "qs/Ui").mkdir(parents=True)
    (imports / "qs/Commons").symlink_to(root / "stubs/qs/Commons", target_is_directory=True)
    shell = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
    (imports / "qs/Ui/PanelKeyCatcher.qml").symlink_to(shell / "Ui/PanelKeyCatcher.qml")
    (imports / "qs/Ui/qmldir").write_text("module qs.Ui\nPanelKeyCatcher 1.0 PanelKeyCatcher.qml\nPanel 1.0 Panel.qml\nKeyboardPanel 1.0 KeyboardPanel.qml\nTextField 1.0 TextField.qml\nPanelToolTip 1.0 PanelToolTip.qml\n")
    (imports / "qs/Ui/PanelToolTip.qml").write_text("import QtQuick.Controls\nToolTip {}\n")
    (imports / "qs/Ui/TextField.qml").write_text("""import QtQuick.Controls
TextField {
    property real verticalPadding: 4
    topPadding: verticalPadding
    bottomPadding: verticalPadding
}
""")
    (imports / "qs/Ui/Panel.qml").write_text("""import QtQuick
Item {
    property string moduleName
    property bool manageIpc
    property var bar: null
    property bool opened: true
}
""")
    (imports / "qs/Ui/KeyboardPanel.qml").write_text("""import QtQuick
Item {
    property var anchorItem
    property var owner
    property var bar
    property bool open
    property var focusTarget
    property real contentWidth
    property real contentHeight
    function fittedContentWidth(value) { return value }
    function fittedContentHeight(value) { return value }
    width: contentWidth
    height: contentHeight
}
""")
    # Exercise the real QML Process without touching the desktop clipboard.
    copier = imports / "wl-copy"
    copier.write_text("""#!/usr/bin/python3
import sys
expected = ["--type", "text/plain", "--"]
valid = ["https://github.com/example/tools/pull/42", "example/tools#42"]
sys.exit(0 if sys.argv[1:4] == expected and sys.argv[4:] in [[v] for v in valid] else 1)
""")
    copier.chmod(0o700)
    env["PATH"] = str(imports) + os.pathsep + env.get("PATH", "")
    # Qt 6.12 adds its own Color to QtQuick, which shadows the shell's qs.Commons Color in
    # qmltestrunner (Quickshell still resolves the shell's). Test a copy pinned below 6.12.
    mirror = imports / "mirror"
    (mirror / "tests/ui").mkdir(parents=True)
    pinned = re.compile(r"^import QtQuick$", re.M)
    for source in [*root.parent.glob("*.qml"), *(root / "ui").glob("*.qml")]:
        target = mirror / source.relative_to(root.parent)
        target.write_text(pinned.sub("import QtQuick 6.11", source.read_text()))
    tests = subprocess.run([runner, "-input", str(mirror / "tests/ui"), "-import", str(imports)],
                           env=env, capture_output=True, text=True)
    if tests.returncode:
        raise SystemExit(tests.stdout + tests.stderr)
    for name in ("preview.png", "preview-light.png"):
        shutil.copyfile(mirror / name, root.parent / name)
    env["QML_IMPORT_PATH"] = str(imports)
    runtime = imports / "runtime"
    runtime.mkdir(mode=0o700)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env.pop("WAYLAND_DISPLAY", None)
    plugin = imports / "plugin"
    plugin.mkdir()
    for name in ("Panel.qml", "PrContent.qml", "PrRow.qml"):
        (plugin / name).write_text(pinned.sub("import QtQuick 6.11", (root.parent / name).read_text()))
    harness = imports / "shell.qml"
    harness.write_text((root / "clipboard.qml").read_text().replace('import ".." as Plugin', 'import "plugin" as Plugin'))
    # Quickshell modules are static plugins unavailable to qmltestrunner.
    # Run the production Panel with the real Process in an isolated shell.
    quickshell = shutil.which("quickshell")
    for missing in (False, True):
        if missing:
            copier.unlink()
            env["PATH"] = str(imports)
            env["TEST_COPY_MISSING"] = "1"
        subprocess.run([quickshell, "-p", str(harness)], env=env, check=True, timeout=15)
