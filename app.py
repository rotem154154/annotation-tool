import json, random, time, uuid, re
from datetime import datetime
from pathlib import Path
from filelock import FileLock
import re
from flask import (
    Flask, jsonify, make_response, render_template,
    request, send_from_directory
)

# ── Config ──────────────────────────────────────────────────── #
IMAGE_ROOT   = Path("static/images")
DATA_PATH    = Path("votes.jsonl")      # NEW: json lines file
LOCK_PATH    = DATA_PATH.with_suffix(".lock")
COOKIE_ID    = "client_id"
COOKIE_NAME  = "user_name"
IMG_EXT      = ".png"
TOP_N        = 10
NAME_RX = re.compile(r'^[^\s\0-\x1F\x7F]{1,40}$')
USERAGENT_RX = re.compile(r"^Mozilla\/", re.I)

# NEW: root for 4-way variation task (outside static)
VAR_IMAGE_ROOT = Path.home() / "datasets/image_variations"
CLAUDE_FOLDER  = VAR_IMAGE_ROOT / "claude"
if not CLAUDE_FOLDER.exists():
    raise RuntimeError("Folder 'claude' must exist under ~/datasets/image_variations")

# Load all other variation_* folders (exclude claude)
VAR_FOLDERS = [p for p in VAR_IMAGE_ROOT.iterdir()
               if p.is_dir() and p.name.startswith("variation_")]
if len(VAR_FOLDERS) < 1:
    raise RuntimeError("Need at least one 'variation_*' folder in addition to 'claude'")
VAR_FOLDERS.sort(key=lambda p: p.name)

# Build list of image ids (stem) from first variation folder
VAR_IMAGE_IDS: list[str] = sorted(
    f.stem for f in VAR_FOLDERS[0].iterdir()
    if f.is_file() and f.suffix.lower() == IMG_EXT
)

# Where we persist the votes for 4-way task
VAR_DATA_PATH = Path("variation_votes.jsonl")
VAR_LOCK_PATH = VAR_DATA_PATH.with_suffix(".lock")

app = Flask(__name__)

# ── Image folder scan (skip hidden) ─────────────────────────── #
try:
    folders = [p for p in IMAGE_ROOT.iterdir()
               if p.is_dir() and not p.name.startswith(".")]
except FileNotFoundError:
    folders = []  # directory missing

PAIRWISE_ENABLED = len(folders) >= 2
if not PAIRWISE_ENABLED:
    print("[WARN] Pairwise comparison disabled – 'static/images' missing or insufficient folders")
    folders = []
    image_ids = []
else:
    image_ids = sorted(f.stem for f in folders[0].iterdir()
                       if f.is_file() and f.suffix.lower() == IMG_EXT)

def random_pair():
    if not PAIRWISE_ENABLED:
        raise RuntimeError("Pairwise comparison feature not enabled")
    left, right = random.sample(folders, 2)
    img = random.choice(image_ids)
    if random.random() < .5:
        left, right = right, left
    return img, left.name, right.name

# ── In-memory leaderboard built from JSONL (if exists) ──────── #
scores: dict[str, int] = {}
if DATA_PATH.exists():
    with open(DATA_PATH, encoding='utf-8') as f:
        for line in f:
            try:
                rec = json.loads(line)
                name = rec.get("user_name", "anonymous")
                if USERAGENT_RX.match(name):   # ignore corrupted UA names
                    name = "anonymous"
                scores[name] = scores.get(name, 0) + 1
            except Exception:
                continue   # skip bad lines

def set_cookie(resp, key, val):
    resp.set_cookie(key, val,
                    max_age=60*60*24*365*2,
                    httponly=False,
                    samesite="Lax")

# ── Routes ──────────────────────────────────────────────────── #
@app.route("/")
def index():
    """Default page – serve the variations task."""
    return variations_page()

@app.route("/api/next")
def api_next():
    if not PAIRWISE_ENABLED:
        return jsonify({"error":"pairwise_disabled"}), 404
    img, left, right = random_pair()
    token = str(int(time.time()*1000))
    return jsonify({
        "image_id": img,
        "left_url":  f"/static/images/{left}/{img}{IMG_EXT}?v={token}",
        "right_url": f"/static/images/{right}/{img}{IMG_EXT}?v={token}",
        "left_folder": left,
        "right_folder": right
    })

@app.route("/api/name", methods=["GET", "POST"])
def api_name():
    if request.method == "POST":
        name = request.get_json(force=True).get("name", "anonymous").strip()[:40]
        if not NAME_RX.match(name):     # basic sanitisation
            name = "anonymous"
        resp = jsonify({"status": "ok", "name": name})
        set_cookie(resp, COOKIE_NAME, name)
        return resp
    return jsonify({"name": request.cookies.get(COOKIE_NAME, "anonymous")})

@app.route("/api/leaderboard")
def api_leaderboard():
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
    return jsonify(top)

