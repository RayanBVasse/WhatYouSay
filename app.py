import os
import sys
import re
import tempfile
import uuid
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter
from flask import ( Flask, render_template, request, redirect, url_for, session, Response, jsonify)
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
PAYWALL_ENABLED = os.environ.get("PAYWALL_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
CLEANUP_RETENTION_HOURS = int(os.environ.get("CLEANUP_RETENTION_HOURS", "24"))
CLEANUP_TOKEN = (os.environ.get("CRON_SECRET") or os.environ.get("CLEANUP_TOKEN") or "").strip()

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


def load_result_text(run_id: str, filename: str):
    if not store.ready:
        return None
    object_path = f"{run_id}/{filename}"
    try:
        raw = store.download_bytes(store.results_bucket, object_path)
    except Exception:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def load_result_json(run_id: str, filename: str):
    txt = load_result_text(run_id, filename)
    if not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def save_result_json(run_id: str, filename: str, payload: dict):
    if not store.ready:
        return
    object_path = f"{run_id}/{filename}"
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    store.upload_bytes(
        bucket=store.results_bucket,
        path=object_path,
        data=data,
        content_type="application/json",
    )


EMOTION_DESCRIPTIONS = {
    "joy": "Positive affect, shared enjoyment, and social warmth in your language.",
    "trust": "Language indicating confidence, alignment, and cooperative intent.",
    "anticipation": "Forward-looking phrasing linked to plans, expectation, and momentum.",
    "surprise": "Moments where expectations shift and reactions become more expressive.",
    "sadness": "Signals of disappointment, concern, or reflective low-tone moments.",
    "anger": "Sharper or confrontational phrasing under pressure or disagreement.",
    "fear": "Worry, risk sensitivity, and uncertainty in challenging moments.",
    "disgust": "Rejection or aversion framing toward ideas, behaviors, or events.",
    "positive": "Overall positive emotional loading across your messages.",
    "negative": "Overall negative emotional loading across your messages.",
}

MORAL_DESCRIPTIONS = {
    "moral_positive": "Value-forward language around fairness, care, responsibility, and shared norms.",
    "moral_negative": "Critical or disapproving value language in conflict or disagreement contexts.",
}

EMOTION_KEYWORDS = {
    "joy": ["great", "love", "fun", "happy", "nice", "amazing", "yay", "haha", "lol", "thanks", "glad"],
    "trust": ["agree", "sure", "exactly", "yes", "thanks", "appreciate", "reliable", "trust"],
    "anticipation": ["will", "going to", "gonna", "soon", "tomorrow", "next", "later", "plan"],
    "surprise": ["wow", "whoa", "omg", "unexpected", "did not expect", "no way"],
    "sadness": ["sad", "upset", "sorry", "miss", "disappointed"],
    "anger": ["angry", "annoyed", "frustrat", "ridiculous", "unfair", "wtf"],
    "fear": ["worry", "worried", "afraid", "concern", "anxious", "risk"],
    "disgust": ["disgust", "gross", "awful", "hate"],
}

MORAL_KEYWORDS = {
    "moral_positive": ["should", "fair", "care", "respect", "responsib", "ethical", "empathy", "justice", "help"],
    "moral_negative": ["wrong", "unfair", "blame", "shame", "disgrace", "harm", "bad faith"],
}


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _clip_text(text: str, max_len: int = 180) -> str:
    t = _normalize_space(text)
    if len(t) <= max_len:
        return t
    return t[: max_len - 3].rstrip() + "..."


def _extract_self_lines(run_id: str, safe_user: str, limit: int = 1200):
    raw = load_result_text(run_id, f"{safe_user}_only_chat.txt")
    if not raw:
        return []
    out = []
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        txt = parts[2] if len(parts) == 3 else line
        txt = _normalize_space(txt)
        if len(txt) >= 8:
            out.append(txt)
    return out[-limit:]


def _pick_snippets(lines, keywords, limit: int = 3):
    if not lines:
        return []
    kws = [k.lower() for k in (keywords or []) if k]
    chosen = []
    seen = set()
    for ln in lines:
        low = ln.lower()
        if kws and (not any(k in low for k in kws)):
            continue
        clipped = _clip_text(ln, 170)
        if clipped.lower() in seen:
            continue
        seen.add(clipped.lower())
        chosen.append(clipped)
        if len(chosen) >= limit:
            break

    if chosen:
        return chosen

    # fallback when no keyword hit: return the most readable short lines
    for ln in lines:
        clipped = _clip_text(ln, 140)
        if clipped.lower() in seen:
            continue
        seen.add(clipped.lower())
        chosen.append(clipped)
        if len(chosen) >= min(2, limit):
            break
    return chosen


def _top_items(counter_dict: dict, limit: int = 4, skip_keys=None):
    skip = set(skip_keys or [])
    pairs = []
    for k, v in (counter_dict or {}).items():
        try:
            val = float(v)
        except Exception:
            continue
        if val <= 0 or k in skip:
            continue
        pairs.append((str(k), val))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:limit]


