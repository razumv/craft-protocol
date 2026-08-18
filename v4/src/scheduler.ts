// SPDX-License-Identifier: Apache-2.0

import {
  FakeCraftAdapter,
  FakeGitHubAdapter,
  FakeWorkspaceAdapter,
} from "./adapters";
import type { Claim, ProjectStatus, RunIdentity, TrackerIssueSnapshot, WorkflowConfig } from "./domain";
import { IdentityFactory } from "./identity";
import { ModelPolicy, RiskPolicy } from "./policy";
import { projectStatus } from "./status";

export interface Clock {
  nowMs(): number;
}

export class ManualClock implements Clock {
  constructor(private value = 0) {}
  nowMs(): number { return this.value; }
  advance(ms: number): void {
    if (!Number.isFinite(ms) || ms < 0) throw new Error("clock advance must be non-negative");
    this.value += ms;
  }
}

export type CrashPoint = "after-claim" | "after-workspace" | "after-session";
export class SimulatedCrash extends Error {
  constructor(readonly point: CrashPoint) {
    super(`simulated scheduler crash ${point}`);
  }
}

export interface SchedulerAdapters {
  github: FakeGitHubAdapter;
  craft: FakeCraftAdapter;
  workspaces: FakeWorkspaceAdapter;
}

export class DeterministicScheduler {
  readonly #identity: IdentityFactory;
  readonly #models: ModelPolicy;
  readonly #risk: RiskPolicy;

  constructor(
    readonly config: WorkflowConfig,
    readonly adapters: SchedulerAdapters,
    readonly clock: Clock,
  ) {
    if (config.scheduler.wipLimit !== 1) throw new Error("v4 increment 1 requires WIP=1");
    this.#identity = new IdentityFactory(config.workspace.root);
    this.#models = new ModelPolicy(config.model);
    this.#risk = new RiskPolicy(config.verification);
  }

