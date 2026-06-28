package com.pyebsdom.agent;

import javax.swing.SwingUtilities;
import java.awt.Component;
import java.awt.Point;
import java.awt.Rectangle;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Promotes the rows of a tree control (Oracle EWT
 * {@code oracle.ewt.dTree.DTree},
 * Swing {@code JTree}, and look-alikes) into real {@link DomNode} children.
 *
 * <h3>Why this exists</h3>
 * Previously a tree was scanned as a single leaf node and its rows were
 * flattened into a {@code treeRows} string attribute
 * ({@code depth\tselected\texpanded\tlabel || ...}). The individual rows
 * ("Today's Orders", "Search Results", "Personal Folders", "Public Folders")
 * therefore had <b>no element id, no bounds, no locators, and no identity</b> —
 * they were lost inside the JVM before Python ever saw them. The recorder could
 * not bind a deterministic action to "click Personal Folders".
 *
 * <p>
 * This expander gives every visible tree row its own {@link DomNode} with:
 * <ul>
 * <li>screen bounds (from {@code getRowBounds(i)} offset by the tree's screen
 * origin) so a Robot click can hit it — the same execution path the agent
 * already uses for tabs;</li>
 * <li>a deterministic {@code treePath} locator (the root→leaf label chain),
 * which survives id/path churn across scans;</li>
 * <li>a {@code treeRow} fallback locator (tree locator + row index);</li>
 * <li>{@code selected} / {@code expanded} / {@code depth} state.</li>
 * </ul>
 *
 * <p>
 * Must be invoked from {@link DomScanner#buildNode} for nodes whose
 * {@code semanticType == "Tree"}, BEFORE locator/identity resolution, so the
 * new children flow through {@link IdentityResolver} like any other node.
 *
 * <h3>Reflection robustness</h3>
 * EWT DTree and Swing JTree expose the same row API shape
 * ({@code getRowCount}, {@code getPathForRow}, {@code getRowBounds},
 * {@code isRowSelected}, {@code isExpanded}). Each is resolved reflectively and
 * absence degrades gracefully — a row with no bounds still keeps its identity
 * and treePath locator, which is strictly better than today's zero.
 */
public final class TreeItemExpander {

    private TreeItemExpander() {
    }

    /** Returns true if {@code node} is a tree we know how to expand. */
    public static boolean isTree(DomNode node) {
        return node != null && "Tree".equals(node.semanticType);
    }

    /**
     * Expands {@code comp}'s rows into children of {@code treeNode}.
     * Safe to call off the EDT — geometry reads are marshalled onto it.
     *
     * @return number of row children added
     */
    public static int expand(DomNode treeNode, Component comp, AtomicInteger idGen) {
        if (treeNode == null || comp == null)
            return 0;
        try {
            List<Row> rows = readRows(comp);
            if (rows.isEmpty())
                return 0;

            // Maintain a running path stack keyed by depth so each row can
            // build its full root→leaf chain (treePath) deterministically.
            String[] chain = new String[64];
            int added = 0;
            for (Row r : rows) {
                int d = Math.max(0, Math.min(r.depth, chain.length - 1));
                chain[d] = r.label;
                StringBuilder tp = new StringBuilder();
                if (treeNode.accessibleName != null && !treeNode.accessibleName.isEmpty()) {
                    tp.append(treeNode.accessibleName);
                }
                for (int k = 0; k <= d; k++) {
                    if (chain[k] == null)
                        continue;
                    if (tp.length() > 0)
                        tp.append('/');
                    tp.append(chain[k]);
                }
                DomNode item = buildRowNode(treeNode, r, tp.toString(), idGen);
                treeNode.children.add(item);
                added++;
            }

            // Keep the flat string for back-compat consumers, but the children
            // are now the source of truth.
            treeNode.attributes.put("treeRowCount", Integer.toString(rows.size()));
            return added;
        } catch (Throwable t) {
            treeNode.attributes.put("_treeExpandError",
                    t.getClass().getName() + ": " + t.getMessage());
            return 0;
        }
    }

    // ── Row node construction ─────────────────────────────────────────────

    private static DomNode buildRowNode(DomNode tree, Row r, String treePath,
            AtomicInteger idGen) {
        DomNode n = new DomNode();
        n.id = idGen.incrementAndGet();
        n.depth = tree.depth + 1;
        n.index = r.rowIndex;
        n.siblingCount = 0; // fixed up by caller-side normalisation if needed
        n.parentPath = tree.path;
        n.path = tree.path + "/TreeItem[" + r.rowIndex + "]";

        n.type = "oracle.ewt.dTree.DTreeItem";
        n.className = n.type;
        n.simpleClassName = "DTreeItem";
        n.packageName = "oracle.ewt.dTree";
        n.semanticType = "TreeItem";
        n.containerRole = "TreeItem";

        n.accessibleName = r.label;
        n.displayName = r.label;
        n.canonicalLabel = r.label;
        n.text = r.label;
        n.confidence = 0.85;

        n.treePath = treePath;
        n.expanded = r.expanded;
        n.selected = r.selected;
        n.current = r.selected;
        n.enabled = true;
        n.visible = true;
        n.showing = r.bounds != null;
        n.focusable = true;

        if (r.bounds != null) {
            n.bounds = new Bounds(r.bounds.x, r.bounds.y, r.bounds.width, r.bounds.height);
            if (r.screenX >= 0 && r.screenY >= 0) {
                n.screenBounds = new Bounds(r.bounds.x, r.bounds.y,
                        r.bounds.width, r.bounds.height,
                        r.screenX, r.screenY, r.bounds.width, r.bounds.height);
            } else {
                n.screenBounds = new Bounds(r.bounds.x, r.bounds.y,
                        r.bounds.width, r.bounds.height);
            }
        } else {
            n.bounds = new Bounds(0, 0, 0, 0);
            n.screenBounds = new Bounds(0, 0, 0, 0);
        }

        // Deterministic locators. treePath is the stable, replay-friendly one;
        // treeRow (tree locator + index) is the fallback. Both are verified
        // unique later by IdentityResolver against the materialised children.
        n.locators.add(new LocatorCandidate("treePath", treePath, 0.92));
        n.locators.add(new LocatorCandidate(
                "treeRow", treeLocatorValue(tree) + "#" + r.rowIndex, 0.80));
        if (r.label != null && !r.label.isEmpty()) {
            n.locators.add(new LocatorCandidate("accessibleName", r.label, 0.70));
        }
        return n;
    }

    /** Best stable handle to the owning tree, for the treeRow fallback. */
    private static String treeLocatorValue(DomNode tree) {
        if (tree.accessibleName != null && !tree.accessibleName.isEmpty()) {
            return tree.accessibleName;
        }
        return tree.name != null ? tree.name : tree.path;
    }

    // ── Reflective row read (marshalled onto EDT) ─────────────────────────

    private static List<Row> readRows(final Component comp) throws Exception {
        final List<Row> out = new ArrayList<>();
        Runnable work = new Runnable() {
            public void run() {
                try {
                    readRowsOnEdt(comp, out);
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
        return out;
    }

    private static void readRowsOnEdt(Component comp, List<Row> out) throws Exception {
        Class<?> c = comp.getClass();
        Method rowCount = method(c, "getRowCount");
        Method pathForRow = intMethod(c, "getPathForRow");
        if (rowCount == null || pathForRow == null)
            return;

        Method rowBounds = intMethod(c, "getRowBounds");
        Method rowSel = intMethod(c, "isRowSelected");
        Method expanded = intMethod(c, "isExpanded");

        int count = toInt(rowCount.invoke(comp));
        if (count <= 0)
            return;
        int limit = Math.min(count, 512);

        Point treeOrigin = null;
        try {
            if (comp.isShowing())
                treeOrigin = comp.getLocationOnScreen();
        } catch (Exception ignored) {
        }

        for (int i = 0; i < limit; i++) {
            Object tp;
            try {
                tp = pathForRow.invoke(comp, i);
            } catch (Exception e) {
                continue;
            }
            if (tp == null)
                continue;

            int depth = treePathDepth(tp);
            String label = stripLevelPrefix(treePathLabel(tp));
            if (label.isEmpty())
                continue;

            Row r = new Row();
            r.rowIndex = i;
            r.depth = Math.max(0, depth);
            r.label = label;
            r.selected = invokeBool(rowSel, comp, i);
            r.expanded = invokeBool(expanded, comp, i);

            if (rowBounds != null) {
                try {
                    Object rb = rowBounds.invoke(comp, i);
                    if (rb instanceof Rectangle) {
                        r.bounds = (Rectangle) rb;
                        if (treeOrigin != null) {
                            r.screenX = treeOrigin.x + r.bounds.x;
                            r.screenY = treeOrigin.y + r.bounds.y;
                        }
                    }
                } catch (Exception ignored) {
                }
            }
            out.add(r);
        }
    }

    // ── TreePath helpers (mirror ReflectionExtractor) ─────────────────────

    private static int treePathDepth(Object treePath) {
        try {
            Method getPathCount = treePath.getClass().getMethod("getPathCount");
            Object v = getPathCount.invoke(treePath);
            if (v instanceof Integer)
                return Math.max(0, (Integer) v - 1);
        } catch (Exception ignored) {
        }
        return 0;
    }

    private static String treePathLabel(Object treePath) {
        try {
            Method getLast = treePath.getClass().getMethod("getLastPathComponent");
            Object last = getLast.invoke(treePath);
            if (last != null) {
                String s = String.valueOf(last).trim();
                if (!s.isEmpty())
                    return s;
            }
        } catch (Exception ignored) {
        }
        try {
            return String.valueOf(treePath).trim();
        } catch (Exception ignored) {
        }
        return "";
    }

    /**
     * Oracle EWT renders labels like "Level 0 Today's Orders" — drop the prefix.
     */
    private static String stripLevelPrefix(String label) {
        if (label == null)
            return "";
        String s = label.trim();
        if (s.startsWith("Level ")) {
            int sp = s.indexOf(' ', 6);
            if (sp > 0 && sp + 1 < s.length()) {
                return s.substring(sp + 1).trim();
            }
        }
        return s;
    }

    private static boolean invokeBool(Method m, Component comp, int i) {
        if (m == null)
            return false;
        try {
            return Boolean.TRUE.equals(m.invoke(comp, i));
        } catch (Exception ignored) {
            return false;
        }
    }

    private static Method method(Class<?> c, String name) {
        try {
            return c.getMethod(name);
        } catch (Exception e) {
            return null;
        }
    }

    private static Method intMethod(Class<?> c, String name) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                return k.getMethod(name, int.class);
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static int toInt(Object v) {
        if (v instanceof Number)
            return ((Number) v).intValue();
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (Exception e) {
            return 0;
        }
    }

    /** Lightweight row carrier. */
    private static final class Row {
        int rowIndex;
        int depth;
        String label = "";
        boolean selected;
        boolean expanded;
        Rectangle bounds; // tree-relative
        int screenX = -1;
        int screenY = -1;
    }
}
