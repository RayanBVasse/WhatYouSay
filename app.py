import os
import re
import shutil
from pathlib import Path
from collections import Counter

from flask import ( Flask, render_template, request, redirect, url_for, session, send_from_directory)
from werkzeug.utils import secure_filename

# Shared IO (unchanged)
from a_LevelA_IO import load_chat_from_file, run_level_a_pipeline, get_substantial_speakers

# -----------------------------
# App setup
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# results/ is created by a_LevelA_IO under its own BASE_DIR/results/<safe_user>
# but we keep local path references consistent here too.
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {"txt"}
MAX_FILE_MB = 5

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["UPLOAD_FOLDER"] = str(UPLOAD_DIR)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_MB * 1024 * 1024

    
    
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

# -----------------------------
# Routes
# -----------------------------
@app.route("/WhatYouSay/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/WhatYouSay/upload", methods=["POST"])
def upload():
    #print("CONTENT TYPE:", request.content_type)
    #print("FORM KEYS:", list(request.form.keys()))
    #print("FILES KEYS:", list(request.files.keys()))
    
    user_handle = request.form.get("user_handle", "").strip()
    platform = request.form.get("platform", "").strip()  # optional, if you still collect it

    file = request.files.get("text")
    if not file or file.filename == "":
        return render_template("error.html", message="No file selected.")

    if not allowed_file(file.filename):
        return render_template("error.html", message="Please upload a .txt WhatsApp export.")

    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)
    session["chat_path"] = save_path
    
    # Resolve user -> match against canonicalized speakers from parsed file
    safe_user, resolved_user_handle, messages, speaker_counts = resolve_user_handle_from_file(save_path, user_handle)
    speaker_data = anonymize_and_rank_speakers(speaker_counts, resolved_user_handle, top_n=10)
    ranked_speakers = speaker_data["ranked"]
    chart_labels = speaker_data["chart_labels"]
    chart_values = speaker_data["chart_values"]
    chart_percentages = speaker_data["chart_percentages"]
    
    
    if not safe_user:
        # cleanup upload
        try:
            os.remove(save_path)
        except Exception:
            pass
        return render_template("error.html", message="Please enter a user handle.")

    if resolved_user_handle is None:
        # cleanup upload
        try:
            os.remove(save_path)
        except Exception:
            pass
        return render_template(
            "error.html",
            message=f"No match found for '{user_handle}'. "
                    f"Tip: type the name/number as it appears in WhatsApp (any casing/punctuation is fine)."
        )
    
    
    # Basic stats for confirmation page
    
    #substantial = get_substantial_speakers(messages)  # uses MIN_CONTRIBUTION_PCT inside IO
    #top_speakers = sorted(speaker_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]
    
    speaker_counts = Counter(m["speaker"] for m in messages if m.get("speaker"))
    total_messages = sum(speaker_counts.values())
    user_messages = speaker_counts.get(resolved_user_handle, 0)
    char_count = sum(len((m.get("text") or "")) for m in messages)
    line_count = len(messages)  # better than splitlines for structured messages

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
    session["chat_path"] = save_path
    session["user_handle"] = resolved_user_handle      # EXACT speaker label (IO needs this)
    session["safe_user"] = safe_user                   # canonical safe id (folders/urls)
    session["platform"] = platform
    session["paid"] = False

    #print("TOP_SPEAKERS:", speaker_counts.most_common(15))

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
        ranked_speakers= ranked_speakers,
        chart_labels=chart_labels,
        chart_values=chart_values,
        chart_percentages = chart_percentages
    )


@app.route("/WhatYouSay/level-a", methods=["GET"])
def level_a():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))

    chat_path = session.get("chat_path")
    user_handle = session.get("user_handle")  # exact speaker label
    safe_user = session.get("safe_user")

    if not chat_path or not user_handle or not safe_user:
        return redirect(url_for("index"))

    # Run pipeline once per session (cache in session)
    if "metrics" not in session:
        try:
            metrics = run_level_a_pipeline(
                chat_path=chat_path,
                user_handle=user_handle,
                safe_user=safe_user,
                out_dir=None,
                storage_mode="memory"  # "disk" | "memory"
            )
        except Exception as e:
            return render_template("error.html", message=str(e))

        session["metrics"] = metrics

    return render_template(
        "level_a.html",
        metrics=session["metrics"],
        user_handle=user_handle,
        safe_user=safe_user,
        paid=session.get("paid", False)
    )


@app.route("/WhatYouSay/results/<safe_user>/<filename>", methods=["GET"])
def serve_results(safe_user, filename):
    # Results are stored under BASE_DIR/results/<safe_user>/...
    directory = RESULTS_DIR / safe_user
    return send_from_directory(str(directory), filename)


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


@app.route("/WhatYouSay/level-b", methods=["GET"])
def level_b():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    if not session.get("paid", False):
        return redirect(url_for("level_a"))

    # Placeholder for now
    return render_template(
        "level_b.html",
        user_handle=session.get("user_handle"),
        safe_user=session.get("safe_user")
    )


# -----------------------------
# Delete & Exit
# -----------------------------
@app.route("/WhatYouSay/delete", methods=["POST"])
def delete_and_exit():
    # delete uploaded file
    chat_path = session.get("chat_path")
    if chat_path and os.path.exists(chat_path):
        try:
            os.remove(chat_path)
        except Exception:
            pass

    # delete results folder
    safe_user = session.get("safe_user")
    if safe_user:
        user_results = RESULTS_DIR / safe_user
        if user_results.exists():
            try:
                shutil.rmtree(user_results)
            except Exception:
                pass

    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)