def build_level_b_sidebar(metrics: dict, self_lines):
    metrics = metrics or {}
    emotions_src = metrics.get("emotion_norm") or {}
    morals_src = metrics.get("moral_norm") or {}

    emotion_items = _top_items(emotions_src, limit=4, skip_keys={"positive", "negative"})
    if len(emotion_items) < 3:
        seen = {k for k, _ in emotion_items}
        for k, v in _top_items(emotions_src, limit=6):
            if k in seen:
                continue
            emotion_items.append((k, v))
            seen.add(k)
            if len(emotion_items) >= 4:
                break

    emotions = []
    for key, value in emotion_items:
        k = key.lower()
        emotions.append(
            {
                "label": key.replace("_", " ").title(),
                "score_pct": round(value * 100, 1),
                "description": EMOTION_DESCRIPTIONS.get(k, "Emotion signal detected in the conversation."),
                "examples": _pick_snippets(self_lines, EMOTION_KEYWORDS.get(k, []), limit=3),
            }
        )

    moral_items = _top_items(morals_src, limit=2)
    morals = []
    for key, value in moral_items:
        k = key.lower()
        morals.append(
            {
                "label": key.replace("_", " ").title(),
                "score_pct": round(value * 100, 1),
                "description": MORAL_DESCRIPTIONS.get(k, "Moral framing signal detected in language use."),
                "examples": _pick_snippets(self_lines, MORAL_KEYWORDS.get(k, []), limit=2),
            }
        )

    mode = metrics.get("mode") or {}
    role = metrics.get("role") or {}
    top_mode = _top_items(mode, limit=2)
    top_role = _top_items(role, limit=2)

    mode_copy = {
        "affiliative": "You often keep the social tone warm and connective.",
        "corrective": "You step in to clarify, refine, or correct details when needed.",
        "challenge": "You raise pressure when standards or accuracy feel at stake.",
        "question": "You use questions to open discussion and probe uncertainty.",
        "hedge": "You soften assertions when precision or social balance matters.",
    }
    role_copy = {
        "stabilizer": "You contribute steady structure that helps anchor group flow.",
        "initiator": "You often initiate new threads and move the conversation forward.",
        "critic": "You pressure-test ideas and expose weak spots in arguments.",
        "connector": "You bridge people and topics to keep conversation integrated.",
    }

    group_points = []
    for k, _v in top_mode:
        group_points.append(mode_copy.get(k, f"Mode pattern: {k.replace('_', ' ')}."))
    for k, _v in top_role:
        group_points.append(role_copy.get(k, f"Role pattern: {k.replace('_', ' ')}."))

    chart_emotions = []
    for key, value in emotion_items[:3]:
        chart_emotions.append(
            {
                "label": key.replace("_", " ").title(),
                "value_pct": round(value * 100, 1),
            }
        )

    moral_pos = float((morals_src or {}).get("moral_positive", 0.0) or 0.0)
    moral_neg = float((morals_src or {}).get("moral_negative", 0.0) or 0.0)
    if moral_pos == 0 and moral_neg == 0 and moral_items:
        for k, v in moral_items:
            if "positive" in k.lower():
                moral_pos = float(v)
            elif "negative" in k.lower():
                moral_neg = float(v)

    return {
        "emotions": emotions,
        "morals": morals,
        "group_points": group_points,
        "confidence": metrics.get("confidence", "unknown"),
        "chart_emotions": chart_emotions,
        "chart_morals": {
            "positive_pct": round(moral_pos * 100, 1),
            "negative_pct": round(moral_neg * 100, 1),
        },
    }


