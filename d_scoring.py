import math
from collections import Counter, defaultdict

# NRC emotions commonly include: anger, anticipation, disgust, fear, joy, sadness, surprise, trust, positive, negative

def normalize_counter(c: Counter):
    total = sum(c.values()) or 1
    return {k: v/total for k, v in c.items()}

def tone_from_nrc(emotion_norm: dict):
    """
    Simple, explainable valence proxy.
    """
    pos = emotion_norm.get("positive", 0) + emotion_norm.get("joy", 0) + emotion_norm.get("trust", 0)
    neg = emotion_norm.get("negative", 0) + emotion_norm.get("sadness", 0) + emotion_norm.get("anger", 0) + emotion_norm.get("fear", 0) + emotion_norm.get("disgust", 0)
    valence = pos - neg
    return valence

def mode_scores(heur_counts: dict):
    """
    Map heuristic indicators to soft comm modes.
    """
    total = heur_counts.get("total_msgs", 1)

    exploratory = (heur_counts.get("is_question",0) + heur_counts.get("hedge",0)) / total
    corrective   = (heur_counts.get("corrective",0) + heur_counts.get("challenge",0)) / total
    affiliative  = (heur_counts.get("affiliative",0)) / total

    # Assertive as residual-ish: statements + challenges without hedges
    assertive = max(0.0, 1.0 - (exploratory + affiliative)/2.0) * 0.5 + corrective * 0.5
    return {
        "exploratory": exploratory,
        "corrective": corrective,
        "affiliative": affiliative,
        "assertive": assertive
    }

def role_scores(mode: dict, burst_ratio: float, initiation_proxy: float):
    """
    Very lightweight role inference.
    - initiator: more initiation_proxy (first-after-long-gap)
    - responder: lower initiation, higher burstiness
    - stabilizer: affiliative high
    - challenger: corrective high
    """
    initiator  = initiation_proxy
    responder  = burst_ratio * 0.7 + (1-initiation_proxy)*0.3
    stabilizer = mode.get("affiliative",0)
    challenger = mode.get("corrective",0)

    return {"initiator": initiator, "responder": responder, "stabilizer": stabilizer, "challenger": challenger}

def confidence_band(n_msgs: int):
    """
    Simple confidence indicator based on sample size.
    """
    if n_msgs >= 500: return "high"
    if n_msgs >= 150: return "medium"
    return "low"

