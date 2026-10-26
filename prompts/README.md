# Judge prompts

Both prompts are the ones released by the Full-Duplex-Bench authors
(https://github.com/DanielLin94144/Full-Duplex-Bench, `v1_v1.5/evaluation/`), copied verbatim:

- `v1_interruption_rating.txt`: system prompt from `eval_user_interruption.py` (0–5 relevance of the reply to the interrupting turn).
- `v15_behaviour.txt`: `instruction/behavior.txt` (C_RESPOND / C_RESUME / C_UNCERTAIN_HANDLING / C_UNKNOWN).

The user message formats are also copied from the authors' code (see `fdbench/score/judge.py`).