def _extract_bearer_token(header_value: str) -> str:
    if not header_value:
        return ""
    parts = header_value.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def _cron_authorized() -> bool:
    if not CLEANUP_TOKEN:
        return False
    token = _extract_bearer_token(request.headers.get("Authorization", ""))
    return token == CLEANUP_TOKEN


def delete_run_objects(run_id: str, upload_path: str):
    deleted_upload = 0
    deleted_results = 0
    errors = []

    if upload_path:
        try:
            store.remove_many(store.upload_bucket, [upload_path])
            deleted_upload = 1
        except Exception as e:
            errors.append(f"upload_remove_failed:{e}")

    if run_id:
        try:
            result_paths = store.list_prefix(store.results_bucket, run_id)
            if result_paths:
                store.remove_many(store.results_bucket, result_paths)
                deleted_results = len(result_paths)
        except Exception as e:
            errors.append(f"results_remove_failed:{e}")

    return deleted_upload, deleted_results, errors

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
    try:
        safe_user, resolved_user_handle, messages, speaker_counts = resolve_user_handle_from_file(save_path, user_handle)
    except Exception as e:
        try:
            store.remove_many(store.upload_bucket, [upload_path])
        except Exception:
            pass
        return render_template(
            "error.html",
            message=f"Unable to parse this chat export. Please upload a standard WhatsApp .txt export. Details: {e}",
        )
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
    session["paid"] = (not PAYWALL_ENABLED)

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
        message_count=total_messages,
        total_messages=total_messages,
        user_messages=user_messages,
        user_share=user_share,
        warnings=warnings,
        speaker_data=speaker_data,
        ranked_speakers=speaker_data.get("ranked", []),
        chart_labels=speaker_data.get("chart_labels", []),
        chart_values=speaker_data.get("chart_values", []),
        chart_percentages=speaker_data.get("chart_percentages", []),
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
        paid=session.get("paid", False),
        paywall_enabled=PAYWALL_ENABLED,
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

    if PAYWALL_ENABLED and (not session.get("paid", False)):
        return redirect(url_for("level_a"))

    return render_template(
        "level_b_intro.html",
        safe_user=session.get("safe_user")
    )


def ensure_level_b_report(run_id: str, safe_user: str):
    # Reuse existing Level B output (avoids repeat LLM calls).
    report = load_result_json(run_id, "levelB_output.json")
    if report is not None:
        return report

    anon_filename = f"{safe_user}_anonymized_chat.txt"
    self_filename = f"{safe_user}_only_chat.txt"
    anon_text = load_result_text(run_id, anon_filename)
    self_text = load_result_text(run_id, self_filename)
    metrics = load_level_a_metrics(run_id)

    if not anon_text or not self_text or not metrics:
        raise RuntimeError(
            "Level B needs completed Level A artifacts. Please run Level A first and try again."
        )

    store.safe_update_run(run_id, {"status": "levelb_running"})
    from levelB_runner import generate_levelB_narrative

    report = generate_levelB_narrative(
        anon_text=anon_text,
        self_text=self_text,
        metrics=metrics,
        evidence={},
        speaker_alias=safe_user,
    )

    try:
        save_result_json(run_id, "levelB_output.json", report)
    except Exception as e:
        print(f"[Level B save warning] {e}")

    store.safe_update_run(
        run_id,
        {
            "status": "levelb_done",
            "levelb_path": f"{run_id}/levelB_output.json",
        },
    )
    return report


@app.route("/WhatYouSay/level-b-generate", methods=["POST"])
def level_b_generate():
    if not session.get("parsed_data"):
        return jsonify({"ok": False, "error": "Session expired. Please re-upload your chat."}), 400
    if PAYWALL_ENABLED and (not session.get("paid", False)):
        return jsonify({"ok": False, "error": "Payment required for Level B."}), 403

    run_id = session.get("run_id")
    safe_user = session.get("safe_user")
    if not run_id or not safe_user:
        return jsonify({"ok": False, "error": "Session context missing."}), 400

    try:
        ensure_level_b_report(run_id, safe_user)
        return jsonify({"ok": True, "redirect": url_for("level_b")})
    except Exception as e:
        store.safe_update_run(run_id, {"status": "error", "error_message": f"levelb:{e}"})
        return jsonify({"ok": False, "error_type": type(e).__name__, "error": str(e)}), 500


