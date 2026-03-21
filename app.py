import os
import sys
import re
import tempfile
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
from flask import ( Flask, render_template, request, redirect, url_for, session, Response)
from werkzeug.utils import secure_filename

# Shared IO (unchanged)
from a_LevelA_IO import load_chat_from_file, run_level_a_pipeline, get_substantial_speakers
from supabase_store import SupabaseStore, guess_mime_type
# -----------------------------
# App setup
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent

# Vercel filesystem is read-only except /tmp.
# Keep runtime writes in a tmp namespace so the app can boot in serverless.
RUNTIME_BASE = Path(tempfile.gettempdir()) / "wys_runtime"
UPLOAD_DIR = RUNTIME_BASE / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# results/ is created by a_LevelA_IO under its own BASE_DIR/results/<safe_user>
# but we keep local path references consistent here too.
RESULTS_DIR = RUNTIME_BASE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"txt"}
MAX_FILE_MB = 5

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024
store = SupabaseStore()

    
    
# -----------------------------
# Canonicalization (ONE function)
# -----------------------------
def canonicalize_handle(s: str) -> str:
    """
    Lowercase + remove everything except a-z and 0-9.
    This matches your spec: strip spaces, brackets, dots, dashes, underscores, emojis, etc.
    """
    s = (s or "").strip().lower()
    # remove all non-alphanumeric
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def resolve_user_handle_from_file(chat_path: str, user_input: str):
    """
    Returns (safe_user, resolved_user_handle, messages, speaker_counts_dict)
    - safe_user is canonicalized user_input
    - resolved_user_handle is the *exact* speaker label from the file (needed by IO)
    """
    safe_user = canonicalize_handle(user_input)
    if not safe_user:
        return None, None, None, None

    messages = load_chat_from_file(chat_path)
    # speaker -> count
    speakers = {}
    for m in messages:
        sp = m.get("speaker", "")
        if not sp:
            continue
        speakers[sp] = speakers.get(sp, 0) + 1

    # canonical speaker map: canonical -> original speaker label
    canonical_map = {}
    for sp in speakers.keys():
        key = canonicalize_handle(sp)
        if key and key not in canonical_map:
            canonical_map[key] = sp

    resolved = canonical_map.get(safe_user)
    return safe_user, resolved, messages, speakers

def anonymize_and_rank_speakers( speaker_counts: dict, resolved_user_handle: str, top_n: int = 10):
    
    total_msgs = sum(speaker_counts.values())

    # 1. Sort once, globally
    ranked = sorted(
        speaker_counts.items(),
        key=lambda x: x[1],
        reverse=True
    )

    results = []
    anon_counter = 1

    for speaker, count in ranked[:top_n]:
        if speaker == resolved_user_handle:
            label = "You"
            is_user = True
        else:
            label = f"Usr {anon_counter}"
            anon_counter += 1
            is_user = False

        percent = round((count / total_msgs) * 100, 1)

        results.append({
            "label": label,
            "count": count,
            "percent": percent,
            "is_user": is_user,
        })

    return {
        "total_messages": total_msgs,
        "ranked": results,
        "chart_labels": [r["label"] for r in results],
        "chart_values": [r["count"] for r in results],
        "chart_percentages": [r["percent"] for r in results],
    }


def write_temp_chat(run_id: str, data: bytes) -> str:
    tmp_path = UPLOAD_DIR / f"{run_id}.txt"
    with open(tmp_path, "wb") as f:
        f.write(data)
    return str(tmp_path)


def upload_level_a_artifacts(run_id: str, out_dir: Path) -> None:
    if not store.ready:
        return
    for child in out_dir.iterdir():
        if not child.is_file():
            continue
        path = f"{run_id}/{child.name}"
        with open(child, "rb") as f:
            store.upload_bytes(
                bucket=store.results_bucket,
                path=path,
                data=f.read(),
                content_type=guess_mime_type(child.name),
            )


def load_level_a_metrics(run_id: str):
    if not store.ready:
        return None
    try:
        raw = store.download_bytes(store.results_bucket, f"{run_id}/metrics_levelA.json")
    except Exception:
        return None
    try:
        if isinstance(raw, bytes):
            return json.loads(raw.decode("utf-8"))
        return json.loads(raw)
    except Exception:
        return None

# -----------------------------
# Routes
# -----------------------------
@app.route("/WhatYouSay/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/", methods=["GET"])
def root():
    return redirect(url_for("index"))


