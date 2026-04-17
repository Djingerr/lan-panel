from flask import Flask, render_template, redirect, request, Response
import time
import os
import json
import subprocess
from datetime import datetime
from werkzeug.utils import secure_filename

# Séparation claire :
# - CODE_DIR : là où se trouve le code
# - DATA_DIR : là où on stocke les fichiers modifiables (sur le Pi : /opt/lan-panel)
CODE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.abspath(os.environ.get("APP_DIR", CODE_DIR))
APP_DIR = os.path.abspath(os.environ.get("APP_DIR", CODE_DIR))

MACHINES_FILE = os.path.join(DATA_DIR, "machines.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

HISTORY_RETENTION = 7 * 24 * 3600   # 7 jours conservés
HISTORY_WINDOW = 7 * 24 * 3600      # 7 jours affichés sur le graphe
HISTORY_KEEPALIVE = 30 * 60         # on ajoute un point au moins toutes les 30 min

BACKGROUND_DIR = os.path.join(CODE_DIR, "static", "backgrounds")
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}

static_path = os.path.join(CODE_DIR, "static")
app = Flask(__name__, static_folder=static_path)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB pour les uploads


def ensure_dirs():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(BACKGROUND_DIR, exist_ok=True)


def load_machines():
    if not os.path.exists(MACHINES_FILE):
        return []
    with open(MACHINES_FILE) as f:
        return json.load(f)


