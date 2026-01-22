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

def bucket_time_of_day(messages, label_map):
    results = {}
    for speaker, label in label_map.items():
        results[label] = {"hourly_counts": {hour: 0 for hour in range(24)}, "peak_hour": None}

    for m in messages:
        speaker = m.get("speaker")
        if speaker not in label_map:
            continue
        ts = m.get("timestamp")
        if not ts:
            continue
        results[label_map[speaker]]["hourly_counts"][ts.hour] += 1

    for label, data in results.items():
        hourly = data["hourly_counts"]
        if hourly:
            data["peak_hour"] = max(hourly, key=lambda h: (hourly[h], -h))

    return results

def bucket_day_of_week(messages, label_map):
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    results = {
        label: {"day_counts": {day: 0 for day in days}, "peak_day": None}
        for label in label_map.values()
    }

    for m in messages:
        speaker = m.get("speaker")
        if speaker not in label_map:
            continue
        ts = m.get("timestamp")
        if not ts:
            continue
        day_label = days[ts.weekday()]
        results[label_map[speaker]]["day_counts"][day_label] += 1

    for label, data in results.items():
        day_counts = data["day_counts"]
        if day_counts:
            data["peak_day"] = max(day_counts, key=lambda d: (day_counts[d], -days.index(d)))

    return results

def compute_emoji_usage(messages, label_map):
    counts = Counter()
    msg_counts = Counter()

    for m in messages:
        speaker = m.get("speaker")
        if speaker not in label_map:
            continue
        label = label_map[speaker]
        msg_counts[label] += 1
        counts[label] += len(m.get("emojis") or [])

    results = {}
    for label in label_map.values():
        total_msgs = msg_counts[label]
        results[label] = {
            "emoji_count": int(counts[label]),
            "messages": int(total_msgs),
            "emoji_per_message": round(counts[label] / max(1, total_msgs), 4),
        }

    return results

def compute_question_behavior(messages, primary_speaker, label_map):
    question_counts = Counter()
    response_targets = Counter()
    question_response_targets = Counter()

    for m in messages:
        speaker = m.get("speaker")
        if speaker not in label_map:
            continue
        label = label_map[speaker]
        text = m.get("text") or ""
        if "?" in text:
            question_counts[label] += 1

    for prev_msg, msg in zip(messages, messages[1:]):
        if msg.get("speaker") != primary_speaker:
            continue
        prev_speaker = prev_msg.get("speaker")
        if prev_speaker not in label_map:
            continue
        target_label = label_map[prev_speaker]
        response_targets[target_label] += 1
        if "?" in (prev_msg.get("text") or ""):
            question_response_targets[target_label] += 1

    return {
        "question_counts": dict(question_counts),
        "responses_by_user": dict(response_targets),
        "top_response_target": response_targets.most_common(1)[0][0] if response_targets else None,
        "question_responses_by_user": dict(question_response_targets),
        "top_question_response_target": (
            question_response_targets.most_common(1)[0][0] if question_response_targets else None
        ),
    }

def compute_interaction_partners(messages, primary_speaker, label_map):
    interactions = Counter()

    for prev_msg, msg in zip(messages, messages[1:]):
        speakers = {prev_msg.get("speaker"), msg.get("speaker")}
        if primary_speaker not in speakers:
            continue
        other = (speakers - {primary_speaker}).pop() if len(speakers) == 2 else None
        if other not in label_map:
            continue
        interactions[label_map[other]] += 1

    ranked = [
        {"user": label, "count": count}
        for label, count in interactions.most_common()
    ]

    return {
        "counts": dict(interactions),
        "ranked": ranked,
    }




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



def extract_emotion_evidence(self_msgs,nrc_lex,target_emotions=("joy", "trust", "anticipation", "surprise"),
    top_k=5,
):
    """
    Extract representative message examples contributing to selected NRC emotions.
    Returns a dict keyed by emotion.
    """

    evidence = {e: [] for e in target_emotions}

    for m in self_msgs:
        text = m.get("text", "") or ""
        if not text.strip():
            continue

        toks = tokenize(text)
        token_count = len(toks)
        hits = lexicon_hits(toks, nrc_lex)

        for emo in target_emotions:
            count = hits.get(emo, 0)
            if count > 0:
                evidence[emo].append({
                    "text": text,
                    "base": float(count),
                    "token_count": token_count,
                })

    # Display-only illustrative selection; does not change aggregate metrics.
    for emo in evidence:
        evidence[emo] = select_top_examples(evidence[emo], top_k=top_k)

    return evidence

