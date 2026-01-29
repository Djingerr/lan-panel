from flask import Flask, render_template, redirect, request, Response
import time, os, json, subprocess
from werkzeug.utils import secure_filename

# Séparation claire :
# - CODE_DIR : là où se trouve le code (côté laptop exporté)
# - DATA_DIR : là où on stocke les fichiers modifiables (sur le Pi: /opt/lan-panel ; en dev: CODE_DIR par défaut)
CODE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.abspath(os.environ.get("APP_DIR", CODE_DIR))
# Variable manquante : utilisée pour cibler les scripts (wake/shutdown/status)
APP_DIR = os.path.abspath(os.environ.get("APP_DIR", CODE_DIR))

MACHINES_FILE = os.path.join(DATA_DIR, "machines.json")

HISTORY_DIR = os.path.join(DATA_DIR, "history")
HISTORY_RETENTION = 7 * 24 * 3600  # 7 jours de rétention des échantillons
HISTORY_WINDOW = 24 * 3600         # fenêtre affichée sur le graphe (24h)

CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

# Les fonds d'écran doivent être servis par Flask => ils restent dans le dossier static du code
BACKGROUND_DIR = os.path.join(CODE_DIR, "static", "backgrounds")
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp"}

static_path = os.path.join(CODE_DIR, "static")
app = Flask(__name__, static_folder=static_path)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB pour les uploads


# ---------- Helpers ----------
def ensure_dirs():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    os.makedirs(BACKGROUND_DIR, exist_ok=True)

def load_machines():
    with open(MACHINES_FILE) as f:
        return json.load(f)

def save_machines(data):
    with open(MACHINES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_config():
    # config par défaut
    cfg = {
        "bg_mode": "image",            # "image" ou "color"
        "bg_color": "#0f172a",
        "bg_image": "/static/macronlunettes.jpeg",
        "bg_history": ["#0f172a"]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                disk = json.load(f)
            cfg.update(disk if isinstance(disk, dict) else {})
        except Exception:
            pass
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def history_file(name: str):
    safe = name.replace(" ", "_")
    return f"{HISTORY_DIR}/{safe}.json"

def log_status(name, status):
    ensure_dirs()
    path = history_file(name)
    now = int(time.time())

    data = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            data = []

    data.append({"ts": now, "status": status})

    cutoff = now - HISTORY_RETENTION
    data = [d for d in data if isinstance(d, dict) and d.get("ts", 0) >= cutoff]

    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        # ne jamais faire planter le site pour un souci de fichier
        pass

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


# ---------- Routes ----------
@app.route("/")
def index():
    ensure_dirs()
    cfg = load_config()

    machines = load_machines()
    for m in machines:
        status = run_status(m.get("ip", ""))
        m["status"] = status
        log_status(m.get("name", "UNKNOWN"), status)

    return render_template("index.html", machines=machines, cfg=cfg)

@app.route("/history/<name>.svg")
def history_svg(name):
    ensure_dirs()
    # name arrive avec underscores depuis le template
    path = f"{HISTORY_DIR}/{name}.json"
    if not os.path.exists(path):
        return Response("", mimetype="image/svg+xml")

    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = []

    # on filtre les points en dehors de la fenêtre visible
    now = int(time.time())
    data = [d for d in data if isinstance(d, dict) and d.get("ts") and d.get("ts") >= now - HISTORY_WINDOW]

    if not data:
        return Response("", mimetype="image/svg+xml")

    # On place les points proportionnellement au temps (pas juste à l'ordre)
    data = sorted(data, key=lambda d: d.get("ts", 0))
    start = max(now - HISTORY_WINDOW, data[0]["ts"])
    end = data[-1]["ts"]
    span = max(end - start, 1)

    width, height = 240, 40
    points = []
    for d in data:
        st = d.get("status")
        y = 10 if st == "ON" else 30
        x = int(((d["ts"] - start) / span) * (width - 2)) + 1
        points.append(f"{x},{y}")

    svg = f"""<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
  <polyline fill="none" stroke="#22c55e" stroke-width="2" points="{' '.join(points)}" />
</svg>"""
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
                    [f"{APP_DIR}/scripts/shutdown.sh", m.get("ssh_user", ""), m.get("ip", "")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
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


# --------- Background settings UI ----------
def allowed_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in ALLOWED_EXT

@app.route("/settings", methods=["GET", "POST"])
def settings():
    ensure_dirs()
    cfg = load_config()

    if request.method == "POST":
        mode = request.form.get("bg_mode", "color")
        # hex libre OU valeur de palette
        color_txt = request.form.get("bg_color", "").strip()
        color_pick = request.form.get("bg_color_picker", "").strip()
        color = color_txt or color_pick or "#0f172a"

        # normalisation simple : doit commencer par # et être long de 7 caractères (#RRGGBB)
        if not (color.startswith("#") and len(color) == 7):
            color = "#0f172a"

        # Upload image (optional)
        f = request.files.get("bg_file")
        if f and f.filename:
            if allowed_file(f.filename):
                fname = secure_filename(f.filename)
                # pour éviter écrasements / caractères
                ts = int(time.time())
                base, ext = os.path.splitext(fname)
                saved = f"{base}_{ts}{ext.lower()}"
                dest = os.path.join(BACKGROUND_DIR, saved)
                f.save(dest)
                cfg["bg_image"] = f"/static/backgrounds/{saved}"
                mode = "image"  # si on upload, on bascule image
            else:
                # extension refusée, on ignore l'upload
                pass

        cfg["bg_mode"] = mode if mode in ("image", "color") else "color"
        cfg["bg_color"] = color

        # Historique des 5 dernières couleurs utilisées (dédup en conservant l'ordre)
        history = [color] + [c for c in cfg.get("bg_history", []) if c != color]
        cfg["bg_history"] = history[:5]
        save_config(cfg)
        return redirect("/")

    return render_template("settings.html", cfg=cfg)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
