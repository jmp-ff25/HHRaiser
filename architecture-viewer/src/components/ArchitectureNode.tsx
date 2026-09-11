import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  CircleStop,
  Database,
  Diamond,
  MousePointerClick,
  Play,
} from "lucide-react";

import type { ArchitectureNode as ArchitectureNodeType, NodeKind } from "../types";

const icons: Record<NodeKind, typeof Play> = {
  entry: Play,
  action: MousePointerClick,
  decision: Diamond,
  storage: Database,
  terminal: CircleStop,
};

export function ArchitectureNode({ data, selected }: NodeProps<ArchitectureNodeType>) {
  const Icon = icons[data.kind];
  if (data.section) return <div className="section-heading">{data.title}</div>;

  return (
    <article className={`architecture-node kind-${data.kind}${selected ? " is-selected" : ""}`}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <header className="node-heading">
        <span className="node-icon" aria-hidden="true">
          <Icon size={16} strokeWidth={1.8} />
        </span>
        <span className="node-phase">{data.phase}</span>
      </header>
      <h3>{data.title}</h3>
      <p>{data.summary}</p>
      <div className="node-transitions">
        {data.transitions?.map(link => (
          <button key={link.target + link.label} type="button" className="nodrag nopan"
            onClick={event => {
              event.stopPropagation();
              window.dispatchEvent(new CustomEvent("architecture-navigate", { detail: link.target }));
            }}>{link.label}</button>
        ))}
      </div>
      <span className="node-open">Открыть описание</span>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </article>
  );
}
