import { describe, expect, it } from "vitest";
import { architectureViews } from "./data/architecture";
import { layoutArchitecture } from "./layout";

describe("section layout", () => {
  for (const view of architectureViews) {
    it(`${view.id}: preserves every node and every semantic connection without overlap`, async () => {
      const assigned = view.sections!.flatMap(s => s.nodes);
      expect([...assigned].sort()).toEqual(view.nodes.map(n => n.id).sort());
      const result = await layoutArchitecture(view);
      const nodes = result.nodes.filter(n => !n.data.section);
      for (const [i, a] of nodes.entries()) {
        for (const b of nodes.slice(i + 1)) {
          const overlap = a.position.x < b.position.x + 250 &&
            b.position.x < a.position.x + 250 &&
            a.position.y < b.position.y + 180 &&
            b.position.y < a.position.y + 180;
          expect(overlap, `${a.id} overlaps ${b.id}`).toBe(false);
        }
      }
      for (const section of view.sections!) {
        const local = section.nodes.map(id => nodes.find(n => n.id === id)!);
        for (let i = 1; i < local.length; i++) {
          expect(local[i].position.x).toBeGreaterThan(local[i - 1].position.x);
          expect(local[i].position.y).toBe(local[i - 1].position.y);
        }
      }
      for (const edge of view.edges) {
        const rendered = result.edges.some(e => e.id === edge.id);
        const transition = nodes.find(n => n.id === edge.source)!.data.transitions!
          .some(link => link.target === edge.target);
        expect(rendered || transition, `lost edge ${edge.id}`).toBe(true);
      }
      for (const edge of result.edges) {
        const source = nodes.find(n => n.id === edge.source)!;
        const target = nodes.find(n => n.id === edge.target)!;
        expect(source.position.y).toBe(target.position.y);
        expect(target.position.x - source.position.x).toBeGreaterThan(250);
      }
    });
  }
});

