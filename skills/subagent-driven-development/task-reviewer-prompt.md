# Optional Independent Review Brief

Use only when review is justified by risk, project requirements or the user's request. Current tool schema controls invocation.

Provide the requirement, exact baseline and diff/files, affected interfaces, known concerns and existing verification evidence. Independently inspect relevant source and confirm whether the patch meets the requirement. Do not assume the implementer's conclusion is proof.

Reuse verification that still covers unchanged inputs. Run additional checks only for a concrete gap or risk; do not mechanically rerun a full suite. Focus findings on actionable defects, evidence, location and impact, not speculative expansion.

Return findings and limitations concisely. Do not require per-task review, task-specific commits or mandatory report files merely because a plan exists. The controller decides how to resolve findings and applies the two-round circuit breaker.
