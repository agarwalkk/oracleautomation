package com.pyebsdom.agent.render;

import com.pyebsdom.agent.extract.DomScanner;
import com.pyebsdom.agent.json.Json;
import com.pyebsdom.agent.json.Results;
import com.pyebsdom.agent.model.Bounds;
import com.pyebsdom.agent.model.DomNode;
import com.pyebsdom.agent.model.LocatorCandidate;
import com.pyebsdom.agent.model.TableModel;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Renders the extraction model to the JSON the Python clients consume.
 *
 * <h3>Size policy — omit-at-default (lossless)</h3>
 * Fields are emitted only when they carry information: empty strings, empty
 * arrays/maps, {@code false} flags that default to false,
 * {@code recordIndex==-1},
 * {@code cursorType==0}, {@code confidence==0.0}, and null objects are omitted.
 * A consumer reading a missing key with {@code .get(key, default)} sees exactly
 * the value it would have read before — so nothing useful is lost; the dump is
 * just smaller (measured ~29% on a live Order Organizer scan).
 *
 * <p>
 * Two fields are kept deliberately even when "default": {@code visible} /
 * {@code showing} / {@code enabled} (state a consumer may assume; nearly always
 * non-default anyway), and the structural ints
 * {@code id/depth/index/siblingCount}
 * (0 is a valid value). The {@code path}-strategy entry is dropped from
 * {@code locators[]} because it duplicates the node's own {@code path} (which
 * the
 * locator resolver already uses); every other locator strategy is kept.
 *
 * <p>
 * {@code parentPath} is retained (it is convenient for flat processing) even
 * though it is derivable from the tree — see JSON-SIZE-REVIEW for the optional
 * further cut.
 */
public final class DomJson {

    private DomJson() {
        /* static utility */ }

    // ── Top-level envelopes ───────────────────────────────────────────────

