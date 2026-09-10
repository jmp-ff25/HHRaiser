import { describe, expect, it } from "vitest";

import { architectureViews } from "./architecture";

describe("architecture model", () => {
  it("uses unique node and edge identifiers within every view", () => {
    for (const view of architectureViews) {
      expect(new Set(view.nodes.map((node) => node.id)).size).toBe(view.nodes.length);
      expect(new Set(view.edges.map((edge) => edge.id)).size).toBe(view.edges.length);
    }
  });

  it("connects only nodes that exist in the same view", () => {
    for (const view of architectureViews) {
      const nodeIds = new Set(view.nodes.map((node) => node.id));
      for (const edge of view.edges) {
        expect(nodeIds.has(edge.source), `${view.id}: missing source ${edge.source}`).toBe(true);
        expect(nodeIds.has(edge.target), `${view.id}: missing target ${edge.target}`).toBe(true);
      }
    }
  });

  it("provides inspector content for every node", () => {
    for (const view of architectureViews) {
      for (const node of view.nodes) {
        expect(node.data.title.trim()).not.toBe("");
        expect(node.data.summary.trim()).not.toBe("");
        expect(node.data.responsibility.trim()).not.toBe("");
        expect(node.data.success.trim()).not.toBe("");
      }
    }
  });
});
