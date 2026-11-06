#!/usr/bin/env python3
"""Drive the real panel in an OmaBox desktop: moving and excluding by menu, drag and keys.

The plugin runs in Omarchy's own shell inside a throwaway box. Only GitHub is fictional
(tests/omabox/python3); group changes go through the real helper into the box HOME, and
each step is checked there, not just on screen. The panel reports where its named controls
are (OMACONTRIB_REPORT_LAYOUT), so clicks and drags aim at controls, not at pixels.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
PLUGIN = "community.omacontrib"
URL = "https://github.com/{}/pull/{}".format
OMARCHY, QUICKSHELL = URL("omacom/omarchy", 13961), URL("quickshell-mirror/quickshell", 842)
THEME_PACK, CLOSED = URL("river-tools/theme-pack", 97), URL("omacom/omarchy", 13870)


def omabox(*args, check=True):
    proc = subprocess.run(["omabox", "-b", box, *args], capture_output=True, text=True)
    if check and proc.returncode:
        raise AssertionError(f"omabox {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def wait_until(what, probe, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for " + what)
        time.sleep(0.2)


def layout():
    lines = omabox("log", "shell", "--grep", "omacontrib-layout", check=False).splitlines()
    match = re.search(r"omacontrib-layout (.*)", lines[-1]) if lines else None
    controls = {}
    for entry in match.group(1).split() if match else []:
        name, _, box_ = entry.rpartition("=")
        controls.setdefault(name, tuple(int(value) for value in box_.split(",")))
    return controls


def centre(name):
    x, y, width, height = wait_until(name + " on screen", lambda: layout().get(name))
    return str(x + width // 2), str(y + height // 2)


def config():
    try:
        return json.loads((home / ".config/omacontrib/config.json").read_text())
    except FileNotFoundError:
        return {"groups": []}


def group(name):
    return next((group for group in config()["groups"] if group["name"] == name), None)


def in_group(name, key, value):
    return lambda: (group(name) or {}).get(key, []).count(value) == 1


def step(title):
    print("·", title, flush=True)


box = subprocess.run(["omabox", "up", "--new", "--plugin", str(repo), "--env", "OMACONTRIB_REPORT_LAYOUT=1"],
                     capture_output=True, text=True, check=True).stdout.split()[-1]
try:
    home = Path(omabox("path").strip()) / "home"
    (home / ".local/bin").mkdir(parents=True, exist_ok=True)
    (home / ".local/bin/python3").symlink_to(repo / "tests/omabox/python3")
    omabox("restart-shell")
    omabox("run", "--", "omarchy-shell", "shell", "summon", PLUGIN)

    step("move menu: an item to Excluded")
    omabox("click", *centre("moveAction@13961"))
    omabox("click", *centre("destination-excluded"))
    wait_until("the menu move to be saved", in_group("Excluded", "items", OMARCHY))
    wait_until("the item to leave Active", lambda: "moveAction@13961" not in layout())

    step("drag: an item onto the Excluded tab")
    omabox("drag", "--steps", "20", *centre("moveAction@842"), *centre("groupTab-excluded"))
    wait_until("the dropped item to be saved", in_group("Excluded", "items", QUICKSHELL))
    wait_until("the item to leave Active", lambda: "moveAction@842" not in layout())

    step("x: the selected row to Excluded")
    omabox("keys", "x")
    wait_until("the item rule", in_group("Excluded", "items", URL("waycrate/panel-kit", 214)))

    step("drag back: from Excluded onto the Active tab")
    omabox("keys", "2")
    omabox("drag", "--steps", "20", *centre("moveAction@13961"), *centre("groupTab-inbox"))
    wait_until("the item to return", in_group("Active", "items", OMARCHY))
    assert OMARCHY not in group("Excluded")["items"], config()

    step("drop on +: name a new group for the dropped item")
    omabox("keys", "1")
    omabox("drag", "--steps", "20", *centre("moveAction@97"), *centre("addGroupTab"))
    centre("groupName")
    omabox("keys", "--wait", "-t", "Later", "Return")
    wait_until("the new group with the item", in_group("Later", "items", THEME_PACK))
    wait_until("the new group's tab", lambda: any(name.startswith("groupTab-") and name != "groupTab-inbox"
                                                  and name != "groupTab-excluded" for name in layout()))
    step("closed row: summary placeholder, then dismiss")
    omabox("keys", "1")
    wait_until("the closed row's actions", lambda: "dismissAction@13870" in layout())
    omabox("click", *centre("dismissAction@13870"))
    wait_until("the dismissal to be saved", lambda: CLOSED in config().get("dismissed", []))
    wait_until("the closed row to go", lambda: "moveAction@13870" not in layout())
    print("box UI checks passed")
except BaseException:
    print("failed; screen at", omabox("shot", check=False).strip(), file=sys.stderr)
    raise
finally:
    omabox("down", check=False)
