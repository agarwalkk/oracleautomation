package com.pyebsdom.agent.model;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A node in the EBS Java UI component tree ("DOM node").
 *
 * <p>
 * Each node represents one AWT/Swing component OR a logical item that the
 * agent has promoted to a first-class node (tree rows, menu items, grid cells).
 * The tree is built by {@code extract.DomScanner} on the Event Dispatch Thread,
 * then enriched by {@code extract.StructureAnnotator} and
 * {@code extract.IdentityResolver} so that <b>structure and identity are fully
 * resolved inside the JVM</b> — Python is a thin renderer and does not re-infer
 * either.
 *
 * <p>
 * This is a <b>pure data object</b>: it carries no serialisation logic. JSON
 * rendering lives in {@code render.DomJson}; the plain-text layout lives in
 * {@code render.TextLayout}. Keeping the model free of output concerns lets the
 * same node feed multiple renderers without change.
 *
 * <p>
 * Children are stored in insertion order. The {@link #id} is a per-scan
 * sequential integer; it is NOT stable across scans. Use {@link #semanticId}
 * for cross-scan identity and {@link #primaryLocator} for deterministic replay.
 */
public final class DomNode {

    // ── Identity ──────────────────────────────────────────────────────────
    public int id;
    public String path;
    public String parentPath;
    public int depth;
    public int index;
    public int siblingCount;

    /**
     * Stable, human-readable cross-scan identity, of the form
     * {@code <scope>::<canonicalLabel>::<ordinal>} (see
     * {@code extract.IdentityResolver}). Persist THIS (never {@code eN}) in
     * recordings and the repository.
     */
    public String semanticId;

    /**
     * The single locator the agent has verified resolves to exactly one live
     * component in this scan. Replay should prefer this over the
     * {@link #locators} candidate list. {@code null} only when no unique
     * locator could be constructed (rare; flagged via {@link #locatorAmbiguous}).
     */
    public LocatorCandidate primaryLocator;
    /**
     * True when more than one node shares this node's best label and only an
     * ordinal/recordIndex disambiguates it.
     */
    public boolean locatorAmbiguous;

    // ── Type ──────────────────────────────────────────────────────────────
    /** Fully-qualified Java class name. */
    public String type;
    /** Human-readable semantic type (e.g. {@code "Field"}, {@code "Button"}). */
    public String semanticType;
    public String className;
    public String simpleClassName;
    public String packageName;

    // ── Structure (resolved in Java; Python must not re-infer) ────────────
    /**
     * Structural role of this node within the form layout. One of:
     * {@code TabFolder, TabPage, Grid, GridRow, GridCell, TreeItem, FieldGroup,
     * Mirror} — or {@code null} for ordinary leaf controls. Set by
     * {@code extract.StructureAnnotator}.
     */
    public String containerRole;
    /**
     * For content owned by a tab page: the owning tab title (deterministic,
     * from TabPanelSheet ancestry — NOT a name-prefix guess).
     */
    public String ownerTab;
    /** Zero-based record/row index when this node is inside a multi-record grid. */
    public int recordIndex = -1;
    /** Canonical column key when this node is a grid cell (un-prefixed label). */
    public String columnKey;
    /** True when this is the currently-selected record/row/tree-item. */
    public boolean current;
    /**
     * True when this is a read-only echo of another editable field (a mirror).
     * Decided from the live item + editability, not a name regex.
     */
    public boolean isMirror;
    /**
     * Tree-item path chain, root→leaf, e.g. {@code "Orders Tree/Personal Folders"}.
     */
    public String treePath;
    /** True when an expandable tree-item is expanded. */
    public boolean expanded;

    // ── Labels / text ─────────────────────────────────────────────────────
    public String name;
    public String title;
    public String text;
    public String value;
    public String accessibleName;
    public String accessibleDescription;
    public String accessibleRole;
    public String tooltip;
    /** Composite best-effort display name, resolved from the fields above. */
    public String displayName;
    /**
     * Canonical, un-prefixed label used for identity/columns (e.g. "Order
     * Number" rather than "Quote/Order Information tab page Order Number").
     */
    public String canonicalLabel;
    /** Confidence [0,1] in the displayName being correct/useful. */
    public double confidence;
    /** Safe, read-only option values exposed by list/combo components. */
    public final List<String> valueOptions = new ArrayList<>();

    // ── Geometry ──────────────────────────────────────────────────────────
    public Bounds bounds;
    public Bounds screenBounds;

    // ── State ─────────────────────────────────────────────────────────────
    public boolean visible;
    public boolean showing;
    public boolean enabled;
    public boolean focusable;
    public boolean focused;
    public boolean editable;
    public boolean selected;
    public int cursorType;
    public String cursorName;

    // ── Extended attributes (reflection results) ──────────────────────────
    /** Key-value pairs from safe reflection methods, e.g. getItemCount. */
    public final Map<String, String> attributes = new LinkedHashMap<>();
    /** Raw map from every reflection method that returned a value. */
    public final Map<String, String> reflection = new LinkedHashMap<>();

    // ── Locators ──────────────────────────────────────────────────────────
    public final List<LocatorCandidate> locators = new ArrayList<>();

    // ── Tree ──────────────────────────────────────────────────────────────
    public final List<DomNode> children = new ArrayList<>();

    @Override
    public String toString() {
        return "DomNode{id=" + id + ", semanticType=" + semanticType
                + ", containerRole=" + containerRole
                + ", semanticId=" + semanticId + ", path=" + path + "}";
    }
}
