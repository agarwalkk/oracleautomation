import { useEffect, useMemo, useRef, useState } from "react";

type ContainerSummary = {
  container_ref: string;
  title: string;
  screenshot?: string | null;
  status?: string;
};

type TreeElement = {
  element_ref?: string;
  elementid?: string;
  parent_ref?: string | null;
  filteredparentid?: string | null;
  friendly_name?: string;
  name?: string;
  role?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  bounds?: { x: number; y: number; width: number; height: number };
  included?: boolean;
};

type ContainerDetail = {
  container_ref: string;
  title: string;
  screenshot?: string | null;
  metadata?: Record<string, unknown>;
  tree: TreeElement[] | TreeNode[];
  full_elements?: FullElement[];
};

type FullElement = {
  element_ref: string;
  name: string;
  role: string;
  type: string;
  actions: string[];
  value: string;
  enabled?: boolean;
  bounds: { x: number; y: number; width: number; height: number };
};

type ScanTarget = {
  source: string;
  pid: number;
  label: string;
};

type ScanResult = {
  scan_id: string;
  title: string;
  container_ref: string;
  snapshot_text?: string;
  screenshot_origin?: { x: number; y: number };
  capture_mode?: string;
  screenshot_path: string;
  tree: TreeNode[];
  full_elements?: FullElement[];
};

type TreeNode = {
  element_ref: string;
  label: string;
  role: string;
  included: boolean;
  bounds: { x: number; y: number; width: number; height: number };
  children: TreeNode[];
};

function screenshotUrl(detail: { screenshot?: string | null } | null): string | null {
  if (!detail?.screenshot) {
    return null;
  }
  const fileName = detail.screenshot.split("/").pop();
  return fileName ? `/screenshots/${fileName}` : null;
}

