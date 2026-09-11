import { useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Focus, Moon, Search, Sun } from "lucide-react";

import { ArchitectureNode } from "./components/ArchitectureNode";
import { DetailsPanel } from "./components/DetailsPanel";
import { ToolbarButton } from "./components/ToolbarButton";
import { architectureViews, getArchitectureView } from "./data/architecture";
import { layoutArchitecture } from "./layout";
import type { ArchitectureEdge, ArchitectureNode as ArchitectureNodeType } from "./types";

const nodeTypes = { architecture: ArchitectureNode };

function readInitialState() {
  const hash = new URLSearchParams(window.location.hash.slice(1));
  return {
    viewId: getArchitectureView(hash.get("view") ?? "").id,
    nodeId: hash.get("node") ?? "",
  };
}

function Diagram() {
  const initial = useMemo(readInitialState, []);
  const [viewId, setViewId] = useState(initial.viewId);
  const [selectedId, setSelectedId] = useState(initial.nodeId);
  const [query, setQuery] = useState("");
  const [dark, setDark] = useState(() =>
    localStorage.getItem("hhraiser-diagram-theme") !== "light",
  );
  const [nodes, setNodes] = useState<ArchitectureNodeType[]>([]);
  const [edges, setEdges] = useState<ArchitectureEdge[]>([]);
  const [layoutReady, setLayoutReady] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const { fitView } = useReactFlow();
  const view = getArchitectureView(viewId);

  useEffect(() => {
    const navigate = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      if (!view.nodes.some(n => n.id === id)) return;
      setSelectedId(id);
      fitView({ nodes: [{ id }], maxZoom: 1, padding: 0.5, duration: 350 });
    };
    window.addEventListener("architecture-navigate", navigate);
    return () => window.removeEventListener("architecture-navigate", navigate);
  }, [view, fitView]);

  useEffect(() => {
    let active = true;
    setLayoutReady(false);
    setSelectedId((current) => view.nodes.some((item) => item.id === current) ? current : "");
    layoutArchitecture(view).then((result) => {
      if (!active) return;
      setNodes(result.nodes);
      setEdges(result.edges);
      setLayoutReady(true);
      window.requestAnimationFrame(() => fitView({ padding: 0.18, duration: 450 }));
    });
    return () => { active = false; };
  }, [fitView, view]);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("hhraiser-diagram-theme", dark ? "dark" : "light");
  }, [dark]);

  useEffect(() => {
    const hash = new URLSearchParams({ view: viewId });
    if (selectedId) hash.set("node", selectedId);
    window.history.replaceState(null, "", `#${hash}`);
  }, [selectedId, viewId]);

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "/" && document.activeElement?.tagName !== "INPUT") {
        event.preventDefault();
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        setSelectedId("");
        setQuery("");
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, []);

  const normalizedQuery = query.trim().toLocaleLowerCase("ru");
  const visibleNodes = nodes.map((item) => {
    const haystack = [
      item.data.title,
      item.data.summary,
      item.data.phase,
      ...(item.data.tags ?? []),
      ...(item.data.configKeys ?? []),
    ].join(" ").toLocaleLowerCase("ru");
    const dimmed = !item.data.section && normalizedQuery.length > 0 && !haystack.includes(normalizedQuery);
    return {
      ...item,
      selected: item.id === selectedId,
      className: dimmed ? "is-dimmed" : "",
    };
  });
  const selectedNode = nodes.find((item) => item.id === selectedId);
  const matchCount = normalizedQuery
    ? visibleNodes.filter((item) => !item.data.section && item.className !== "is-dimmed").length
    : view.nodes.length;

  return (
    <Tooltip.Provider delayDuration={320}>
      <div className="app-shell">
        <header className="app-header">
          <a className="brand" href="#view=lifecycle" onClick={() => setViewId("lifecycle")}>
            <span className="brand-mark">HH</span>
            <span>
              <strong>HHRaiser</strong>
              <small>Architecture explorer</small>
            </span>
          </a>
          <nav className="view-tabs" aria-label="Представления архитектуры">
            {architectureViews.map((item) => (
              <button
                key={item.id}
                className={item.id === viewId ? "is-active" : ""}
                type="button"
                onClick={() => setViewId(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <div className="header-actions">
            <ToolbarButton label={dark ? "Светлая тема" : "Тёмная тема"} onClick={() => setDark((value) => !value)}>
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </ToolbarButton>
          </div>
        </header>

        <main className="diagram-layout">
          <section className="canvas-column" aria-label="Интерактивная карта архитектуры">
            <div className="canvas-titlebar">
              <div>
                <span className="eyebrow">Интерактивная документация</span>
                <h1>{view.title}</h1>
                <p>{view.description}</p>
              </div>
              <div className="canvas-tools">
                <label className="search-box">
                  <Search size={16} aria-hidden="true" />
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Найти элемент…"
                    aria-label="Поиск по схеме"
                  />
                  <kbd>/</kbd>
                </label>
                <span className="match-count">{matchCount} из {view.nodes.length}</span>
                <ToolbarButton label="Вписать схему" onClick={() => fitView({ padding: 0.18, duration: 450 })}>
                  <Focus size={18} />
                </ToolbarButton>
              </div>
            </div>

            <div className={`flow-stage${layoutReady ? " is-ready" : ""}`}>
              {!layoutReady && (
                <div className="layout-loader" role="status">
                  <span />
                  <strong>Вычисляем аккуратную раскладку</strong>
                </div>
              )}
              <ReactFlow
                nodes={visibleNodes}
                edges={edges}
                nodeTypes={nodeTypes}
                onNodeClick={(_, item) => { if (!item.data.section) setSelectedId(item.id); }}
                onPaneClick={() => setSelectedId("")}
                nodesDraggable={false}
                nodesConnectable={false}
                edgesFocusable
                minZoom={0.28}
                maxZoom={1.8}
                fitView
                fitViewOptions={{ padding: 0.18 }}
                proOptions={{ hideAttribution: true }}
              >
                <Background variant={BackgroundVariant.Dots} gap={24} size={1.2} />
                <MiniMap
                  className="minimap"
                  pannable
                  zoomable
                  nodeColor={(item) => {
                    const kind = (item.data as ArchitectureNodeType["data"]).kind;
                    return `var(--kind-${kind})`;
                  }}
                  maskColor="var(--minimap-mask)"
                />
                <Controls showInteractive={false} position="bottom-left" />
              </ReactFlow>
            </div>
          </section>

          <DetailsPanel node={selectedNode} open={Boolean(selectedNode)} onClose={() => setSelectedId("")} />
        </main>
      </div>
    </Tooltip.Provider>
  );
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Diagram />
    </ReactFlowProvider>
  );
}
