import re
from collections import Counter, defaultdict
from emoji_semantics import EMOJI_SEMANTICS

TOKEN_RE = re.compile(r"[A-Za-z']+")

HEDGE_WORDS = {"maybe","perhaps","i think","i guess","kind of","sort of","possibly","might","could"}
CORRECTIVE_MARKERS = {"actually","no","wrong","not really","that’s not","you're mistaken","correction"}
AFFILIATIVE_MARKERS = {"thanks","thank you","appreciate","love","great point","haha","lol","😂","🤣"}
CHALLENGE_MARKERS = {"but","however","yet","hold on","wait","i disagree","no way","come on"}

def tokenize(text: str):
    return [t.lower() for t in TOKEN_RE.findall(text)]

def extract_emoji_valence(emojis):
    if not emojis:
        return 0.0, Counter()
    vals = []
    tags = Counter()
    for e in emojis:
        meta = EMOJI_SEMANTICS.get(e)
        if meta:
            vals.append(meta["valence"])
            tags[meta["tag"]] += 1
    return (sum(vals)/len(vals) if vals else 0.0), tags

def lexicon_hits(tokens, word_to_labels):
    # word_to_labels: dict[word]->set/list labels
    counts = Counter()
    for t in tokens:
        if t in word_to_labels:
            for lab in word_to_labels[t]:
                counts[lab] += 1
    return counts

def message_heuristics(text: str):
    t = text.lower()
    is_question = t.strip().endswith("?")
    hedge = any(h in t for h in HEDGE_WORDS)
    corrective = any(m in t for m in CORRECTIVE_MARKERS)
    affiliative = any(m in t for m in AFFILIATIVE_MARKERS)
    challenge = any(m in t for m in CHALLENGE_MARKERS)
    return {
        "is_question": is_question,
        "hedge": hedge,
        "corrective": corrective,
        "affiliative": affiliative,
        "challenge": challenge
    }


