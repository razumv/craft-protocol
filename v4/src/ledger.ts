// SPDX-License-Identifier: Apache-2.0

export interface OwnerDirective {
  id: string;
  issueId: string;
  receivedAtMs: number;
  acknowledgedAtMs: number;
  verbatim: string;
}

function canonical(entry: OwnerDirective): string {
  return JSON.stringify(entry);
}

export class OwnerDirectiveLedger {
  readonly #entries: OwnerDirective[] = [];
  readonly #byId = new Map<string, OwnerDirective>();

  append(entry: OwnerDirective): Readonly<OwnerDirective> {
    if (!entry.id.trim() || !entry.issueId.trim() || !entry.verbatim.trim()) throw new Error("directive fields must not be blank");
    if (entry.acknowledgedAtMs < entry.receivedAtMs || entry.acknowledgedAtMs - entry.receivedAtMs > 60_000) {
      throw new Error("owner directive acknowledgement must be recorded within 60 seconds");
    }
    const immutable = Object.freeze({ ...entry });
    const existing = this.#byId.get(entry.id);
    if (existing) {
      if (canonical(existing) !== canonical(immutable)) throw new Error(`directive ${entry.id} is immutable`);
      return existing;
    }
    this.#entries.push(immutable);
    this.#byId.set(immutable.id, immutable);
    return immutable;
  }

  entries(): readonly Readonly<OwnerDirective>[] {
    return Object.freeze([...this.#entries]);
  }
}

export type OwnerGateDecision =
  | { kind: "approve"; gateId: string }
  | { kind: "reject"; gateId: string; reason: string };

export function parseOwnerGateDecision(verbatim: string, expectedGateId: string): OwnerGateDecision {
  if (verbatim === `APPROVE ${expectedGateId}`) return { kind: "approve", gateId: expectedGateId };
  const prefix = `REJECT ${expectedGateId}: `;
  if (verbatim.startsWith(prefix) && verbatim.slice(prefix.length).trim()) {
    return { kind: "reject", gateId: expectedGateId, reason: verbatim.slice(prefix.length).trim() };
  }
  throw new Error(`owner decision does not exactly match gate ${expectedGateId}`);
}
