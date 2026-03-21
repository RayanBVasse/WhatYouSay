# cli.py — clean, minimal, from scratch

import argparse
import os
import re
import unicodedata
from pathlib import Path
from collections import Counter
import subprocess

from a_LevelA_IO import load_chat_from_file, run_level_a_pipeline

BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"


# -------------------------
# Normalization utilities
# -------------------------

def normalize_speaker(raw: str) -> str:
    """
    Normalize speaker labels so humans can type reasonable handles.
    """
    if not raw:
        return ""

    s = unicodedata.normalize("NFKC", raw).strip()

    # remove WhatsApp tilde
    if s.startswith("~"):
        s = s[1:].strip()

    # collapse whitespace
    s = " ".join(s.split())

    # normalize phone numbers
    digits = re.sub(r"\D", "", s)
    if len(digits) >= 8:
        return f"phone_{digits[-6:]}"

    return s.lower()


def canonical_user_id(raw: str) -> str:
    raw = raw.strip().lower()
    return "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_")

def run_level_b(safe_user: str):
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "\n❌ OPENAI_API_KEY not set.\n"
            "Level B requires an API key.\n"
        )

    print("\nRunning Level B...\n")

    result = subprocess.run(
        ["python", "level_B/levelB_runner.py", safe_user],
        cwd=BASE_DIR,
        text=True,
    )

    if result.returncode != 0:
        raise SystemExit("\n❌ Level B failed.")
# -------------------------
# CLI
# -------------------------

def main():
    parser = argparse.ArgumentParser("ReflectIQ – Level A CLI")
    parser.add_argument("--input", required=True, help="Path to WhatsApp chat .txt")
    parser.add_argument("--handle", required=True, nargs="+", help="Speaker name or phone number (spaces allowed)")
    parser.add_argument("--min-pct", type=float, default=2.0,help="Minimum %% of messages required for reliable analysis (default 2.0)")
    args = parser.parse_args()

    chat_path = Path(args.input)
    if not chat_path.exists():
        raise SystemExit(f"Input file not found: {chat_path}")

    # Load messages
    msgs = load_chat_from_file(str(chat_path))
    total_msgs = len(msgs)

    # Count speakers (raw)
    raw_counts = Counter(m["speaker"] for m in msgs)

    # Build normalized maps
    norm_counts = {}
    norm_to_raw = {}

    for raw, count in raw_counts.items():
        norm = normalize_speaker(raw)
        norm_counts[norm] = norm_counts.get(norm, 0) + count
        norm_to_raw.setdefault(norm, raw)

    # Normalize user handle
    raw_handle_input = " ".join(args.handle)
    user_norm = normalize_speaker(raw_handle_input)

    # Case 1: speaker does not exist
    if user_norm not in norm_counts:
        print("\n❌ Speaker not found in this chat.")
        print("\nDetected speakers:")
        for s in sorted(norm_counts.keys()):
            print(f"  - {s}")
        raise SystemExit()

    # Compute percentage
    msg_count = norm_counts[user_norm]
    pct = (msg_count / total_msgs) * 100

    # Case 2: speaker exists but below threshold
    if pct < args.min_pct:
        raise SystemExit(
            f"\n⚠️ Speaker found, but contribution is too low.\n"
            f"Messages: {msg_count} ({pct:.2f}% of chat)\n\n"
            f"Results below {args.min_pct}% may not be statistically reliable.\n"
            f"You can lower --min-pct if you want to proceed anyway.\n"
        )

    # Case 3: proceed with Level A
    raw_handle = norm_to_raw[user_norm]
    safe_user = canonical_user_id(user_norm)

    out_dir = RESULTS_DIR / safe_user
    out_dir.mkdir(parents=True, exist_ok=True)

    run_level_a_pipeline(
        chat_path=str(chat_path),
        user_handle=raw_handle,   # IMPORTANT: raw label for Level A
        safe_user=safe_user,
        out_dir=str(out_dir),
    )

    print("\n✔ Level A complete")
    print(f"Speaker: {raw_handle}")
    print(f"Messages: {msg_count} ({pct:.2f}%)")
    print(f"Results written to: {out_dir}")

    print(
        "\nLevel B details:\n"
        "• Requires OPENAI_API_KEY\n"
        "• Estimated usage: ~100k–200k tokens (20-45 seconds)\n"
        "• Estimated cost: Less than $1.00\n"
    )


    choice = input("Proceed to Level B? (y/n): ").strip().lower()
    if choice == "y":
        run_level_b(safe_user)
        print("\n✔ Level B complete")
    else:
        print("\nSkipping Level B.")


if __name__ == "__main__":
    main()
