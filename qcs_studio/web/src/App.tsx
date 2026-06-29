import { useEffect, useMemo, useRef, useState } from "react";

type ContainerSummary = {
  container_ref: string;
  title: string;
  screenshot?: string | null;
  status?: string;
  scan_id?: string;
  has_tree?: boolean;
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
  descriptor?: string;
  locator_params?: Record<string, unknown>;
};

type ScanTarget = {
  source: string;
  pid: number;
  label: string;
  title?: string;
  type?: string;
};

type ScanResult = {
  scan_id: string;
  title: string;
  container_ref: string;
  snapshot_text?: string;
  capture_mode?: string;
  screenshot_path: string;
  tree: TreeNode[];
  full_elements?: FullElement[];
  tab_screenshots?: Record<string, string>;
};

type TableCell = {
  element_ref: string;
  current_value?: string;
  role?: string;
};

type TableRow = {
  marker: string;
  cells: TableCell[];
};

type TreeNode = {
  element_ref: string;
  label: string;
  role: string;
  included: boolean;
  bounds: { x: number; y: number; width: number; height: number };
  children: TreeNode[];
  tab_path?: string;
  // Table-specific fields (preserved through normalizeTree).
  table_columns?: string[];
  table_rows?: TableRow[];
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
  // Hover mode: "tree" = only tree elements highlight (default),
  // "all" = every scanned element from raw DOM highlights.
  const [hoverMode, setHoverMode] = useState<"tree" | "all">("tree");
  // Tree node hover tooltip: tracks which element's full details to show and where.
  const [treeTooltip, setTreeTooltip] = useState<{ fe: FullElement; x: number; y: number } | null>(null);

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
      // Phase 1: capture raw DOM + screenshot (no tree yet).
      const res = await fetch("/api/v1/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const phase1 = await res.json();
      const scanId = phase1.scan_id;
      // Show the screenshot immediately so the user sees instant feedback.
      setScan(phase1);
      setEditTitle(phase1.title || "");
      setDetail(null);
      setSelectedRef("");

      // Phase 2: compute AI snapshot tree from the captured raw DOM.
      // This can fail independently; if it does, the user can retry via
      // the "Recalculate Tree" button on the draft entry.
      try {
        const recalcRes = await fetch("/api/v1/scan/recalculate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scan_id: scanId })
        });
        const phase2 = await recalcRes.json();
        setScan(phase2);
      } catch {
        // Phase 2 failed — screenshot still shows, user can retry later.
      }

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

  async function recalculateDraft(scanId: string) {
    setBusy(true);
    try {
      const res = await fetch("/api/v1/scan/recalculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_id: scanId })
      });
      const json = await res.json();
      // If this draft is currently the active scan, refresh the view.
      if (scan?.scan_id === scanId) {
        setScan(json);
        setEditTitle(json.title || "");
      }
      await refreshContainers();
    } finally {
      setBusy(false);
    }
  }

  async function deleteDraft(scanId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setBusy(true);
    try {
      await fetch(`/api/v1/drafts/${encodeURIComponent(scanId)}`, { method: "DELETE" });
      if (scan?.scan_id === scanId) {
        setScan(null);
        setDetail(null);
        setSelectedRef("");
      }
      await refreshContainers();
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

  // WHY: Screenshots are no longer cropped, so element coordinates already
  // align with the full screenshot. No origin adjustment needed.
  const baseTree = useMemo<TreeNode[]>(() => {
    if (scan?.tree?.length) {
      return normalizeTree(scan.tree, fullByRef);
    }
    if (detail) {
      const displayTree = detail.metadata?.display_tree as TreeNode[] | undefined;
      if (Array.isArray(displayTree) && displayTree.length > 0) {
        return normalizeTree(displayTree, fullByRef);
      }
      if (detail.tree?.length) {
        return normalizeTree(detail.tree, fullByRef);
      }
    }
    return [];
  }, [detail, scan, fullByRef]);

  // Reset any pending tree edits whenever the active scan/container changes.
  useEffect(() => {
    setTreeOverride(null);
  }, [scan, detail]);

  const activeTree = treeOverride ?? baseTree;

  // Wrap the tree in a synthetic root node showing the container title.
  // This gives the tree a clear top-level name instead of raw element refs.
  const containerTitle = useMemo(() => {
    if (scan?.title) {
      return scan.title;
    }
    if (detail?.title) {
      return detail.title;
    }
    return null;
  }, [scan, detail]);

  const displayTree = useMemo<TreeNode[]>(() => {
    if (!containerTitle || activeTree.length === 0) {
      return activeTree;
    }
    // Compute a bounding box covering all root nodes for the synthetic root.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of activeTree) {
      minX = Math.min(minX, node.bounds.x);
      minY = Math.min(minY, node.bounds.y);
      maxX = Math.max(maxX, node.bounds.x + node.bounds.width);
      maxY = Math.max(maxY, node.bounds.y + node.bounds.height);
    }
    const rootNode: TreeNode = {
      element_ref: "__container_root__",
      label: containerTitle,
      role: "Form",
      included: true,
      bounds: {
        x: Number.isFinite(minX) ? minX : 0,
        y: Number.isFinite(minY) ? minY : 0,
        width: Number.isFinite(minX) ? Math.max(1, maxX - minX) : 1,
        height: Number.isFinite(minY) ? Math.max(1, maxY - minY) : 1,
      },
      children: activeTree,
    };
    return [rootNode];
  }, [activeTree, containerTitle]);

  // Elements present on screen but not yet in the curated tree — these are the
  // draggable "discovery" elements the user can drop onto a tree node.
  const discoveryElements = useMemo(() => {
    const inTree = new Set(flattenTree(displayTree).map((n) => n.element_ref));
    return fullElements.filter((fe) => !inTree.has(fe.element_ref) && fe.bounds.width > 0 && fe.bounds.height > 0);
  }, [fullElements, displayTree]);

  function addElementToNode(targetRef: string, elementRef: string) {
    const fe = fullByRef.get(elementRef);
    if (!fe) {
      return;
    }
    const current = treeOverride ?? displayTree;
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


  const tabPathMap = useMemo(() => {
    return buildTabPathMap(displayTree);
  }, [displayTree]);

  const activeScreenshot = useMemo(() => {
    let tabPath: string | undefined = undefined;
    const targetRef = hoveredRef || selectedRef;
    if (targetRef) {
      tabPath = tabPathMap[targetRef];
    }

    if (scan?.screenshot_path) {
      const base = `/api/v1/scans/${encodeURIComponent(scan.scan_id)}/screenshot`;
      if (tabPath && scan.tab_screenshots && scan.tab_screenshots[tabPath]) {
        return `${base}?tab=${encodeURIComponent(tabPath)}`;
      }
      return base;
    }
    return screenshotUrl(detail);
  }, [detail, scan, hoveredRef, selectedRef, tabPathMap]);

  useEffect(() => {
    setExpanded(explorerModeExpandedState(displayTree));
  }, [displayTree]);

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
            {targets.map((target) => {
              const displayLabel = target.title && target.type
                ? `[${target.pid}] ${target.title} (${target.type})`
                : target.title
                  ? `[${target.pid}] ${target.title}`
                  : target.label;
              return (
                <option key={target.pid} value={target.pid}>
                  {displayLabel}
                </option>
              );
            })}
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
                  onClick={async () => {
                    if (container.status === "draft" && container.scan_id) {
                      // Load the draft from the in-memory cache.
                      const res = await fetch(`/api/v1/drafts/${encodeURIComponent(container.scan_id)}`);
                      if (res.ok) {
                        const json = await res.json();
                        setScan(json);
                        setEditTitle(json.title || "");
                        setDetail(null);
                        setSelectedRef("");
                      }
                    } else {
                      loadContainer(container.container_ref);
                    }
                  }}
                >
                  <span className="container-title-row">
                    <span className="container-title">{container.title || container.container_ref}</span>
                    {container.status === "draft" ? <span className="badge-new">Draft</span> : null}
                  </span>
                  <small>{container.container_ref}</small>
                  {container.status === "draft" && container.scan_id ? (
                    <div className="draft-actions" onClick={(e) => e.stopPropagation()}>
                      <button
                        className="draft-action-btn"
                        title="Recalculate tree from saved raw DOM"
                        onClick={() => recalculateDraft(container.scan_id!)}
                        disabled={busy}
                      >
                        ↻ Recalc
                      </button>
                      <button
                        className="draft-action-btn draft-delete-btn"
                        title="Delete draft"
                        onClick={(e) => deleteDraft(container.scan_id!, e)}
                        disabled={busy}
                      >
                        ✕
                      </button>
                    </div>
                  ) : null}
                </button>
              ))}
            </div>
            {displayTree.length > 0 && (
              <div className="tree-divider">
                {treeOverride && detail && !scan ? (
                  <div className="tree-toolbar">
                    <button className="tree-save-btn" onClick={saveDisplayTree} disabled={busy}>
                      Save Tree
                    </button>
                  </div>
                ) : null}
                <div className="tree-list">
                  {renderTreeRows(displayTree, hoveredRef, setHoveredRef, expanded, toggleNode, addElementToNode, fullByRef, (fe, x, y) => setTreeTooltip(fe ? { fe, x, y } : null))}
                </div>
              </div>
            )}
          </section>
          {/* Fixed-position tooltip for tree node hover — rendered outside
              the scroll container so it isn't clipped. */}
          {treeTooltip ? (
            <div className="tree-tooltip" style={{ left: treeTooltip.x + 14, top: treeTooltip.y + 8 }}>
              <div className="tt-row"><strong>{treeTooltip.fe.name || treeTooltip.fe.element_ref}</strong></div>
              <div className="tt-row">Ref: <code>{treeTooltip.fe.element_ref}</code></div>
              <div className="tt-row">
                Bounds: x={treeTooltip.fe.bounds.x}, y={treeTooltip.fe.bounds.y},
                w={treeTooltip.fe.bounds.width}, h={treeTooltip.fe.bounds.height}
              </div>
              {treeTooltip.fe.role ? <div className="tt-row">Role: {treeTooltip.fe.role}</div> : null}
              {treeTooltip.fe.type ? <div className="tt-row">Type: {treeTooltip.fe.type}</div> : null}
              {treeTooltip.fe.value ? <div className="tt-row">Value: <code>{treeTooltip.fe.value}</code></div> : null}
              {treeTooltip.fe.actions?.length ? <div className="tt-row">Actions: {treeTooltip.fe.actions.join(", ")}</div> : null}
              {treeTooltip.fe.enabled === false ? <div className="tt-row">⚠ Disabled</div> : null}
              {treeTooltip.fe.descriptor ? <div className="tt-row">Descriptor: <code>{treeTooltip.fe.descriptor}</code></div> : null}
              {treeTooltip.fe.locator_params && Object.keys(treeTooltip.fe.locator_params).length > 0 ? (
                <div className="tt-row">
                  Locator params:
                  <pre className="tt-pre">{JSON.stringify(treeTooltip.fe.locator_params, null, 2)}</pre>
                </div>
              ) : null}
            </div>
          ) : null}
        </aside>

        <section className="canvas-panel">
          <div className="canvas-header">
            <div className="canvas-title">
              {scan ? (
                <>
                  <span className="badge-new">Draft</span>
                  <input
                    className="title-input"
                    value={editTitle}
                    onChange={(e) => setEditTitle(e.target.value)}
                    placeholder="Container name"
                    title="Edit container name, then Save Scan"
                  />
                  {!scan.tree?.length ? (
                    <button
                      className="recalc-btn"
                      disabled={busy}
                      onClick={() => recalculateDraft(scan.scan_id)}
                      title="Calculate AI snapshot tree from raw DOM"
                    >
                      ↻ Compute Tree
                    </button>
                  ) : null}
                </>
              ) : (
                <h2>{detail?.title || "No container selected"}</h2>
              )}
            </div>
            <div className="zoom-controls">
              {scan ? <span className="capture-mode">{scan.capture_mode || "unknown"}</span> : null}
              <button
                type="button"
                className={hoverMode === "tree" ? "toggle-btn active" : "toggle-btn"}
                onClick={() => setHoverMode((m) => (m === "tree" ? "all" : "tree"))}
                title={hoverMode === "tree" ? "Hover: Tree elements only. Click to show ALL scanned elements." : "Hover: All scanned elements. Click to show tree only."}
              >
                {hoverMode === "tree" ? "🌳 Tree" : "⊞ All"}
              </button>
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
                tree={displayTree}
                hoveredRef={hoveredRef}
                onHover={(ref) => setHoveredRef(ref)}
                zoom={zoom}
                fitMode={fitMode}
                discovery={discoveryElements}
                fullByRef={fullByRef}
                hoverMode={hoverMode}
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
  hoverMode: "tree" | "all";
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

  // All elements from raw DOM (fullByRef values).
  // Sort by area DESCENDING so the smallest elements render last (on top)
  // and catch mouse/drag events before larger parent containers at the
  // same screen location.
  const allElements = useMemo<FullElement[]>(() => {
    const arr = Array.from(props.fullByRef.values());
    arr.sort((a, b) => (b.bounds.width * b.bounds.height) - (a.bounds.width * a.bounds.height));
    return arr;
  }, [props.fullByRef]);

  // Discovery elements sorted by area descending (smallest on top).
  const discoverySorted = useMemo<FullElement[]>(() => {
    const arr = [...props.discovery];
    arr.sort((a, b) => (b.bounds.width * b.bounds.height) - (a.bounds.width * a.bounds.height));
    return arr;
  }, [props.discovery]);

  // Flat tree nodes sorted by area descending (smallest on top).
  const flatNodesSorted = useMemo<TreeNode[]>(() => {
    const arr = [...flatNodes];
    arr.sort((a, b) => (b.bounds.width * b.bounds.height) - (a.bounds.width * a.bounds.height));
    return arr;
  }, [flatNodes]);

  // Elements in the tree (for dimming in "all" mode).
  const treeRefs = useMemo(() => new Set(flatNodesSorted.map((n) => n.element_ref)), [flatNodesSorted]);

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
        {props.hoverMode === "all" ? (
          /* "All" mode: every scanned element from raw DOM is hoverable and
             draggable onto the tree, but invisible until hovered — same
             visual behavior as discovery elements in "tree" mode. */
          <>
            <div className="overlay-layer discovery-layer">
              {allElements.map((fe) => {
                const b = fe.bounds;
                if (!b.width || !b.height) {
                  return null;
                }
                return (
                  <div
                    key={`${fe.element_ref}-${b.x}-${b.y}`}
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
                    onMouseEnter={(e) => {
                      props.onHover(fe.element_ref);
                      showTip(fe, e);
                    }}
                    onMouseMove={(e) => showTip(fe, e)}
                    onMouseLeave={() => {
                      props.onHover("");
                      setTip(null);
                    }}
                  />
                );
              })}
            </div>
          </>
        ) : (
          /* "Tree" mode: current default — tree elements are primary
             (colored + hover), discovery elements are transparent + draggable. */
          <>
            <div className="overlay-layer discovery-layer">
              {discoverySorted.map((fe) => {
                const b = fe.bounds;
                if (!b.width || !b.height) {
                  return null;
                }
                return (
                  <div
                    key={`${fe.element_ref}-${b.x}-${b.y}`}
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
              {flatNodesSorted.map((node) => {
                const b = node.bounds;
                if (!b.width || !b.height) {
                  return null;
                }
                return (
                  <button
                    type="button"
                    key={`${node.element_ref || "ref"}-${b.x}-${b.y}`}
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
          </>
        )}
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
  fullByRef: Map<string, FullElement>,
  showTooltip: (fe: FullElement | null, x: number, y: number) => void,
  depth = 0,
  keyPrefix = ""
): JSX.Element[] {
  const rows: JSX.Element[] = [];
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const hasChildren = (node.children || []).length > 0;
    const isExpanded = hasChildren ? !!expanded[node.element_ref] : false;
    const meta = elementMeta(node);
    const fe = fullByRef.get(node.element_ref);
    const isRoot = node.element_ref === "__container_root__";
    // Build a unique key: parent path + index + ref.  This prevents React
    // key collisions when the merged multi-tab DOM produces nodes that
    // share the same element_ref at different tree positions.
    const nodeKey = `${keyPrefix}${i}:${node.element_ref}`;
    rows.push(
      <div
        key={nodeKey}
        className={`tree-item${hoveredRef === node.element_ref ? " hovered" : ""}${isRoot ? " tree-root" : ""}`}
        style={{ paddingLeft: `${6 + depth * 14}px` }}
        onMouseEnter={(e) => {
          setHoveredRef(node.element_ref);
          if (!isRoot && fe) {
            showTooltip(fe, e.clientX, e.clientY);
          }
        }}
        onMouseMove={(e) => {
          if (!isRoot && fe) {
            showTooltip(fe, e.clientX, e.clientY);
          }
        }}
        onMouseLeave={() => {
          setHoveredRef("");
          showTooltip(null, 0, 0);
        }}
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
        >
          {meta.icon}
        </span>
        <span className="tree-label">
          {isRoot ? `📁 ${meta.text}` : meta.text}
        </span>
      </div>
    );
    if (hasChildren && isExpanded) {
      rows.push(...renderTreeRows(node.children, hoveredRef, setHoveredRef, expanded, toggleNode, onDropElement, fullByRef, showTooltip, depth + 1, `${nodeKey}/`));
    }
  }
  return rows;
}

function explorerModeExpandedState(nodes: TreeNode[]): Record<string, boolean> {
  // Auto-expand the synthetic container root node so the tree is visible
  // by default. All other nodes start collapsed.
  const state: Record<string, boolean> = {};
  for (const node of nodes) {
    if (node.element_ref === "__container_root__") {
      state[node.element_ref] = true;
    }
  }
  return state;
}

function computeEnclosingBounds(children: TreeNode[]): { x: number; y: number; width: number; height: number } | undefined {
  if (!children || children.length === 0) {
    return undefined;
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  let hasValid = false;
  for (const child of children) {
    if (child.bounds) {
      minX = Math.min(minX, child.bounds.x);
      minY = Math.min(minY, child.bounds.y);
      maxX = Math.max(maxX, child.bounds.x + child.bounds.width);
      maxY = Math.max(maxY, child.bounds.y + child.bounds.height);
      hasValid = true;
    }
  }
  if (!hasValid) {
    return undefined;
  }
  return {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

function fillAndAdjustTree(nodes: TreeNode[], fullByRef?: Map<string, FullElement>): TreeNode[] {
  return nodes.map((node) => {
    let children = node.children || [];
    if (children.length > 0) {
      children = fillAndAdjustTree(children, fullByRef);
      const enclosing = computeEnclosingBounds(children);
      return {
        ...node,
        bounds: enclosing || node.bounds || { x: 0, y: 0, width: 0, height: 0 },
        children,
      };
    } else {
      let bounds = node.bounds;
      if (!bounds || (bounds.width === 0 && bounds.height === 0)) {
        const lookedUp = node.element_ref && fullByRef?.get(node.element_ref)?.bounds;
        if (lookedUp) {
          bounds = lookedUp;
        }
      }
      return {
        ...node,
        bounds: bounds || { x: 0, y: 0, width: 0, height: 0 },
      };
    }
  });
}

// WHY: No origin parameter — screenshots are no longer cropped so coordinates
// are raw (unadjusted) and align directly with the full screenshot.
function normalizeTree(input: TreeElement[] | TreeNode[], fullByRef?: Map<string, FullElement>): TreeNode[] {
  const items = input || [];
  if (!items.length) {
    return [];
  }

  const first = items[0] as Partial<TreeNode>;
  let result: TreeNode[];
  if (Array.isArray(first.children)) {
    result = (items as TreeNode[]).map((node) => {
      // For Table nodes, convert table_rows into child TreeNodes so the rows
      // appear as expandable entries in the left panel tree.
      let children = normalizeTree(node.children || [], fullByRef);
      if (node.role === "Table" && node.table_rows && node.table_rows.length > 0 && children.length === 0) {
        const cols = node.table_columns || [];
        children = node.table_rows.map((row) => {
          const rowLabel = `Row ${row.marker.replace(/\*\*/g, "")}` +
            (row.cells[0]?.current_value ? ` — ${row.cells[0].current_value}` : "");
          const cellChildren: TreeNode[] = row.cells
            .map((cell, ci) => {
              if (!cell.element_ref) return null;
              return {
                element_ref: cell.element_ref,
                label: `${cols[ci] ?? `Col ${ci + 1}`}: ${cell.current_value ?? ""}`,
                role: cell.role || "Field",
                included: true,
                bounds: fullByRef?.get(cell.element_ref)?.bounds || { x: 0, y: 0, width: 0, height: 0 },
                children: [],
              } as TreeNode;
            })
            .filter((c): c is TreeNode => c !== null);
          // Enclosing bounds = union of all visible cells in the row.
          let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
          let hasBounds = false;
          for (const c of cellChildren) {
            if (c.bounds.width > 0 && c.bounds.height > 0) {
              minX = Math.min(minX, c.bounds.x);
              minY = Math.min(minY, c.bounds.y);
              maxX = Math.max(maxX, c.bounds.x + c.bounds.width);
              maxY = Math.max(maxY, c.bounds.y + c.bounds.height);
              hasBounds = true;
            }
          }
          return {
            element_ref: `${node.element_ref}_row_${row.marker.replace(/\*\*/g, "")}`,
            label: rowLabel,
            role: "TableRow",
            included: true,
            bounds: hasBounds
              ? { x: minX, y: minY, width: Math.max(1, maxX - minX), height: Math.max(1, maxY - minY) }
              : { x: 0, y: 0, width: 0, height: 0 },
            children: cellChildren,
          } as TreeNode;
        });
      }
      return {
        ...node,
        children,
      };
    });
  } else {
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
          x: Number(b.x || 0),
          y: Number(b.y || 0),
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
        if (a.bounds?.y !== b.bounds?.y) {
          return (a.bounds?.y || 0) - (b.bounds?.y || 0);
        }
        if (a.bounds?.x !== b.bounds?.x) {
          return (a.bounds?.x || 0) - (b.bounds?.x || 0);
        }
        return a.label.localeCompare(b.label);
      });
      for (const node of nodes) {
        node.children = sortNodes(node.children);
      }
      return nodes;
    };

    result = sortNodes(roots);
  }

  return fillAndAdjustTree(result, fullByRef);
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
  TableRow: "≡",
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
  const label = node.label ?? node.element_ref ?? "";
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

function buildTabPathMap(nodes: TreeNode[], parentPath: string[] = []): Record<string, string> {
  const map: Record<string, string> = {};
  for (const node of nodes) {
    const currentPath = [...parentPath];
    if (node.role === "Tab") {
      const tabName = node.label.endsWith(" Tab") ? node.label.slice(0, -4) : node.label;
      currentPath.push(tabName);
    }
    if (currentPath.length > 0) {
      map[node.element_ref] = currentPath.join(" -> ");
    }
    if (node.children && node.children.length > 0) {
      Object.assign(map, buildTabPathMap(node.children, currentPath));
    }
  }
  return map;
}


