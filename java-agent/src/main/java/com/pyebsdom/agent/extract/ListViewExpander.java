package com.pyebsdom.agent.extract;

import com.pyebsdom.agent.model.Bounds;
import com.pyebsdom.agent.model.DomNode;
import com.pyebsdom.agent.model.LocatorCandidate;

import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Insets;
import java.awt.Point;
import java.awt.Rectangle;
import java.lang.reflect.Field;
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
            int rowErrs = 0;
            for (Row r : model.rows) {
                try {
                    DomNode item = buildRowNode(listNode, model, r, idGen);
                    if (item != null) {
                        listNode.children.add(item);
                        added++;
                    }
                } catch (Throwable rowErr) {
                    rowErrs++;
                    // Keep aggregate count only; avoid verbose per-row diagnostics
                    // in normal scans to keep payload lean.
                }
            }
            if (added == 0) {
                if (rowErrs > 0) {
                    listNode.attributes.put("_listExpandError", "all rows failed; rowErrs=" + rowErrs);
                }
                return 0;
            }
            if (rowErrs > 0) {
                listNode.attributes.put("_listExpandRowErrors", Integer.toString(rowErrs));
            }

            // The rows are now first-class children — reuse the tree render/action
            // path and record the column headers + count for downstream consumers.
            listNode.semanticType = "Tree";
            listNode.attributes.put("treeRowCount", Integer.toString(added));
            if (!model.headers.isEmpty()) {
                listNode.attributes.put("listColumns", join(model.headers, " | "));
            }
            listNode.attributes.put("listGeomPlan", model.geomPlan);
            return added;
        } catch (Throwable t) {
            listNode.attributes.put("_listExpandError",
                    shortError(t));
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
        n.showing = r.bounds != null || r.primaryCellBounds != null;
        n.focusable = true;

        Rectangle targetBounds = (r.primaryCellBounds != null) ? r.primaryCellBounds : r.bounds;
        if (targetBounds != null) {
            if (r.screenX >= 0 && r.screenY >= 0) {
                n.bounds = new Bounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height);
                n.screenBounds = new Bounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height,
                        r.screenX, r.screenY, targetBounds.width, targetBounds.height);
            } else {
                n.bounds = new Bounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height);
                n.screenBounds = new Bounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height);
            }
        } else {
            n.bounds = new Bounds(0, 0, 0, 0);
            n.screenBounds = new Bounds(0, 0, 0, 0);
        }
        n.attributes.put("rowCellBoundsPx", rectsToCsv(r.cellBounds));
        n.attributes.put("rowGeomMethod", r.geomMethod != null ? r.geomMethod : "none");

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
        if (list.name != null && !list.name.isEmpty()) {
            return list.name;
        }
        if (list.path != null && !list.path.isEmpty()) {
            return list.path;
        }
        return "ListView";
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
        boolean onEdt = false;
        try {
            onEdt = SwingUtilities.isEventDispatchThread();
        } catch (Throwable ignored) {
            // WHY: In injected/attach contexts Oracle AWT can throw from toolkit
            // lookup during EDT checks. Fall back to direct execution.
            onEdt = false;
        }
        if (onEdt) {
            work.run();
        } else {
            try {
                SwingUtilities.invokeAndWait(work);
            } catch (Throwable ignored) {
                // Fallback: run directly if invokeAndWait is unavailable in this
                // runtime context (e.g., toolkit/appcontext edge cases).
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
        Insets insets = readInsets(comp, c);
        model.columnWidths = readColumnWidths(comp, c, cols);
        model.columnX = columnX(insets, model.columnWidths, cols);
        GeomSeed seed = buildGeomSeed(comp, c, rows, cols, model);
        model.geomPlan = seed.plan;
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
                Rectangle cb = cellRectangle(comp, col, rIdx, cellBounds);
                if (cb == null && !model.columnWidths.isEmpty()) {
                    Rectangle rr = rowRectangle(comp, rIdx, rowBounds, cellBounds);
                    if (rr != null && col < model.columnWidths.size() && col < model.columnX.size()) {
                        int cw = Math.max(0, model.columnWidths.get(col));
                        int cx = model.columnX.get(col);
                        cb = new Rectangle(cx, rr.y, cw, rr.height);
                    }
                }
                r.cellBounds.add(cb);
            }
            r.geomSource = "none";
            r.geomMethod = "none";
            r.bounds = rowRectangle(comp, rIdx, rowBounds, cellBounds);
            if (r.bounds != null) {
                r.geomSource = "rowBounds";
                r.geomMethod = "getRowBounds";
            }
            if (r.bounds == null) {
                r.bounds = union(r.cellBounds);
                if (r.bounds != null) {
                    r.geomSource = "cellUnion";
                    r.geomMethod = "getCellBounds";
                }
            }
            if (r.bounds == null && seed.canSynthesize) {
                synthesizeRowGeometry(r, cols, model, seed);
                if (r.bounds != null) {
                    r.geomSource = "synthColumnModel";
                    r.geomMethod = seed.methodTag;
                }
            }
            r.primaryCellBounds = firstNonNull(r.cellBounds);
            if (r.primaryCellBounds != null && "none".equals(r.geomSource)) {
                r.geomSource = "primaryCell";
                r.geomMethod = "getCellBounds";
            }
            if (r.bounds != null && origin != null) {
                Rectangle screenRect = (r.primaryCellBounds != null) ? r.primaryCellBounds : r.bounds;
                r.screenX = origin.x + screenRect.x;
                r.screenY = origin.y + screenRect.y;
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

    private static Rectangle cellRectangle(Component comp, int col, int row, Method cellBounds) {
        if (cellBounds != null) {
            try {
                Object cb = cellBounds.invoke(comp, col, row);
                if (cb instanceof Rectangle) {
                    return (Rectangle) cb;
                }
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static Insets readInsets(Component comp, Class<?> c) {
        Method m = m0(c, "getColumnInsets");
        if (m == null) {
            return null;
        }
        try {
            Object obj = m.invoke(comp);
            if (obj instanceof Insets) {
                return (Insets) obj;
            }
            if (obj == null) {
                return null;
            }
            Integer top = intMember(obj, "top");
            Integer left = intMember(obj, "left");
            Integer bottom = intMember(obj, "bottom");
            Integer right = intMember(obj, "right");
            if (top != null || left != null || bottom != null || right != null) {
                return new Insets(
                        top != null ? top : 0,
                        left != null ? left : 0,
                        bottom != null ? bottom : 0,
                        right != null ? right : 0);
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static Integer intMember(Object obj, String name) {
        try {
            Method getter = obj.getClass().getMethod("get" + Character.toUpperCase(name.charAt(0)) + name.substring(1));
            Object v = getter.invoke(obj);
            return toInt(v);
        } catch (Exception ignored) {
        }
        try {
            Field f = obj.getClass().getField(name);
            f.setAccessible(true);
            Object v = f.get(obj);
            return toInt(v);
        } catch (Exception ignored) {
        }
        return null;
    }

    private static List<Integer> readColumnWidths(Component comp, Class<?> c, int cols) {
        List<Integer> out = new ArrayList<>();
        Method actual = m1(c, "getActualColumnWidth");
        Method plain = m1(c, "getColumnWidth");
        Method widthGetter = (actual != null) ? actual : plain;
        if (widthGetter == null || cols <= 0) {
            return out;
        }
        for (int i = 0; i < cols; i++) {
            int w = 0;
            try {
                w = Math.max(0, toInt(widthGetter.invoke(comp, i)));
            } catch (Exception ignored) {
            }
            out.add(w);
        }
        return out;
    }

    private static List<Integer> columnX(Insets insets, List<Integer> widths, int cols) {
        List<Integer> out = new ArrayList<>();
        if (cols <= 0) {
            return out;
        }
        int x = (insets != null) ? Math.max(0, insets.left) : 0;
        for (int i = 0; i < cols; i++) {
            out.add(x);
            int w = (i < widths.size()) ? Math.max(0, widths.get(i)) : 0;
            x += w;
        }
        return out;
    }

    private static GeomSeed buildGeomSeed(Component comp, Class<?> c, int rows, int cols, Model model) {
        GeomSeed s = new GeomSeed();
        Insets scrollInsets = readImmInsets(comp, c, "getScrollInsets");
        Integer canvasOriginX = readIntFromNoArg(comp, c, "getCanvasOriginX");
        Integer canvasOriginY = readIntFromNoArg(comp, c, "getCanvasOriginY");
        if (canvasOriginX == null || canvasOriginY == null) {
            Point p = readPointFromNoArg(comp, c, "getCanvasOrigin");
            if (p != null) {
                if (canvasOriginX == null) {
                    canvasOriginX = p.x;
                }
                if (canvasOriginY == null) {
                    canvasOriginY = p.y;
                }
            }
        }

        Integer rowStep = resolveVLineIncrement(comp);
        Integer canvasH = readIntFromNoArg(comp, c, "getCanvasHeight");
        if (canvasH == null || canvasH <= 0) {
            Integer innerH = readIntFromNoArg(comp, c, "getInnerHeight");
            if (innerH != null && innerH > 0) {
                canvasH = innerH;
            }
        }
        if (rowStep == null || rowStep <= 0) {
            int safeRows = Math.max(1, rows);
            int fallback = (canvasH != null && canvasH > 0) ? Math.max(16, canvasH / Math.max(safeRows, 4)) : 18;
            rowStep = fallback;
        }

        s.canvasOriginX = canvasOriginX != null ? canvasOriginX : 0;
        s.canvasOriginY = canvasOriginY != null ? canvasOriginY : 0;
        s.scrollTop = (scrollInsets != null) ? Math.max(0, scrollInsets.top) : 0;
        s.rowStep = Math.max(1, rowStep);
        s.rowHeight = Math.max(1, s.rowStep);
        s.methodTag = "columnWidth+columnInsets+scrollInsets+canvasOrigin+vLineIncrement";
        s.canSynthesize = cols > 0 && !model.columnWidths.isEmpty();
        s.plan = "method=" + s.methodTag
            + ",rowStep=" + s.rowStep
            + ",scrollTop=" + s.scrollTop
            + ",canvasOriginX=" + s.canvasOriginX
            + ",canvasOriginY=" + s.canvasOriginY;
        return s;
    }

    private static void synthesizeRowGeometry(Row r, int cols, Model model, GeomSeed seed) {
        int rowY = Math.max(0, seed.scrollTop + (r.rowIndex * seed.rowStep) - seed.canvasOriginY);
        int rowMaxX = 0;
        for (int col = 0; col < cols; col++) {
            Rectangle existing = (col < r.cellBounds.size()) ? r.cellBounds.get(col) : null;
            if (existing != null && existing.width > 0 && existing.height > 0) {
                rowMaxX = Math.max(rowMaxX, existing.x + existing.width);
                continue;
            }
            int cx = seed.canvasOriginX;
            if (col < model.columnX.size()) {
                cx += Math.max(0, model.columnX.get(col));
            }
            int cw = (col < model.columnWidths.size()) ? Math.max(1, model.columnWidths.get(col)) : 1;
            Rectangle synth = new Rectangle(cx, rowY, cw, seed.rowHeight);
            if (col < r.cellBounds.size()) {
                r.cellBounds.set(col, synth);
            } else {
                r.cellBounds.add(synth);
            }
            rowMaxX = Math.max(rowMaxX, cx + cw);
        }
        if (!r.cellBounds.isEmpty()) {
            r.primaryCellBounds = firstNonNull(r.cellBounds);
            int rowX = seed.canvasOriginX;
            int rowW = Math.max(1, rowMaxX - rowX);
            r.bounds = new Rectangle(rowX, rowY, rowW, seed.rowHeight);
        }
    }

    private static Insets readImmInsets(Component comp, Class<?> c, String methodName) {
        Method m = m0(c, methodName);
        if (m == null) {
            return null;
        }
        try {
            Object obj = m.invoke(comp);
            if (obj instanceof Insets) {
                return (Insets) obj;
            }
            if (obj == null) {
                return null;
            }
            Integer top = intMember(obj, "top");
            Integer left = intMember(obj, "left");
            Integer bottom = intMember(obj, "bottom");
            Integer right = intMember(obj, "right");
            if (top != null || left != null || bottom != null || right != null) {
                return new Insets(
                        top != null ? top : 0,
                        left != null ? left : 0,
                        bottom != null ? bottom : 0,
                        right != null ? right : 0);
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static Integer readIntFromNoArg(Component comp, Class<?> c, String methodName) {
        Method m = m0(c, methodName);
        if (m == null) {
            return null;
        }
        try {
            return toInt(m.invoke(comp));
        } catch (Exception ignored) {
            return null;
        }
    }

    private static Point readPointFromNoArg(Component comp, Class<?> c, String methodName) {
        Method m = m0(c, methodName);
        if (m == null) {
            return null;
        }
        try {
            Object p = m.invoke(comp);
            if (p instanceof Point) {
                return (Point) p;
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static Integer resolveVLineIncrement(Component comp) {
        for (Component cur = comp; cur != null; cur = cur.getParent()) {
            Method m = m0(cur.getClass(), "getVLineIncrement");
            if (m == null) {
                continue;
            }
            try {
                int v = toInt(m.invoke(cur));
                if (v > 0) {
                    return v;
                }
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static Rectangle union(List<Rectangle> rects) {
        Rectangle out = null;
        for (Rectangle r : rects) {
            if (r == null) {
                continue;
            }
            if (out == null) {
                out = new Rectangle(r);
            } else {
                out = out.union(r);
            }
        }
        return out;
    }

    private static Rectangle firstNonNull(List<Rectangle> rects) {
        for (Rectangle r : rects) {
            if (r != null && r.width > 0 && r.height > 0) {
                return r;
            }
        }
        return null;
    }

    private static String rectsToCsv(List<Rectangle> rects) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < rects.size(); i++) {
            if (i > 0) {
                sb.append('\t');
            }
            Rectangle r = rects.get(i);
            if (r == null) {
                sb.append(i).append(':').append("0,0,0,0");
            } else {
                sb.append(i).append(':')
                        .append(r.x).append(',').append(r.y).append(',')
                        .append(r.width).append(',').append(r.height);
            }
        }
        return sb.toString();
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

    private static String shortError(Throwable t) {
        if (t == null) {
            return "unknown";
        }
        String msg = t.getClass().getName() + ": " + String.valueOf(t.getMessage());
        StackTraceElement[] st = t.getStackTrace();
        if (st != null && st.length > 0) {
            msg += " @ " + st[0].toString();
        }
        Throwable cause = t.getCause();
        if (cause != null && cause != t) {
            msg += " | cause=" + cause.getClass().getName() + ": " + String.valueOf(cause.getMessage());
            StackTraceElement[] cst = cause.getStackTrace();
            if (cst != null && cst.length > 0) {
                msg += " @ " + cst[0].toString();
            }
        }
        return msg;
    }

    // ── Carriers ──────────────────────────────────────────────────────────

    private static final class Model {
        final List<String> headers = new ArrayList<>();
        final List<Row> rows = new ArrayList<>();
        List<Integer> columnWidths = new ArrayList<>();
        List<Integer> columnX = new ArrayList<>();
        String geomPlan = "";
    }

    private static final class Row {
        int rowIndex;
        boolean selected;
        final List<String> values = new ArrayList<>();
        Rectangle bounds;      // component-relative
        Rectangle primaryCellBounds; // first visible column cell
        final List<Rectangle> cellBounds = new ArrayList<>();
        String geomSource;
        String geomMethod;
        int screenX = -1;
        int screenY = -1;
    }

    private static final class GeomSeed {
        boolean canSynthesize;
        int canvasOriginX;
        int canvasOriginY;
        int scrollTop;
        int rowStep;
        int rowHeight;
        String methodTag;
        String plan;
    }
}