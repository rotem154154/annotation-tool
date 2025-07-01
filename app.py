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

# NEW: root for multi-model variation task
VAR_IMAGE_ROOT = Path.home() / "datasets/image_variations"

# --------------- Model discovery --------------- #
MODEL_DIRS = [p for p in VAR_IMAGE_ROOT.iterdir() if p.is_dir()]
if len(MODEL_DIRS) < 2:
    raise RuntimeError("Need at least two model folders under ~/datasets/image_variations")

# Map model name -> list[Path] of variation folders
MODEL_VARIATIONS: dict[str, list[Path]] = {}
for model_dir in MODEL_DIRS:
    v_folders = [vf for vf in model_dir.iterdir()
                 if vf.is_dir() and vf.name.startswith("variation_")]
    if not v_folders:
        print(f"[WARN] Model '{model_dir.name}' has no variation_* folders – skipped")
        continue
    v_folders.sort(key=lambda p: p.name)
    MODEL_VARIATIONS[model_dir.name] = v_folders

if len(MODEL_VARIATIONS) < 2:
    raise RuntimeError("Need at least two models with variation_* folders")

# Build global list of image IDs (use first model's first variation folder)
FIRST_MODEL = next(iter(MODEL_VARIATIONS))
FIRST_VAR_FOLDER = MODEL_VARIATIONS[FIRST_MODEL][0]
VAR_IMAGE_IDS: list[str] = sorted(
    f.stem for f in FIRST_VAR_FOLDER.iterdir()
    if f.is_file() and f.suffix.lower() == IMG_EXT
)

VAR_DATA_PATH = Path("variation_votes.jsonl")
VAR_LOCK_PATH = VAR_DATA_PATH.with_suffix(".lock")

# Build per-model image ID sets (across all its variation folders)
MODEL_IMAGE_IDS: dict[str, set[str]] = {}
for model, var_dirs in MODEL_VARIATIONS.items():
    ids: set[str] = set()
    for vf in var_dirs:
        ids.update(f.stem for f in vf.iterdir() if f.is_file() and f.suffix.lower()==IMG_EXT)
    MODEL_IMAGE_IDS[model] = ids

# Global pool – intersection across all models (fallback)
GLOBAL_IMAGE_IDS: list[str] = sorted(set.intersection(*MODEL_IMAGE_IDS.values()))

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
    """Return JSON containing a pair of images – same image ID from two random models and variations."""

    # Pick two distinct models
    model_left, model_right = random.sample(list(MODEL_VARIATIONS.keys()), 2)

    # Determine common image IDs between the two models
    common_ids = MODEL_IMAGE_IDS[model_left].intersection(MODEL_IMAGE_IDS[model_right])
    if not common_ids:
        # fall back to global intersection
        if not GLOBAL_IMAGE_IDS:
            return jsonify({"error":"no_common_images"}), 500
        img_id = random.choice(GLOBAL_IMAGE_IDS)
    else:
        img_id = random.choice(list(common_ids))

    token  = str(int(time.time()*1000))

    # Randomly choose a variation folder for each model
    var_left_path  = random.choice(MODEL_VARIATIONS[model_left])
    var_right_path = random.choice(MODEL_VARIATIONS[model_right])

    # Randomly swap sides
    if random.random() < 0.5:
        model_left, model_right = model_right, model_left
        var_left_path, var_right_path = var_right_path, var_left_path

    return jsonify({
        "image_id": img_id,
        "left_url":  f"/variation_images/{model_left}/{var_left_path.name}/{img_id}{IMG_EXT}?v={token}",
        "right_url": f"/variation_images/{model_right}/{var_right_path.name}/{img_id}{IMG_EXT}?v={token}",
        "left_model":  model_left,
        "left_variation": var_left_path.name,
        "right_model": model_right,
        "right_variation": var_right_path.name
    })

@app.route("/api/variations/vote", methods=["POST"])
def api_variations_vote():
    """Persist a vote in new schema: image_id, left/right model+variation, winner key."""
    data = request.get_json(force=True)

    # Basic fields from client
    left_model       = data.get("left_model")
    left_variation   = data.get("left_variation")
    right_model      = data.get("right_model")
    right_variation  = data.get("right_variation")
    winner           = data.get("winner")  # expected values: left/right/both_good/both_bad/skip

    if winner == "skip":
        return {"status":"skipped"}

    record = {
        "image_id":          data.get("image_id"),
        "left_model":        left_model,
        "left_variation":    left_variation,
        "right_model":       right_model,
        "right_variation":   right_variation,
        "winner":            winner
    }

    with FileLock(str(VAR_LOCK_PATH)):
        with open(VAR_DATA_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"status":"ok"}

@app.route("/variation_images/<model>/<variation>/<path:filename>")
def variation_images(model: str, variation: str, filename: str):
    """Serve image file for given model/variation."""
    # Safety checks
    if model not in MODEL_VARIATIONS:
        return "Model not found", 404
    if variation not in {v.name for v in MODEL_VARIATIONS[model]}:
        return "Variation not found", 404
    dir_path = VAR_IMAGE_ROOT / model / variation
    return send_from_directory(dir_path, filename)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico')

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8000)