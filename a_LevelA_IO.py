import os
import re
import uuid
import json
from collections import Counter
from dateutil import parser as dateparser
import emoji

from b_lexicon_loader import (load_nrc_emotion_lexicon,load_categorical_moral_lexicon_tsv,load_weighted_moral_lexicon_tsv)
from c_feature_extractor import (tokenize,lexicon_hits,message_heuristics,extract_emoji_valence)
from d_scoring import (normalize_counter,tone_from_nrc,mode_scores,role_scores,confidence_band)
from e_visuals import (save_bar,save_line)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
LEX_DIR = os.path.join(BASE_DIR, "lexicons")

# ================================
# CONFIG
# ================================

MIN_CONTRIBUTION_PCT = 2.0  # threshold for "substantial contributors"

# ================================
# PARSER
# ================================

TIMESTAMP_PATTERN = re.compile(
    r'^(?P<ts>\d{1,2}[\/\.]\d{1,2}[\/\.]\d{2,4},\s\d{1,2}:\d{2}.*)\s-\s(?P<body>.+)$'
)

SPEAKER_PATTERN = re.compile(r'^(?P<speaker>[^:]+):\s(?P<text>.*)$')

SYSTEM_HINTS = [
    "added", "removed", "left", "joined", "created group",
    "changed the subject", "changed the description",
    "end-to-end encrypted", "missed call", "deleted this message"
]

def is_system_message(text):
    return any(h in text.lower() for h in SYSTEM_HINTS)

def extract_emojis(text):
    return [c for c in text if c in emoji.EMOJI_DATA]

def parse_whatsapp(file_path):
    messages = []
    current = None
    idx = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue

            m = TIMESTAMP_PATTERN.match(line)
            if m:
                if current:
                    messages.append(current)
                    current = None

                body = m.group("body")
                if is_system_message(body):
                    continue

                sm = SPEAKER_PATTERN.match(body)
                if not sm:
                    continue

                try:
                    ts = dateparser.parse(m.group("ts"), fuzzy=True)
                except Exception:
                    ts = None

                speaker = sm.group("speaker").strip()
                text = sm.group("text").strip()

                current = {
                    "id": str(uuid.uuid4()),
                    "speaker": speaker,
                    "speaker_canon": canonicalize(speaker),
                    "timestamp": ts,
                    "text": text,
                    "emojis": extract_emojis(text),
                    "word_count": len(text.split()),
                    "is_question": text.endswith("?"),
                    "idx": idx
                }
                idx += 1
            else:
                if current:
                    current["text"] += "\n" + line
                    current["word_count"] = len(current["text"].split())

        if current:
            messages.append(current)

    return messages

def canonicalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    return "".join(ch for ch in s if ch.isalnum())

# ================================
# LEVEL A
# ================================

def level_a_stats(self_msgs):
    total = len(self_msgs)
    avg_len = sum(m["word_count"] for m in self_msgs) / total
    q_ratio = sum(m["is_question"] for m in self_msgs) / total
    emoji_ratio = sum(bool(m["emojis"]) for m in self_msgs) / total
    return avg_len, q_ratio, emoji_ratio

# ================================
# MAIN
# ================================
def load_chat_from_file(file_path: str):
    msgs = parse_whatsapp(file_path)
    if not msgs:
        raise ValueError("No messages parsed from file")
    return msgs


def get_substantial_speakers(messages, min_pct=MIN_CONTRIBUTION_PCT):
    counts = Counter(m["speaker"] for m in messages)
    total = len(messages)
    return {
        speaker: count
        for speaker, count in counts.items()
        if (count / total) * 100 >= min_pct
    }


def anonymize_and_split(messages, user_handle):
    anon_map = {}
    anon_msgs = []
    user_canon = canonicalize(user_handle)
    self_msgs = []

    for msg in messages:
        sp = msg.get("speaker", "")

        # Build anonymized messages (group context)
        if sp not in anon_map:
            anon_map[sp] = f"User_{len(anon_map)+1}"

        anon = msg.copy()
        anon["speaker"] = anon_map[sp]
        anon_msgs.append(anon)

        # Collect ONLY user's own messages (primary signal)
        if msg.get("speaker_canon", "") == user_canon:
            self_msgs.append(msg)

    return anon_msgs, self_msgs


