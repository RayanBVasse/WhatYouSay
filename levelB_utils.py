import json
import csv
from pathlib import Path

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_csv_summary(path: Path) -> dict:
    summary = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            summary[row["token"]] = int(row["count"])
    return summary

def assert_exists(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