@app.route("/WhatYouSay/upload", methods=["POST"])
def upload():
    print("CONTENT TYPE:", request.content_type)
    print("FORM KEYS:", list(request.form.keys()))
    print("FILES KEYS:", list(request.files.keys()))
    
    user_handle = request.form.get("user_handle", "").strip()
    platform = request.form.get("platform", "").strip()  # optional, if you still collect it

    file = request.files.get("text")
    if not file or file.filename == "":
        return render_template("error.html", message="No file selected.")

    if not allowed_file(file.filename):
        return render_template("error.html", message="Please upload a .txt WhatsApp export.")

    if not store.ready:
        return render_template("error.html", message="Server storage is not configured. Please try again later.")

    filename = secure_filename(file.filename)
    run_id = uuid.uuid4().hex[:12]
    raw_bytes = file.read()
    if not raw_bytes:
        return render_template("error.html", message="Uploaded file is empty.")

    upload_path = f"{run_id}.txt"
    try:
        store.upload_bytes(
            bucket=store.upload_bucket,
            path=upload_path,
            data=raw_bytes,
            content_type=guess_mime_type(filename),
        )
    except Exception as e:
        return render_template("error.html", message=f"Upload storage error: {e}")

    save_path = write_temp_chat(run_id, raw_bytes)

    # Resolve user -> match against canonicalized speakers from parsed file
    safe_user, resolved_user_handle, messages, speaker_counts = resolve_user_handle_from_file(save_path, user_handle)
    if not safe_user:
        # cleanup upload
        try:
            store.remove_many(store.upload_bucket, [upload_path])
        except Exception:
            pass
        return render_template("error.html", message="Please enter a user handle.")

    if resolved_user_handle is None:
        # cleanup upload
        try:
            store.remove_many(store.upload_bucket, [upload_path])
        except Exception:
            pass
        return render_template(
            "error.html",
            message=f"No match found for '{user_handle}'. "
                    f"Tip: type the name/number as it appears in WhatsApp (any casing/punctuation is fine)."
        )

    speaker_data = anonymize_and_rank_speakers(speaker_counts, resolved_user_handle, top_n=10)
    #print ("Safe User: ", safe_user, "Resolved usr:", resolved_user_handle, "User Handl:", user_handle)
    # Basic stats for confirmation page
    
    #substantial = get_substantial_speakers(messages)  # uses MIN_CONTRIBUTION_PCT inside IO
    #top_speakers = sorted(speaker_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    speaker_counts = Counter(m["speaker"] for m in messages if m.get("speaker"))
    total_messages = sum(speaker_counts.values())
    user_messages = speaker_counts.get(resolved_user_handle, 0)
    char_count = sum(len((m.get("text") or "")) for m in messages)
    line_count = len(messages)  # better than splitlines for structured messages

    #print("RESOLVED_USER_HANDLE:", resolved_user_handle)
    #print("USER MSG COUNT:", speaker_counts.get(resolved_user_handle, 0))
    #print("TOP 5:", speaker_counts.most_common(5))

    warnings = []
# Individual analysis threshold
    if user_messages < 250:
        warnings.append(
            f"Individual-level analysis may be unreliable: "
            f"only {user_messages} messages found (minimum recommended: 250)."
        )

# Group comparative threshold
    user_share = (user_messages / total_messages) * 100 if total_messages > 0 else 0

    MIN_SHARE_PERCENT = 5  # adjust later if needed

    if user_share < MIN_SHARE_PERCENT:
        warnings.append(
            f"Group comparison may be unreliable: "
            f"your contribution is {user_share:.1f}% of the corpus "
            f"(minimum recommended: {MIN_SHARE_PERCENT}%)."
        )

    # Persist for next steps
    session.clear()
    session["parsed_data"] = True
    session["run_id"] = run_id
    session["upload_path"] = upload_path
    session["chat_path"] = save_path
    session["user_handle"] = resolved_user_handle      # EXACT speaker label (IO needs this)
    session["safe_user"] = safe_user                   # canonical safe id (folders/urls)
    session["platform"] = platform
    session["paid"] = False

    store.safe_upsert_run({
        "run_id": run_id,
        "safe_user": safe_user,
        "status": "uploaded",
        "upload_path": upload_path,
    })

    print("TOP_SPEAKERS:", speaker_counts.most_common(15))

    # Confirmation page
    return render_template(
        "confirmation.html",
        user_handle=user_handle,
        safe_user=safe_user,
        platform=platform,
        char_count=char_count,
        line_count=line_count,
        total_messages=total_messages,
        user_messages=user_messages,
        user_share=user_share,
        warnings=warnings,
        speaker_data=speaker_data
    )

