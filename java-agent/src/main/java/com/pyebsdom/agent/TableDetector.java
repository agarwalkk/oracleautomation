package com.pyebsdom.agent;

import javax.swing.*;
import java.awt.*;
import java.lang.reflect.Method;
import java.util.*;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Detects tables and grids inside a running AWT/Swing JVM using four
 * complementary strategies.
 *
 * <h3>Strategy 1 — Native JTable</h3>
 * Any {@code javax.swing.JTable} is detected directly.  Column names come
 * from the table model; cell values from {@code getValueAt(row, col)}.
 * Only rows where the row is visible (i.e. {@code row < getRowCount()}) are
 * included.  A maximum of {@value #MAX_ROWS} rows is read per table.
 *
 * <h3>Strategy 2 — Oracle/custom class names</h3>
 * Components whose simple class name contains one of the trigger keywords
 * (table, grid, block, folder, spreadtable, multirow, datatable) are flagged
 * as possible Oracle-specific tables.  Row/column counts are obtained via
 * reflection where available.
 *
 * <h3>Strategy 3 — Inferred coordinate grid</h3>
 * Under each container that has {@code >= 2} visible, focusable, named child
 * fields (leaves), the detector groups them by Y-coordinate (± {@value #Y_TOLERANCE}
 * pixels).  If there are {@code >= 2} Y-groups and {@code >= 2} X-positions
 * within a group (i.e. at least a 2×2 grid shape), the container is treated
 * as an inferred table.  Column headers are derived from the X-sorted names
 * in the first row; subsequent rows supply cell values.
 *
 * <h3>Strategy 4 — Repeated field names</h3>
 * Any group of sibling leaf components whose names match the pattern
 * {@code BASE_N} (where N is a decimal suffix, e.g. {@code ITEM_0},
 * {@code ITEM_1}) are collected into an inferred table.  Each unique base
 * name becomes a column; the row index is the numeric suffix.
 *
 * <h3>Thread safety</h3>
 * {@link #detect(Window[])} must be called on the Event Dispatch Thread.
 * All component state reads are therefore consistent and race-free.
 */
public final class TableDetector {

    // ── Tuning constants ──────────────────────────────────────────────────

    /** Maximum data rows extracted per table (avoid memory blow-up on huge grids). */
    static final int MAX_ROWS = 200;

    /** Y-coordinate tolerance (pixels) for grouping fields into the same row. */
    static final int Y_TOLERANCE = 4;

    /**
     * Class-name keywords that suggest an Oracle/custom table-like component.
     * Matched case-insensitively against {@code Class.getSimpleName()}.
     */
    private static final String[] ORACLE_KEYWORDS = {
        "table", "grid", "block", "folder", "spreadtable", "multirow", "datatable"
    };

    /** Minimum number of y-groups (rows) required for the coordinate-grid heuristic. */
    private static final int MIN_GRID_ROWS = 2;

    /** Minimum number of x-groups (columns) required for the coordinate-grid heuristic. */
    private static final int MIN_GRID_COLS = 2;

    /** Minimum repetitions of a pattern name to trigger the repeated-fields heuristic. */
    private static final int MIN_REPEAT_COUNT = 2;

    /**
     * Pattern matching names like {@code ITEM_0}, {@code QTY_3}, {@code LINE_NUM_12}.
     * Group 1 = base name (everything before the last {@code _N}).
     * Group 2 = numeric suffix.
     */
    private static final Pattern REPEATED_FIELD_PATTERN =
            Pattern.compile("^(.+)_(\\d+)$");

    // ── Public entry point ────────────────────────────────────────────────

    private TableDetector() {}

    /**
     * Run all four detection strategies across every window.
     *
     * <p>Must be called on the AWT Event Dispatch Thread.
     *
     * @param windows top-level windows from {@link Window#getWindows()}
     * @return ordered list of detected {@link TableModel} instances (may be empty)
     */
    public static List<TableModel> detect(Window[] windows) {
        List<TableModel> result = new ArrayList<>();
        AtomicInteger idGen = new AtomicInteger(0);

        for (Window win : windows) {
            if (!win.isVisible()) continue;
            walkForTables(win, win, "", result, idGen, new HashSet<Component>());
        }

        return result;
    }

    // ── Recursive tree walk ───────────────────────────────────────────────

    /**
     * Recursively walks a component subtree, applying all detection strategies.
     *
     * @param comp      current component
     * @param root      the top-level window (used for path construction)
     * @param pathSoFar slash-separated path accumulated so far
     * @param result    accumulated list of detected tables
     * @param idGen     monotonically increasing id counter
     * @param seen      set of components already captured as native JTables
     *                  (prevents double-reporting them under strategy 2)
     */
    private static void walkForTables(Component comp,
                                      Window root,
                                      String pathSoFar,
                                      List<TableModel> result,
                                      AtomicInteger idGen,
                                      Set<Component> seen) {
        if (!comp.isVisible()) return;

        String seg = comp.getClass().getSimpleName()
                     + (notBlank(comp.getName()) ? "[" + comp.getName() + "]" : "");
        String path = pathSoFar.isEmpty() ? seg : pathSoFar + "/" + seg;

        // Strategy 1: native JTable
        if (comp instanceof JTable) {
            seen.add(comp);
            TableModel tm = detectNativeJTable((JTable) comp, path, idGen);
            result.add(tm);
            // Do NOT recurse into JTable's children (renderers/editors)
            return;
        }

        // Strategy 2: Oracle/custom class name
        if (!seen.contains(comp) && isOracleTableLike(comp)) {
            seen.add(comp);
            TableModel tm = detectOracleComponent(comp, path, idGen);
            result.add(tm);
            // Still recurse — there may be nested real tables inside
        }

        if (comp instanceof Container) {
            Container container = (Container) comp;
            Component[] children = container.getComponents();

            // Strategy 3 + 4 operate on the children of this container
            if (children.length >= MIN_REPEAT_COUNT) {
                detectCoordinateGrid(children, path, result, idGen, seen);
                detectRepeatedFields(children, path, result, idGen, seen);
            }

            // Recurse
            for (Component child : children) {
                walkForTables(child, root, path, result, idGen, seen);
            }
        }
    }

    // ── Strategy 1: native JTable ─────────────────────────────────────────

    private static TableModel detectNativeJTable(JTable table, String path,
                                                  AtomicInteger idGen) {
        TableModel tm = new TableModel();
        tm.id         = idGen.incrementAndGet();
        tm.path       = path;
        tm.name       = nvl(table.getName());
        tm.title      = nvl(accessibleName(table));
        tm.source     = "native-jtable";
        tm.confidence = 0.95;

        javax.swing.table.TableModel model = table.getModel();
        int colCount = model.getColumnCount();
        int rowCount = model.getRowCount();

        // Column names
        for (int c = 0; c < colCount; c++) {
            try {
                Object hdr = model.getColumnName(c);
                tm.columns.add(hdr != null ? hdr.toString() : "Col" + c);
            } catch (Exception e) {
                tm.columns.add("Col" + c);
                tm.warnings.add("Column " + c + " name error: " + e.getMessage());
            }
        }

        // Rows
        int limit = Math.min(rowCount, MAX_ROWS);
        if (rowCount > MAX_ROWS) {
            tm.warnings.add("Row count " + rowCount + " exceeds limit " + MAX_ROWS
                    + "; only first " + MAX_ROWS + " rows included.");
        }

        for (int r = 0; r < limit; r++) {
            Map<String, String> row = new LinkedHashMap<>();
            for (int c = 0; c < colCount; c++) {
                String colName = tm.columns.get(c);
                try {
                    Object val = model.getValueAt(r, c);
                    row.put(colName, val != null ? val.toString() : "");
                } catch (Exception e) {
                    row.put(colName, "");
                    tm.warnings.add("Cell [" + r + "," + c + "] error: " + e.getMessage());
                }
            }
            tm.visibleRows.add(row);
        }

        return tm;
    }

    // ── Strategy 2: Oracle/custom component ───────────────────────────────

    private static boolean isOracleTableLike(Component comp) {
        String name = comp.getClass().getSimpleName().toLowerCase(Locale.ROOT);
        for (String kw : ORACLE_KEYWORDS) {
            if (name.contains(kw)) return true;
        }
        return false;
    }

    private static TableModel detectOracleComponent(Component comp, String path,
                                                     AtomicInteger idGen) {
        TableModel tm = new TableModel();
        tm.id         = idGen.incrementAndGet();
        tm.path       = path;
        tm.name       = nvl(comp.getName());
        tm.title      = nvl(accessibleName(comp));
        tm.source     = comp.getClass().getPackage() != null
                        && comp.getClass().getPackage().getName().startsWith("oracle")
                        ? "oracle-component" : "unknown";
        tm.confidence = 0.60;

        // Try to extract row/column counts via reflection
        int rowCount = reflectInt(comp, "getRowCount");
        int colCount = reflectInt(comp, "getColumnCount");

        if (rowCount >= 0) tm.warnings.add("rowCount=" + rowCount + " (from reflection)");
        if (colCount >= 0) tm.warnings.add("columnCount=" + colCount + " (from reflection)");

        // Try to get column names
        if (colCount > 0) {
            for (int c = 0; c < Math.min(colCount, 50); c++) {
                tm.columns.add("Col" + c);
            }
        }

        tm.warnings.add("Cell values not extractable from non-JTable component.");
        return tm;
    }

    // ── Strategy 3: coordinate-grid inference ─────────────────────────────

    private static void detectCoordinateGrid(Component[] children,
                                              String parentPath,
                                              List<TableModel> result,
                                              AtomicInteger idGen,
                                              Set<Component> seen) {
        // Collect visible, focusable, named leaf components not already captured
        List<Component> leaves = new ArrayList<>();
        for (Component c : children) {
            if (c.isVisible() && c.isFocusable()
                    && notBlank(c.getName())
                    && !(c instanceof Container && ((Container) c).getComponentCount() > 0)
                    && !seen.contains(c)) {
                leaves.add(c);
            }
        }

        if (leaves.size() < MIN_GRID_ROWS * MIN_GRID_COLS) return;

        // Group by Y-coordinate band
        TreeMap<Integer, List<Component>> yGroups = groupByY(leaves);
        if (yGroups.size() < MIN_GRID_ROWS) return;

        // Verify at least one row has >= MIN_GRID_COLS entries
        boolean hasMultiCol = false;
        for (List<Component> row : yGroups.values()) {
            if (row.size() >= MIN_GRID_COLS) { hasMultiCol = true; break; }
        }
        if (!hasMultiCol) return;

        // Use the first row's X-sorted names as column headers
        List<Component> firstRow = new ArrayList<>(yGroups.firstEntry().getValue());
        firstRow.sort(Comparator.comparingInt(c -> c.getBounds().x));

        TableModel tm = new TableModel();
        tm.id         = idGen.incrementAndGet();
        tm.path       = parentPath;
        tm.name       = "";
        tm.source     = "inferred-coordinate-grid";
        tm.confidence = 0.55;

        for (Component c : firstRow) {
            tm.columns.add(nvl(c.getName()));
        }

        // Build rows
        for (Map.Entry<Integer, List<Component>> entry : yGroups.entrySet()) {
            List<Component> rowComps = new ArrayList<>(entry.getValue());
            rowComps.sort(Comparator.comparingInt(c -> c.getBounds().x));

            Map<String, String> row = new LinkedHashMap<>();
            for (int ci = 0; ci < tm.columns.size() && ci < rowComps.size(); ci++) {
                row.put(tm.columns.get(ci), extractText(rowComps.get(ci)));
            }
            tm.visibleRows.add(row);

            if (tm.visibleRows.size() >= MAX_ROWS) break;
        }

        result.add(tm);
    }

    /** Group components by Y-band (within Y_TOLERANCE pixels). */
    private static TreeMap<Integer, List<Component>> groupByY(List<Component> comps) {
        TreeMap<Integer, List<Component>> groups = new TreeMap<>();
        for (Component c : comps) {
            int y = c.getBounds().y;
            Integer key = findNearbyKey(groups, y);
            if (key == null) {
                key = y;
                groups.put(key, new ArrayList<>());
            }
            groups.get(key).add(c);
        }
        return groups;
    }

    /** Find an existing key within Y_TOLERANCE of {@code y}, or null. */
    private static Integer findNearbyKey(TreeMap<Integer, List<Component>> map, int y) {
        for (Integer existing : map.keySet()) {
            if (Math.abs(existing - y) <= Y_TOLERANCE) return existing;
        }
        return null;
    }

    // ── Strategy 4: repeated field names ─────────────────────────────────

    private static void detectRepeatedFields(Component[] children,
                                              String parentPath,
                                              List<TableModel> result,
                                              AtomicInteger idGen,
                                              Set<Component> seen) {
        // Map: base-name → sorted-by-suffix list of (suffix, component)
        Map<String, TreeMap<Integer, Component>> baseMap = new LinkedHashMap<>();

        for (Component c : children) {
            if (!c.isVisible() || seen.contains(c)) continue;
            String name = c.getName();
            if (!notBlank(name)) continue;

            Matcher m = REPEATED_FIELD_PATTERN.matcher(name);
            if (!m.matches()) continue;

            String base    = m.group(1);
            int    suffix  = Integer.parseInt(m.group(2));
            baseMap.computeIfAbsent(base, k -> new TreeMap<>()).put(suffix, c);
        }

        // Keep only bases that repeat at least MIN_REPEAT_COUNT times
        Map<String, TreeMap<Integer, Component>> filtered = new LinkedHashMap<>();
        for (Map.Entry<String, TreeMap<Integer, Component>> e : baseMap.entrySet()) {
            if (e.getValue().size() >= MIN_REPEAT_COUNT) {
                filtered.put(e.getKey(), e.getValue());
            }
        }

        if (filtered.isEmpty()) return;

        // Each unique base is a column; each numeric suffix is a row index
        // Determine the union of row indices
        TreeSet<Integer> allSuffixes = new TreeSet<>();
        for (TreeMap<Integer, Component> m : filtered.values()) {
            allSuffixes.addAll(m.keySet());
        }

        // Mark all involved components as seen to avoid double-reporting
        for (TreeMap<Integer, Component> colMap : filtered.values()) {
            seen.addAll(colMap.values());
        }

        TableModel tm = new TableModel();
        tm.id         = idGen.incrementAndGet();
        tm.path       = parentPath;
        tm.name       = "";
        tm.source     = "inferred-repeated-fields";
        tm.confidence = 0.70;
        tm.columns.addAll(filtered.keySet());

        int rowsAdded = 0;
        for (int suffix : allSuffixes) {
            if (rowsAdded >= MAX_ROWS) break;
            Map<String, String> row = new LinkedHashMap<>();
            for (String base : filtered.keySet()) {
                Component c = filtered.get(base).get(suffix);
                row.put(base, c != null ? extractText(c) : "");
            }
            tm.visibleRows.add(row);
            rowsAdded++;
        }

        result.add(tm);
    }

    // ── Reflection helpers ────────────────────────────────────────────────

    /** Invoke a zero-arg int-returning method via reflection; return -1 on failure. */
    private static int reflectInt(Component comp, String methodName) {
        try {
            Method m = comp.getClass().getMethod(methodName);
            Object val = m.invoke(comp);
            if (val instanceof Number) return ((Number) val).intValue();
        } catch (Exception ignored) {}
        return -1;
    }

    /** Extract the displayable text value from a component. */
    private static String extractText(Component comp) {
        // JTextField / JLabel / JTextArea
        for (String method : new String[] { "getText", "getSelectedItem",
                                            "getSelectedValue", "getValue" }) {
            try {
                Method m = comp.getClass().getMethod(method);
                Object v = m.invoke(comp);
                if (v != null) return v.toString();
            } catch (Exception ignored) {}
        }
        // Fallback: name
        return nvl(comp.getName());
    }

    /** Return the accessible name of a component, or empty string. */
    private static String accessibleName(Component comp) {
        try {
            javax.accessibility.AccessibleContext ac =
                    ((javax.accessibility.Accessible) comp).getAccessibleContext();
            if (ac != null && ac.getAccessibleName() != null) {
                return ac.getAccessibleName();
            }
        } catch (Exception ignored) {}
        return "";
    }

    // ── String utilities ──────────────────────────────────────────────────

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty();
    }

    /** Null-safe string coercion (returns "" for null). */
    private static String nvl(String s) {
        return s != null ? s : "";
    }
}