# -----------------------------
# LEVEL_B call
# -----------------------------
@app.route("/WhatYouSay/level-b", methods=["GET"])
def level_b():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    if PAYWALL_ENABLED and (not session.get("paid", False)):
        return redirect(url_for("level_a"))

    run_id = session.get("run_id")
    safe_user = session.get("safe_user")
    user_handle = session.get("user_handle")
    if not run_id or not safe_user:
        return redirect(url_for("index"))

    try:
        report = ensure_level_b_report(run_id, safe_user)
    except Exception as e:
        store.safe_update_run(run_id, {"status": "error", "error_message": f"levelb:{e}"})
        err_type = type(e).__name__
        return render_template(
            "error.html",
            title="Level B generation failed",
            message=f"Level B generation failed ({err_type}). Details: {e}",
        )

    level_a_metrics = load_level_a_metrics(run_id) or {}
    self_lines = _extract_self_lines(run_id, safe_user)
    sidebar = build_level_b_sidebar(level_a_metrics, self_lines)

    return render_template(
        "level_b.html",
        levelB_report=report,
        user_handle=user_handle,
        safe_user=safe_user,
        levela_metrics=level_a_metrics,
        sidebar=sidebar,
    )

# -----------------------------
# Dummy payment flow
# -----------------------------
@app.route("/WhatYouSay/pay", methods=["GET"])
def pay():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    if not PAYWALL_ENABLED:
        return redirect(url_for("level_a"))
    return render_template("paypal_stub.html")


@app.route("/WhatYouSay/pay/confirm", methods=["POST"])
def paypal_confirm():
    if not session.get("parsed_data"):
        return redirect(url_for("index"))
    if not PAYWALL_ENABLED:
        return redirect(url_for("level_a"))
    session["paid"] = True
    return redirect(url_for("level_a"))


# -----------------------------
# Delete & Exit
# -----------------------------
@app.route("/WhatYouSay/delete", methods=["POST"])
def delete_and_exit():
    run_id = session.get("run_id")
    upload_path = session.get("upload_path")
    now_iso = datetime.now(timezone.utc).isoformat()

    _, _, errors = delete_run_objects(run_id, upload_path)
    if errors:
        print(f"[Delete errors] {'; '.join(errors)}")

    if run_id:
        # Prefer hard delete for privacy; fallback to soft marker if schema/rules differ.
        try:
            store.delete_run(run_id)
        except Exception:
            store.safe_update_run(run_id, {"status": "deleted", "deleted_at": now_iso})

    # delete local temp files if present
    chat_path = session.get("chat_path")
    if chat_path and os.path.exists(chat_path):
        try:
            os.remove(chat_path)
        except Exception:
            pass

    session.clear()
    return redirect(url_for("index"))


@app.route("/WhatYouSay/cron/cleanup", methods=["GET"])
def cron_cleanup():
    if not CLEANUP_TOKEN:
        return jsonify({"ok": False, "error": "cleanup_token_not_configured"}), 500

    if not _cron_authorized():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    if not store.ready:
        return jsonify({"ok": False, "error": "storage_not_configured"}), 500

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=CLEANUP_RETENTION_HOURS)
    rows = store.list_runs_older_than(cutoff.isoformat(), limit=1000)

    scanned = len(rows)
    runs_processed = 0
    uploads_deleted = 0
    result_objects_deleted = 0
    row_deletes = 0
    errors = []

    for row in rows:
        run_id = row.get("run_id")
        upload_path = row.get("upload_path")
        if not run_id:
            continue

        du, dr, err = delete_run_objects(run_id, upload_path)
        uploads_deleted += du
        result_objects_deleted += dr
        if err:
            errors.extend([f"{run_id}:{e}" for e in err])

        try:
            store.delete_run(run_id)
            row_deletes += 1
        except Exception as e:
            errors.append(f"{run_id}:row_delete_failed:{e}")
            store.safe_update_run(run_id, {"status": "deleted", "deleted_at": now.isoformat()})

        runs_processed += 1

    return jsonify(
        {
            "ok": True,
            "retention_hours": CLEANUP_RETENTION_HOURS,
            "cutoff_utc": cutoff.isoformat(),
            "scanned_rows": scanned,
            "runs_processed": runs_processed,
            "uploads_deleted": uploads_deleted,
            "result_objects_deleted": result_objects_deleted,
            "rows_deleted": row_deletes,
            "errors_count": len(errors),
            "errors_sample": errors[:20],
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
