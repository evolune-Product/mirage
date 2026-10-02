import os

# Keep tests hermetic: no local LLM moderation classifier unless a test opts in.
os.environ.setdefault("MIRAGE_MODERATION_OLLAMA_MODEL", "")
