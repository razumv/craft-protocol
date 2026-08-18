// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from "bun:test";
import { ScopedGitHubTransport, type GitHubComment, type GitHubTransport } from "../src";

function fixture() {
  const calls: string[] = [];
  const delegate = {
    appendComment: async (issueId: string, body: string): Promise<GitHubComment> => {
      calls.push(`comment:${issueId}:${body}`);
      return { databaseId: 1, body, authorLogin: "owner", createdAt: "2026-08-18T00:00:00Z", updatedAt: "2026-08-18T00:00:00Z" };
    },
    replaceLabels: async (_repository: string, issueNumber: number) => { calls.push(`labels:${issueNumber}`); },
    updateProjectSingleSelect: async (_projectId: string, itemId: string, fieldId: string) => { calls.push(`status:${itemId}:${fieldId}`); },
    updateProjectText: async (_projectId: string, itemId: string, fieldId: string) => { calls.push(`gate:${itemId}:${fieldId}`); },
  } as unknown as GitHubTransport;
  const scoped = new ScopedGitHubTransport(delegate, {
    issueId: "I_52",
    issueNumber: 52,
    fenceIssueId: "I_48",
    projectId: "PROJECT",
    projectItemId: "ITEM_52",
    statusFieldId: "STATUS",
    gateFieldId: "GATE",
  });
  return { calls, scoped };
}

describe("v4 live runner mutation scope", () => {
  test("permits only the configured issue, fence, and exact Project item fields", async () => {
    const { calls, scoped } = fixture();
    await scoped.appendComment("I_52", "event");
    await scoped.appendComment("I_48", "fence");
    await scoped.replaceLabels("razumv/craft-protocol", 52, ["agent-running"]);
    await scoped.updateProjectSingleSelect("PROJECT", "ITEM_52", "STATUS", "in-progress");
    await scoped.updateProjectText("PROJECT", "ITEM_52", "GATE", "gate-52");

    expect(calls).toEqual([
      "comment:I_52:event",
      "comment:I_48:fence",
      "labels:52",
      "status:ITEM_52:STATUS",
      "gate:ITEM_52:GATE",
    ]);
    expect(() => scoped.appendComment("I_51", "escape")).toThrow("escaped");
    expect(() => scoped.replaceLabels("razumv/craft-protocol", 51, [])).toThrow("escaped");
    expect(() => scoped.updateProjectSingleSelect("PROJECT", "ITEM_51", "STATUS", "x")).toThrow("escaped");
    expect(() => scoped.updateProjectText("OTHER", "ITEM_52", "GATE", "x")).toThrow("escaped");
  });
});
