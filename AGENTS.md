# Working instructions

Read [docs/plan.md](docs/plan.md) before implementation. The owner's current
instructions govern; the plan is an agent-written proposal, not a transcript.

- Start from the selected source captures and the answers the owner needs.
  Choose the representation from their meaning. Existing TechDeals code, schema,
  briefs, tests, issues, and module boundaries are not inherited requirements.
- Build one executable capture-to-query path in small, complete slices. Show
  the actual stored answer and its source before expanding the scope.
- Carry forward requirements, not the old implementation. Import previous code
  or design documents only when the owner explicitly requests that reuse.
  Existing raw source captures may be read as evidence after inspecting them.
- Keep verification to concrete expected answers, negative cases, and focused
  checks of the working path. Do not introduce scoring, confidence numbers,
  rankings, evaluation frameworks, or generated score reports for this spike.
- Use batched free models. Keep model selection in committed configuration and
  credentials in Doppler-injected process environment; no automatic .env loading.
- Use existing Neon infrastructure with an isolated branch for development.
  Keep production and the existing cluster unchanged.
- Keep the plan current with the selected behavior. Put review discussion on
  its PR and ongoing task tracking in GitHub issues; keep repository documents
  focused on purpose, behavior, and running the implementation.
- When delegating, use fork_turns: "none" and give each agent a self-contained
  task with paths, scope, and expected output. Delegate only within the active
  authorization.
