import { ExternalLink, Info, X } from "lucide-react";

import type { ArchitectureNode } from "../types";

interface DetailsPanelProps {
  node?: ArchitectureNode;
  open: boolean;
  onClose: () => void;
}

const kindNames = {
  entry: "Начало",
  action: "Действие",
  decision: "Решение",
  storage: "Данные",
  terminal: "Результат",
};

function ListSection({ title, values }: { title: string; values?: string[] }) {
  if (!values?.length) return null;
  return (
    <section className="detail-section">
      <h3>{title}</h3>
      <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul>
    </section>
  );
}

export function DetailsPanel({ node, open, onClose }: DetailsPanelProps) {
  return (
    <aside className={`details-panel${open ? " is-open" : ""}`} aria-live="polite">
      {node ? (
        <>
          <div className="details-header">
            <div>
              <span className={`kind-label kind-${node.data.kind}`}>
                {kindNames[node.data.kind]}
              </span>
              <span className="phase-label">{node.data.phase}</span>
            </div>
            <button className="icon-button close-details" type="button" onClick={onClose} aria-label="Закрыть описание">
              <X size={18} />
            </button>
          </div>
          <div className="details-scroll">
            <h2>{node.data.title}</h2>
            <p className="details-lead">{node.data.summary}</p>

            <section className="detail-section emphasis">
              <h3>Что делает этот блок</h3>
              <p>{node.data.responsibility}</p>
            </section>
            <ListSection title="Что должно быть готово" values={node.data.preconditions} />
            {node.data.success && (
              <section className="detail-section outcome success">
                <h3>Успешный результат</h3>
                <p>{node.data.success}</p>
              </section>
            )}
            {node.data.unknown && (
              <section className="detail-section outcome unknown">
                <h3>Если результат непонятен</h3>
                <p>{node.data.unknown}</p>
              </section>
            )}
            <ListSection title="Что может пойти не так" values={node.data.failures} />
            {node.data.retry && (
              <section className="detail-section">
                <h3>Что делать дальше</h3>
                <p>{node.data.retry}</p>
              </section>
            )}
            <ListSection title="Настройки, которые влияют" values={node.data.configKeys} />
            {!!node.data.codeLinks?.length && (
              <section className="detail-section">
                <h3>Связанный код и решения</h3>
                <div className="code-links">
                  {node.data.codeLinks.map((link) => (
                    <a key={link.path} href={`../../${link.path}`} target="_blank" rel="noreferrer">
                      <span>{link.label}</span>
                      <ExternalLink size={14} />
                    </a>
                  ))}
                </div>
              </section>
            )}
            {!!node.data.tags?.length && (
              <section className="detail-section">
                <h3>Метки</h3>
                <div className="tags">
                  {node.data.tags.map((tag) => <span key={tag}>{tag}</span>)}
                </div>
              </section>
            )}
          </div>
        </>
      ) : (
        <div className="empty-details">
          <span><Info size={22} /></span>
          <h2>Выберите элемент</h2>
          <p>Нажмите на блок схемы, чтобы увидеть его назначение, исходы, настройки и связанный код.</p>
        </div>
      )}
    </aside>
  );
}
