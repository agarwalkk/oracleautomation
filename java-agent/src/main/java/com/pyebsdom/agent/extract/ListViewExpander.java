package com.pyebsdom.agent.extract;

import com.pyebsdom.agent.model.Bounds;
import com.pyebsdom.agent.model.DomNode;
import com.pyebsdom.agent.model.LocatorCandidate;

import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Point;
import java.awt.Rectangle;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Promotes the rows of an Oracle Forms {@code oracle.forms.ui.ListView}
 * (the results grid inside a List-of-Values dialog) into real {@link DomNode}
 * children — the exact counterpart of {@link TreeItemExpander} for lists.
 *
 * <h3>Why this exists</h3>
 * A ListView is scanned as a single leaf whose rows are flattened into a
 * {@code treeRows} string attribute ({@code depth\tselected\texpanded\t
 * header:value\t...  || ...}). The individual rows (e.g. the four order types
 * "INTERNAL_RMD_RMT", "INTL_AFF_RMD_RMT", ...) therefore have <b>no element id,
 * no bounds, no locators, and no identity</b>, and the flat string is fragile:
 * a Description cell containing {@code ':'}, a tab, or {@code " || "} corrupts
 * parsing downstream. The recorder cannot bind "select INTERNAL_RMD_RMT" to a
 * deterministic target.
 *
 * <p>This expander gives every visible row its own {@link DomNode} with:
 * <ul>
 *   <li>{@code recordIndex} = the ListView row index — the handle the
 *       {@code setSelectedRow(int)} action uses;</li>
 *   <li>a {@code listRow} locator (list handle + row index) plus an
 *       accessibleName locator (the row's key-column value);</li>
 *   <li>structured column values kept in {@code attributes["rowCells"]} as
 *       {@code header:value} pairs (tab-separated), read straight from
 *       {@code getCellData}/{@code getHeaderData} — no lossy re-parse;</li>
 *   <li>screen bounds (from {@code getCellBounds}/{@code getRowBounds} offset by
 *       the list's screen origin) when the runtime exposes them.</li>
 * </ul>
 *
 * <p>The owning ListView node's {@code semanticType} is switched to
 * {@code "Tree"} so the existing Python tree renderer lists the rows (each with
 * its own {@code eN} ref) and the {@code select} action applies — no renderer
 * change required.
 *
 * <p>Must be invoked from {@link DomScanner} BEFORE locator/identity resolution
 * so the new children flow through {@link IdentityResolver} like any other node.
 * Because {@link IdentityResolver} recomputes {@code canonicalLabel} from
 * {@code accessibleName}, each row's label is stamped onto {@code accessibleName}
 * (and {@code text}) so it survives that pass.
 *
 * <h3>Reflection robustness</h3>
 * Every accessor is resolved reflectively and its absence degrades gracefully —
 * a row with no bounds still keeps its identity, recordIndex, and locators,
 * which is strictly better than the zero-identity flat string.
 */
public final class ListViewExpander {

    /** Hard cap so a runaway model can never explode the tree. */
    private static final int MAX_ROWS = 512;

    private ListViewExpander() {
    }

    /** Returns true if {@code node} is a ListView we know how to expand. */
    public static boolean isListView(DomNode node) {
        return node != null && "ListView".equals(node.simpleClassName);
    }

    /**
     * Expands {@code comp}'s rows into children of {@code listNode}.
     * Safe to call off the EDT — reflective reads are marshalled onto it.
     *
     * @return number of row children added (0 if the component is not a
     *         readable ListView or exposes no rows)
     */
    public static int expand(DomNode listNode, Component comp, AtomicInteger idGen) {
        if (listNode == null || comp == null) {
            return 0;
        }
        try {
            Model model = readModel(comp);
            if (model == null || model.rows.isEmpty()) {
                return 0;
            }

            int added = 0;
            for (Row r : model.rows) {
                DomNode item = buildRowNode(listNode, model, r, idGen);
                listNode.children.add(item);
                added++;
            }
            if (added == 0) {
                return 0;
            }

            // The rows are now first-class children — reuse the tree render/action
            // path and record the column headers + count for downstream consumers.
            listNode.semanticType = "Tree";
            listNode.attributes.put("treeRowCount", Integer.toString(added));
            if (!model.headers.isEmpty()) {
                listNode.attributes.put("listColumns", join(model.headers, " | "));
            }
            return added;
        } catch (Throwable t) {
            listNode.attributes.put("_listExpandError",
                    t.getClass().getName() + ": " + t.getMessage());
            return 0;
        }
    }

    // ── Row node construction ─────────────────────────────────────────────

    private static DomNode buildRowNode(DomNode list, Model model, Row r,
            AtomicInteger idGen) {
        DomNode n = new DomNode();
        n.id = idGen.incrementAndGet();
        n.depth = list.depth + 1;
        n.index = r.rowIndex;
        n.siblingCount = model.rows.size();
        n.parentPath = list.path;
        n.path = list.path + "/ListRow[" + r.rowIndex + "]";

        n.type = "oracle.forms.ui.ListView$Row";
        n.className = n.type;
        n.simpleClassName = "ListRow";
        n.packageName = "oracle.forms.ui";
        n.semanticType = "TreeItem";
        n.containerRole = "TreeItem";

        // recordIndex is the handle the setSelectedRow(int) action targets.
        n.recordIndex = r.rowIndex;
        if (!model.headers.isEmpty()) {
            n.columnKey = model.headers.get(0);
        }

        // Display label = the non-empty cell values joined; stamped onto
        // accessibleName/text so IdentityResolver keeps it as canonicalLabel.
        String label = joinNonEmpty(r.values, "  —  ");
        if (label.isEmpty()) {
            label = "Row " + (r.rowIndex + 1);
        }
        n.accessibleName = label;
        n.displayName = label;
        n.canonicalLabel = label;
        n.text = label;
        n.confidence = 0.85;

        // Structured columns preserved without the fragile flat-string re-parse.
        n.attributes.put("rowCells", headerValuePairs(model.headers, r.values));

        n.selected = r.selected;
        n.current = r.selected;
        n.enabled = true;
        n.visible = true;
        n.showing = r.bounds != null;
        n.focusable = true;

        if (r.bounds != null) {
            if (r.screenX >= 0 && r.screenY >= 0) {
                n.bounds = new Bounds(r.bounds.x, r.bounds.y, r.bounds.width, r.bounds.height);
                n.screenBounds = new Bounds(r.bounds.x, r.bounds.y, r.bounds.width, r.bounds.height,
                        r.screenX, r.screenY, r.bounds.width, r.bounds.height);
            } else {
                n.bounds = new Bounds(r.bounds.x, r.bounds.y, r.bounds.width, r.bounds.height);
                n.screenBounds = new Bounds(r.bounds.x, r.bounds.y, r.bounds.width, r.bounds.height);
            }
        } else {
            n.bounds = new Bounds(0, 0, 0, 0);
            n.screenBounds = new Bounds(0, 0, 0, 0);
        }

        // Deterministic locators. listRow (list handle + row index) is the stable,
        // replay-friendly one; the key-column value is a human fallback. Both are
        // verified unique later by IdentityResolver against the materialised rows.
        n.locators.add(new LocatorCandidate("listRow", listLocatorValue(list) + "#" + r.rowIndex, 0.90));
        if (!label.isEmpty()) {
            n.locators.add(new LocatorCandidate("accessibleName", label, 0.70));
        }
        return n;
    }

    /** Best stable handle to the owning list, for the listRow fallback. */
    private static String listLocatorValue(DomNode list) {
        if (list.accessibleName != null && !list.accessibleName.isEmpty()) {
            return list.accessibleName;
        }
        return list.name != null ? list.name : list.path;
    }

    // ── Reflective model read (marshalled onto EDT) ───────────────────────

    private static Model readModel(final Component comp) throws Exception {
        final Model[] holder = new Model[1];
        Runnable work = new Runnable() {
            public void run() {
                try {
                    holder[0] = readModelOnEdt(comp);
                } catch (Throwable ignored) {
                }
            }
        };
        if (SwingUtilities.isEventDispatchThread()) {
            work.run();
        } else {
            try {
                SwingUtilities.invokeAndWait(work);
            } catch (Throwable t) {
                work.run();
            }
        }
        return holder[0];
    }

    private static Model readModelOnEdt(Component comp) throws Exception {
        Class<?> c = comp.getClass();
        Method rowCount = m0(c, "getRowCount");
        Method colCount = m0(c, "getColumnCount");
        Method getCell = m2(c, "getCellData");
        if (rowCount == null || colCount == null || getCell == null) {
            return null;
        }

        int rows = toInt(rowCount.invoke(comp));
        int cols = toInt(colCount.invoke(comp));
        if (rows <= 0 || cols <= 0) {
            return null;
        }

        Model model = new Model();

        // Column headers (optional).
        Method getHeader = m1(c, "getHeaderData");
        for (int col = 0; col < cols; col++) {
            String h = "";
            if (getHeader != null) {
                try {
                    Object hv = getHeader.invoke(comp, col);
                    h = (hv != null) ? hv.toString().trim() : "";
                } catch (Exception ignored) {
                }
            }
            model.headers.add(h);
        }

        // Currently selected row (optional).
        int selectedRow = -1;
        Method getSel = m0(c, "getSelectedRow");
        if (getSel != null) {
            try {
                selectedRow = toInt(getSel.invoke(comp));
            } catch (Exception ignored) {
            }
        }

        // Row/cell geometry (optional; several method names across runtimes).
        Method rowBounds = m1(c, "getRowBounds");
        Method cellBounds = m2(c, "getCellBounds");
        Point origin = null;
        try {
            if (comp.isShowing()) {
                origin = comp.getLocationOnScreen();
            }
        } catch (Exception ignored) {
        }

        int limit = Math.min(rows, MAX_ROWS);
        for (int rIdx = 0; rIdx < limit; rIdx++) {
            Row r = new Row();
            r.rowIndex = rIdx;
            r.selected = (rIdx == selectedRow);
            for (int col = 0; col < cols; col++) {
                Object v = null;
                // Oracle Forms ListView uses getCellData(column, row) arg order.
                try {
                    v = getCell.invoke(comp, col, rIdx);
                } catch (Exception ignored) {
                }
                r.values.add(v != null ? v.toString().trim() : "");
            }
            r.bounds = rowRectangle(comp, rIdx, rowBounds, cellBounds);
            if (r.bounds != null && origin != null) {
                r.screenX = origin.x + r.bounds.x;
                r.screenY = origin.y + r.bounds.y;
            }
            model.rows.add(r);
        }
        return model;
    }

    /** Best-effort row rectangle: prefer getRowBounds(row), else getCellBounds(0,row). */
    private static Rectangle rowRectangle(Component comp, int row,
            Method rowBounds, Method cellBounds) {
        if (rowBounds != null) {
            try {
                Object rb = rowBounds.invoke(comp, row);
                if (rb instanceof Rectangle) {
                    return (Rectangle) rb;
                }
            } catch (Exception ignored) {
            }
        }
        if (cellBounds != null) {
            try {
                Object cb = cellBounds.invoke(comp, 0, row);
                if (cb instanceof Rectangle) {
                    return (Rectangle) cb;
                }
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    // ── String helpers ────────────────────────────────────────────────────

    private static String headerValuePairs(List<String> headers, List<String> values) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < values.size(); i++) {
            if (sb.length() > 0) {
                sb.append('\t');
            }
            String h = (i < headers.size()) ? headers.get(i) : "";
            if (h != null && !h.isEmpty()) {
                sb.append(h).append(':');
            }
            sb.append(values.get(i));
        }
        return sb.toString();
    }

    private static String joinNonEmpty(List<String> parts, String sep) {
        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (p == null || p.isEmpty()) {
                continue;
            }
            if (sb.length() > 0) {
                sb.append(sep);
            }
            sb.append(p);
        }
        return sb.toString();
    }

    private static String join(List<String> parts, String sep) {
        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (sb.length() > 0) {
                sb.append(sep);
            }
            sb.append(p == null ? "" : p);
        }
        return sb.toString();
    }

    // ── Reflection resolvers (mirror TreeItemExpander) ────────────────────

    /** No-arg getter. */
    private static Method m0(Class<?> c, String name) {
        try {
            return c.getMethod(name);
        } catch (Exception e) {
            return null;
        }
    }

    /** Single-int getter, searching the superclass chain. */
    private static Method m1(Class<?> c, String name) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                return k.getMethod(name, int.class);
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    /** Two-int getter (e.g. getCellData(col,row)), searching the superclass chain. */
    private static Method m2(Class<?> c, String name) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                return k.getMethod(name, int.class, int.class);
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static int toInt(Object v) {
        if (v instanceof Number) {
            return ((Number) v).intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (Exception e) {
            return 0;
        }
    }

    // ── Carriers ──────────────────────────────────────────────────────────

    private static final class Model {
        final List<String> headers = new ArrayList<>();
        final List<Row> rows = new ArrayList<>();
    }

    private static final class Row {
        int rowIndex;
        boolean selected;
        final List<String> values = new ArrayList<>();
        Rectangle bounds;      // component-relative
        int screenX = -1;
        int screenY = -1;
    }
}