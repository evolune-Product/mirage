import os

# Keep tests hermetic: no local LLM moderation classifier unless a test opts in.
os.environ.setdefault("MIRAGE_MODERATION_OLLAMA_MODEL", "")
os.environ.setdefault("MIRAGE_PRELOAD", "0")  # no model loading at app startup in tests
os.environ.setdefault("MIRAGE_MAX_CONVOS", "0")  # admission control off by default in tests (tests/test_capacity*.py opt in)
# Legacy suites open sockets with ?api_key= and many connections from one "IP": keep that working; tests/test_ws_security.py
# turns the real defaults back on explicitly.
os.environ.setdefault("MIRAGE_ALLOW_KEY_IN_URL", "1")
os.environ.setdefault("MIRAGE_WS_CONNECT_IP", "100000/60")
os.environ.setdefault("MIRAGE_WS_CONNECT_ACCOUNT", "100000/60")
os.environ.setdefault("MIRAGE_WS_AUTH_FAIL_IP", "100000/60")
os.environ.setdefault("MIRAGE_WS_MAX_SESSIONS", "0")