    /** The {@code scan} / {@code raw} response envelope. */
    public static String scanResult(DomScanner.ScanResult result, String command) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(Json.quoted(command)).append(',');
        sb.append("\"agent\":{")
                .append("\"name\":\"").append(Results.AGENT_NAME).append("\",")
                .append("\"version\":\"").append(Results.AGENT_VERSION).append("\"")
                .append(",\"schema\":\"2.0\"")
                .append("},");
        sb.append("\"scan\":{")
                .append("\"timestamp\":").append(Json.quoted(result.timestamp)).append(',')
                .append("\"windowCount\":").append(result.windowCount).append(',')
                .append("\"visibleWindowCount\":").append(result.visibleWindowCount).append(',')
                .append("\"raw\":").append(result.raw)
                .append("},");
        sb.append("\"windows\":[");
        for (int i = 0; i < result.windows.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(node(result.windows.get(i), true));
        }
        sb.append("]}");
        return sb.toString();
    }

    /** The {@code tables} response envelope. */
    public static String tables(List<TableModel> tables) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"tables\",");
        sb.append("\"tableCount\":").append(tables.size()).append(',');
        sb.append("\"tables\":[");
        for (int i = 0; i < tables.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(table(tables.get(i)));
        }
        sb.append("]}");
        return sb.toString();
    }

    // ── DomNode ───────────────────────────────────────────────────────────

    /**
     * Serialises one node (and, when requested, its descendants), omitting
     * fields at their default value.
     */
    public static String node(DomNode n, boolean includeChildren) {
        Obj o = new Obj();

        // Identity — structural ints always (0 is valid)
        o.raw("id", Integer.toString(n.id));
        o.str("path", n.path);
        o.str("parentPath", n.parentPath);
        o.raw("depth", Integer.toString(n.depth));
        o.raw("index", Integer.toString(n.index));
        o.raw("siblingCount", Integer.toString(n.siblingCount));
        o.str("semanticId", n.semanticId);
        o.json("primaryLocator", n.primaryLocator != null ? locator(n.primaryLocator) : null);
        o.bool("locatorAmbiguous", n.locatorAmbiguous);

        // Type
        o.str("semanticType", n.semanticType);
        o.str("className", n.className);
        o.str("simpleClassName", n.simpleClassName);

        // Structure
        o.str("containerRole", n.containerRole);
        o.str("ownerTab", n.ownerTab);
        o.intD("recordIndex", n.recordIndex, -1);
        o.str("columnKey", n.columnKey);
        o.bool("current", n.current);
        o.bool("isMirror", n.isMirror);
        o.str("treePath", n.treePath);
        o.bool("expanded", n.expanded);

        // Labels
        o.str("name", n.name);
        o.str("title", n.title);
        o.str("text", n.text);
        o.str("value", n.value);
        o.str("accessibleName", n.accessibleName);
        o.str("accessibleDescription", n.accessibleDescription);
        o.str("accessibleRole", n.accessibleRole);
        o.str("tooltip", n.tooltip);
        o.str("canonicalLabel", n.canonicalLabel);
        o.dec("confidence", n.confidence);
        o.json("valueOptions", stringArrayOrNull(n.valueOptions));

        // Geometry — bounds kept when present; screenBounds only when on-screen
        o.json("bounds", n.bounds != null ? bounds(n.bounds) : null);
        o.json("screenBounds",
                (n.screenBounds != null && n.screenBounds.hasScreen()) ? screenBounds(n.screenBounds) : null);

        // State — visible/showing/enabled kept; the rest omit-when-false
        o.raw("visible", Boolean.toString(n.visible));
        o.raw("showing", Boolean.toString(n.showing));
        o.raw("enabled", Boolean.toString(n.enabled));
        o.bool("focusable", n.focusable);
        o.bool("focused", n.focused);
        o.bool("editable", n.editable);
        o.bool("selected", n.selected);
        o.intD("cursorType", n.cursorType, 0);
        o.str("cursorName", n.cursorName);

        // Attributes (structural; colors + "null"/empty already filtered out)
        o.json("attributes", attributesOrNull(n.attributes));

        // Locators (the path-strategy entry duplicates node.path — dropped)
        o.json("locators", locatorsArrayOrNull(n.locators));

        // Children
        if (includeChildren) {
            o.json("children", childrenArrayOrNull(n.children));
        }
        return o.done();
    }

    // ── Value objects ─────────────────────────────────────────────────────

    public static String bounds(Bounds b) {
        return "{\"x\":" + b.x + ",\"y\":" + b.y
                + ",\"width\":" + b.width + ",\"height\":" + b.height + "}";
    }

    public static String screenBounds(Bounds b) {
        if (!b.hasScreen())
            return "null";
        return "{\"x\":" + b.screenX + ",\"y\":" + b.screenY
                + ",\"width\":" + b.screenWidth + ",\"height\":" + b.screenHeight + "}";
    }

    public static String locator(LocatorCandidate l) {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"strategy\":").append(Json.quoted(l.strategy)).append(',');
        sb.append("\"value\":").append(Json.quoted(l.value)).append(',');
        sb.append("\"confidence\":").append(Json.decimal2(l.confidence)).append(',');
        sb.append("\"verifiedUnique\":").append(l.verifiedUnique);
        if (l.scope != null) {
            sb.append(",\"scope\":").append(Json.quoted(l.scope));
        }
        if (l.ordinal >= 0) {
            sb.append(",\"ordinal\":").append(l.ordinal);
        }
        sb.append('}');
        return sb.toString();
    }

    public static String table(TableModel t) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"id\":").append(t.id).append(',');
        sb.append("\"path\":").append(Json.quoted(t.path)).append(',');
        sb.append("\"name\":").append(Json.quoted(t.name)).append(',');
        sb.append("\"title\":").append(Json.quoted(t.title)).append(',');
        sb.append("\"source\":").append(Json.quoted(t.source)).append(',');
        sb.append("\"confidence\":").append(Json.decimal2(t.confidence)).append(',');

        sb.append("\"columns\":").append(stringArray(t.columns)).append(',');

        sb.append("\"visibleRows\":[");
        for (int r = 0; r < t.visibleRows.size(); r++) {
            if (r > 0)
                sb.append(',');
            sb.append(map(t.visibleRows.get(r)));
        }
        sb.append("],");

        sb.append("\"warnings\":").append(stringArray(t.warnings));
        sb.append('}');
        return sb.toString();
    }

    // ── Array/map helpers (return null when empty so the key is omitted) ───

    private static String stringArrayOrNull(List<String> items) {
        if (items == null || items.isEmpty())
            return null;
        return stringArray(items);
    }

    private static String locatorsArrayOrNull(List<LocatorCandidate> locs) {
        if (locs == null || locs.isEmpty())
            return null;
        StringBuilder sb = new StringBuilder("[");
        boolean first = true;
        for (LocatorCandidate l : locs) {
            if (l == null || "path".equals(l.strategy))
                continue; // path duplicates node.path
            if (!first)
                sb.append(',');
            first = false;
            sb.append(locator(l));
        }
        sb.append(']');
        return first ? null : sb.toString();
    }

    private static String childrenArrayOrNull(List<DomNode> children) {
        if (children == null || children.isEmpty())
            return null;
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < children.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(node(children.get(i), true));
        }
        sb.append(']');
        return sb.toString();
    }

    private static String attributesOrNull(Map<String, String> m) {
        String s = attributes(m);
        return "{}".equals(s) ? null : s;
    }

    // ── Primitives ────────────────────────────────────────────────────────

    private static String stringArray(List<String> items) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < items.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(Json.quoted(items.get(i)));
        }
        sb.append(']');
        return sb.toString();
    }

    private static String map(Map<String, String> m) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : m.entrySet()) {
            if (!first)
                sb.append(',');
            first = false;
            sb.append(Json.quoted(e.getKey())).append(':').append(Json.quoted(e.getValue()));
        }
        sb.append('}');
        return sb.toString();
    }

    /** Colors are never used for automation and were stored redundantly. */
    private static final Set<String> DROP_ATTR_KEYS = new HashSet<>(Arrays.asList("getBackground", "getForeground"));

    /** Serialises attributes, dropping colors and empty/"null" values. */
    private static String attributes(Map<String, String> m) {
        StringBuilder sb = new StringBuilder("{");
        boolean first = true;
        for (Map.Entry<String, String> e : m.entrySet()) {
            String k = e.getKey();
            String v = e.getValue();
            if (k == null || DROP_ATTR_KEYS.contains(k))
                continue;
            if (v == null || v.isEmpty() || "null".equals(v))
                continue;
            if (!first)
                sb.append(',');
            first = false;
            sb.append(Json.quoted(k)).append(':').append(Json.quoted(v));
        }
        sb.append('}');
        return sb.toString();
    }

    // ── Comma-aware object builder (omits keys whose value is a default) ───

    private static final class Obj {
        private final StringBuilder sb = new StringBuilder("{");
        private boolean first = true;

        void raw(String key, String rawVal) {
            if (!first)
                sb.append(',');
            first = false;
            sb.append('"').append(key).append("\":").append(rawVal);
        }

        void str(String key, String val) {
            if (val != null && !val.isEmpty())
                raw(key, Json.quoted(val));
        }

        void bool(String key, boolean v) {
            if (v)
                raw(key, "true");
        }

        void intD(String key, int v, int dflt) {
            if (v != dflt)
                raw(key, Integer.toString(v));
        }

        void dec(String key, double v) {
            if (v != 0.0)
                raw(key, Json.decimal2(v));
        }

        /** Emits only when {@code j} is non-null (helpers return null when empty). */
        void json(String key, String j) {
            if (j != null)
                raw(key, j);
        }

        String done() {
            sb.append('}');
            return sb.toString();
        }
    }
}
