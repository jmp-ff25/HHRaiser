import type { Edge, Node } from "@xyflow/react";

export type NodeKind = "entry" | "action" | "decision" | "storage" | "terminal";

export interface CodeLink {
  label: string;
  path: string;
}

export interface ArchitectureNodeData extends Record<string, unknown> {
  title: string;
  summary: string;
  kind: NodeKind;
  phase: string;
  responsibility: string;
  preconditions?: string[];
  success: string;
  unknown?: string;
  failures?: string[];
  retry?: string;
  configKeys?: string[];
  codeLinks?: CodeLink[];
  tags?: string[];
  section?: boolean;
  transitions?: { target: string; label: string }[];
}

export type ArchitectureNode = Node<ArchitectureNodeData, "architecture">;
export type ArchitectureEdge = Edge<Record<string, never>, "smoothstep">;

export interface ArchitectureView {
  id: string;
  label: string;
  title: string;
  description: string;
  nodes: ArchitectureNode[];
  edges: ArchitectureEdge[];
  sections?: { title: string; nodes: string[] }[];
}
