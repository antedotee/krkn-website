# `.docs-sync/` — krkn-docs-sync harness

Auto-syncs docs at [krkn-chaos.dev](https://krkn-chaos.dev) when upstream code in any of the krkn-chaos repos changes.

## Status

- [x] Slice 0a: docs digest builder (this directory)
- [ ] Slice 0.5: migration markers
- [ ] Slice 0b: upstream digest builder
- [ ] Slice 0c: harness skeleton + relevance gate
- [ ] Slice 1+: extractors, prose generation, harvest, audits

See [`tasks/plan.md`](../../krkn/tasks/plan.md) and [`tasks/todo.md`](../../krkn/tasks/todo.md) for full design (planning artifacts kept local — not in git).

## Layout

Flat module structure adopted from [redhat-community-ai-tools/code-to-docs](https://github.com/redhat-community-ai-tools/code-to-docs/tree/main/src):

```
.docs-sync/
├── digest/                    # standalone CLI scripts run by GH Actions
│   └── build_docs_digest.py
├── tests/                     # pytest suite
├── conftest.py                # adds .docs-sync/ to sys.path
└── (future: config.py, github_ops.py, discovery.py, generation.py,
   doc_index.py, security_utils.py, utils.py, orchestrator.py)
```

## Running locally

```bash
# Setup once
python3 -m venv .docs-sync/.venv
.docs-sync/.venv/bin/pip install pytest pyyaml

# Run tests
.docs-sync/.venv/bin/python -m pytest .docs-sync/tests/ -v

# Build the docs digest
.docs-sync/.venv/bin/python .docs-sync/digest/build_docs_digest.py
```

## Adopted patterns from Red Hat code-to-docs

1. **OpenAI-compatible LLM client** (Pattern 1) — use the `openai` SDK pointed at any compatible endpoint via `MODEL_API_BASE`. Defaults to Gemini's OpenAI-compat endpoint, reads `GEMINI_API_KEY` directly.
2. **Modular flat layout** (Pattern 2) — single-responsibility files, no nested module dirs.
3. **`security_utils.py`** (Pattern 3) — `sanitize_output` strips secrets from logs; `run_command_safe` wraps subprocess with redaction; `validate_path` rejects directory traversal.

We deliberately do NOT adopt their LLM-generated folder indexes, comment-trigger orchestration, or Docker action wrapper.
