# pmpa — Documentation Conventions

1. **Accuracy over completeness** — Only document verified behavior. Cite `file:line`. Never invent APIs, flags, configs, or metrics.
2. **Single source of truth** — Each fact lives in exactly one doc. Cross-reference instead of duplicating.
3. **Concise & skimmable** — Tables over prose. Short sections. Descriptive headers. No filler.
4. **Update, don't append** — Revise existing docs in place. Delete stale content.
5. **Audience: engineers** — Assume domain knowledge but not codebase familiarity.
6. **session-log.md updates are scoped to your work** — Only add or update sections related to the current conversation's work. Read the existing session-log first and preserve all unrelated content.
7. **Archive, don't delete** — Completed plans and superseded designs move to `archive/`, not the trash.
8. **Stable doc names** — Don't rename docs; downstream links and memory point to them.
9. **Date format** — Absolute dates (`YYYY-MM-DD`), never relative.
10. **No secrets** — Never commit credentials or API keys.
11. **`repo/` is upstream** — `pmpa/repo` is a clean clone of `hsh754/PMPA`. Nothing private goes in it; reproduction deltas are patches or wrappers under `pmpa/bin`, recorded here.