export function App() {
  const [containers, setContainers] = useState<ContainerSummary[]>([]);
  const [selectedRef, setSelectedRef] = useState<string>("");
  const [detail, setDetail] = useState<ContainerDetail | null>(null);
  const [hoveredRef, setHoveredRef] = useState<string>("");
  const [targets, setTargets] = useState<ScanTarget[]>([]);
  const [selectedPid, setSelectedPid] = useState<string>("");
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [editTitle, setEditTitle] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [zoom, setZoom] = useState<number>(1);
  const [fitMode, setFitMode] = useState<boolean>(true);
  // User-edited curated tree (e.g. after dragging a non-tree element in).
  // When non-null it overrides the auto-built tree and is persisted on save.
  const [treeOverride, setTreeOverride] = useState<TreeNode[] | null>(null);

  useEffect(() => {
    void refreshContainers();
    void refreshTargets();
  }, []);

  async function refreshContainers() {
    const res = await fetch("/api/v1/containers");
    const json = await res.json();
    setContainers(json.items || []);
  }

  async function refreshTargets() {
    const res = await fetch("/api/v1/windows");
    const json = await res.json();
    setTargets(json.items || []);
    if ((json.items || []).length > 0) {
      setSelectedPid(String(json.items[0].pid));
    }
  }

  async function loadContainer(ref: string) {
    setSelectedRef(ref);
    setHoveredRef("");
    const res = await fetch(`/api/v1/containers/${encodeURIComponent(ref)}`);
    const json = await res.json();
    setDetail(json);
  }

  async function runScan() {
    setBusy(true);
    try {
      const payload = selectedPid ? { pid: Number(selectedPid) } : {};
      const res = await fetch("/api/v1/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const json = await res.json();
      setScan(json);
      setEditTitle(json.title || "");
      setDetail(null);
      setSelectedRef("");
      // The scan is persisted as a draft server-side; refresh so it appears in
      // the navigator marked "New" right away.
      await refreshContainers();
    } finally {
      setBusy(false);
    }
  }

  async function saveScan() {
    if (!scan) {
      return;
    }
    setBusy(true);
    try {
      await fetch("/api/v1/scan/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scan_id: scan.scan_id,
          container_ref: scan.container_ref,
          title: (editTitle || scan.title).trim() || scan.title,
          metadata: { saved_from: "qcs_studio_web" },
          // Persist the curated tree including any drag-added elements.
          display_tree: treeOverride ?? undefined
        })
      });
      await refreshContainers();
      await loadContainer(scan.container_ref);
      setScan(null);
    } finally {
      setBusy(false);
    }
  }

  async function saveDisplayTree() {
    if (!detail || !treeOverride) {
      return;
    }
    setBusy(true);
    try {
      await fetch(`/api/v1/containers/${encodeURIComponent(detail.container_ref)}/display-tree`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_tree: treeOverride })
      });
      await loadContainer(detail.container_ref);
    } finally {
      setBusy(false);
    }
  }

  const baseTree = useMemo<TreeNode[]>(() => {
    if (scan?.tree) {
      return normalizeTree(scan.tree, scan.screenshot_origin ?? { x: 0, y: 0 });
    }
    if (detail) {
      // Prefer the persisted hierarchical display tree (mirrors ai_snapshot.txt)
      // so a reloaded container shows the same structure as a live scan.
      const displayTree = detail.metadata?.display_tree as TreeNode[] | undefined;
      if (Array.isArray(displayTree) && displayTree.length > 0) {
        return normalizeTree(displayTree, { x: 0, y: 0 });
      }
      if (detail.tree) {
        const rawOrigin = (detail.metadata?.screenshot_origin as { x?: number; y?: number } | undefined) ??
          inferOrigin(detail.tree as TreeElement[]);
        return normalizeTree(detail.tree, { x: Number(rawOrigin?.x || 0), y: Number(rawOrigin?.y || 0) });
      }
    }
    return [];
  }, [detail, scan]);

  // Reset any pending tree edits whenever the active scan/container changes.
  useEffect(() => {
    setTreeOverride(null);
  }, [scan, detail]);

  const activeTree = treeOverride ?? baseTree;

  // Full hoverable element overlay (every scanned element) for the active view.
  const fullElements = useMemo<FullElement[]>(() => {
    if (scan?.full_elements) {
      return scan.full_elements;
    }
    if (detail?.full_elements) {
      return detail.full_elements;
    }
    return [];
  }, [scan, detail]);

  const fullByRef = useMemo(() => {
    const map = new Map<string, FullElement>();
    for (const fe of fullElements) {
      map.set(fe.element_ref, fe);
    }
    return map;
  }, [fullElements]);

  // Elements present on screen but not yet in the curated tree — these are the
  // draggable "discovery" elements the user can drop onto a tree node.
  const discoveryElements = useMemo(() => {
    const inTree = new Set(flattenTree(activeTree).map((n) => n.element_ref));
    return fullElements.filter((fe) => !inTree.has(fe.element_ref) && fe.bounds.width > 0 && fe.bounds.height > 0);
  }, [fullElements, activeTree]);

  function addElementToNode(targetRef: string, elementRef: string) {
    const fe = fullByRef.get(elementRef);
    if (!fe) {
      return;
    }
    const current = treeOverride ?? baseTree;
    if (flattenTree(current).some((n) => n.element_ref === elementRef)) {
      return; // already in the tree
    }
    const newNode: TreeNode = {
      element_ref: fe.element_ref,
      label: fe.name || fe.element_ref,
      role: fe.role || fe.type,
      included: true,
      bounds: { ...fe.bounds },
      children: [],
    };
    const clone = cloneTree(current);
    const inserted = insertChild(clone, targetRef, newNode);
    setTreeOverride(inserted);
    setExpanded((prev) => ({ ...prev, [targetRef]: true }));
  }


  const activeScreenshot = useMemo(() => {
    if (scan?.screenshot_path) {
      return `/api/v1/scans/${encodeURIComponent(scan.scan_id)}/screenshot`;
    }
    return screenshotUrl(detail);
  }, [detail, scan]);

  useEffect(() => {
    setExpanded(explorerModeExpandedState(activeTree));
  }, [activeTree]);

  function toggleNode(ref: string) {
    setExpanded((prev) => ({ ...prev, [ref]: !prev[ref] }));
  }

  return (
    <div className="studio-shell">
      <header className="topbar">
        <div>
          <h1>QCS Studio</h1>
          <p>Container / Element master repository editor</p>
        </div>
        <div className="scan-controls">
          <select value={selectedPid} onChange={(e) => setSelectedPid(e.target.value)}>
            <option value="">Select Oracle Java window</option>
            {targets.map((target) => (
              <option key={target.pid} value={target.pid}>
                {target.label}
              </option>
            ))}
          </select>
          <button onClick={runScan} disabled={busy || !selectedPid}>
            {busy ? "Scanning..." : "Scan"}
          </button>
          <button onClick={saveScan} disabled={!scan || busy}>
            Save Scan
          </button>
        </div>
      </header>

      <main className="layout">
        <aside className="left-panel">
          <section className="container-panel">
            <div className="container-header">
              <h2>Containers</h2>
              <div className="container-tabs">
                <button className="tab-button active">All</button>
              </div>
            </div>
            <div className="container-list">
              {containers.map((container) => (
                <button
                  key={container.container_ref}
                  className={container.container_ref === selectedRef ? "container-item active" : "container-item"}
                  onClick={() => loadContainer(container.container_ref)}
                >
                  <span className="container-title-row">
                    <span className="container-title">{container.title || container.container_ref}</span>
                    {container.status === "draft" ? <span className="badge-new">New</span> : null}
                  </span>
                  <small>{container.container_ref}</small>
                </button>
              ))}
            </div>
            {activeTree.length > 0 && (
              <div className="tree-divider">
                {treeOverride && detail && !scan ? (
                  <div className="tree-toolbar">
                    <button className="tree-save-btn" onClick={saveDisplayTree} disabled={busy}>
                      Save Tree
                    </button>
                  </div>
                ) : null}
                <div className="tree-list">
                  {renderTreeRows(activeTree, hoveredRef, setHoveredRef, expanded, toggleNode, addElementToNode)}
                </div>
              </div>
            )}
          </section>
        </aside>

        <section className="canvas-panel">
          <div className="canvas-header">
            <div className="canvas-title">
              {scan ? (
                <>
                  <span className="badge-new">New</span>
                  <input
                    className="title-input"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="Container name"
                    title="Edit container name, then Save Scan"
                  />
                </>
              ) : (
                <h2>{detail?.title || "No container selected"}</h2>
              )}
            </div>
            <div className="zoom-controls">
              {scan ? <span className="capture-mode">{scan.capture_mode || "unknown"}</span> : null}
              <button
                type="button"
                onClick={() => {
                  setFitMode(false);
                  setZoom((z) => Math.max(0.1, +(z - 0.1).toFixed(2)));
                }}
                title="Zoom out"
              >
                −
              </button>
              <span className="zoom-label">{fitMode ? "Fit" : `${Math.round(zoom * 100)}%`}</span>
              <button
                type="button"
                onClick={() => {
                  setFitMode(false);
                  setZoom((z) => Math.min(4, +(z + 0.1).toFixed(2)));
                }}
                title="Zoom in"
              >
                +
              </button>
              <button
                type="button"
                className={!fitMode && zoom === 1 ? "active" : ""}
                onClick={() => {
                  setFitMode(false);
                  setZoom(1);
                }}
              >
                100%
              </button>
              <button
                type="button"
                className={fitMode ? "active" : ""}
                onClick={() => setFitMode(true)}
              >
                Fit
              </button>
            </div>
          </div>
          <div className="shot-frame">
            {activeScreenshot ? (
              <ImageCanvas
                src={activeScreenshot}
                tree={activeTree}
                hoveredRef={hoveredRef}
                onHover={(ref) => setHoveredRef(ref)}
                zoom={zoom}
                fitMode={fitMode}
                discovery={discoveryElements}
                fullByRef={fullByRef}
              />
            ) : (
              <div className="empty">Run scan or select a container.</div>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}

function ImageCanvas(props: {
  src: string;
  tree: TreeNode[];
  hoveredRef: string;
  onHover: (ref: string) => void;
  zoom: number;
  fitMode: boolean;
  discovery: FullElement[];
  fullByRef: Map<string, FullElement>;
}) {
  const [natural, setNatural] = useState({ w: 1, h: 1 });
  const [containerW, setContainerW] = useState(1);
  const [tip, setTip] = useState<{ fe: FullElement; x: number; y: number } | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const flatNodes = useMemo(() => flattenTree(props.tree), [props.tree]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) {
      return;
    }
    const update = () => setContainerW(el.clientWidth || 1);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // In fit mode scale the natural image down to the container width; otherwise
  // use the explicit zoom factor. Overlay boxes use the same scale so they stay
  // aligned with the image at any zoom level.
  const scale = props.fitMode
    ? Math.min(1, containerW / (natural.w || 1))
    : props.zoom;
  const dispW = Math.max(1, Math.round(natural.w * scale));
  const dispH = Math.max(1, Math.round(natural.h * scale));

  const showTip = (fe: FullElement | undefined, e: { clientX: number; clientY: number }) => {
    if (!fe) {
      return;
    }
    const host = wrapRef.current;
    const rect = host ? host.getBoundingClientRect() : { left: 0, top: 0 };
    setTip({ fe, x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 12 });
  };

  return (
    <div className="image-canvas" ref={wrapRef}>
      <div className="image-stack" style={{ width: `${dispW}px`, height: `${dispH}px` }}>
        <img
          src={props.src}
          alt="container screenshot"
          style={{ width: `${dispW}px`, height: `${dispH}px` }}
          onLoad={(e) => {
            const img = e.currentTarget;
            setNatural({ w: img.naturalWidth || 1, h: img.naturalHeight || 1 });
          }}
        />
        {/* Discovery overlay: every scanned element not yet in the tree.
            Hoverable (shows details) and draggable onto a tree node. */}
        <div className="overlay-layer discovery-layer">
          {props.discovery.map((fe) => {
            const b = fe.bounds;
            if (!b.width || !b.height) {
              return null;
            }
            return (
              <div
                key={fe.element_ref}
                className="box discovery"
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", fe.element_ref);
                  e.dataTransfer.effectAllowed = "copy";
                }}
                style={{
                  left: `${Math.max(0, b.x) * scale}px`,
                  top: `${Math.max(0, b.y) * scale}px`,
                  width: `${Math.max(1, b.width) * scale}px`,
                  height: `${Math.max(1, b.height) * scale}px`
                }}
                onMouseEnter={(e) => showTip(fe, e)}
                onMouseMove={(e) => showTip(fe, e)}
                onMouseLeave={() => setTip(null)}
              />
            );
          })}
        </div>
        <div className="overlay-layer">
          {flatNodes.map((node) => {
            const b = node.bounds;
            if (!b.width || !b.height) {
              return null;
            }
            return (
              <button
                type="button"
                key={node.element_ref}
                className={props.hoveredRef === node.element_ref ? "box active" : "box"}
                style={{
                  left: `${Math.max(0, b.x) * scale}px`,
                  top: `${Math.max(0, b.y) * scale}px`,
                  width: `${Math.max(1, b.width) * scale}px`,
                  height: `${Math.max(1, b.height) * scale}px`
                }}
                onMouseEnter={(e) => {
                  props.onHover(node.element_ref);
                  showTip(props.fullByRef.get(node.element_ref), e);
                }}
                onMouseMove={(e) => showTip(props.fullByRef.get(node.element_ref), e)}
                onMouseLeave={() => {
                  props.onHover("");
                  setTip(null);
                }}
                title={node.element_ref}
              />
            );
          })}
        </div>
        {tip ? (
          <div className="hover-tip" style={{ left: `${tip.x}px`, top: `${tip.y}px` }}>
            <div className="hover-tip-head">
              <span className="hover-tip-id">{tip.fe.element_ref}</span>
              <span className="hover-tip-role">{tip.fe.role || tip.fe.type}</span>
            </div>
            {tip.fe.name ? <div className="hover-tip-name">{tip.fe.name}</div> : null}
            <div className="hover-tip-type">{tip.fe.type}</div>
            {tip.fe.value ? <div className="hover-tip-value">= {tip.fe.value}</div> : null}
            {tip.fe.actions?.length ? (
              <div className="hover-tip-actions">{tip.fe.actions.join(" · ")}</div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function renderTreeRows(
  nodes: TreeNode[],
  hoveredRef: string,
  setHoveredRef: (ref: string) => void,
  expanded: Record<string, boolean>,
  toggleNode: (ref: string) => void,
  onDropElement: (targetRef: string, elementRef: string) => void,
  depth = 0
): JSX.Element[] {
  const rows: JSX.Element[] = [];
  for (const node of nodes) {
    const hasChildren = (node.children || []).length > 0;
    const isExpanded = hasChildren ? !!expanded[node.element_ref] : false;
    const meta = elementMeta(node);
    rows.push(
      <div
        key={node.element_ref}
        className={hoveredRef === node.element_ref ? "tree-item hovered" : "tree-item"}
        style={{ paddingLeft: `${6 + depth * 14}px` }}
        onMouseEnter={() => setHoveredRef(node.element_ref)}
        onMouseLeave={() => setHoveredRef("")}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          e.currentTarget.classList.add("drop-target");
        }}
        onDragLeave={(e) => e.currentTarget.classList.remove("drop-target")}
        onDrop={(e) => {
          e.preventDefault();
          e.currentTarget.classList.remove("drop-target");
          const ref = e.dataTransfer.getData("text/plain");
          if (ref) {
            onDropElement(node.element_ref, ref);
          }
        }}
      >
        <button
          type="button"
          className={hasChildren ? "tree-toggle" : "tree-toggle ghost"}
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) {
              toggleNode(node.element_ref);
            }
          }}
          aria-label={hasChildren ? (isExpanded ? "Collapse" : "Expand") : "Leaf"}
        >
          {hasChildren ? (isExpanded ? "▾" : "▸") : ""}
        </button>
        <span
          className={meta.enabled ? "tree-icon" : "tree-icon disabled"}
          title={`${meta.typeLabel}${meta.enabled ? "" : " · disabled"}`}
        >
          {meta.icon}
        </span>
        <span className="tree-label" title={node.label || node.element_ref}>
          {meta.text}
        </span>
      </div>
    );
    if (hasChildren && isExpanded) {
      rows.push(...renderTreeRows(node.children, hoveredRef, setHoveredRef, expanded, toggleNode, onDropElement, depth + 1));
    }
  }
  return rows;
}

function explorerModeExpandedState(_nodes: TreeNode[]): Record<string, boolean> {
  // Collapsed by default: no nodes are expanded; only root rows are visible
  // until the user expands a branch.
  return {};
}

function normalizeTree(input: TreeElement[] | TreeNode[], origin: { x: number; y: number }): TreeNode[] {
  const items = input || [];
  if (!items.length) {
    return [];
  }

  const first = items[0] as Partial<TreeNode>;
  if (Array.isArray(first.children)) {
    return (items as TreeNode[]).map((node) => ({
      ...node,
      children: normalizeTree(node.children || [], { x: 0, y: 0 }),
    }));
  }

  const byRef = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];
  const parentByRef = new Map<string, string>();

  for (const raw of items as TreeElement[]) {
    const ref = String(raw.element_ref || raw.elementid || "").trim();
    if (!ref) {
      continue;
    }
    const b = raw.bounds || {
      x: Number(raw.x || 0),
      y: Number(raw.y || 0),
      width: Number(raw.width || 0),
      height: Number(raw.height || 0),
    };
    byRef.set(ref, {
      element_ref: ref,
      label: String(raw.friendly_name || raw.name || ref),
      role: String(raw.role || ""),
      included: raw.included !== false,
      bounds: {
        x: Number(b.x || 0) - Number(origin.x || 0),
        y: Number(b.y || 0) - Number(origin.y || 0),
        width: Number(b.width || 0),
        height: Number(b.height || 0),
      },
      children: [],
    });
    const parent = String(raw.parent_ref || raw.filteredparentid || "").trim();
    if (parent) {
      parentByRef.set(ref, parent);
    }
  }

  for (const [ref, node] of byRef.entries()) {
    const parent = parentByRef.get(ref);
    if (parent && byRef.has(parent)) {
      byRef.get(parent)!.children.push(node);
    } else {
      roots.push(node);
    }
  }

  const sortNodes = (nodes: TreeNode[]): TreeNode[] => {
    nodes.sort((a, b) => {
      if (a.bounds.y !== b.bounds.y) {
        return a.bounds.y - b.bounds.y;
      }
      if (a.bounds.x !== b.bounds.x) {
        return a.bounds.x - b.bounds.x;
      }
      return a.label.localeCompare(b.label);
    });
    for (const node of nodes) {
      node.children = sortNodes(node.children);
    }
    return nodes;
  };

  return sortNodes(roots);
}