@app.route("/api/vote", methods=["POST"])
def api_vote():
    data = request.get_json(force=True)

    # Compute winner/loser folders
    if data["winner"] in ("both_bad", "both_good"):
        winner_folder = loser_folder = data["winner"]
    elif data["winner_side"] == "left":
        winner_folder, loser_folder = data["left_folder"], data["right_folder"]
    else:
        winner_folder, loser_folder = data["right_folder"], data["left_folder"]

    user_name = request.cookies.get(COOKIE_NAME, "anonymous")
    if USERAGENT_RX.match(user_name):   # extra defence
        user_name = "anonymous"

    record = {
        "timestamp_iso": datetime.utcnow().isoformat(),
        "user_name": user_name,
        "client_id": request.cookies.get(COOKIE_ID, ""),
        "image_id": data["image_id"],
        "winner_folder": winner_folder,
        "loser_folder":  loser_folder,
        "left_folder":   data["left_folder"],
        "right_folder":  data["right_folder"],
        "winner_side":   data["winner_side"],
        "decision_ms":   data["decision_ms"],
        "orientation":   data.get("orientation",""),
        "load_ms":       data.get("load_ms",""),
        "input_method":  data.get("input_method",""),
        "hover_left_ms": data.get("hover_left_ms",""),
        "hover_right_ms":data.get("hover_right_ms",""),
        "resolution":    data.get("resolution",""),
        "remote_addr":   request.headers.get("X-Forwarded-For",
                                             request.remote_addr),
        "user_agent":    request.headers.get("User-Agent",""),
    }

    with FileLock(str(LOCK_PATH)):
        with open(DATA_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    scores[user_name] = scores.get(user_name, 0) + 1
    return {"status": "ok"}

@app.route("/variations")
def variations_page():
    """Render the 4-way variation selection page."""
    resp = make_response(render_template("variations.html"))
    if not request.cookies.get(COOKIE_ID):
        set_cookie(resp, COOKIE_ID, uuid.uuid4().hex)
    if not request.cookies.get(COOKIE_NAME):
        set_cookie(resp, COOKIE_NAME, "anonymous")
    return resp

@app.route("/api/variations/next")
def api_variations_next():
    """Return JSON with a Claude image paired with a random variation image."""
    img = random.choice(VAR_IMAGE_IDS)
    token = str(int(time.time()*1000))

    other_var = random.choice(VAR_FOLDERS)

    # start with Claude left, other right then maybe flip
    left_var_path, right_var_path = CLAUDE_FOLDER, other_var

    if random.random() < 0.5:
        left_var_path, right_var_path = right_var_path, left_var_path

    return jsonify({
        "image_id": img,
        "left_url":  f"/variation_images/{left_var_path.name}/{img}{IMG_EXT}?v={token}",
        "right_url": f"/variation_images/{right_var_path.name}/{img}{IMG_EXT}?v={token}",
        "left_variation":  left_var_path.name,
        "right_variation": right_var_path.name
    })

@app.route("/api/variations/vote", methods=["POST"])
def api_variations_vote():
    """Persist a vote for the best variation (pair-wise)."""
    data = request.get_json(force=True)

    user_name = request.cookies.get(COOKIE_NAME, "anonymous")
    if USERAGENT_RX.match(user_name):
        user_name = "anonymous"

    # Determine winner & loser variations based on choice
    choice = data.get("winner_choice")
    left_var  = data.get("left_variation")
    right_var = data.get("right_variation")
    if choice == "left":
        winner_var, loser_var = left_var, right_var
    elif choice == "right":
        winner_var, loser_var = right_var, left_var
    elif choice in ("both_good", "both_bad"):
        winner_var = loser_var = choice  # symmetrical outcome
    else:  # skip or unknown
        winner_var = loser_var = "skip"

    record = {
        "timestamp_iso": datetime.utcnow().isoformat(),
        "user_name": user_name,
        "client_id": request.cookies.get(COOKIE_ID, ""),
        "image_id": data.get("image_id"),
        "winner_variation": winner_var,
        "loser_variation":  loser_var,
        "left_variation":   left_var,
        "right_variation":  right_var,
        "winner_choice":    choice,
        "decision_ms":      data.get("decision_ms"),
        "orientation":      data.get("orientation", ""),
        "resolution":       data.get("resolution", ""),
        "remote_addr":      request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent":       request.headers.get("User-Agent", ""),
    }

    # Skip votes are not persisted
    if choice != "skip":
        with FileLock(str(VAR_LOCK_PATH)):
            with open(VAR_DATA_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"status": "ok"}

@app.route("/variation_images/<variation>/<path:filename>")
def variation_images(variation: str, filename: str):
    """Serve images from the external variation datasets."""
    # Basic safety check – allow 'claude' plus variation_* folders
    allowed = {p.name for p in VAR_FOLDERS}
    allowed.add(CLAUDE_FOLDER.name)
    if variation not in allowed:
        return "Not found", 404
    dir_path = VAR_IMAGE_ROOT / variation
    return send_from_directory(dir_path, filename)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico')

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)