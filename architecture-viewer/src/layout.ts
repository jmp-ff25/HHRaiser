import ELK from "elkjs/lib/elk.bundled.js";
import { Position } from "@xyflow/react";

import type { ArchitectureEdge, ArchitectureNode } from "./types";

const elk = new ELK();
const NODE_WIDTH = 228;
const NODE_HEIGHT = 104;

export interface LayoutResult {
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
}

export async function layoutArchitecture(
  nodes: ArchitectureNode[],
  edges: ArchitectureEdge[],
): Promise<LayoutResult> {
  const graph = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.layered.spacing.nodeNodeBetweenLayers": "72",
      "elk.spacing.nodeNode": "38",
      "elk.spacing.edgeNode": "24",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.cycleBreaking.strategy": "GREEDY",
      "elk.layered.wrapping.strategy": "MULTI_EDGE",
      "elk.layered.wrapping.cutting.strategy": "MSD",
      "elk.layered.wrapping.correctionFactor": "1.15",
      "elk.aspectRatio": "1.65",
      "elk.padding": "[top=44,left=44,bottom=44,right=44]",
    },
    children: nodes.map((item) => ({
      id: item.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: edges.map((item) => ({
      id: item.id,
      sources: [item.source],
      targets: [item.target],
    })),
  });

  const positions = new Map(
    graph.children?.map((item) => [item.id, { x: item.x ?? 0, y: item.y ?? 0 }]),
  );

  return {
    nodes: nodes.map((item) => ({
      ...item,
      position: positions.get(item.id) ?? { x: 0, y: 0 },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    })),
    edges,
  };
}
