WYS — What You Say

WYS (What You Say) is a text-analysis tool that examines conversational data to identify linguistic, emotional, and interactional patterns at both descriptive and interpretive levels.
WYS is designed for researchers, analysts, and reflective users who want insight into how communication unfolds over time, without diagnosing intent, personality, or mental state.

The system operates in two stages:
Level A — quantitative, deterministic analysis
Level B — structured, interpretive analysis built on Level A outputs

Overview

WYS analyzes exported chat logs (e.g. WhatsApp .txt files) and produces structured reports focused on:
-communication style
-emotional signaling
-interaction dynamics
-temporal patterns

The system is explicitly non-diagnostic and non-prescriptive. Outputs describe observable language patterns rather than making claims about intent, character, or psychology.

Project Structure
WYS/
├── cli.py                  # Command-line interface (Level A + optional Level B)
├── a_LevelA_IO.py           # Level A processing logic
├── levelB_prompt.py         # Public Level B prompt template
├── level_B/
│   └── levelB_runner.py     # Level B execution logic
├── results/
│   └── <speaker>/           # Analysis outputs
└── README.md

Levels of Analysis
Level A — Descriptive Metrics
Level A performs deterministic analysis on conversational data, including:
-message counts and proportions
-speaker participation balance
-temporal distribution of messages
-lexical and stylistic metrics

Level A produces structured files written to: results/<speaker>/

These outputs form the required input for Level B.
Level B — Interpretive Analysis
Level B generates a structured, descriptive interpretation of the patterns identified in Level A.
It examines:
-communication style tendencies
-emotional signaling through language
-interpersonal positioning within the group
-changes and stability over time

Important notes on Level B:
-Level B outputs are descriptive, not advisory
-No psychological diagnosis or intent attribution is performed
-The analysis focuses on patterns, not individuals as persons

About the Level B Prompt
The file levelB_prompt.py included in this repository defines the public structural template for Level B analysis.
It specifies:
-analytical scope
-section structure
-ethical constraints
-formatting expectations

This file is not the complete runtime prompt.

The deployed system assembles the final prompt at runtime by combining this public template with additional layers that manage:
-tone moderation and depersonalization
-safety and non-intrusiveness
-uncertainty calibration
-consistency across edge cases

This separation keeps the analytical framework transparent while allowing the system’s behavior to evolve safely.

Command-Line Interface (CLI)
WYS includes a command-line interface for local testing and beta evaluation.

Requirements
Python 3.9+, WhatsApp chat export (.txt), (Optional) OpenAI API key for Level B

Running Level A via CLI
Basic usage:
python cli.py --input chat.txt --handle "user handle"
For phone numbers (spaces allowed):
python cli.py --input chat.txt --handle +XX NNNN YYYYY

The CLI:
-normalizes speaker labels (names, emojis, phone numbers)
-computes message contribution percentage
-warns if contribution is below reliability threshold
-runs Level A when sufficient data is present

Reliability Threshold
By default, Level A requires the selected speaker to contribute at least 2% of total messages.
If the speaker is found but below threshold, the CLI reports:
-number of messages
-percentage of the chat
-a warning about reliability

You may override this:
python cli.py --input chat.txt --handle "user handle" --min-pct 0.1

Running Level B
Level B is optional and requires an API key.
Before running Level B, set your environment variable:
-Windows (PowerShell, once):
-setx OPENAI_API_KEY "sk-xxxxxxxx"
macOS / Linux: export OPENAI_API_KEY="sk-xxxxxxxx"

After Level A completes, the CLI will prompt:
Proceed to Level B? (y/n)
If confirmed, Level B will run using the Level A outputs.

Output Location
All outputs are written to:
results/<speaker>/

This directory contains:
Level A metrics and intermediate files
Level B interpretive report (if run)

Intended Use
WYS is intended for:
-exploratory analysis
-reflective research
-communication pattern studies

It is not intended for:
-psychological diagnosis
-individual evaluation
-prescriptive or therapeutic use

Beta Testing Notes
This repository includes experimental features under active development.
Beta testers are encouraged to provide feedback on:
-clarity of outputs
-perceived fairness and neutrality
-usefulness of structure and framing
-error messaging and usability

Feedback on output quality is more valuable than feedback on internal implementation details.
rayan@living-literature.org

License / Ethics

Use responsibly.
Analyze data you have the right to analyze.
Do not apply results to real-world decision-making without appropriate context and safeguards.
