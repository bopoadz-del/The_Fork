# Posture model enable rules

`FORK_POSTURE_MODEL` is `off`, `shadow`, or `on`.

- `off` — current final-answer path only. This is the default.
- `shadow` — run the current path and the posture path. Serve the current path. Log both answers and the grader verdicts.
- `on` — serve the posture path.

The shipped map is `configs/posture/flag.json`. A project entry wins over the env var. Projects with no entry use `FORK_POSTURE_MODEL` when it is set, otherwise `default` (`off`).

`master_corpus` is `shadow`. No project is `on`.

Do not set any project to `on`, and do not set the env var to `on`, until a `master_corpus` shadow window has been open for 48 hours and every line below is true. If one line fails, stay on `shadow` and put that failing number first in the report.

- fact-leak is zero
- citation validity is 100%
- refusal/qualification is at least the current path
- latency p95 is within +500 ms of the current path

The posture model is sampled only when retrieval context is attached. The system prompt is the file named by `system_prompt_file` in cerebrum-slm `configs/serve/posture-epoch2.json` (client pin `4e167a7`). It is not edited per case. Facts come from the retrieval already attached to the turn and from formula-tool payloads already on the turn. The grounding gate decides. The frontier model stays the escalation target. Every escalation is logged.

Sampling needs the cerebrum-slm client and `TINKER_API_KEY`. If the key is absent the posture path escalates and the current path is what the user sees. Shadow does not change the served answer.