def save_machines(data):
    with open(MACHINES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_config():
    cfg = {
        "bg_mode": "image",
        "bg_color": "#0f172a",
        "bg_image": "/static/macronlunettes.jpeg",
        "bg_history": ["#0f172a"]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                cfg.update(disk)
        except Exception:
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def history_file(name: str):
    safe = name.replace(" ", "_")
    return os.path.join(HISTORY_DIR, f"{safe}.json")


def load_history(name: str):
    ensure_dirs()
    path = history_file(name)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    except Exception:
        pass
    return []


def save_history(name: str, data):
    path = history_file(name)
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def log_status(name, status):
    now = int(time.time())
    data = load_history(name)
    cutoff = now - HISTORY_RETENTION
    data = [d for d in data if d.get("ts", 0) >= cutoff]

    should_append = False
    if not data:
        should_append = True
    else:
        last = data[-1]
        last_status = last.get("status")
        last_ts = int(last.get("ts", 0))
        if last_status != status:
            should_append = True
        elif now - last_ts >= HISTORY_KEEPALIVE:
            should_append = True

    if should_append:
        data.append({"ts": now, "status": status})

    save_history(name, data)
    return data


def run_status(ip: str) -> str:
    try:
        res = subprocess.run(
            [f"{APP_DIR}/scripts/status.sh", ip],
            capture_output=True,
            text=True,
            timeout=2
        )
        out = (res.stdout or "").strip()
        return out if out in ("ON", "OFF") else "OFF"
    except Exception:
        return "OFF"


def fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")


def get_last_transition(data):
    if not data:
        return "Aucun historique"
    if len(data) == 1:
        return f"État connu depuis {fmt_dt(data[0].get('ts', 0))}"

    for idx in range(len(data) - 1, 0, -1):
        if data[idx].get("status") != data[idx - 1].get("status"):
            state = data[idx].get("status", "OFF")
            return f"{state} depuis {fmt_dt(data[idx].get('ts', 0))}"

    return f"État inchangé depuis {fmt_dt(data[0].get('ts', 0))}"


def list_backgrounds():
    ensure_dirs()
    bgs = []
    try:
        for name in sorted(os.listdir(BACKGROUND_DIR)):
            path = os.path.join(BACKGROUND_DIR, name)
            if os.path.isfile(path) and allowed_file(name):
                bgs.append(f"/static/backgrounds/{name}")
    except Exception:
        pass
    return bgs


def allowed_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXT


@app.route("/")
def index():
    ensure_dirs()
    cfg = load_config()
    machines = load_machines()

    online_count = 0
    for m in machines:
        status = run_status(m.get("ip", ""))
        m["status"] = status
        history = log_status(m.get("name", "UNKNOWN"), status)
        m["last_transition"] = get_last_transition(history)
        m["history_points"] = len(history)
        if status == "ON":
            online_count += 1

    summary = {
        "total": len(machines),
        "online": online_count,
        "offline": max(len(machines) - online_count, 0),
        "updated_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    }

    return render_template("index.html", machines=machines, cfg=cfg, summary=summary)


@app.route("/history/<name>.svg")
def history_svg(name):
    now = int(time.time())
    start = now - HISTORY_WINDOW
    width = 420
    height = 120
    left_pad = 42
    right_pad = 12
    top_pad = 14
    bottom_pad = 28
    inner_w = width - left_pad - right_pad
    y_on = 32
    y_off = 84

    path = os.path.join(HISTORY_DIR, f"{name}.json")
    if not os.path.exists(path):
        empty_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<rect width="100%" height="100%" rx="12" fill="rgba(2,6,23,0.68)" />
<text x="{left_pad}" y="64" fill="#94a3b8" font-size="12">Pas encore d'historique</text>
</svg>'''
        return Response(empty_svg, mimetype="image/svg+xml")

    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        raw = []

    data = [d for d in raw if isinstance(d, dict) and d.get("ts") and d.get("status") in ("ON", "OFF")]
    data = [d for d in data if d["ts"] >= start]
    data.sort(key=lambda d: d["ts"])

    if not data:
        empty_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<rect width="100%" height="100%" rx="12" fill="rgba(2,6,23,0.68)" />
<text x="{left_pad}" y="64" fill="#94a3b8" font-size="12">Pas d'activité sur 7 jours</text>
</svg>'''
        return Response(empty_svg, mimetype="image/svg+xml")

    if data[0]["ts"] > start:
        data.insert(0, {"ts": start, "status": data[0]["status"]})
    if data[-1]["ts"] < now:
        data.append({"ts": now, "status": data[-1]["status"]})

    def x_pos(ts):
        return left_pad + int(((ts - start) / max(HISTORY_WINDOW, 1)) * inner_w)

    def y_pos(status):
        return y_on if status == "ON" else y_off

    points = []
    prev = data[0]
    points.append(f"{x_pos(prev['ts'])},{y_pos(prev['status'])}")
    for cur in data[1:]:
        points.append(f"{x_pos(cur['ts'])},{y_pos(prev['status'])}")
        points.append(f"{x_pos(cur['ts'])},{y_pos(cur['status'])}")
        prev = cur

    tick_positions = [0, 0.25, 0.5, 0.75, 1]
    tick_labels = []
    tick_lines = []
    for ratio in tick_positions:
        tick_ts = start + int(HISTORY_WINDOW * ratio)
        tick_x = left_pad + int(inner_w * ratio)
        tick_lines.append(f'<line x1="{tick_x}" y1="{top_pad}" x2="{tick_x}" y2="{height - bottom_pad}" stroke="rgba(148,163,184,0.18)" stroke-width="1" />')
        tick_labels.append(
            f'<text x="{tick_x}" y="{height - 8}" text-anchor="middle" fill="#94a3b8" font-size="10">{datetime.fromtimestamp(tick_ts).strftime("%d/%m %H:%M")}</text>'
        )

    transition_labels = []
    for i in range(1, len(data) - 1):
        if data[i]["status"] != data[i - 1]["status"]:
            tx = x_pos(data[i]["ts"])
            ty = y_pos(data[i]["status"])
            transition_labels.append(f'<circle cx="{tx}" cy="{ty}" r="2.6" fill="#f8fafc" />')
            transition_labels.append(
                f'<text x="{tx + 4}" y="{ty - 6}" fill="#cbd5e1" font-size="9">{datetime.fromtimestamp(data[i]["ts"]).strftime("%H:%M")}</text>'
            )
            if len(transition_labels) >= 8:
                break

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" rx="12" fill="rgba(2,6,23,0.68)" />
  <line x1="{left_pad}" y1="{y_on}" x2="{width - right_pad}" y2="{y_on}" stroke="rgba(34,197,94,0.18)" stroke-width="1" />
  <line x1="{left_pad}" y1="{y_off}" x2="{width - right_pad}" y2="{y_off}" stroke="rgba(239,68,68,0.18)" stroke-width="1" />
  {''.join(tick_lines)}
  <text x="10" y="{y_on + 4}" fill="#22c55e" font-size="11">ON</text>
  <text x="8" y="{y_off + 4}" fill="#ef4444" font-size="11">OFF</text>
  <polyline fill="none" stroke="#38bdf8" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" points="{' '.join(points)}" />
  {''.join(transition_labels)}
  {''.join(tick_labels)}
</svg>'''

    return Response(svg, mimetype="image/svg+xml")


@app.route("/wake/<name>")
def wake(name):
    machines = load_machines()
    for m in machines:
        if m.get("name") == name:
            try:
                subprocess.Popen([f"{APP_DIR}/scripts/wake.sh", m.get("mac", "")])
            except Exception:
                pass
    return redirect("/")


@app.route("/shutdown/<name>")
def shutdown(name):
    machines = load_machines()
    for m in machines:
        if m.get("name") == name:
            try:
                subprocess.Popen(
                    [
                        f"{APP_DIR}/scripts/shutdown.sh",
                        m.get("ssh_user", ""),
                        m.get("ip", ""),
                        m.get("os", "linux")
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"Shutdown error for {name}: {e}")
    return redirect("/")


@app.route("/add", methods=["POST"])
def add():
    machines = load_machines()
    machines.append({
        "name": request.form.get("name", "").strip(),
        "ip": request.form.get("ip", "").strip(),
        "mac": request.form.get("mac", "").strip(),
        "ssh_user": request.form.get("ssh_user", "").strip()
    })
    save_machines(machines)
    return redirect("/")


@app.route("/delete/<name>")
def delete(name):
    machines = load_machines()
    machines = [m for m in machines if m.get("name") != name]
    save_machines(machines)
    return redirect("/")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    ensure_dirs()
    cfg = load_config()
    backgrounds = list_backgrounds()

    if request.method == "POST":
        mode = request.form.get("bg_mode", "color")
        color_txt = request.form.get("bg_color", "").strip()
        color_pick = request.form.get("bg_color_picker", "").strip()
        color = color_txt or color_pick or "#0f172a"
        selected_bg = request.form.get("bg_choice", "")

        if not (color.startswith("#") and len(color) == 7):
            color = "#0f172a"

        if selected_bg and selected_bg in backgrounds:
            cfg["bg_image"] = selected_bg
            mode = "image"

        f = request.files.get("bg_file")
        if f and f.filename and allowed_file(f.filename):
            fname = secure_filename(f.filename)
            ts = int(time.time())
            base, ext = os.path.splitext(fname)
            saved = f"{base}_{ts}{ext.lower()}"
            dest = os.path.join(BACKGROUND_DIR, saved)
            f.save(dest)
            cfg["bg_image"] = f"/static/backgrounds/{saved}"
            mode = "image"

        cfg["bg_mode"] = mode if mode in ("image", "color") else "color"
        cfg["bg_color"] = color
        history = [color] + [c for c in cfg.get("bg_history", []) if c != color]
        cfg["bg_history"] = history[:5]
        save_config(cfg)
        return redirect("/")

    return render_template("settings.html", cfg=cfg, err=request.args.get("err"), backgrounds=backgrounds)


@app.errorhandler(413)
def too_large(e):
    return redirect("/settings?err=too_large")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
