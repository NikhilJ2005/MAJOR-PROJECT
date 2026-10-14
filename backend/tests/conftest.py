import os
import tempfile

# Isolate all run data / checkpoints before any agent module reads settings.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="vibestack-test-")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ["ACCESS_CODE"] = ""

import pytest

from agent import llm as llm_mod
from agent.fake_llm import BLOG_SPEC, FakeLLM  # noqa: F401


@pytest.fixture
def fake_llm():
    fake = FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)


@pytest.fixture
def offline():
    llm_mod.set_llm(None)
    yield
