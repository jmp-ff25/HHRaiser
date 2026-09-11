import ELK from "elkjs/lib/elk.bundled.js";
import { MarkerType, Position } from "@xyflow/react";
import type { ArchitectureEdge, ArchitectureNode, ArchitectureView } from "./types";

const elk = new ELK();
const WIDTH = 250;
const HEIGHT = 180;

export interface LayoutResult {
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
}

/** Lay out editorial sections independently; long links become explicit navigation. */
export async function layoutArchitecture(view: ArchitectureView): Promise<LayoutResult> {
  const sections = view.sections ?? [{ title: view.title, nodes: view.nodes.map(n => n.id) }];
  const result: LayoutResult = { nodes: [], edges: [] };
  let top = 0;
  for (const [index, section] of sections.entries()) {
    const localEdges = view.edges.filter(e =>
      section.nodes.indexOf(e.target) === section.nodes.indexOf(e.source) + 1
      && section.nodes.includes(e.source));
    const localIds = new Set(localEdges.map(e => e.id));
    // Order constraints belong to layout only: they never imply a business dependency.
    const graph = await elk.layout({
      id: "section", width: 0, height: 0,
      layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": "RIGHT",
        "elk.layered.spacing.nodeNodeBetweenLayers": "86",
        "elk.padding": "[top=0,left=0,bottom=0,right=0]",
      },
      children: section.nodes.map(id => ({ id, width: WIDTH, height: HEIGHT })),
      edges: section.nodes.slice(1).map((id, i) => ({
        id: `order-${i}`, sources: [section.nodes[i]], targets: [id],
      })),
    });
    result.nodes.push({
      id: `section-${index}`, type: "architecture", position: { x: 0, y: top },
      selectable: false, focusable: false,
      style: { width: graph.width ?? WIDTH, height: 36 },
      data: { section: true, title: `${index + 1}. ${section.title}`, summary: "",
        responsibility: "", success: "", kind: "entry", phase: "" },
    });
    for (const item of graph.children ?? []) {
      const original = view.nodes.find(n => n.id === item.id)!;
      const transitions = view.edges.filter(e => e.source === item.id && !localIds.has(e.id))
        .map(e => ({
          target: e.target,
          label: `${e.label ? e.label + " → " : "→ "}${view.nodes.find(n => n.id === e.target)!.data.title}`,
        }));
      result.nodes.push({
        ...original, position: { x: item.x ?? 0, y: top + 48 + (item.y ?? 0) },
        style: { width: WIDTH, height: HEIGHT },
        sourcePosition: Position.Right, targetPosition: Position.Left,
        data: { ...original.data, transitions },
      });
    }
    result.edges.push(...localEdges.map(e => ({
      ...e, animated: false,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#8c9cad" },
    })));
    top += (graph.height ?? HEIGHT) + 96;
  }
  return result;
}
