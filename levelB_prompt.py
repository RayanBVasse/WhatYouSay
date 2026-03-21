import json
from datetime import datetime

def build_levelB_prompt(*, anon_text: str, self_text: str, metrics: dict, evidence: dict, speaker_alias: str) -> str:
    """
    Returns a single prompt that forces structured JSON output with 5 fixed sections.
    speaker_alias = safe_user (sanitized handle) e.g. "Marius"
    """

    # Keep tokens sane: cap text if needed (you can tune these)
    anon_cap = 120_000
    self_cap = 80_000
    anon_text = anon_text[-anon_cap:]
    self_text = self_text[-self_cap:]

    schema = {
        "report_version": "B-1.0",
        "generated_utc": "ISO-8601",
        "speaker_alias": speaker_alias,
        "word_count_target": "800-1000",
        "sections": [
            {
                "id": 1,
                "title": "Your role in the group (in context)",
                "body": "4–8 short paragraphs, letter tone, precise. Include 2–3 anonymous comparisons (e.g., Member A, Member B). No extremes or insults.",
                "highlights": ["3–6 bullets, short"],
            },
            {
                "id": 2,
                "title": "Emotional tone & pressure points",
                "body": "Explain what shows up, when it shifts, what tends to trigger it. Compare gently to 1–2 anonymous members.",
                "highlights": ["3–6 bullets"],
            },
            {
                "id": 3,
                "title": "Ideas, complexity, and conversational style",
                "body": "Comment on richness, pacing, clarity, how you frame topics. Include 1–2 concrete examples (short quotes) from SELF text.",
                "highlights": ["3–6 bullets"],
            },
            {
                "id": 4,
                "title": "Values / moral framing (light touch)",
                "body": "Interpret moral-loading directionally without moralizing. Compare gently to group distribution.",
                "highlights": ["3–6 bullets"],
            },
            {
                "id": 5,
                "title": "Practical upgrades (small, doable, non-cringy)",
                "body": "Actionable tweaks: phrasing, timing, questions, warmth, boundaries. No therapy voice. No diagnosing.",
                "highlights": ["3–8 bullets"],
            },
        ],
        "closing_note": "2–4 lines, supportive but not cheesy."
    }

    # We embed the schema in the prompt and demand pure JSON output.
    prompt = f"""
You are an intelligent human observer. Write a short & sharp reflective letter-style report (total 800–1000 words)
about how the speaker shows up in a WhatsApp group chat, using the inputs below.

Hard rules:
- Output MUST be valid JSON only. No markdown. No extra text.
- Follow this exact JSON schema shape (same keys). Fill values.
- Use anonymous labels for other members: Member A, Member B, Member C (do not reuse the same labels in every section if possible).
- Do not claim certainty. Avoid extreme judgments. Keep it constructive.
- Use at most 3 short direct quotes (<= 20 words each) from SELF text.
- Treat "confidence level" as model confidence in signal stability, NOT self-confidence.

JSON SCHEMA:
{json.dumps(schema, indent=2)}

INPUTS:
SPEAKER_ALIAS: {speaker_alias}

LEVEL_A_METRICS_JSON:
{json.dumps(metrics, indent=2)[:40_000]}

EVIDENCE_JSON (may be empty):
{json.dumps(evidence, indent=2)[:20_000]}

SELF_ONLY_TEXT (speaker only, may be long):
{self_text}

ANONYMIZED_GROUP_TEXT (all members anonymized; speaker identity is not tagged):
{anon_text}
""".strip()

    return prompt
