# Codex Coordination Principles

The current exposed Codex tool schema and runtime instructions are always the authority for tool interfaces. Local skills do not duplicate dynamic API contracts, fork parameters, model allowlists, effort rules, configuration keys or fixed wait timeouts.

Use agents only when focused independent work or risk-based review makes them useful. Prefer minimal necessary context and isolation where it prevents shared-state conflicts. Give each task clear scope, authority, acceptance and expected evidence.

Continue useful local work while agents run. When genuinely dependent on a result, use the current runtime's bounded event-wait mechanism. Do not use meaningless short polling or status checks solely for liveness, and do not list agents automatically after every wait.

An agent's success report is a claim. Inspect the actual diff and relevant verification before relying on it. Reuse valid evidence when relevant inputs have not changed. Respect current user authorization, project constraints and runtime limits.
