import os

# Keep tests hermetic: no local LLM moderation classifier unless a test opts in.
os.environ.setdefault("MIRAGE_MODERATION_OLLAMA_MODEL", "")
os.environ.setdefault("MIRAGE_PRELOAD", "0")  # no model loading at app startup in tests
os.environ.setdefault("MIRAGE_MAX_CONVOS", "0")  # admission control off by default in tests (tests/test_capacity*.py opt in)
