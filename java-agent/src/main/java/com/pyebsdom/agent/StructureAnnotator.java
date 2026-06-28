package com.pyebsdom.agent;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Post-scan pass that resolves <b>layout structure inside the JVM</b> and
 * writes
 * it onto the nodes explicitly, so Python never re-infers it from
 * accessible-name
 * prefixes or pixel geometry.
 *
 * <p>
 * It sets, where applicable:
 * <ul>
 * <li>{@code containerRole = TabFolder / TabPage} and {@link DomNode#ownerTab}
 * on every node owned by a tab page — derived from {@code TabPanelSheet}
 * ancestry (deterministic) with the legacy "&lt;Tab&gt; tab page " prefix
 * only as a fallback.</li>
 * <li>{@code containerRole = Grid} on multi-record blocks and
 * {@code containerRole = GridCell} + {@link DomNode#recordIndex} +
 * {@link DomNode#columnKey} on the repeating cells, with
 * {@link DomNode#current} on the selected record.</li>
 * <li>{@link DomNode#isMirror} on read-only fields that merely echo an
 * editable field with the same canonical label.</li>
 * </ul>
 *
 * <p>
 * Run order: after {@link DomScanner} builds + materialises the tree
 * (including {@link TreeItemExpander}) and before {@link IdentityResolver},
 * because the resolver's scoping uses {@code containerRole} / {@code ownerTab}
 * / {@code recordIndex}.
 */
public final class StructureAnnotator {

    private StructureAnnotator() {
    }

    public static void annotate(DomNode window) {
        if (window == null)
            return;
        // canonicalLabel is needed by grid + mirror grouping; compute up front
        // (IdentityResolver recomputes the same value — cheap and idempotent).
        forEach(window, n -> {
            if (n.canonicalLabel == null)
                n.canonicalLabel = IdentityResolver.canonicalLabel(n);
        });
        annotateTabs(window);
        annotateGrids(window);
        annotateMirrors(window);
    }

    // ── Tabs ──────────────────────────────────────────────────────────────

    private static void annotateTabs(DomNode root) {
        List<DomNode> all = new ArrayList<>();
        flatten(root, all);

        for (DomNode tabPanel : all) {
            if (!"FormsTabPanel".equals(tabPanel.simpleClassName)
                    && !"TabBar".equals(tabPanel.simpleClassName)) {
                continue;
            }
            String[] titles = tabTitles(tabPanel);
            if (titles.length == 0)
                continue;
            tabPanel.containerRole = "TabFolder";

            // Deterministic path: TabPanelSheet[i] children of the FormsTabPanel
            // ARE the page contents, in tab order. Map sheet index -> title.
            List<DomNode> sheets = descendantsOfType(tabPanel, "TabPanelSheet");
            for (int i = 0; i < sheets.size() && i < titles.length; i++) {
                DomNode sheet = sheets.get(i);
                sheet.containerRole = "TabPage";
                sheet.ownerTab = titles[i];
                forEach(sheet, n -> {
                    if (n.ownerTab == null)
                        n.ownerTab = sheet.ownerTab;
                });
            }
        }

        // Fallback for forms that expose page content as siblings (not under a
        // TabPanelSheet): use the un-volatile part of the prefix, but compute it
        // HERE in Java so Python receives ownerTab directly.
        for (DomNode n : all) {
            if (n.ownerTab != null)
                continue;
            String an = n.accessibleName;
            if (an == null)
                continue;
            int idx = an.indexOf(" tab page ");
            if (idx > 0)
                n.ownerTab = an.substring(0, idx).trim();
        }
    }

    private static String[] tabTitles(DomNode tabPanelOrBar) {
        String raw = tabPanelOrBar.attributes.get("tabTitles");
        if (raw == null) {
            // TabBar holds the titles; a FormsTabPanel may wrap one.
            for (DomNode d : descendantsOfType(tabPanelOrBar, "TabBar")) {
                raw = d.attributes.get("tabTitles");
                if (raw != null)
                    break;
            }
        }
        if (raw == null || raw.trim().isEmpty())
            return new String[0];
        List<String> out = new ArrayList<>();
        for (String t : raw.split("\\|")) {
            String s = t.trim();
            if (!s.isEmpty())
                out.add(s);
        }
        return out.toArray(new String[0]);
    }

    // ── Grids ─────────────────────────────────────────────────────────────

    /**
     * Generalises the old {@code DomScanner.annotateGridRows}: any container
     * whose Field/Checkbox/ComboBox children include labels that repeat with
     * regular vertical spacing is a multi-record grid. Cells receive a
     * {@code recordIndex} (the row) and {@code columnKey} (the canonical label),
     * and the focused/selected row is flagged {@code current}.
     */
    private static void annotateGrids(DomNode root) {
        List<DomNode> all = new ArrayList<>();
        flatten(root, all);
        for (DomNode container : all) {
            if (container.children.size() < 3)
                continue;
            annotateGridContainer(container);
        }
    }

    private static void annotateGridContainer(DomNode container) {
        // Collect candidate cell children (positioned, labelled controls).
        List<DomNode> cells = new ArrayList<>();
        for (DomNode c : container.children) {
            if (!isCellRole(c.semanticType))
                continue;
            if (c.canonicalLabel == null || c.canonicalLabel.isEmpty())
                continue;
            if (c.bounds == null)
                continue;
            cells.add(c);
        }
        if (cells.size() < 6)
            return; // need at least a couple of multi-col rows

        // Group by canonical label; a label that repeats >=3 times at regular
        // y-spacing is a grid column.
        Map<String, List<DomNode>> byLabel = new LinkedHashMap<>();
        for (DomNode c : cells) {
            byLabel.computeIfAbsent(c.canonicalLabel, k -> new ArrayList<>()).add(c);
        }

        Map<String, List<DomNode>> gridColumns = new LinkedHashMap<>();
        for (Map.Entry<String, List<DomNode>> e : byLabel.entrySet()) {
            List<DomNode> inst = e.getValue();
            if (inst.size() < 3)
                continue;
            inst.sort((a, b) -> Integer.compare(a.bounds.y, b.bounds.y));
            if (hasRegularSpacing(inst))
                gridColumns.put(e.getKey(), inst);
        }
        if (gridColumns.isEmpty())
            return;

        container.containerRole = "Grid";

        // Build a canonical row y-ladder from the longest column so every
        // column's cells map to the same record indices.
        List<DomNode> longest = null;
        for (List<DomNode> col : gridColumns.values()) {
            if (longest == null || col.size() > longest.size())
                longest = col;
        }
        int[] rowYs = new int[longest.size()];
        for (int i = 0; i < longest.size(); i++)
            rowYs[i] = longest.get(i).bounds.y;

        for (List<DomNode> col : gridColumns.values()) {
            for (DomNode cell : col) {
                int row = nearestRow(rowYs, cell.bounds.y);
                cell.containerRole = "GridCell";
                cell.recordIndex = row;
                cell.columnKey = cell.canonicalLabel;
            }
        }

        // Current record: the row containing the focused/selected cell.
        int currentRow = -1;
        for (List<DomNode> col : gridColumns.values()) {
            for (DomNode cell : col) {
                if ((cell.focused || cell.selected) && cell.recordIndex >= 0) {
                    currentRow = cell.recordIndex;
                    break;
                }
            }
            if (currentRow >= 0)
                break;
        }
        if (currentRow >= 0) {
            for (List<DomNode> col : gridColumns.values()) {
                for (DomNode cell : col) {
                    if (cell.recordIndex == currentRow)
                        cell.current = true;
                }
            }
        }
    }

    private static boolean hasRegularSpacing(List<DomNode> sortedByY) {
        if (sortedByY.size() < 3)
            return false;
        int[] gaps = new int[sortedByY.size() - 1];
        for (int i = 0; i < gaps.length; i++) {
            gaps[i] = sortedByY.get(i + 1).bounds.y - sortedByY.get(i).bounds.y;
        }
        int[] sorted = gaps.clone();
        Arrays.sort(sorted);
        int median = sorted[sorted.length / 2];
        if (median <= 0)
            return false;
        int regular = 0;
        for (int g : gaps)
            if (Math.abs(g - median) <= median / 2)
                regular++;
        return regular >= gaps.length * 0.6;
    }

    private static int nearestRow(int[] rowYs, int y) {
        int best = 0, bestD = Integer.MAX_VALUE;
        for (int i = 0; i < rowYs.length; i++) {
            int d = Math.abs(rowYs[i] - y);
            if (d < bestD) {
                bestD = d;
                best = i;
            }
        }
        return best;
    }

    private static boolean isCellRole(String st) {
        return "Field".equals(st) || "Checkbox".equals(st)
                || "ComboBox".equals(st) || "LOV".equals(st) || "Button".equals(st);
    }

    // ── Mirrors ───────────────────────────────────────────────────────────

    /**
     * A read-only field that echoes an editable field with the same canonical
     * label (Oracle Forms pairs an input item with a non-editable display item).
     * Decided from editability + label, not a name regex.
     */
    private static void annotateMirrors(DomNode root) {
        List<DomNode> all = new ArrayList<>();
        flatten(root, all);
        Map<String, Boolean> hasEditable = new HashMap<>();
        for (DomNode n : all) {
            if (!"Field".equals(n.semanticType))
                continue;
            if (n.canonicalLabel == null || n.canonicalLabel.isEmpty())
                continue;
            if (n.editable)
                hasEditable.put(n.ownerTab + "|" + n.canonicalLabel, Boolean.TRUE);
        }
        for (DomNode n : all) {
            if (!"Field".equals(n.semanticType))
                continue;
            if (n.editable || n.canonicalLabel == null || n.canonicalLabel.isEmpty())
                continue;
            if (n.containerRole != null)
                continue; // grid cells are not mirrors
            if (Boolean.TRUE.equals(hasEditable.get(n.ownerTab + "|" + n.canonicalLabel))) {
                n.isMirror = true;
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    private interface NodeVisitor {
        void visit(DomNode n);
    }

    private static void forEach(DomNode n, NodeVisitor v) {
        v.visit(n);
        for (DomNode c : n.children)
            forEach(c, v);
    }

    private static void flatten(DomNode n, List<DomNode> out) {
        out.add(n);
        for (DomNode c : n.children)
            flatten(c, out);
    }

    private static List<DomNode> descendantsOfType(DomNode n, String simpleClassName) {
        List<DomNode> out = new ArrayList<>();
        collectType(n, simpleClassName, out);
        return out;
    }

    private static void collectType(DomNode n, String scn, List<DomNode> out) {
        for (DomNode c : n.children) {
            if (scn.equals(c.simpleClassName))
                out.add(c);
            collectType(c, scn, out);
        }
    }
}