// Icon + clean label for a tree node. The snapshot text encodes the element
// type and enabled/disabled state inside the label (e.g. "Open Folder...
// (Button, enabled)"). We surface those as an icon + status colour instead of
// inline bracket text, and strip the annotations from the displayed label.
const TYPE_ICONS: Record<string, string> = {
  Button: "🔘",
  Field: "🔤",
  LOV: "🔽",
  ComboBox: "🔽",
  Checkbox: "☑",
  RadioButton: "⦿",
  Tab: "📑",
  Table: "▦",
  Tree: "🌳",
  Form: "🪟",
  Menu: "≡",
  MenuItem: "•",
  Item: "•",
};

function elementMeta(node: TreeNode): {
  icon: string;
  text: string;
  enabled: boolean;
  typeLabel: string;
} {
  const label = node.label || node.element_ref;
  const enabled = !/disabled/i.test(label);
  let role = node.role || "";
  const low = label.toLowerCase();
  if (!role || role === "Item" || role === "Field") {
    if (low.includes("(button")) role = "Button";
    else if (low.includes("(lov)")) role = "LOV";
    else if (low.includes("(combobox")) role = "ComboBox";
    else if (low.includes("checkbox") || low.startsWith("[ ]") || low.startsWith("[x]")) role = "Checkbox";
    else if (low.includes("tabs:")) role = "Tab";
  }
  // Clean display text: drop "(...)" annotations and a leading "Tabs:" marker.
  let text = label.replace(/\s*\([^)]*\)/g, "").trim();
  text = text.replace(/^Tabs:\s*/i, "").trim();
  if (!text) {
    text = node.element_ref;
  }
  const icon = TYPE_ICONS[role] || "▪";
  return { icon, text, enabled, typeLabel: role || "Element" };
}