def run_level_a_pipeline(chat_path, user_handle, safe_user, out_dir):
    if storage_mode == "disk":
        out_dir = os.path.join(RESULTS_DIR, safe_user)
        os.makedirs(out_dir, exist_ok=True)
    else:
        out_dir = None
    
    msgs = load_chat_from_file(chat_path)
    anon_msgs, self_msgs = anonymize_and_split(msgs, user_handle)
    n_msgs = len(self_msgs)
    if n_msgs == 0:
        raise ValueError("Selected handle has 0 messages in this export.")

    avg_len, q_ratio, emoji_ratio = level_a_stats(self_msgs)

    # --- Lexicons
    nrc_path = os.path.join(LEX_DIR, "NRC-Emotion-Lexicon-Wordlevel-v0.92.txt")
    moral_cat_path = os.path.join(LEX_DIR, "lexicon_filtered.tsv")  # if you use it
    moral_weight_path = os.path.join(LEX_DIR, "liberty_moral_lexicon.tsv")

    nrc_lex = load_nrc_emotion_lexicon(nrc_path)
    moral_cat = None
    if os.path.exists(moral_cat_path):
        moral_cat = load_categorical_moral_lexicon_tsv(moral_cat_path)

    # (B) weighted -> float scores (DO NOT pass into lexicon_hits)
    moral_weight = None
    if os.path.exists(moral_weight_path):
       moral_weight = load_weighted_moral_lexicon_tsv(moral_weight_path)

    # --- Aggregate features
    emotion_counts = Counter()
    moral_counts = Counter()
    heur_counts = Counter()
    emoji_tag_counts = Counter()
    valence_timeline = []

    long_gap_hits = 0
    burst_hits = 0
    prev_idx = None

    for m in self_msgs:
        text = m.get("text", "") or ""
        toks = tokenize(text)

        # NRC emotion hits (categorical labels)
        emotion_counts += lexicon_hits(toks, nrc_lex)

        # Moral lexicon:
        if moral_cat:
            moral_counts += lexicon_hits(toks, moral_cat)
        elif moral_weight:
        # weighted: treat positive vs negative moral loading as two bins
            score = 0.0
            for t in toks:
                if t in moral_weight:
                    score += moral_weight[t]
            if score > 0.2:
                moral_counts["moral_positive"] += 1
            elif score < -0.2:
                moral_counts["moral_negative"] += 1

        # heuristics (question/hedge/corrective/affiliative/challenge)
        h = message_heuristics(text)
        for k, v in h.items():
            if v:
                heur_counts[k] += 1
        heur_counts["total_msgs"] += 1

        # emoji valence + tags
        emojis = m.get("emojis", []) or []
        ev, tags = extract_emoji_valence(emojis)
        valence_timeline.append(ev)
        emoji_tag_counts += tags

        # crude burst / initiation proxies
        idx = m.get("idx")
        if isinstance(idx, int) and prev_idx is not None:
            d = idx - prev_idx
            if d <= 3:
                burst_hits += 1
            if d >= 50:
                long_gap_hits += 1
        prev_idx = idx

    emotion_norm = normalize_counter(emotion_counts)
    moral_norm = normalize_counter(moral_counts) if moral_counts else {}
    tone_valence = tone_from_nrc(emotion_norm)

    burst_ratio = (burst_hits / max(1, n_msgs))
    initiation_proxy = (long_gap_hits / max(1, n_msgs))

    #print("NRC raw counts:", emotion_counts)
    #print("NRC normalized:", emotion_norm)

    mode = mode_scores(heur_counts)
    role = role_scores(mode, burst_ratio=burst_ratio, initiation_proxy=initiation_proxy)
    conf = confidence_band(n_msgs)

    plots = {}

    if storage_mode == "disk":
        emotion_plot = os.path.join(out_dir, "emotion_distribution.png")
        save_bar(emotion_norm, "Emotion distribution (NRC)", emotion_plot)
        plots["emotion"] = {"type": "file", "value": emotion_plot}
    
        moral_plot = os.path.join(out_dir, "moral_loading.png")
        if moral_norm:
            save_bar(moral_norm, "Moral / value framing", moral_plot)
            plots["moral"] = {"type": "file", "value": moral_plot}
        else:
            plots["moral"] = None
    
        valence_plot = os.path.join(out_dir, "valence_timeline.png")
        save_line(valence_timeline, "Emoji valence timeline (proxy)", valence_plot, ylabel="Valence")
        plots["valence"] = {"type": "file", "value": valence_plot}
    else:
        plots["emotion"] = {"type": "data", "value": emotion_norm}
        plots["moral"] = {"type": "data", "value": moral_norm}
        plots["valence"] = {"type": "data", "value": valence_timeline}

    for m in anon_msgs:
        if "timestamp" in m and m["timestamp"] is not None:
            m["timestamp"] = m["timestamp"].isoformat()
    
    def write_messages_txt(path: str, messages: list):
        with open(path, "w", encoding="utf-8") as f:
            for m in messages:
                ts = m.get("timestamp", "")
                sp = m.get("speaker", "")
                text = m.get("text", "") or ""
                f.write(f"{ts}\t{sp}\t{text}\n")

    # Save anonymized chat if you want (optional)
    # Save anonymized + self-only text files
    prefix = safe_user
    #anon_file = os.path.join(out_dir, f"{prefix}_anonymized_chat.txt")
    #self_file = os.path.join(out_dir, f"{prefix}_only_chat.txt")
    #write_messages_txt(anon_file, anon_msgs)
    #write_messages_txt(self_file, self_msgs)

    metrics = {
        "safe_user": safe_user,
        "n_messages": n_msgs,
        "avg_len_words": round(avg_len, 2),
        "question_pct": round(q_ratio * 100, 1),
        "emoji_pct": round(emoji_ratio * 100, 1),
        "confidence": conf,
        "tone_valence": round(tone_valence, 3),
        "mode": {k: round(v, 3) for k, v in mode.items()},
        "role": {k: round(v, 3) for k, v in role.items()},
        "emotion_norm": {k: round(v, 4) for k, v in emotion_norm.items()},
        "moral_norm": {k: round(v, 4) for k, v in moral_norm.items()} if moral_norm else {},
        "emoji_tags": dict(emoji_tag_counts.most_common(10)),
        "files": {
            "emotion_distribution": "emotion_distribution.png",
            "moral_loading": "moral_loading.png" if moral_norm else None,
            "valence_timeline": "valence_timeline.png",
            "anonymized_chat": f"{prefix}_anonymized_chat.txt",
            "speaker_only_chat": f"{prefix}_only_chat.txt",
        }
    }

    if storage_mode == "disk":
        # metrics JSON
        with open(os.path.join(out_dir, "metrics_levelA.json"), "w") as f:
            json.dump(metrics, f, indent=2)
    
        # evidence CSV (simple example)
        with open(os.path.join(out_dir, "evidence_levelA.csv"), "w", encoding="utf-8") as f:
            f.write("token,count\n")
            for k, v in emotion_counts.items():
                f.write(f"{k},{v}\n")
         files = {
            "metrics_json": "metrics_levelA.json",
            "evidence_csv": "evidence_levelA.csv",
            } 


    return metrics

