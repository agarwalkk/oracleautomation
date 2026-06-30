package com.pyebsdom.agent;

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
        annotateActions(all);
    }

    // ── 5. Authoritative action verbs (Forms item type + state) ───────────

    /**
     * Stamp {@link DomNode#formsActions} — the exact actuator verb(s) the agent
     * supports for this element — derived from the handler type and live state.
     * Lets the recorder dispatch without re-deriving from role. {@code null} for
     * non-Forms items, where the renderer keeps its role heuristic.
     */
    private static void annotateActions(List<DomNode> all) {
        for (DomNode n : all) {
            String a = actionsFor(n);
            if (a != null)
                n.formsActions = a;
        }
    }

    private static String actionsFor(DomNode n) {
        if (n.treePath != null && !n.treePath.isEmpty())
            return "treeAction"; // tree / LOV-list row
        if (n.locked || n.isMirror)
            return "inspect";
        String ft = n.formsType;
        if (ft == null)
            return null;
        switch (ft) {
            case "TextFieldItem":
                if (!n.editable)
                    return "inspect";
                return n.hasLov ? "setText,openLov" : "setText";
            case "CheckboxItem":
                return "setCheckbox";
            case "ButtonItem":
            case "IconicButtonItem":
                return "pressButton";
            case "PopListItem":
                return "setPoplist";
            case "TListItem":
                return "setList";
            case "RadioButtonItem":
                return "selectRadio";
            default:
                return null;
        }
    }

    // ── 1. LOV reclassification ───────────────────────────────────────────

    private static void reclassifyLov(List<DomNode> all) {
        for (DomNode n : all) {
            if (!"Field".equals(n.semanticType))
                continue;
            // Ground truth first: the item's isLOVButtonDisplayed() flag. Fall
            // back to the "<label> List of Values" accessibleName suffix only
            // when the flag is unavailable (non-text item types, older agent).
            if (n.hasLov) {
                n.semanticType = "LOV";
                continue;
            }
            String an = n.accessibleName != null ? n.accessibleName : "";
            String cl = n.canonicalLabel != null ? n.canonicalLabel : "";
            if (endsWithLov(an) || endsWithLov(cl)) {
                n.semanticType = "LOV";
                n.hasLov = true; // normalise so downstream sees a single signal
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
        // Pairs of (effective cell node, bounds-provider) — for direct children
        // the cell is its own bounds-provider; for LWCheckbox grandchildren the
        // AWT CheckBox wrapper provides the absolute bounds used for row-matching
        // (the LWCheckbox bounds are relative to the wrapper).
        List<DomNode[]> cellPairs = new ArrayList<>();

        for (DomNode c : container.children) {
            if ("CheckBox".equals(c.simpleClassName) && c.bounds != null) {
                // AWT CheckBox wrapper — look for a single LWCheckbox child that
                // is a real Checkbox (Oracle Forms "On Hold"-style column).
                // Must be handled before the isCellRole check because the wrapper
                // also carries semanticType="Checkbox" and would otherwise be
                // picked up with its meaningless placeholder canonicalLabel.
                DomNode lw = findSingleLwCheckbox(c);
                if (lw != null && lw.canonicalLabel != null && !lw.canonicalLabel.isEmpty()) {
                    cells.add(lw);
                    cellPairs.add(new DomNode[] { lw, c }); // use wrapper bounds for row-matching
                }
            } else if (isCellRole(c.semanticType)) {
                if (c.canonicalLabel == null || c.canonicalLabel.isEmpty())
                    continue;
                if (c.bounds == null)
                    continue;
                cells.add(c);
                cellPairs.add(new DomNode[] { c, c });
            }
        }
        if (cells.size() < 6)
            return;

        Map<String, List<DomNode[]>> byLabel = new LinkedHashMap<>();
        for (DomNode[] pair : cellPairs) {
            DomNode cell = pair[0];
            // Authoritative column identity: the Forms item name (mName) when
            // present, else the display label. Distinguishes columns that share a
            // header and is stable across scans/language; the display header stays
            // canonicalLabel (set as columnKey below).
            String colKey = (cell.formsItemName != null && !cell.formsItemName.isEmpty())
                    ? cell.formsItemName
                    : cell.canonicalLabel;
            byLabel.computeIfAbsent(colKey, k -> new ArrayList<>()).add(pair);
        }

        Map<String, List<DomNode[]>> gridColumns = new LinkedHashMap<>();
        for (Map.Entry<String, List<DomNode[]>> e : byLabel.entrySet()) {
            List<DomNode[]> inst = e.getValue();
            if (inst.size() < 3)
                continue;
            // Sort by the bounds-provider's y position.
            inst.sort((a, b) -> Integer.compare(a[1].bounds.y, b[1].bounds.y));
            if (hasRegularSpacingPairs(inst))
                gridColumns.put(e.getKey(), inst);
        }
        // A real grid has at least TWO repeating columns; one repeating field
        // (e.g. a column of "To" date fields) is NOT a table.
        if (gridColumns.size() < 2)
            return;

        container.containerRole = "Grid";

        // Build row Y array from the longest column (using bounds-provider y).
        List<DomNode[]> longest = null;
        for (List<DomNode[]> col : gridColumns.values()) {
            if (longest == null || col.size() > longest.size())
                longest = col;
        }
        int[] rowYs = new int[longest.size()];
        for (int i = 0; i < longest.size(); i++)
            rowYs[i] = longest.get(i)[1].bounds.y;

        int currentRow = -1;
        for (List<DomNode[]> col : gridColumns.values()) {
            for (DomNode[] pair : col) {
                DomNode cell = pair[0];
                DomNode boundsProvider = pair[1];
                int row = nearestRow(rowYs, boundsProvider.bounds.y);
                cell.containerRole = "GridCell";
                cell.recordIndex = row;
                cell.columnKey = cell.canonicalLabel;
                if ((cell.focused || cell.selected) && currentRow < 0)
                    currentRow = row;
            }
        }
        if (currentRow >= 0) {
            for (List<DomNode[]> col : gridColumns.values()) {
                for (DomNode[] pair : col) {
                    DomNode cell = pair[0];
                    if (cell.recordIndex == currentRow)
                        cell.current = true;
                }
            }
        }
    }

    /**
     * If {@code wrapper} is an AWT CheckBox node with exactly one child that
     * has {@code semanticType == "Checkbox"}, return that child; else null.
     * Searches up to two levels deep to handle intermediate wrapper layers.
     */
    private static DomNode findSingleLwCheckbox(DomNode wrapper) {
        if (wrapper.children.size() == 1) {
            DomNode child = wrapper.children.get(0);
            if ("Checkbox".equals(child.semanticType))
                return child;
            // One more level (e.g. CheckBox → LWComponent → LWCheckbox).
            if (child.children.size() == 1) {
                DomNode grandchild = child.children.get(0);
                if ("Checkbox".equals(grandchild.semanticType))
                    return grandchild;
            }
        }
        return null;
    }

    private static boolean hasRegularSpacingPairs(List<DomNode[]> sortedByY) {
        if (sortedByY.size() < 3)
            return false;
        int[] gaps = new int[sortedByY.size() - 1];
        for (int i = 0; i < gaps.length; i++) {
            gaps[i] = sortedByY.get(i + 1)[1].bounds.y - sortedByY.get(i)[1].bounds.y;
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
        // Layout heuristic (prefix / FScrollBox dedication) — only meaningful
        // when tab regions exist. Handler-authoritative names are applied after.
        if (!(dedicated.isEmpty() && tabRegion.isEmpty())) {
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

        // Authoritative override: the Forms handler's own tab name
        // (FormsHandler.getParentTabName) supersedes the heuristic wherever an
        // item carries it — deterministic, and it covers items the prefix scan
        // can't place (e.g. tree rows). This is what retires the
        // box-dedication heuristic for items that have a handler.
        for (DomNode n : all) {
            if (n.formsTabName != null && !n.formsTabName.trim().isEmpty()) {
                n.ownerTab = n.formsTabName;
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