function flattenTree(nodes: TreeNode[]): TreeNode[] {
  const out: TreeNode[] = [];
  const walk = (list: TreeNode[]) => {
    for (const node of list) {
      out.push(node);
      walk(node.children || []);
    }
  };
  walk(nodes || []);
  return out;
}

function cloneTree(nodes: TreeNode[]): TreeNode[] {
  return (nodes || []).map((node) => ({
    ...node,
    bounds: { ...node.bounds },
    children: cloneTree(node.children || []),
  }));
}

function insertChild(nodes: TreeNode[], targetRef: string, child: TreeNode): TreeNode[] {
  for (const node of nodes) {
    if (node.element_ref === targetRef) {
      node.children = [...(node.children || []), child];
      return nodes;
    }
    insertChild(node.children || [], targetRef, child);
  }
  return nodes;
}

function inferOrigin(items: TreeElement[]): { x: number; y: number } {
  if (!Array.isArray(items) || items.length === 0) {
    return { x: 0, y: 0 };
  }
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  for (const item of items) {
    const b = item.bounds || {
      x: Number(item.x || 0),
      y: Number(item.y || 0),
      width: Number(item.width || 0),
      height: Number(item.height || 0),
    };
    if (Number(b.width || 0) <= 0 || Number(b.height || 0) <= 0) {
      continue;
    }
    minX = Math.min(minX, Number(b.x || 0));
    minY = Math.min(minY, Number(b.y || 0));
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) {
    return { x: 0, y: 0 };
  }
  return { x: Math.max(0, minX), y: Math.max(0, minY) };
}
