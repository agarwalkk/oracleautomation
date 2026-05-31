package com.pyebsdom.agent;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A node in the EBS Java UI component tree ("DOM node").
 *
 * <p>Each node represents one AWT/Swing component. The tree is built by
 * {@link DomScanner} on the Event Dispatch Thread. All fields are public
 * and mutable during construction, then effectively frozen once the scanner
 * finishes.
 *
 * <p>Children are stored in insertion order. The {@link #id} is a simple
 * sequential integer assigned during tree construction.
 */
public final class DomNode {

    // ── Identity ──────────────────────────────────────────────────────────
    public int    id;
    public String path;
    public String parentPath;
    public int    depth;
    public int    index;
    public int    siblingCount;

    // ── Type ──────────────────────────────────────────────────────────────
    /** Fully-qualified Java class name. */
    public String type;
    /** Human-readable semantic type (e.g. {@code "Field"}, {@code "Button"}). */
    public String semanticType;
    public String className;
    public String simpleClassName;
    public String packageName;

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
    public int     cursorType;
    public String  cursorName;

    // ── Extended attributes (reflection results) ──────────────────────────
    /** Key-value pairs from safe reflection methods, e.g. getItemCount. */
    public final Map<String, String> attributes  = new LinkedHashMap<>();
    /** Raw map from every reflection method that returned a value. */
    public final Map<String, String> reflection  = new LinkedHashMap<>();

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

        // Type
        sb.append("\"type\":").append(JsonUtil.quoted(type)).append(',');
        sb.append("\"semanticType\":").append(JsonUtil.quoted(semanticType)).append(',');
        sb.append("\"className\":").append(JsonUtil.quoted(className)).append(',');
        sb.append("\"simpleClassName\":").append(JsonUtil.quoted(simpleClassName)).append(',');
        sb.append("\"packageName\":").append(JsonUtil.quoted(packageName)).append(',');

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
        sb.append("\"confidence\":").append(String.format("%.2f", confidence)).append(',');
        sb.append("\"valueOptions\":[");
        for (int i = 0; i < valueOptions.size(); i++) {
            if (i > 0) sb.append(',');
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
            if (i > 0) sb.append(',');
            sb.append(locators.get(i).toJson());
        }
        sb.append(']');

        // Children array
        if (includeChildren) {
            sb.append(",\"children\":[");
            for (int i = 0; i < children.size(); i++) {
                if (i > 0) sb.append(',');
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
            if (!first) sb.append(',');
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
                + ", displayName=" + displayName + ", path=" + path + "}";
    }
}
