from app.config import Settings


def test_bot_mode_defaults_to_faq():
    assert Settings().BOT_MODE == "faq"


def test_bot_mode_reads_env(monkeypatch):
    monkeypatch.setenv("BOT_MODE", "generative")
    assert Settings().BOT_MODE == "generative"


def test_faq_mode_needs_no_ai_keys():
    # FAQ mode must construct cleanly with every AI key blank.
    s = Settings(OPENROUTER_API_KEY="", EMBEDDING_API_KEY="", LANGFUSE_PUBLIC_KEY="")
    assert s.BOT_MODE == "faq"
    assert s.OPENROUTER_API_KEY == ""


def test_agent_portal_settings_default_to_empty():
    s = Settings()
    assert s.AGENT_PASSWORD == ""
    assert s.AGENT_SESSION_SECRET == ""


def test_agent_portal_settings_read_env(monkeypatch):
    monkeypatch.setenv("AGENT_PASSWORD", "hunter2")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "s3cret")
    s = Settings()
    assert s.AGENT_PASSWORD == "hunter2"
    assert s.AGENT_SESSION_SECRET == "s3cret"
