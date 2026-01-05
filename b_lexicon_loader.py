"""
b_lexicon_loader.py

Lexicon loaders for ReflectIQ Level A.

Supports:
1) NRC Emotion Lexicon (word -> set(emotions))
2) Categorical moral/value lexicons (word -> set(categories))
3) Weighted moral/value lexicons (word -> float score)

All loaders are:
- deterministic
- transparent
- publication-safe
"""

from collections import defaultdict
import pandas as pd


# ============================================================
# NRC EMOTION LEXICON
# ============================================================

def load_nrc_emotion_lexicon(path: str):
    """
    Load NRC Emotion Lexicon.

    Expected format:
        word <TAB> emotion <TAB> 0/1

    Returns:
        dict[word] -> set(emotions)
    """
    word_to_emotions = defaultdict(set)

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) != 3:
                continue

            word, emotion, flag = parts
            if flag == "1":
                word_to_emotions[word.lower()].add(emotion.lower())

    return dict(word_to_emotions)


# ============================================================
# CATEGORICAL MORAL / VALUE LEXICON
# ============================================================

def load_categorical_moral_lexicon_tsv(path: str):
    """
    Load a categorical moral/value lexicon from TSV.

    Supported formats:
    1) Header with 'word' / 'term' / 'token' and 'category'
    2) Header with 'word' and multiple category columns (binary or weighted)
    3) Headerless TSV where column 0 is the word

    Returns:
        dict[word] -> set(categories)
    """
    df = pd.read_csv(path, sep="\t", header=0)

    # Normalize column names
    colmap = {c.lower(): c for c in df.columns}

    # Identify word column
    word_col = None
    for candidate in ["word", "term", "token", "lemma"]:
        if candidate in colmap:
            word_col = colmap[candidate]
            break

    # If no explicit word column, assume first column
    if word_col is None:
        word_col = df.columns[0]

    out = defaultdict(set)

    # Case 1: explicit category column
    if "category" in colmap:
        cat_col = colmap["category"]
        for _, r in df.iterrows():
            w = str(r[word_col]).strip().lower()
            c = str(r[cat_col]).strip().lower()
            if w and c:
                out[w].add(c)
        return dict(out)

    # Case 2: multiple category columns
    cat_cols = [c for c in df.columns if c != word_col]

    for _, r in df.iterrows():
        w = str(r[word_col]).strip().lower()
        for c in cat_cols:
            val = r[c]
            if isinstance(val, (int, float)) and val > 0:
                out[w].add(c.lower())

    return dict(out)


# ============================================================
# WEIGHTED MORAL / VALUE LEXICON
# ============================================================

def load_weighted_moral_lexicon_tsv(path: str):
    """
    Load a weighted moral/value lexicon.

    Expected format (NO HEADER):
        word <TAB> score

    Example:
        freedom     1.27
        authority  -0.83

    Returns:
        dict[word] -> float
    """
    lex = {}

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            parts = line.split("\t")
            if len(parts) != 2:
                continue

            word, val = parts
            try:
                lex[word.lower()] = float(val)
            except ValueError:
                continue

    return lex


# ============================================================
# OPTIONAL TEST HARNESS (COMMENT OUT IN PRODUCTION)
# ============================================================

if __name__ == "__main__":
    print("Testing lexicon loaders...\n")

    try:
        nrc = load_nrc_emotion_lexicon(
            "lexicons/NRC-Emotion-Lexicon-Wordlevel-v0.92.txt"
        )
        print(f"NRC loaded: {len(nrc)} words")
    except Exception as e:
        print("NRC load failed:", e)

    try:
        weighted = load_weighted_moral_lexicon_tsv(
            "lexicons/liberty_moral_lexicon.tsv"
        )
        sample = next(iter(weighted.items()))
        print(f"Weighted moral lexicon loaded: {len(weighted)} words")
        print(f"Sample entry: {sample}")
    except Exception as e:
        print("Weighted moral load failed:", e)