def extract_emotion_quotes(self_msgs, seed=42, max_samples=5, min_words=4):
    import random
    random.seed(seed)

    TARGET_EMOTIONS = ["joy", "trust", "anticipation", "surprise"]
    evidence = {"emotion": {}}

    for emo in TARGET_EMOTIONS:
        hits = [
            m["text"]
            for m in self_msgs
            if m.get(f"emo_{emo}", 0) > 0 and m["word_count"] >= min_words
        ]

        total = len(hits)
        samples = random.sample(hits, min(max_samples, total)) if total else []

        evidence["emotion"][emo] = {
            "total": total,
            "samples": samples
        }

    return evidence

def extract_moral_evidence(self_msgs,moral_weight,pos_threshold=0.2,neg_threshold=-0.2,top_k=5,):
    """
    Extract representative moral-positive and moral-negative message examples.
    """

    evidence = {
        "moral_positive": [],
        "moral_negative": [],
    }

    if not moral_weight:
        return evidence

    for m in self_msgs:
        text = m.get("text", "") or ""
        if not text.strip():
            continue

        toks = tokenize(text)
        token_count = len(toks)
        score = 0.0

        for t in toks:
            if t in moral_weight:
                score += moral_weight[t]

        if score >= pos_threshold:
            evidence["moral_positive"].append({
                "text": text,
                "base": float(score),
                "token_count": token_count,
            })
        elif score <= neg_threshold:
            evidence["moral_negative"].append({
                "text": text,
                "base": abs(float(score)),
                "token_count": token_count,
            })

    # Display-only illustrative selection; does not change aggregate metrics.
    for k in evidence:
        evidence[k] = select_top_examples(evidence[k], top_k=top_k)

    return evidence

def extract_moral_quotes(self_msgs, seed=42, max_samples=5, min_words=4):
    import random
    random.seed(seed)

    pos = [
        m["text"] for m in self_msgs
        if m.get("moral_positive", 0) > 0 and m["word_count"] >= min_words
    ]
    neg = [
        m["text"] for m in self_msgs
        if m.get("moral_negative", 0) > 0 and m["word_count"] >= min_words
    ]

    return {
        "moral": {
            "positive": {
                "total": len(pos),
                "samples": random.sample(pos, min(max_samples, len(pos))) if pos else []
            },
            "negative": {
                "total": len(neg),
                "samples": random.sample(neg, min(max_samples, len(neg))) if neg else []
            }
        }
    }



def run_level_a_pipeline(chat_path, user_handle, safe_user, out_dir, storage_mode="disk"):
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

    other_speakers = get_top_other_speakers(msgs, user_handle, limit=4)
    label_map = build_user_label_map(user_handle, other_speakers)
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
        
        emo_hits = lexicon_hits(toks, nrc_lex)
        for emo, count in emo_hits.items():
            m[f"emo_{emo}"] = int(count)

        # Moral lexicon:
        m["moral_score"] = 0.0
        m["moral_positive"] = False
        m["moral_negative"] = False

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
        
        m["moral_score"] = score
        m["moral_positive"] = score > 0.2
        m["moral_negative"] = score < -0.2

        
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
    emotion_order = [
        "anger",
        "anticipation",
        "disgust",
        "fear",
        "joy",
        "sadness",
        "surprise",
        "trust",
        "positive",
        "negative",
    ] 
    ordered_emotion_norm = {label: emotion_norm.get(label, 0) for label in emotion_order}
    
    moral_norm = normalize_counter(moral_counts) if moral_counts else {}
    tone_valence = tone_from_nrc(emotion_norm)

    burst_ratio = (burst_hits / max(1, n_msgs))
    initiation_proxy = (long_gap_hits / max(1, n_msgs))

    mode = mode_scores(heur_counts)
    role = role_scores(mode, burst_ratio=burst_ratio, initiation_proxy=initiation_proxy)
    conf = confidence_band(n_msgs)

    plots = {
        "emotion": None,
        "moral": None,
        "valence": None,
        }

    if storage_mode == "disk":
        plots["emotion"] = {"type": "file", "file": "emotion_distribution.png"}

        if moral_norm:
            plots["moral"] = {"type": "file", "file": "moral_loading.png"}

        plots["valence"] = {"type": "file", "file": "valence_timeline.png"}

    else:  # memory mode␊
        plots["emotion"] = {"type": "data", "data": ordered_emotion_norm}
        plots["moral"] = {"type": "data", "data": moral_norm if moral_norm else None}
        
        plots["valence"] = {"type": "data", "data": list(valence_timeline)}


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

    prefix = safe_user

    metrics = {
        "safe_user": safe_user,
        "n_messages": n_msgs,
        "plots" : plots,
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
    else:
        files = {}

    return metrics












