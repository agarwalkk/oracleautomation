package com.pyebsdom.agent.extract;

import com.pyebsdom.agent.model.*;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Post-scan pass that resolves <b>layout structure inside the JVM</b> and
 * writes
 * it onto the nodes explicitly, so Python renders without re-inferring
 * anything.
 *
 * <p>
 * Pass order (each depends on the previous):
 * <ol>
 * <li>{@link #reclassifyLov} — a field whose label ends "List of Values" is an
 * LOV; set {@code semanticType=LOV} (canonicalLabel is stripped in
 * {@link IdentityResolver}).</li>
 * <li>{@link #annotateGrids} — multi-record blocks → {@code Grid} /
 * {@code GridCell}
 * with {@code recordIndex} + {@code columnKey} (requires &ge;2 repeating
 * columns, so a lone repeating field is NOT a spurious table).</li>
 * <li>{@link #annotateTabs} — every field gets {@code ownerTab} from the
 * <b>innermost FScrollBox whose subtree contains exactly one tab prefix</b>
 * (Oracle omits the "&lt;Tab&gt; tab page " prefix on most fields, so we
 * propagate from the container, not the field). Data fields inside a tab
 * region but matching no tab are marked
 * {@code containerRole=OrphanTabContent} (background views → dropped).</li>
 * <li>{@link #annotateMirrors} — a non-editable plain field that is not a grid
 * cell, orphan, or LOV is a read-only display echo → {@code isMirror}.</li>
 * </ol>
 *
 * <p>
 * Runs after {@link DomScanner}/{@link TreeItemExpander} and before
 * {@link IdentityResolver}.
 */
public final class StructureAnnotator {

    private static final String LOV_SUFFIX = "List of Values";

    private StructureAnnotator() {
    }

    public static void annotate(DomNode window) {
        if (window == null)
            return;
        List<DomNode> all = new ArrayList<>();
        flatten(window, all);
        Map<DomNode, DomNode> parent = new HashMap<>();
        linkParents(window, parent);

        for (DomNode n : all) {
            if (n.canonicalLabel == null)
                n.canonicalLabel = IdentityResolver.canonicalLabel(n);
        }
        reclassifyLov(all);
        annotateGrids(all);
        annotateTabs(all, parent);
        annotateMirrors(all);
    }

    // ── 1. LOV reclassification ───────────────────────────────────────────

    private static void reclassifyLov(List<DomNode> all) {
        for (DomNode n : all) {
            if (!"Field".equals(n.semanticType))
                continue;
            String an = n.accessibleName != null ? n.accessibleName : "";
            String cl = n.canonicalLabel != null ? n.canonicalLabel : "";
            if (endsWithLov(an) || endsWithLov(cl)) {
                n.semanticType = "LOV";
            }
        }
    }

    private static boolean endsWithLov(String s) {
        if (s == null)
            return false;
        String t = s.trim();
        return t.endsWith(LOV_SUFFIX);
    }

    // ── 2. Grids (≥2 columns required) ────────────────────────────────────

    private static void annotateGrids(List<DomNode> all) {
        for (DomNode container : all) {
            if (container.children.size() < 6)
                continue;
            annotateGridContainer(container);
        }
    }

    private static void annotateGridContainer(DomNode container) {
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
            return;

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
        // A real grid has at least TWO repeating columns; one repeating field
        // (e.g. a column of "To" date fields) is NOT a table.
        if (gridColumns.size() < 2)
            return;

        container.containerRole = "Grid";

        List<DomNode> longest = null;
        for (List<DomNode> col : gridColumns.values()) {
            if (longest == null || col.size() > longest.size())
                longest = col;
        }
        int[] rowYs = new int[longest.size()];
        for (int i = 0; i < longest.size(); i++)
            rowYs[i] = longest.get(i).bounds.y;

        int currentRow = -1;
        for (List<DomNode> col : gridColumns.values()) {
            for (DomNode cell : col) {
                int row = nearestRow(rowYs, cell.bounds.y);
                cell.containerRole = "GridCell";
                cell.recordIndex = row;
                cell.columnKey = cell.canonicalLabel;
                if ((cell.focused || cell.selected) && currentRow < 0)
                    currentRow = row;
            }
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
        return "Field".equals(st) || "LOV".equals(st) || "Checkbox".equals(st)
                || "ComboBox".equals(st) || "Button".equals(st);
    }

    // ── 3. Tabs: ownerTab via innermost single-prefix FScrollBox ──────────

    private static void annotateTabs(List<DomNode> all, Map<DomNode, DomNode> parent) {
        // Mark the tab folders.
        for (DomNode n : all) {
            if (("FormsTabPanel".equals(n.simpleClassName) || "TabBar".equals(n.simpleClassName))
                    && tabTitleCount(n) >= 1) {
                n.containerRole = "TabFolder";
            }
        }

        // For each FScrollBox compute the set of distinct tab prefixes in its
        // subtree. Exactly one → "dedicated" (owns that tab). >=1 → "tab region".
        Map<DomNode, String> dedicated = new HashMap<>();
        Set<DomNode> tabRegion = new HashSet<>();
        for (DomNode n : all) {
            if (!"FScrollBox".equals(n.simpleClassName))
                continue;
            Set<String> prefixes = new HashSet<>();
            collectPrefixes(n, prefixes);
            if (!prefixes.isEmpty())
                tabRegion.add(n);
            if (prefixes.size() == 1)
                dedicated.put(n, prefixes.iterator().next());
        }
        if (dedicated.isEmpty() && tabRegion.isEmpty())
            return;

        for (DomNode n : all) {
            DomNode cur = parent.get(n);
            String owner = null;
            boolean inRegion = false;
            while (cur != null) {
                if (owner == null && dedicated.containsKey(cur))
                    owner = dedicated.get(cur);
                if (tabRegion.contains(cur))
                    inRegion = true;
                cur = parent.get(cur);
            }
            if (owner != null) {
                n.ownerTab = owner;
            } else if (inRegion && n.containerRole == null && isDataField(n.semanticType)) {
                // data field inside the tab area but matching no tab → background
                // view (e.g. a non-selected record block). Renderer drops it.
                n.containerRole = "OrphanTabContent";
            }
        }
    }

    private static void collectPrefixes(DomNode n, Set<String> out) {
        String an = n.accessibleName;
        if (an != null) {
            int i = an.indexOf(" tab page ");
            if (i > 0)
                out.add(an.substring(0, i));
        }
        for (DomNode c : n.children)
            collectPrefixes(c, out);
    }

    private static int tabTitleCount(DomNode n) {
        String raw = n.attributes.get("tabTitles");
        if (raw == null || raw.trim().isEmpty())
            return 0;
        int count = 0;
        for (String t : raw.split("\\|"))
            if (!t.trim().isEmpty())
                count++;
        return count;
    }

    private static boolean isDataField(String st) {
        return "Field".equals(st) || "LOV".equals(st) || "ComboBox".equals(st)
                || "Checkbox".equals(st) || "RadioButton".equals(st);
    }

    // ── 4. Mirrors ────────────────────────────────────────────────────────

    private static void annotateMirrors(List<DomNode> all) {
        for (DomNode n : all) {
            if (!"Field".equals(n.semanticType))
                continue;
            if (n.editable)
                continue;
            if ("GridCell".equals(n.containerRole) || "OrphanTabContent".equals(n.containerRole))
                continue;
            if (endsWithLov(n.accessibleName))
                continue;
            n.isMirror = true;
        }
    }

    // ── helpers ───────────────────────────────────────────────────────────

    private static void flatten(DomNode n, List<DomNode> out) {
        out.add(n);
        for (DomNode c : n.children)
            flatten(c, out);
    }

    private static void linkParents(DomNode n, Map<DomNode, DomNode> parent) {
        for (DomNode c : n.children) {
            parent.put(c, n);
            linkParents(c, parent);
        }
    }
}