# -----------------------------
# LEVEL_A call
# -----------------------------
@app.route("/WhatYouSay/level-a", methods=["GET"])
def level_a():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))

    run_id = session.get("run_id")
    upload_path = session.get("upload_path")
    user_handle = session.get("user_handle")  # exact speaker label
    safe_user = session.get("safe_user")

    if not run_id or not upload_path or not user_handle or not safe_user:
        return redirect(url_for("index"))

    metrics = load_level_a_metrics(run_id)
    if metrics is None:
        try:
            store.safe_update_run(run_id, {"status": "analyzing"})

            raw = store.download_bytes(store.upload_bucket, upload_path)
            chat_path = write_temp_chat(run_id, raw)

            out_dir = RESULTS_DIR / run_id
            out_dir.mkdir(parents=True, exist_ok=True)

            metrics = run_level_a_pipeline(
                chat_path=chat_path,
                user_handle=user_handle,
                safe_user=safe_user,
                out_dir=str(out_dir)
            )

            upload_level_a_artifacts(run_id, out_dir)
            store.safe_update_run(
                run_id,
                {
                    "status": "done",
                    "metrics_path": f"{run_id}/metrics_levelA.json",
                },
            )
            metrics = load_level_a_metrics(run_id) or metrics
        except Exception as e:
            store.safe_update_run(run_id, {"status": "error", "error_message": str(e)})
            return render_template("error.html", message=str(e))

    return render_template(
        "level_a.html",
        metrics=metrics,
        user_handle=user_handle,
        safe_user=safe_user,
        paid=session.get("paid", False)
    )


@app.route("/WhatYouSay/results/<safe_user>/<filename>", methods=["GET"])
def serve_results(safe_user, filename):
    run_id = session.get("run_id")
    if not run_id:
        return render_template("error.html", message="Session expired. Please re-upload your chat.")

    object_path = f"{run_id}/{filename}"
    try:
        data = store.download_bytes(store.results_bucket, object_path)
    except Exception as e:
        return render_template("error.html", message=f"Unable to load result file: {e}")

    return Response(
        data,
        mimetype=guess_mime_type(filename),
        headers={"Cache-Control": "no-store"},
    )

# -----------------------------
# LEVEL_B intro call
# -----------------------------
@app.route("/WhatYouSay/level-b-intro", methods=["GET"])
def level_b_intro():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))

    if not session.get("paid", False):
        return redirect(url_for("level_a"))

    return render_template(
        "level_b_intro.html",
        safe_user=session.get("safe_user")
    )


# -----------------------------
# LEVEL_B call
# -----------------------------
@app.route("/WhatYouSay/level-b", methods=["GET"])
def level_b():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    if not session.get("paid", False):
        return redirect(url_for("level_a"))

    parsed_payload = session.get("parsed_data")
    if not isinstance(parsed_payload, dict):
        return render_template(
            "error.html",
            message="Level B is temporarily unavailable on this deployment while storage migration is in progress.",
        )

    # Generate Level-B narrative once per session
    if "levelB_narrative" not in session:
        from levelB_runner import generate_levelB_narrative

        report = generate_levelB_narrative(
            anon_text=parsed_payload["anon_text"],
            self_text=parsed_payload["self_text"],
            metrics=session["metrics"],
            evidence=session.get("evidence", {}),  # optional, empty for now
            speaker_alias=session.get("safe_user"),
        )

        session["levelB_narrative"] = report

    return render_template(
        "level_b.html",
        levelB_report=session["levelB_narrative"],
        user_handle=session.get("user_handle"),
        safe_user=session.get("safe_user")
    )

# -----------------------------
# Dummy payment flow
# -----------------------------
@app.route("/WhatYouSay/pay", methods=["GET"])
def pay():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    return render_template("paypal_stub.html")


@app.route("/WhatYouSay/pay/confirm", methods=["POST"])
def paypal_confirm():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    session["paid"] = True
    return redirect(url_for("level_a"))


# -----------------------------
# Delete & Exit
# -----------------------------
@app.route("/WhatYouSay/delete", methods=["POST"])
def delete_and_exit():
    run_id = session.get("run_id")
    upload_path = session.get("upload_path")

    # delete uploaded object in Supabase
    if upload_path:
        try:
            store.remove_many(store.upload_bucket, [upload_path])
        except Exception as e:
            print(f"[Delete upload failed] {e}")

    # delete result objects in Supabase
    if run_id:
        try:
            result_paths = store.list_prefix(store.results_bucket, run_id)
            store.remove_many(store.results_bucket, result_paths)
        except Exception as e:
            print(f"[Delete results failed] {e}")

        store.safe_update_run(
            run_id,
            {"status": "deleted", "deleted_at": datetime.now(timezone.utc).isoformat()},
        )

    # delete local temp files if present
    chat_path = session.get("chat_path")
    if chat_path and os.path.exists(chat_path):
        try:
            os.remove(chat_path)
        except Exception:
            pass

    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
