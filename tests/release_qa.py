import ctypes
import pathlib
import platform
import subprocess
import time

import pyautogui as gui
from PIL import ImageChops, ImageStat
from pymavlink import mavutil

windows = platform.system() == "Windows"
bundle = pathlib.Path("bundle").resolve()
exe = bundle / ("bluesim.exe" if windows else "bluesim.x86_64")
evidence = pathlib.Path("evidence").resolve()
evidence.mkdir(exist_ok=True)
log = evidence / "godot.log"
app = subprocess.Popen([str(exe), "--position", "20,20", "--resolution", "960x540", "--log-file", str(log)], cwd=bundle)

def wait_for(test, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if result := test():
            return result
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {test.__name__}")

def client():
    if windows:
        from ctypes import wintypes
        user = ctypes.windll.user32
        hwnd = user.FindWindowW(None, "BlueSim")
        if not hwnd:
            return None
        user.SetForegroundWindow(hwnd)
        rect, point = wintypes.RECT(), wintypes.POINT()
        user.GetClientRect(hwnd, ctypes.byref(rect))
        user.ClientToScreen(hwnd, ctypes.byref(point))
        return point.x, point.y, rect.right, rect.bottom
    match = subprocess.run(["xdotool", "search", "--onlyvisible", "--name", "^BlueSim$"], capture_output=True, text=True)
    if match.returncode:
        return None
    wid = match.stdout.split()[0]
    output = subprocess.check_output(["xdotool", "getwindowgeometry", "--shell", wid], text=True)
    geometry = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return tuple(int(geometry[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))

def sitl_listening():
    if windows:
        return subprocess.run(["powershell", "-NoProfile", "-Command",
            "if (!(Get-NetTCPConnection -State Listen -LocalPort 5760 -ErrorAction SilentlyContinue | Where-Object {(Get-Process -Id $_.OwningProcess).ProcessName -eq 'ardusub'})) {exit 1}"],
            capture_output=True).returncode == 0
    return "ardusub" in subprocess.check_output(["ss", "-ltnp", "sport = :5760"], text=True)

try:
    x, y, w, h = wait_for(client, 50)
    assert w >= 900 and h >= 500, (x, y, w, h)
    wait_for(sitl_listening, 150)
    print("Bundled launcher started ArduSub and owns TCP 5760", flush=True)
    def snap(name):
        shot = gui.screenshot(region=(x, y, w, h)).convert("RGB")
        shot.save(evidence / f"{name}.png")
        return shot
    menu = snap("menu")
    # Pool button is centered about (290, 223) in the default layout.
    gui.click(x + 290, y + 223)
    time.sleep(8)
    pool = snap("pool")
    gui.keyDown("up")
    try:
        time.sleep(2)
    finally:
        gui.keyUp("up")
    driven = snap("driven")
    assert ImageStat.Stat(ImageChops.difference(menu, pool)).mean[0] > 8, "Pool did not open"
    # Exclude animated water, overlays and sonar. Compare the tiled pool geometry.
    box = (w // 3, h * 6 // 10, w * 2 // 3, h * 9 // 10)
    change = ImageStat.Stat(ImageChops.difference(pool.crop(box), driven.crop(box))).mean[0]
    assert change > 3, f"Forward input did not change pool geometry: {change}"
    print(f"Pool opened and forward input moved the camera (pixel delta {change:.2f})", flush=True)
    link = mavutil.mavlink_connection("tcp:127.0.0.1:5760", source_system=255)
    heartbeat = link.wait_heartbeat(timeout=25)
    assert heartbeat is not None and heartbeat.type == mavutil.mavlink.MAV_TYPE_SUBMARINE
    print(f"ArduSub MAVLink heartbeat: {heartbeat}", flush=True)
    link.close()
finally:
    if windows:
        subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"], capture_output=True)
    else:
        app.terminate()
    try:
        app.wait(timeout=10)
    except subprocess.TimeoutExpired:
        app.kill()