  async tick(crashAfter?: CrashPoint): Promise<void> {
    await this.reconcile(crashAfter);
    if (this.adapters.github.activeClaims().length >= this.config.scheduler.wipLimit) return;

    const candidates = await this.adapters.github.fetchIssuesByStates(["ready", "retry-wait"]);
    for (const candidate of candidates.sort(compareForDispatch)) {
      if (!this.dispatchable(candidate)) continue;
      this.#models.assertAllowed(candidate.contract.modelProfile);
      this.#risk.budgetFor(candidate.contract);
      const attempt = candidate.retry?.attempt ?? 1;
      const claim = this.#identity.claimFor(
        candidate.issue,
        attempt,
        candidate.version,
        candidate.baseSha,
        { ...this.config.model, defaultProfile: candidate.contract.modelProfile },
        this.clock.nowMs(),
        this.config.scheduler.claimTtlMs,
      );
      const claimed = this.adapters.github.tryClaim(
        candidate.issue.id,
        candidate.version,
        claim,
        this.clock.nowMs(),
      );
      if (!claimed) continue;
      this.crashIf("after-claim", crashAfter);
      await this.startClaim(claimed, crashAfter);
      return;
    }
  }

  status(issueId: string): ProjectStatus {
    return projectStatus(this.adapters.github.get(issueId));
  }

  private async reconcile(crashAfter?: CrashPoint): Promise<void> {
    const now = this.clock.nowMs();
    for (const snapshot of this.adapters.github.activeClaims().sort((a, b) => a.issue.id.localeCompare(b.issue.id))) {
      const claim = snapshot.claim!;
      if (!this.identityMatches(snapshot, claim)) {
        this.adapters.github.transition(snapshot.issue.id, "preservation-unknown", now, {
          fence: claim.fence,
          message: "claim identity no longer matches deterministic workspace/session identity",
        });
        continue;
      }
      try {
        this.#models.assertAllowed(claim.modelProfile);
      } catch (error) {
        this.adapters.github.failClaim(claim.fence, "policy", String(error), now, this.config.scheduler);
        continue;
      }

      if (snapshot.issue.state === "claimed") {
        const session = this.adapters.craft.get(claim.sessionId);
        if (now >= claim.expiresAtMs && !session) {
          this.adapters.github.failClaim(
            claim.fence,
            "runtime",
            "claim expired before its Craft session was durable",
            now,
            this.config.scheduler,
          );
          continue;
        }
        await this.startClaim(snapshot, crashAfter);
        continue;
      }
      if (snapshot.issue.state !== "running") {
        this.adapters.github.heartbeat(claim.fence, now, this.config.scheduler.claimTtlMs);
        continue;
      }

      const session = this.adapters.craft.get(claim.sessionId);
      const stale = now - claim.heartbeatAtMs >= this.config.scheduler.staleRunMs || now >= claim.expiresAtMs;
      if (!session || session.status === "failed") {
        if (stale) {
          this.adapters.github.failClaim(
            claim.fence,
            "runtime",
            session ? "stale Craft run failed" : "stale Craft run is missing",
            now,
            this.config.scheduler,
          );
        }
        continue;
      }
      if (session.status === "running") {
        this.adapters.github.heartbeat(claim.fence, now, this.config.scheduler.claimTtlMs);
      } else if (stale) {
        this.adapters.github.failClaim(
          claim.fence,
          "runtime",
          "agent ended without tracker handoff evidence",
          now,
          this.config.scheduler,
        );
      }
    }
  }

  private async startClaim(snapshot: TrackerIssueSnapshot, crashAfter?: CrashPoint): Promise<void> {
    const claim = snapshot.claim;
    if (!claim) throw new Error("cannot start without a claim");
    const identity: RunIdentity = {
      issueId: claim.issueId,
      issueIdentifier: claim.issueIdentifier,
      attempt: claim.attempt,
      sessionId: claim.sessionId,
      workspaceId: claim.workspaceId,
      workspaceKey: claim.workspaceKey,
      workspacePath: claim.workspacePath,
    };
    try {
      await this.adapters.workspaces.ensure(identity);
      this.crashIf("after-workspace", crashAfter);
      await this.adapters.craft.ensure(identity);
      this.crashIf("after-session", crashAfter);
      this.adapters.github.markRunning(claim.fence, this.clock.nowMs());
    } catch (error) {
      if (error instanceof SimulatedCrash) throw error;
      this.adapters.github.failClaim(
        claim.fence,
        "runtime",
        error instanceof Error ? error.message : String(error),
        this.clock.nowMs(),
        this.config.scheduler,
      );
    }
  }

  private dispatchable(snapshot: TrackerIssueSnapshot): boolean {
    if (!snapshot.issue.dispatchable) return false;
    if (snapshot.issue.state === "retry-wait" && (!snapshot.retry || snapshot.retry.dueAtMs > this.clock.nowMs())) return false;
    return !snapshot.issue.blockedBy.some((blocker) => blocker.state?.trim().toLowerCase() !== "done");
  }

  private identityMatches(snapshot: TrackerIssueSnapshot, claim: Claim): boolean {
    const expected = this.#identity.forAttempt(snapshot.issue, claim.attempt);
    return expected.sessionId === claim.sessionId
      && expected.workspaceId === claim.workspaceId
      && expected.workspaceKey === claim.workspaceKey
      && expected.workspacePath === claim.workspacePath;
  }

  private crashIf(point: CrashPoint, selected?: CrashPoint): void {
    if (point === selected) throw new SimulatedCrash(point);
  }
}

function compareForDispatch(left: TrackerIssueSnapshot, right: TrackerIssueSnapshot): number {
  const priority = (value: number | null): number => value !== null && value >= 1 && value <= 4 ? value : Number.MAX_SAFE_INTEGER;
  const priorityOrder = priority(left.issue.priority) - priority(right.issue.priority);
  if (priorityOrder !== 0) return priorityOrder;
  const created = (value: string | null): number => value ? Date.parse(value) : Number.MAX_SAFE_INTEGER;
  const createdOrder = created(left.issue.createdAt) - created(right.issue.createdAt);
  return createdOrder || left.issue.identifier.localeCompare(right.issue.identifier) || left.issue.id.localeCompare(right.issue.id);
}
