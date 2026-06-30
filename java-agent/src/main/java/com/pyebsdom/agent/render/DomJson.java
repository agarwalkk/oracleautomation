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
 * Renders the extraction model ({@link DomNode} forests, {@link TableModel}s,
 * scan results) to the JSON the Python clients consume.
 *
 * <p>
 * This is the single home for DOM serialisation. It was previously scattered
 * across {@code DomNode.toJson}, {@code Bounds.toJson},
 * {@code LocatorCandidate.toJson},
 * {@code TableModel.toJson}, and {@code DomScanner.ScanResult.toJson}. Pulling
 * it here keeps the model package free of output concerns while preserving the
 * exact wire shape (field names, ordering, and number formatting are
 * unchanged).
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
     * Serialises one node (and, when requested, its descendants).
     *
     * @param includeChildren when {@code false} the {@code "children"} array is
     *                        omitted (flat list representations).
     */
    public static String node(DomNode n, boolean includeChildren) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');

        // Identity
        sb.append("\"id\":").append(n.id).append(',');
        sb.append("\"path\":").append(Json.quoted(n.path)).append(',');
        sb.append("\"parentPath\":").append(Json.quoted(n.parentPath)).append(',');
        sb.append("\"depth\":").append(n.depth).append(',');
        sb.append("\"index\":").append(n.index).append(',');
        sb.append("\"siblingCount\":").append(n.siblingCount).append(',');
        sb.append("\"semanticId\":").append(Json.quoted(n.semanticId)).append(',');
        sb.append("\"primaryLocator\":")
                .append(n.primaryLocator != null ? locator(n.primaryLocator) : "null").append(',');
        sb.append("\"locatorAmbiguous\":").append(n.locatorAmbiguous).append(',');

        // Type — `className` is the FQN; `type` (== className) and `packageName`
        // (its prefix) are dropped as derivable duplicates not consumed by
        // record/replay or overlays.
        sb.append("\"semanticType\":").append(Json.quoted(n.semanticType)).append(',');
        sb.append("\"className\":").append(Json.quoted(n.className)).append(',');
        sb.append("\"simpleClassName\":").append(Json.quoted(n.simpleClassName)).append(',');

        // Structure
        sb.append("\"containerRole\":").append(Json.quoted(n.containerRole)).append(',');
        sb.append("\"ownerTab\":").append(Json.quoted(n.ownerTab)).append(',');
        sb.append("\"recordIndex\":").append(n.recordIndex).append(',');
        sb.append("\"columnKey\":").append(Json.quoted(n.columnKey)).append(',');
        sb.append("\"current\":").append(n.current).append(',');
        sb.append("\"isMirror\":").append(n.isMirror).append(',');
        sb.append("\"treePath\":").append(Json.quoted(n.treePath)).append(',');
        sb.append("\"expanded\":").append(n.expanded).append(',');

        // Labels
        sb.append("\"name\":").append(Json.quoted(n.name)).append(',');
        sb.append("\"title\":").append(Json.quoted(n.title)).append(',');
        sb.append("\"text\":").append(Json.quoted(n.text)).append(',');
        sb.append("\"value\":").append(Json.quoted(n.value)).append(',');
        sb.append("\"accessibleName\":").append(Json.quoted(n.accessibleName)).append(',');
        sb.append("\"accessibleDescription\":").append(Json.quoted(n.accessibleDescription)).append(',');
        sb.append("\"accessibleRole\":").append(Json.quoted(n.accessibleRole)).append(',');
        sb.append("\"tooltip\":").append(Json.quoted(n.tooltip)).append(',');
        // `displayName` dropped — measured 100% identical to canonicalLabel/name/text.
        sb.append("\"canonicalLabel\":").append(Json.quoted(n.canonicalLabel)).append(',');
        sb.append("\"confidence\":").append(Json.decimal2(n.confidence)).append(',');
        sb.append("\"valueOptions\":").append(stringArray(n.valueOptions)).append(',');

        // Geometry
        sb.append("\"bounds\":").append(n.bounds != null ? bounds(n.bounds) : "null").append(',');
        sb.append("\"screenBounds\":")
                .append(n.screenBounds != null ? screenBounds(n.screenBounds) : "null").append(',');

        // State
        sb.append("\"visible\":").append(n.visible).append(',');
        sb.append("\"showing\":").append(n.showing).append(',');
        sb.append("\"enabled\":").append(n.enabled).append(',');
        sb.append("\"focusable\":").append(n.focusable).append(',');
        sb.append("\"focused\":").append(n.focused).append(',');
        sb.append("\"editable\":").append(n.editable).append(',');
        sb.append("\"selected\":").append(n.selected).append(',');
        sb.append("\"cursorType\":").append(n.cursorType).append(',');
        sb.append("\"cursorName\":").append(Json.quoted(n.cursorName)).append(',');

        // Attributes — structural extraction results only. The raw `reflection`
        // getter dump is omitted entirely: it duplicated typed fields (name,
        // enabled, text, editable, …) plus colors, and was not consumed by
        // record/replay or screenshot overlays. Colors and literal "null"/empty
        // values are filtered from the attributes too.
        sb.append("\"attributes\":").append(attributes(n.attributes)).append(',');

        // Locators
        sb.append("\"locators\":[");
        for (int i = 0; i < n.locators.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(locator(n.locators.get(i)));
        }
        sb.append(']');

        // Children
        if (includeChildren) {
            sb.append(",\"children\":[");
            for (int i = 0; i < n.children.size(); i++) {
                if (i > 0)
                    sb.append(',');
                sb.append(node(n.children.get(i), true));
            }
            sb.append(']');
        }

        sb.append('}');
        return sb.toString();
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

    /**
     * Serialises the attributes map, dropping color keys and any value that is
     * empty or the literal string {@code "null"} (a reflection stringification
     * artefact). Diagnostic markers added by the extractor (keys starting with
     * {@code _}) are kept — they are small and aid hardening.
     */
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
}
