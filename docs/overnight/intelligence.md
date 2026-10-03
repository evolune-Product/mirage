# Intelligence + perception (overnight, agent "brain")

Status notes written incrementally (session may be cut). Final report sections are filled in at the end.

## Built so far
- `backend/evals/` : conversation-eval harness (`run_evals.py`), 4 realistic knowledge docs (`evals/docs/*.md`), datasets
  (`datasets.py`: 43 doc Q/A, 12 out-of-scope, 6 guardrail, 8 es/hi, 7 tool, 8 judge, 29 paraphrase-retrieval),
  retrieval eval (`retrieval_eval.py`), VLM eval (`vlm_eval.py`).
- `backend/app/knowledge.py`: section-aware chunking with heading path, BM25 with stop-words/stemming, dense+BM25-bonus
  ranking (`rank`), grounded `format_context`, in-memory `build_index`.
- `backend/app/llm_backends.py`: `GroundedLLM` (reminder on user turn + history trim), `ground_session`, `options=` on chat.
- `backend/app/perception/` + `models_perception.py` + `routers/perception_api.py`: the agent sees the user (details below).
- realtime.py hook (3 small edits: attach perception + grounding after Session creation, close on teardown).
