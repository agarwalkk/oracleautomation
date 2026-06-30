package com.pyebsdom.agent;

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
 * The tree is built by {@link DomScanner} on the Event Dispatch Thread, then
 * enriched by {@link StructureAnnotator} and {@link IdentityResolver} so that
 * <b>structure and identity are fully resolved inside the JVM</b> — Python is a
 * thin renderer and does not re-infer either.
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
     * {@code <scope>::<canonicalLabel>::<ordinal>} (see {@link IdentityResolver}).
     * Persist THIS (never {@code eN}) in recordings and the repository.
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

    /**
     * Oracle Forms item handler id (from {@link FormsHandler}). Unique per item
     * within a form module and Forms-native (more stable than the AWT name), so
     * it is the highest-confidence deterministic locator within a live session.
     * {@code null} for non-Forms-item components.
     */
    public String handlerId;

    // ── Forms item capabilities (from FormsHandler; ground truth, not inferred) ─
    /** Item has a List-of-Values (from {@code isLOVButtonDisplayed()}). */
    public boolean hasLov;
    /** Item is required/mandatory (from {@code mReqdFlag}). */
    public boolean required;
    /** Item is runtime-locked / read-only (from {@code isLocked()}). */
    public boolean locked;
    /**
     * Forms-native item type from the handler peer (e.g. {@code "TextFieldItem"},
     * {@code "CheckboxItem"}) — the authoritative item kind, independent of the
     * AWT widget class. {@code null} for non-Forms-item components.
     */
    public String formsType;
    /**
     * Authoritative owning tab title from the Forms handler
     * ({@code getParentTabName()}). When present it supersedes the layout
     * heuristic that sets {@link #ownerTab}. {@code null} when unavailable.
     */
    public String formsTabName;
    /**
     * Forms item name from the handler ({@code mName}, e.g.
     * {@code "DEMAND_CLASS"}).
     * Authoritative COLUMN identity inside a multi-record block — every cell of a
     * column shares it — and a stable technical id for any item. {@code null} for
     * non-Forms-item components.
     */
    public String formsItemName;
    /**
     * Authoritative actuator verb(s) for this element (comma-separated), derived
     * from the Forms item type + state — e.g. {@code "setText,openLov"},
     * {@code "setCheckbox"}, {@code "pressButton"}, {@code "setPoplist"},
     * {@code "setList"}, {@code "selectRadio"}, {@code "treeAction"}, or
     * {@code "inspect"} for read-only items. {@code null} for non-Forms items
     * (the renderer falls back to its role heuristic).
     */
    public String formsActions;
    /** True when the item has an unsaved edit (handler {@code isDirty()}). */
    public boolean dirty;

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
     * {@link StructureAnnotator}.
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

    // ── JSON serialisation ────────────────────────────────────────────────

    /**
     * Serialises this node (and its descendants recursively) to JSON.
     *
     * @param includeChildren if {@code false} the {@code "children"} array is
     *                        omitted (used for flat list representations).
     */
    public String toJson(boolean includeChildren) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');

        // Identity
        sb.append("\"id\":").append(id).append(',');
        sb.append("\"path\":").append(JsonUtil.quoted(path)).append(',');
        sb.append("\"parentPath\":").append(JsonUtil.quoted(parentPath)).append(',');
        sb.append("\"depth\":").append(depth).append(',');
        sb.append("\"index\":").append(index).append(',');
        sb.append("\"siblingCount\":").append(siblingCount).append(',');
        sb.append("\"semanticId\":").append(JsonUtil.quoted(semanticId)).append(',');
        sb.append("\"primaryLocator\":")
                .append(primaryLocator != null ? primaryLocator.toJson() : "null").append(',');
        sb.append("\"locatorAmbiguous\":").append(locatorAmbiguous).append(',');
        sb.append("\"handlerId\":").append(JsonUtil.quoted(handlerId)).append(',');
        sb.append("\"hasLov\":").append(hasLov).append(',');
        sb.append("\"required\":").append(required).append(',');
        sb.append("\"locked\":").append(locked).append(',');
        sb.append("\"formsType\":").append(JsonUtil.quoted(formsType)).append(',');
        sb.append("\"formsTabName\":").append(JsonUtil.quoted(formsTabName)).append(',');
        sb.append("\"formsItemName\":").append(JsonUtil.quoted(formsItemName)).append(',');
        sb.append("\"formsActions\":").append(JsonUtil.quoted(formsActions)).append(',');
        sb.append("\"dirty\":").append(dirty).append(',');

        // Type
        sb.append("\"type\":").append(JsonUtil.quoted(type)).append(',');
        sb.append("\"semanticType\":").append(JsonUtil.quoted(semanticType)).append(',');
        sb.append("\"className\":").append(JsonUtil.quoted(className)).append(',');
        sb.append("\"simpleClassName\":").append(JsonUtil.quoted(simpleClassName)).append(',');
        sb.append("\"packageName\":").append(JsonUtil.quoted(packageName)).append(',');

        // Structure
        sb.append("\"containerRole\":").append(JsonUtil.quoted(containerRole)).append(',');
        sb.append("\"ownerTab\":").append(JsonUtil.quoted(ownerTab)).append(',');
        sb.append("\"recordIndex\":").append(recordIndex).append(',');
        sb.append("\"columnKey\":").append(JsonUtil.quoted(columnKey)).append(',');
        sb.append("\"current\":").append(current).append(',');
        sb.append("\"isMirror\":").append(isMirror).append(',');
        sb.append("\"treePath\":").append(JsonUtil.quoted(treePath)).append(',');
        sb.append("\"expanded\":").append(expanded).append(',');

        // Labels
        sb.append("\"name\":").append(JsonUtil.quoted(name)).append(',');
        sb.append("\"title\":").append(JsonUtil.quoted(title)).append(',');
        sb.append("\"text\":").append(JsonUtil.quoted(text)).append(',');
        sb.append("\"value\":").append(JsonUtil.quoted(value)).append(',');
        sb.append("\"accessibleName\":").append(JsonUtil.quoted(accessibleName)).append(',');
        sb.append("\"accessibleDescription\":").append(JsonUtil.quoted(accessibleDescription)).append(',');
        sb.append("\"accessibleRole\":").append(JsonUtil.quoted(accessibleRole)).append(',');
        sb.append("\"tooltip\":").append(JsonUtil.quoted(tooltip)).append(',');
        sb.append("\"displayName\":").append(JsonUtil.quoted(displayName)).append(',');
        sb.append("\"canonicalLabel\":").append(JsonUtil.quoted(canonicalLabel)).append(',');
        sb.append("\"confidence\":").append(String.format("%.2f", confidence)).append(',');
        sb.append("\"valueOptions\":[");
        for (int i = 0; i < valueOptions.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(JsonUtil.quoted(valueOptions.get(i)));
        }
        sb.append("],");

        // Geometry
        sb.append("\"bounds\":").append(bounds != null ? bounds.toJson() : "null").append(',');
        sb.append("\"screenBounds\":").append(
                screenBounds != null ? screenBounds.screenToJson() : "null").append(',');

        // State
        sb.append("\"visible\":").append(visible).append(',');
        sb.append("\"showing\":").append(showing).append(',');
        sb.append("\"enabled\":").append(enabled).append(',');
        sb.append("\"focusable\":").append(focusable).append(',');
        sb.append("\"focused\":").append(focused).append(',');
        sb.append("\"editable\":").append(editable).append(',');
        sb.append("\"selected\":").append(selected).append(',');
        sb.append("\"cursorType\":").append(cursorType).append(',');
        sb.append("\"cursorName\":").append(JsonUtil.quoted(cursorName)).append(',');

        // Attributes map
        sb.append("\"attributes\":").append(mapToJson(attributes)).append(',');

        // Reflection map
        sb.append("\"reflection\":").append(mapToJson(reflection)).append(',');

        // Locators array
        sb.append("\"locators\":[");
        for (int i = 0; i < locators.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(locators.get(i).toJson());
        }
        sb.append(']');

        // Children array
        if (includeChildren) {
            sb.append(",\"children\":[");
            for (int i = 0; i < children.size(); i++) {
                if (i > 0)
                    sb.append(',');
                sb.append(children.get(i).toJson(true));
            }
            sb.append(']');
        }

        sb.append('}');
        return sb.toString();
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    private static String mapToJson(Map<String, String> map) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : map.entrySet()) {
            if (!first)
                sb.append(',');
            first = false;
            sb.append(JsonUtil.quoted(e.getKey()))
                    .append(':')
                    .append(JsonUtil.quoted(e.getValue()));
        }
        sb.append('}');
        return sb.toString();
    }

    @Override
    public String toString() {
        return "DomNode{id=" + id + ", semanticType=" + semanticType
                + ", containerRole=" + containerRole
                + ", semanticId=" + semanticId + ", path=" + path + "}";
    }
}
