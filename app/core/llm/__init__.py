"""LLM serving backends. The chat block currently calls Ollama and
llama.cpp directly; this package adds a Tinker-hosted-LoRA-adapter path
gated behind a feature flag so the grounded adapter can be wired into
production with single-flag rollback."""
