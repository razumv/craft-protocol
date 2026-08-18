// SPDX-License-Identifier: Apache-2.0

import type { LifecycleState, ProjectStatus, TrackerIssueSnapshot } from "./domain";

const nextPoint: Record<LifecycleState, string> = {
  ready: "atomic claim",
  claimed: "session start",
  running: "pull request",
  "pr-open": "review or merge",
  review: "review verdict",
  "owner-gate": "exact owner decision",
  merged: "deployment or completion",
  deployed: "deployment readback",
  done: "complete",
  blocked: "blocker resolution",
  "retry-wait": "bounded retry due time",
  failed: "owner handoff",
  cancelled: "none",
  "preservation-unknown": "preservation proof",
};

export function projectStatus(snapshot: TrackerIssueSnapshot): ProjectStatus {
  const events = snapshot.events;
  const ownerGateId = snapshot.evidence.ownerGateId ?? null;
  return {
    projectId: snapshot.contract.projectId,
    issueId: snapshot.issue.id,
    issueIdentifier: snapshot.issue.identifier,
    objective: snapshot.contract.goal,
    state: snapshot.issue.state,
    branchUrl: snapshot.evidence.branchUrl ?? null,
    prUrl: snapshot.evidence.prUrl ?? null,
    deploymentUrl: snapshot.evidence.deploymentUrl ?? null,
    lastMaterialEvent: events.length ? { ...events[events.length - 1] } : null,
    blocker: snapshot.evidence.blocker
      ?? (snapshot.issue.blockedBy.map((item) => item.identifier ?? item.id ?? "unknown blocker").join(", ") || null),
    nextCompletionPoint: nextPoint[snapshot.issue.state],
    ownerGate: ownerGateId ? { id: ownerGateId, command: `APPROVE ${ownerGateId}` } : null,
  };
}
