package com.pyebsdom.agent.extract;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.accessibility.AccessibleState;
import javax.accessibility.AccessibleStateSet;
import java.lang.reflect.Method;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.Set;
import java.util.List;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.IdentityHashMap;
import java.util.HashSet;

/**
 * Safe, allowlist-based reflection extractor for AWT/Swing components.
 *
 * <p>Only zero-argument, public, non-static methods whose names are on the
 * explicit allowlist are called.  Every invocation is wrapped in a
 * try-catch so a broken component cannot propagate exceptions up to the
 * scanner.
 *
 * <p>Return values are converted to strings with {@link Object#toString()}.
 * Null returns are stored as the literal string {@code "null"}.
 * Results that are arrays are rendered as a comma-separated list.
 *
 * <h3>Why an allowlist?</h3>
 * The target JVM contains live EBS business logic.  Calling arbitrary
 * methods (e.g. {@code dispose()}, {@code doClick()}, {@code delete()})
 * would mutate application state or crash the forms session.  The allowlist
 * is intentionally limited to <em>read-only</em>, <em>state-reporting</em>
 * methods.
 */
public final class ComponentReader {

    /** Ordered set of safe zero-argument method names to call. */
    private static final Set<String> ALLOWLIST = Collections.unmodifiableSet(
            new LinkedHashSet<>(Arrays.asList(
                    "getText",
                    "getName",
                    "getLabel",
                    "getValue",
                    "getToolTipText",
                    "getTitle",
                    "getSelectedItem",
                    "getSelectedValue",
                    "getSelectedIndex",
                    "getItemCount",
                    "getRowCount",
                    "getColumnCount",
                    "getSelectedRow",
                    "getSelectedRows",
                    "getLeadSelectionIndex",
                    "getBackground",
                    "getForeground",
                    "isVisible",
                    "isShowing",
                    "isEnabled",
                    "isFocusable",
                    "isFocusOwner",
                    "isEditable",
                    "isSelected",
                    "isChecked",
                    "getState"
            ))
    );

    private ComponentReader() {}

    /**
     * Invokes all allowlisted methods found on {@code obj} and returns the
     * results as a name → stringified-value map.
     *
     * <p>Methods that are not present on the target class are silently
     * skipped.  Methods that throw exceptions during invocation are skipped
     * and the exception is not propagated.
     *
     * @param obj any object, typically an AWT/Swing component
     * @return ordered map of method name → string result
     */
    public static Map<String, String> extract(Object obj) {
        if (obj == null) return Collections.emptyMap();

        Map<String, String> result = new LinkedHashMap<>();
        Class<?> clazz = obj.getClass();

        for (String methodName : ALLOWLIST) {
            try {
                Method m = resolveMethod(clazz, methodName);
                if (m == null) continue;

                Object returnValue = m.invoke(obj);
                result.put(methodName, stringify(returnValue));

            } catch (Exception ignored) {
                // Skip silently — the component may not support this method
                // or may throw for legitimate reasons (e.g. not initialised).
            }
        }

        return result;
    }

    /**
     * Safely extracts visible/selectable option values from components that
     * expose read-only indexed item APIs (Swing JComboBox/JList and many Oracle
     * EWT poplists). Returns an empty list when no such API exists.
     */
    public static List<String> extractOptions(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();

        List<String> direct = extractIndexedOptions(obj, "getItemCount", "getItemAt", limit);
        if (!direct.isEmpty()) return direct;

        try {
            Method getModel = obj.getClass().getMethod("getModel");
            Object model = getModel.invoke(obj);
            if (model != null) {
                List<String> modelOptions = extractIndexedOptions(model, "getSize", "getElementAt", limit);
                if (!modelOptions.isEmpty()) return modelOptions;
            }
        } catch (Exception ignored) {}

        return Collections.emptyList();
    }

    /**
     * Extracts menu item labels from Oracle EWT LWMenu or Swing JMenu.
     * Uses {@code getItemCount()} + {@code getItem(int)} to walk menu items,
     * then extracts the label via {@code getLabel()}, {@code getText()}, or
     * the accessible name.  Returns an empty list when unsupported.
     *
     * <p>Each entry contains the item label and enabled state, formatted as
     * {@code "label\tenabled"} where enabled is {@code "1"} or {@code "0"}.
     * Separator items (null or empty labels) are skipped.
     */
    public static List<String> extractMenuItems(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();
        List<String> items = new ArrayList<>();
        try {
            // Try getItemCount() + getItem(int)
            Method countMethod = resolveMethod(obj.getClass(), "getItemCount");
            if (countMethod == null) return items;
            int count = toInt(countMethod.invoke(obj));
            if (count <= 0) return items;

            Method getItem = resolveIntMethod(obj.getClass(), "getItem");
            if (getItem == null) getItem = resolveIntMethod(obj.getClass(), "getItemAt");
            if (getItem == null) getItem = resolveIntMethod(obj.getClass(), "getMenuItem");
            if (getItem == null) return items;

            int n = Math.min(count, limit);
            for (int i = 0; i < n; i++) {
                try {
                    Object item = getItem.invoke(obj, i);
                    if (item == null) continue;

                    // Extract label
                    String label = "";
                    for (String getter : new String[]{"getLabel", "getText", "getName"}) {
                        Method m = resolveMethod(item.getClass(), getter);
                        if (m != null) {
                            Object val = m.invoke(item);
                            if (val != null) {
                                String s = val.toString().trim();
                                if (!s.isEmpty()) { label = s; break; }
                            }
                        }
                    }
                    // Fallback: accessible name
                    if (label.isEmpty() && item instanceof Accessible) {
                        AccessibleContext ac = ((Accessible) item).getAccessibleContext();
                        if (ac != null && ac.getAccessibleName() != null) {
                            label = ac.getAccessibleName().trim();
                        }
                    }
                    if (label.isEmpty()) continue;  // separator or unknown

                    // Extract enabled state
                    boolean enabled = true;
                    Method isEnabled = resolveMethod(item.getClass(), "isEnabled");
                    if (isEnabled != null) {
                        Object val = isEnabled.invoke(item);
                        if (val instanceof Boolean) enabled = (Boolean) val;
                    }

                    // Extract checked state (for checkbox menu items)
                    boolean checked = false;
                    Method getState = resolveMethod(item.getClass(), "getState");
                    if (getState != null) {
                        Object val = getState.invoke(item);
                        if (val instanceof Boolean) checked = (Boolean) val;
                    }

                    items.add(label + "\t" + (enabled ? "1" : "0") + "\t" + (checked ? "1" : "0"));
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}
        return items;
    }

    /**
     * Extracts menu items via the Accessibility API (accessible children).
     * Works for Oracle EWT LWMenu where getItemCount() returns 0 but
     * accessible children expose each menu item with role and state info.
     *
     * <p>Each entry: {@code "label\trole\tchecked"} where role is
     * {@code "check_box"} or {@code "menu_item"}, checked is {@code "1"} or {@code "0"}.
     */
    public static List<String> extractAccessibleMenuItems(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();
        List<String> items = new ArrayList<>();
        try {
            if (!(obj instanceof Accessible)) return items;
            AccessibleContext ac = ((Accessible) obj).getAccessibleContext();
            if (ac == null) return items;
            int count = ac.getAccessibleChildrenCount();
            if (count <= 0) return items;
            int n = Math.min(count, limit);
            for (int i = 0; i < n; i++) {
                try {
                    Accessible child = ac.getAccessibleChild(i);
                    if (child == null) continue;
                    AccessibleContext childCtx = child.getAccessibleContext();
                    if (childCtx == null) continue;
                    String name = childCtx.getAccessibleName();
                    if (name == null || name.trim().isEmpty()) continue;

                    // Detect checkbox by class name (Oracle EWT LWCheckboxMenuItem)
                    String className = child.getClass().getName().toLowerCase();
                    boolean isCheckbox = className.contains("checkbox");

                    // Also check accessible role
                    if (!isCheckbox) {
                        javax.accessibility.AccessibleRole r = childCtx.getAccessibleRole();
                        if (r != null && r.toString().toLowerCase().contains("check")) {
                            isCheckbox = true;
                        }
                    }

                    // Detect checked state
                    boolean checked = false;
                    AccessibleStateSet states = childCtx.getAccessibleStateSet();
                    if (states != null && states.contains(AccessibleState.CHECKED)) {
                        checked = true;
                    }
                    if (!checked && child instanceof java.awt.Component) {
                        try {
                            Method sel = child.getClass().getMethod("isSelected");
                            Object val = sel.invoke(child);
                            if (Boolean.TRUE.equals(val)) checked = true;
                        } catch (Exception ignored2) {}
                    }
                    if (!checked && child instanceof java.awt.Component) {
                        try {
                            Method gs = child.getClass().getMethod("getState");
                            Object val = gs.invoke(child);
                            if (Boolean.TRUE.equals(val)) checked = true;
                        } catch (Exception ignored2) {}
                    }

                    String role = isCheckbox ? "check_box" : "menu_item";
                    items.add(name.trim() + "\t" + role + "\t" + (checked ? "1" : "0"));
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}
        return items;
    }

    /**
     * Safely extracts tab captions from tab controls (including Oracle EWT
     * TabBar/TabPanel variants). Returns an empty list when unsupported.
     */
    public static List<String> extractTabTitles(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();

        List<String> direct = extractIndexedStrings(
                obj,
                new String[] {"getItemCount", "getTabCount", "getPageCount"},
                new String[] {"getTitleAt", "getLabelAt", "getTextAt", "getItemAt", "getTabAt"},
                limit);
        if (!direct.isEmpty()) return direct;

        // Fallback: selected item only (better than opaque object ids)
        try {
            Method getSelected = obj.getClass().getMethod("getSelectedItem");
            Object selected = getSelected.invoke(obj);
            String label = asDisplayLabel(selected);
            if (!label.isEmpty()) {
                List<String> one = new ArrayList<>();
                one.add(label);
                return one;
            }
        } catch (Exception ignored) {}

        return Collections.emptyList();
    }

    /**
     * Best-effort selected tab title for a tab control.
     *
     * Tries selected index first, then resolves getSelectedItem() against
     * indexed item getters so we can map to the extracted tab title list.
     */
    public static String extractSelectedTabTitle(Object obj, List<String> tabTitles) {
        if (obj == null || tabTitles == null || tabTitles.isEmpty()) return "";

        // 1) Direct selected index when available.
        try {
            Method selectedIndexMethod = obj.getClass().getMethod("getSelectedIndex");
            int idx = toInt(selectedIndexMethod.invoke(obj));
            if (idx >= 0 && idx < tabTitles.size()) {
                return tabTitles.get(idx);
            }
        } catch (Exception ignored) {}

        // 2) Selected item object.
        Object selected = null;
        try {
            Method getSelected = obj.getClass().getMethod("getSelectedItem");
            selected = getSelected.invoke(obj);
        } catch (Exception ignored) {}
        if (selected == null) return "";

        String selectedLabel = asDisplayLabel(selected);
        if (!selectedLabel.isEmpty()) {
            for (String t : tabTitles) {
                if (t != null && t.equalsIgnoreCase(selectedLabel)) {
                    return t;
                }
            }
            return selectedLabel;
        }

        // 3) Compare selected item identity/string to indexed items.
        Integer count = resolveIndexedCount(obj);
        if (count == null || count <= 0) count = tabTitles.size();

        Method itemMethod = null;
        for (String itemName : new String[] {"getItemAt", "getTabAt", "getPageAt", "getElementAt"}) {
            itemMethod = resolveIntMethod(obj.getClass(), itemName);
            if (itemMethod != null) break;
        }
        if (itemMethod == null) return "";

        String selectedStr = selected.toString();
        int n = Math.min(count, tabTitles.size());
        for (int i = 0; i < n; i++) {
            try {
                Object item = itemMethod.invoke(obj, i);
                if (item == selected) {
                    return tabTitles.get(i);
                }
                if (item != null && selectedStr.equals(item.toString())) {
                    return tabTitles.get(i);
                }
            } catch (Exception ignored) {}
        }

        return "";
    }

    /**
     * Per-tab enabled and visible state for tab controls.
     * Returns a pipe-delimited string: "enabled1,visible1 | enabled2,visible2 | ..."
     * where each value is "1" or "0".  Returns null when the component does not
     * expose per-tab state APIs.
     *
     * Tries reflection first (isEnabledAt / isVisibleAt / Oracle EWT variants),
     * then falls back to the Accessible API's per-child state set.
     */
    public static String extractTabStates(Object obj, int tabCount) {
        if (obj == null || tabCount <= 0) return null;

        // ── 1. Try direct indexed boolean methods on the component ───────
        Method enabledAt = resolveIntMethod(obj.getClass(), "isEnabledAt");
        Method visibleAt = resolveIntMethod(obj.getClass(), "isVisibleAt");
        if (enabledAt == null) enabledAt = resolveIntMethod(obj.getClass(), "isItemEnabled");
        if (visibleAt == null) visibleAt = resolveIntMethod(obj.getClass(), "isItemVisible");
        if (enabledAt == null) enabledAt = resolveIntMethod(obj.getClass(), "isTabEnabled");
        if (visibleAt == null) visibleAt = resolveIntMethod(obj.getClass(), "isTabVisible");
        if (enabledAt == null) enabledAt = resolveIntMethod(obj.getClass(), "getTabEnabled");
        if (visibleAt == null) visibleAt = resolveIntMethod(obj.getClass(), "getTabVisible");

        if (enabledAt != null || visibleAt != null) {
            return probeTabStatesWithMethods(obj, tabCount, enabledAt, visibleAt);
        }

        // ── 2. Oracle EWT: TabBar.getItem(int) → TabBarItem ─────────────
        // Get the TabBar (either obj IS the TabBar, or obj has getTabBar()).
        Object tabBar = null;
        try {
            Method getTabBar = obj.getClass().getMethod("getTabBar");
            tabBar = getTabBar.invoke(obj);
        } catch (Exception ignored) {}
        if (tabBar == null) {
            // Maybe obj itself is the TabBar
            Method getItem = resolveIntMethod(obj.getClass(), "getItem");
            if (getItem != null) tabBar = obj;
        }
        if (tabBar != null) {
            Method getItem = resolveIntMethod(tabBar.getClass(), "getItem");
            if (getItem != null) {
                String result = probeTabBarItems(tabBar, getItem, tabCount);
                if (result != null) return result;
            }
        }

        // ── 3. TabPanel.getPage(int) → TabPanelPage ─────────────────────
        Method getPage = resolveIntMethod(obj.getClass(), "getPage");
        if (getPage != null) {
            String result = probeTabBarItems(obj, getPage, tabCount);
            if (result != null) return result;
        }

        // ── 4. Accessible API fallback (via reflection for JVM compat) ───
        try {
            Method getCtx = obj.getClass().getMethod("getAccessibleContext");
            Object ctx = getCtx.invoke(obj);
            if (ctx != null) {
                Method getChildCount = ctx.getClass().getMethod("getAccessibleChildCount");
                Method getChild = ctx.getClass().getMethod("getAccessibleChild", int.class);
                int childCount = toInt(getChildCount.invoke(ctx));
                if (childCount >= tabCount) {
                    StringBuilder sb = new StringBuilder();
                    for (int i = 0; i < tabCount; i++) {
                        if (i > 0) sb.append(" | ");
                        Object child = getChild.invoke(ctx, i);
                        boolean enabled = true;
                        boolean visible = true;
                        if (child != null) {
                            Method getChildCtx = child.getClass().getMethod("getAccessibleContext");
                            Object childCtx = getChildCtx.invoke(child);
                            if (childCtx != null) {
                                Method getStateSet = childCtx.getClass().getMethod("getAccessibleStateSet");
                                Object ss = getStateSet.invoke(childCtx);
                                if (ss != null) {
                                    Method containsState = ss.getClass().getMethod("contains", AccessibleState.class);
                                    enabled = Boolean.TRUE.equals(containsState.invoke(ss, AccessibleState.ENABLED));
                                    visible = Boolean.TRUE.equals(containsState.invoke(ss, AccessibleState.VISIBLE))
                                           || Boolean.TRUE.equals(containsState.invoke(ss, AccessibleState.SHOWING));
                                }
                            }
                        }
                        sb.append(enabled ? "1" : "0").append(",").append(visible ? "1" : "0");
                    }
                    return sb.toString();
                }
            }
        } catch (Exception ignored) {}

        return null;
    }

    /** Probe per-item enabled/visible by calling boolean methods on each item. */
    private static String probeTabBarItems(Object container, Method getItemMethod, int tabCount) {
        try {
            getItemMethod.setAccessible(true);
            // Get first item to discover available boolean methods
            Object sample = getItemMethod.invoke(container, 0);
            if (sample == null) return null;

            // Look for isEnabled/isVisible on the item object
            Method itemEnabled = null;
            Method itemVisible = null;
            for (String name : new String[]{"isEnabled", "getEnabled"}) {
                try { itemEnabled = sample.getClass().getMethod(name); break; } catch (Exception ignore) {}
            }
            for (String name : new String[]{"isVisible", "getVisible", "isShowing"}) {
                try { itemVisible = sample.getClass().getMethod(name); break; } catch (Exception ignore) {}
            }
            if (itemEnabled == null && itemVisible == null) return null;

            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < tabCount; i++) {
                if (i > 0) sb.append(" | ");
                Object item = getItemMethod.invoke(container, i);
                boolean enabled = true;
                boolean visible = true;
                if (item != null) {
                    if (itemEnabled != null) {
                        try { enabled = Boolean.TRUE.equals(itemEnabled.invoke(item)); } catch (Exception ignore) {}
                    }
                    if (itemVisible != null) {
                        try { visible = Boolean.TRUE.equals(itemVisible.invoke(item)); } catch (Exception ignore) {}
                    }
                }
                sb.append(enabled ? "1" : "0").append(",").append(visible ? "1" : "0");
            }
            return sb.toString();
        } catch (Exception ignored) {
            return null;
        }
    }

    private static String probeTabStatesWithMethods(Object obj, int tabCount, Method enabledAt, Method visibleAt) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < tabCount; i++) {
            if (i > 0) sb.append(" | ");
            boolean enabled = true;
            boolean visible = true;
            if (enabledAt != null) {
                try { enabled = Boolean.TRUE.equals(enabledAt.invoke(obj, i)); } catch (Exception ignored) {}
            }
            if (visibleAt != null) {
                try { visible = Boolean.TRUE.equals(visibleAt.invoke(obj, i)); } catch (Exception ignored) {}
            }
            sb.append(enabled ? "1" : "0").append(",").append(visible ? "1" : "0");
        }
        return sb.toString();
    }

    /**
     * Best-effort extraction of visible tree rows from Swing JTree-like
     * controls. Each row is encoded as: depth<TAB>selected<TAB>expanded<TAB>label
     * where selected/expanded are 1 or 0.
     */
    public static List<String> extractTreeRows(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();

        List<String> rows = new ArrayList<>();
        try {
            Method rowCountMethod = obj.getClass().getMethod("getRowCount");
            Method pathForRowMethod = resolveIntMethod(obj.getClass(), "getPathForRow");

            if (pathForRowMethod != null) {
                Method isRowSelectedMethod = resolveIntMethod(obj.getClass(), "isRowSelected");
                Method isExpandedMethod = resolveIntMethod(obj.getClass(), "isExpanded");

                int rowCount = toInt(rowCountMethod.invoke(obj));
                int n = Math.min(rowCount, limit);
                for (int i = 0; i < n; i++) {
                    Object treePath;
                    try {
                        treePath = pathForRowMethod.invoke(obj, i);
                    } catch (Exception ignored) {
                        continue;
                    }
                    if (treePath == null) continue;

                    int depth = treePathDepth(treePath);
                    String label = treePathLabel(treePath);
                    if (label.isEmpty()) continue;

                    boolean selected = false;
                    boolean expanded = false;
                    try {
                        if (isRowSelectedMethod != null) {
                            Object v = isRowSelectedMethod.invoke(obj, i);
                            selected = Boolean.TRUE.equals(v);
                        }
                    } catch (Exception ignored) {}
                    try {
                        if (isExpandedMethod != null) {
                            Object v = isExpandedMethod.invoke(obj, i);
                            expanded = Boolean.TRUE.equals(v);
                        }
                    } catch (Exception ignored) {}

                    rows.add(Integer.toString(Math.max(0, depth))
                            + "\t" + (selected ? "1" : "0")
                            + "\t" + (expanded ? "1" : "0")
                            + "\t" + label);
                }
            }
        } catch (Exception ignored) {}

        if (!rows.isEmpty()) return rows;
        rows = extractTreeRowsFromFlatApi(obj, limit);
        if (!rows.isEmpty()) return rows;
        rows = extractTreeRowsFromGridApi(obj, limit);
        if (!rows.isEmpty()) return rows;
        rows = extractTreeRowsFromModel(obj, limit);
        if (!rows.isEmpty()) return rows;
        return extractTreeRowsFromAccessibility(obj, limit);
    }

    /**
     * Direct extraction for Oracle Forms ListView using getCellData(int,int)
     * and getHeaderData(int).
     */
    public static List<String> extractListViewRows(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();
        List<String> rows = new ArrayList<>();
        try {
            Method rowCountMethod = resolveMethod(obj.getClass(), "getRowCount");
            Method colCountMethod = resolveMethod(obj.getClass(), "getColumnCount");
            Method getCellData = resolveRowColMethod(obj.getClass(), "getCellData");
            Method getHeaderData = resolveIntMethod(obj.getClass(), "getHeaderData");

            if (rowCountMethod == null || colCountMethod == null || getCellData == null) {
                return rows;
            }

            int rowCount = toInt(rowCountMethod.invoke(obj));
            int colCount = toInt(colCountMethod.invoke(obj));
            if (rowCount <= 0 || colCount <= 0) return rows;

            // Get column headers
            String[] headers = new String[colCount];
            if (getHeaderData != null) {
                for (int c = 0; c < colCount; c++) {
                    try {
                        Object hdr = getHeaderData.invoke(obj, c);
                        headers[c] = (hdr != null) ? hdr.toString().trim() : null;
                    } catch (Exception ignored) { headers[c] = null; }
                }
            }

            // Get selected row
            int selectedRow = -1;
            try {
                Method getSelectedRow = resolveMethod(obj.getClass(), "getSelectedRow");
                if (getSelectedRow != null) selectedRow = toInt(getSelectedRow.invoke(obj));
            } catch (Exception ignored) {}

            int n = Math.min(rowCount, limit);
            for (int r = 0; r < n; r++) {
                StringBuilder sb = new StringBuilder();
                for (int c = 0; c < colCount; c++) {
                    Object val = null;
                    // Oracle Forms ListView uses getCellData(column, row) order
                    try { val = getCellData.invoke(obj, c, r); } catch (Exception ignored) {}
                    String cellStr = (val != null) ? val.toString().trim() : "";
                    if (sb.length() > 0) sb.append("\t");
                    if (headers[c] != null && !headers[c].isEmpty()) {
                        sb.append(headers[c]).append(":").append(cellStr);
                    } else {
                        sb.append(cellStr);
                    }
                }
                boolean selected = (r == selectedRow);
                rows.add("0\t" + (selected ? "1" : "0") + "\t0\t" + sb.toString());
            }
        } catch (Exception ignored) {}
        return rows;
    }

    /**
     * Grid/table-style extraction for components that expose
     * getRowCount()/getColumnCount() with cell-level access via
     * getValueAt(row,col) or a column model with header names.
     * Produces one row per grid row with "colHeader:cellValue" pairs.
     */
    private static List<String> extractTreeRowsFromGridApi(Object obj, int limit) {
        if (obj == null || limit <= 0) return Collections.emptyList();
        List<String> rows = new ArrayList<>();

        try {
            Method rowCountMethod = resolveMethod(obj.getClass(), "getRowCount");
            Method colCountMethod = resolveMethod(obj.getClass(), "getColumnCount");
            if (rowCountMethod == null || colCountMethod == null) return rows;

            int rowCount = toInt(rowCountMethod.invoke(obj));
            int colCount = toInt(colCountMethod.invoke(obj));
            if (rowCount <= 0 || colCount <= 0) return rows;

            // Try to find a cell accessor: getValueAt(int,int) on obj or its model
            Method getValueAt = resolveRowColMethod(obj.getClass(), "getValueAt");
            Object cellSource = obj;
            if (getValueAt == null) {
                // Try via model
                Object model = firstObjectResult(obj, new String[]{"getModel", "getListModel", "getDataModel"});
                if (model != null) {
                    getValueAt = resolveRowColMethod(model.getClass(), "getValueAt");
                    if (getValueAt == null) getValueAt = resolveRowColMethod(model.getClass(), "getElementAt");
                    if (getValueAt != null) cellSource = model;
                }
            }
            if (getValueAt == null) {
                // Try alternative two-arg methods
                for (String name : new String[]{"getCellData", "getCellValue", "getItem", "getCellText", "getTextAt"}) {
                    getValueAt = resolveRowColMethod(obj.getClass(), name);
                    if (getValueAt != null) { cellSource = obj; break; }
                }
            }
            if (getValueAt == null) return rows;

            // Try to get column headers
            String[] colHeaders = new String[colCount];
            for (int c = 0; c < colCount; c++) colHeaders[c] = null;
            // Try getColumnName(int) on obj or model
            Method getColName = resolveIntMethod(obj.getClass(), "getColumnName");
            Object colNameSource = obj;
            if (getColName == null) {
                getColName = resolveIntMethod(obj.getClass(), "getHeaderData");
            }
            if (getColName == null) {
                Object model = firstObjectResult(obj, new String[]{"getModel", "getColumnModel"});
                if (model != null) {
                    getColName = resolveIntMethod(model.getClass(), "getColumnName");
                    if (getColName == null) getColName = resolveIntMethod(model.getClass(), "getHeaderAt");
                    if (getColName == null) getColName = resolveIntMethod(model.getClass(), "getHeaderData");
                    if (getColName != null) colNameSource = model;
                }
            }
            if (getColName != null) {
                for (int c = 0; c < colCount; c++) {
                    try {
                        Object hdr = getColName.invoke(colNameSource, c);
                        if (hdr != null) colHeaders[c] = hdr.toString().trim();
                    } catch (Exception ignored) {}
                }
            }

            // Check which row is selected
            int selectedRow = -1;
            try {
                Method getSelectedRow = resolveMethod(obj.getClass(), "getSelectedRow");
                if (getSelectedRow != null) selectedRow = toInt(getSelectedRow.invoke(obj));
            } catch (Exception ignored) {}

            int n = Math.min(rowCount, limit);
            for (int r = 0; r < n; r++) {
                StringBuilder cellsBuilder = new StringBuilder();
                for (int c = 0; c < colCount; c++) {
                    Object val = null;
                    try { val = getValueAt.invoke(cellSource, r, c); } catch (Exception ignored) {}
                    String cellStr = (val != null) ? val.toString().trim() : "";
                    if (cellsBuilder.length() > 0) cellsBuilder.append("\t");
                    if (colHeaders[c] != null && !colHeaders[c].isEmpty()) {
                        cellsBuilder.append(colHeaders[c]).append(":").append(cellStr);
                    } else {
                        cellsBuilder.append(cellStr);
                    }
                }
                if (cellsBuilder.length() == 0) continue;
                boolean selected = (r == selectedRow);
                rows.add("0\t" + (selected ? "1" : "0") + "\t0\t" + cellsBuilder.toString());
            }
        } catch (Exception ignored) {}

        return rows;
    }

    /** Resolve a method that takes (int, int) parameters. */
    private static Method resolveRowColMethod(Class<?> clazz, String name) {
        for (Class<?> c = clazz; c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                Method m = c.getDeclaredMethod(name, int.class, int.class);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException ignored) {}
        }
        // Also try Integer wrapper params
        for (Class<?> c = clazz; c != null && c != Object.class; c = c.getSuperclass()) {
            for (Method m : c.getDeclaredMethods()) {
                if (!m.getName().equals(name)) continue;
                Class<?>[] params = m.getParameterTypes();
                if (params.length == 2
                        && (params[0] == int.class || params[0] == Integer.class)
                        && (params[1] == int.class || params[1] == Integer.class)) {
                    m.setAccessible(true);
                    return m;
                }
            }
        }
        return null;
    }

    private static List<String> extractTreeRowsFromFlatApi(Object treeLike, int limit) {
        if (treeLike == null || limit <= 0) return Collections.emptyList();
        List<String> rows = new ArrayList<>();

        Integer count = firstIntResult(treeLike, new String[] {
                "getRowCount", "getVisibleRowCount", "getNodeCount", "getCount"
        });
        if (count == null || count <= 0) return rows;

        Method itemMethod = firstIntMethod(treeLike.getClass(), new String[] {
                "getPathForRow", "getNodeAt", "getRowAt", "getElementAt", "getItemAt"
        });
        if (itemMethod == null) return rows;

        Set<String> selectedLabels = extractSelectedTreeLabels(treeLike);

        int n = Math.min(count.intValue(), limit);
        for (int i = 0; i < n; i++) {
            Object item;
            try {
                item = itemMethod.invoke(treeLike, Integer.valueOf(i));
            } catch (Exception ignored) {
                continue;
            }
            if (item == null) continue;
            String label = treePathLabel(item);
            if (label.isEmpty()) label = asDisplayLabel(item);
            if (label.isEmpty()) label = String.valueOf(item);
            if (label == null || label.trim().isEmpty()) continue;
            label = label.trim();

            boolean selected = selectedLabels.contains(label);
            boolean expanded = false;
            rows.add(Integer.toString(0)
                    + "\t" + (selected ? "1" : "0")
                    + "\t" + (expanded ? "1" : "0")
                    + "\t" + label);
        }

        return rows;
    }

    private static List<String> extractTreeRowsFromAccessibility(Object obj, int limit) {
        if (!(obj instanceof Accessible) || limit <= 0) return Collections.emptyList();
        List<String> rows = new ArrayList<>();
        try {
            AccessibleContext root = ((Accessible) obj).getAccessibleContext();
            if (root == null) return rows;

            Deque<Object[]> queue = new ArrayDeque<>();
            queue.add(new Object[] { root, Integer.valueOf(0) });
            while (!queue.isEmpty() && rows.size() < limit) {
                Object[] item = queue.removeFirst();
                AccessibleContext ac = (AccessibleContext) item[0];
                int depth = ((Integer) item[1]).intValue();
                if (ac == null) continue;

                int childCount = 0;
                try {
                    childCount = ac.getAccessibleChildrenCount();
                } catch (Exception ignored) {}

                for (int i = 0; i < childCount && rows.size() < limit; i++) {
                    Accessible child;
                    try {
                        child = ac.getAccessibleChild(i);
                    } catch (Exception ignored) {
                        continue;
                    }
                    if (child == null) continue;
                    AccessibleContext cc;
                    try {
                        cc = child.getAccessibleContext();
                    } catch (Exception ignored) {
                        cc = null;
                    }
                    if (cc == null) continue;

                    String label = "";
                    try {
                        label = asDisplayLabel(cc.getAccessibleName());
                        if (label.isEmpty()) label = asDisplayLabel(cc.getAccessibleDescription());
                    } catch (Exception ignored) {}

                    AccessibleStateSet states = null;
                    try {
                        states = cc.getAccessibleStateSet();
                    } catch (Exception ignored) {}
                    boolean selected = states != null && states.contains(AccessibleState.SELECTED);
                    boolean expanded = states != null && states.contains(AccessibleState.EXPANDED);

                    if (!label.isEmpty()) {
                        rows.add(Integer.toString(Math.max(0, depth))
                                + "\t" + (selected ? "1" : "0")
                                + "\t" + (expanded ? "1" : "0")
                                + "\t" + label);
                    }

                    queue.addLast(new Object[] { cc, Integer.valueOf(depth + 1) });
                }
            }
        } catch (Exception ignored) {}
        return rows;
    }

    private static List<String> extractTreeRowsFromModel(Object treeLike, int limit) {
        if (treeLike == null || limit <= 0) return Collections.emptyList();

        List<String> rows = new ArrayList<>();
        try {
            Object model = firstObjectResult(treeLike, new String[] {
                "getModel", "getTreeModel", "model", "treeModel"
            });
            if (model == null) {
                model = firstFieldValueByHint(treeLike, new String[] {"model", "treeModel", "dataModel"});
            }
            if (model == null) return rows;

            Object root = firstObjectResult(model, new String[] {"getRoot", "root"});
            if (root == null) {
                root = firstFieldValueByHint(model, new String[] {"root", "rootNode", "top"});
            }
            if (root == null) return rows;

            Method getChildCount = firstObjectArgMethod(model.getClass(), new String[] {
                "getChildCount", "childCount"
            });
            Method getChild = firstObjectIntArgMethod(model.getClass(), new String[] {
                "getChild", "getChildAt", "childAt"
            });
                if (getChildCount == null || getChild == null) {
                getChildCount = firstObjectArgMethod(root.getClass(), new String[] {
                    "getChildCount", "childCount", "size"
                });
                getChild = firstObjectIntArgMethod(root.getClass(), new String[] {
                    "getChild", "getChildAt", "childAt", "get"
                });
                if (getChildCount == null || getChild == null) return rows;
                model = null;
                }

            Set<String> selectedLabels = extractSelectedTreeLabels(treeLike);

            Deque<Object[]> stack = new ArrayDeque<>();
            stack.push(new Object[] { root, Integer.valueOf(0) });
            IdentityHashMap<Object, Boolean> seen = new IdentityHashMap<>();

            while (!stack.isEmpty() && rows.size() < limit) {
                Object[] item = stack.pop();
                Object node = item[0];
                int depth = ((Integer) item[1]).intValue();
                if (node == null) continue;
                if (seen.containsKey(node)) continue;
                seen.put(node, Boolean.TRUE);

                String label = asDisplayLabel(node);
                if (label.isEmpty()) label = String.valueOf(node);
                if (label == null || label.trim().isEmpty()) continue;
                label = label.trim();

                boolean selected = selectedLabels.contains(label);
                boolean expanded = false;
                rows.add(Integer.toString(Math.max(0, depth))
                        + "\t" + (selected ? "1" : "0")
                        + "\t" + (expanded ? "1" : "0")
                        + "\t" + label);

                int childCount = 0;
                try {
                    if (model != null) {
                        childCount = toInt(getChildCount.invoke(model, node));
                    } else {
                        childCount = toInt(getChildCount.invoke(node));
                    }
                } catch (Exception ignored) {}
                for (int i = childCount - 1; i >= 0; i--) {
                    try {
                        Object child;
                        if (model != null) {
                            child = getChild.invoke(model, node, Integer.valueOf(i));
                        } else {
                            child = getChild.invoke(node, Integer.valueOf(i));
                        }
                        if (child != null) {
                            stack.push(new Object[] { child, Integer.valueOf(depth + 1) });
                        }
                    } catch (Exception ignored) {}
                }
            }
        } catch (Exception ignored) {}
        return rows;
    }

    private static Set<String> extractSelectedTreeLabels(Object treeLike) {
        if (treeLike == null) return Collections.emptySet();
        Set<String> labels = new HashSet<>();

        // Prefer multi-selection API when available.
        try {
            Object paths = firstObjectResult(treeLike, new String[] {
                    "getSelectionPaths", "getSelectedPaths", "selectionPaths"
            });
            if (paths != null && paths.getClass().isArray()) {
                int n = java.lang.reflect.Array.getLength(paths);
                for (int i = 0; i < n; i++) {
                    Object p = java.lang.reflect.Array.get(paths, i);
                    String label = treePathLabel(p);
                    if (!label.isEmpty()) labels.add(label);
                }
            }
        } catch (Exception ignored) {}

        try {
            Object path = firstObjectResult(treeLike, new String[] {
                    "getSelectionPath", "getSelectedPath", "selectionPath"
            });
            String label = treePathLabel(path);
            if (!label.isEmpty()) labels.add(label);
        } catch (Exception ignored) {}

        try {
            Object selectedNode = firstObjectResult(treeLike, new String[] {
                    "getSelectedNode", "getCurrentNode", "selectedNode", "currentNode"
            });
            String label = asDisplayLabel(selectedNode);
            if (label.isEmpty() && selectedNode != null) label = String.valueOf(selectedNode);
            if (!label.isEmpty()) labels.add(label.trim());
        } catch (Exception ignored) {}

        return labels;
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    /**
     * Resolves a public, zero-argument method by name, searching the entire
     * class hierarchy including interfaces.  Returns {@code null} if not found.
     */
    private static Method resolveMethod(Class<?> clazz, String name) {
        // Walk up the hierarchy; getDeclaredMethods misses inherited methods,
        // getMethods only sees public methods — exactly what we want here.
        try {
            return clazz.getMethod(name);        // public + zero-arg
        } catch (NoSuchMethodException e) {
            return null;
        }
    }

    private static List<String> extractIndexedOptions(
            Object obj,
            String countMethodName,
            String itemMethodName,
            int limit) {
        List<String> values = new ArrayList<>();
        try {
            Method countMethod = obj.getClass().getMethod(countMethodName);
            Object countObj = countMethod.invoke(obj);
            int count = toInt(countObj);
            if (count <= 0) return values;

            Method itemMethod = resolveIntMethod(obj.getClass(), itemMethodName);
            if (itemMethod == null) return values;

            int n = Math.min(count, limit);
            for (int i = 0; i < n; i++) {
                try {
                    Object item = itemMethod.invoke(obj, i);
                    values.add(item == null ? "" : item.toString());
                } catch (Exception ignored) {}
            }
        } catch (Exception ignored) {}
        return values;
    }

    private static Method resolveIntMethod(Class<?> clazz, String name) {
        Method m = resolveMethodAny(clazz, name, int.class);
        if (m != null) return m;
        m = resolveMethodAny(clazz, name, Integer.TYPE);
        if (m != null) return m;
        return null;
    }

    private static Method resolveMethodAny(Class<?> clazz, String name, Class<?>... parameterTypes) {
        if (clazz == null || name == null || name.isEmpty()) return null;
        try {
            return clazz.getMethod(name, parameterTypes);
        } catch (NoSuchMethodException ignored) {}

        Class<?> current = clazz;
        while (current != null) {
            try {
                Method declared = current.getDeclaredMethod(name, parameterTypes);
                declared.setAccessible(true);
                return declared;
            } catch (Exception ignored) {}
            current = current.getSuperclass();
        }
        return null;
    }

    private static Method firstIntMethod(Class<?> clazz, String[] names) {
        for (String name : names) {
            Method m = resolveIntMethod(clazz, name);
            if (m != null) return m;
        }
        return null;
    }

    private static Method firstObjectArgMethod(Class<?> clazz, String[] names) {
        for (String name : names) {
            Method m = resolveMethodAny(clazz, name, Object.class);
            if (m != null) return m;
        }
        return null;
    }

    private static Method firstObjectIntArgMethod(Class<?> clazz, String[] names) {
        for (String name : names) {
            Method m = resolveMethodAny(clazz, name, Object.class, int.class);
            if (m == null) m = resolveMethodAny(clazz, name, Object.class, Integer.TYPE);
            if (m == null) m = resolveMethodAny(clazz, name, int.class, Object.class);
            if (m == null) m = resolveMethodAny(clazz, name, Integer.TYPE, Object.class);
            if (m == null) m = resolveMethodAny(clazz, name, int.class);
            if (m == null) m = resolveMethodAny(clazz, name, Integer.TYPE);
            if (m != null) return m;
        }
        return null;
    }

    private static Object firstFieldValueByHint(Object target, String[] hints) {
        if (target == null || hints == null) return null;
        Class<?> current = target.getClass();
        while (current != null) {
            Field[] fields = current.getDeclaredFields();
            for (Field f : fields) {
                String name = f.getName() == null ? "" : f.getName().toLowerCase();
                boolean matches = false;
                for (String hint : hints) {
                    if (hint != null && !hint.isEmpty() && name.contains(hint.toLowerCase())) {
                        matches = true;
                        break;
                    }
                }
                if (!matches) continue;
                try {
                    f.setAccessible(true);
                    Object value = f.get(target);
                    if (value != null) return value;
                } catch (Exception ignored) {}
            }
            current = current.getSuperclass();
        }
        return null;
    }

    private static Object firstObjectResult(Object target, String[] names) {
        if (target == null || names == null) return null;
        for (String name : names) {
            try {
                Method m = resolveMethodAny(target.getClass(), name);
                if (m == null) continue;
                Object value = m.invoke(target);
                if (value != null) return value;
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static Integer firstIntResult(Object target, String[] names) {
        if (target == null || names == null) return null;
        for (String name : names) {
            try {
                Method m = resolveMethodAny(target.getClass(), name);
                if (m == null) continue;
                return Integer.valueOf(toInt(m.invoke(target)));
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static int treePathDepth(Object treePath) {
        if (treePath == null) return 0;
        try {
            Method getPathCount = treePath.getClass().getMethod("getPathCount");
            int count = toInt(getPathCount.invoke(treePath));
            return Math.max(0, count - 1);
        } catch (Exception ignored) {
            return 0;
        }
    }

    private static String treePathLabel(Object treePath) {
        if (treePath == null) return "";
        try {
            Method getLastPathComponent = treePath.getClass().getMethod("getLastPathComponent");
            Object last = getLastPathComponent.invoke(treePath);
            String label = asDisplayLabel(last);
            if (!label.isEmpty()) return label;
            if (last != null) return last.toString();
        } catch (Exception ignored) {}

        String raw = treePath.toString();
        if (raw == null) return "";
        raw = raw.trim();
        if (raw.startsWith("[") && raw.endsWith("]")) {
            raw = raw.substring(1, raw.length() - 1);
        }
        int comma = raw.lastIndexOf(',');
        if (comma >= 0 && comma + 1 < raw.length()) {
            return raw.substring(comma + 1).trim();
        }
        return raw;
    }

    private static List<String> extractIndexedStrings(
            Object obj,
            String[] countMethodNames,
            String[] itemMethodNames,
            int limit) {
        List<String> values = new ArrayList<>();

        Integer count = null;
        for (String countName : countMethodNames) {
            try {
                Method m = obj.getClass().getMethod(countName);
                count = toInt(m.invoke(obj));
                if (count != null && count > 0) break;
            } catch (Exception ignored) {}
        }
        if (count == null || count <= 0) return values;

        Method itemMethod = null;
        for (String itemName : itemMethodNames) {
            itemMethod = resolveIntMethod(obj.getClass(), itemName);
            if (itemMethod != null) break;
        }
        if (itemMethod == null) {
            itemMethod = resolveLikelyIndexedGetter(obj.getClass());
        }
        if (itemMethod == null) return values;

        int n = Math.min(count, limit);
        for (int i = 0; i < n; i++) {
            try {
                Object item = itemMethod.invoke(obj, i);
                String label = asDisplayLabel(item);
                if (!label.isEmpty() && !values.contains(label)) {
                    values.add(label);
                }
            } catch (Exception ignored) {}
        }
        return values;
    }

    private static Method resolveLikelyIndexedGetter(Class<?> clazz) {
        for (Method m : clazz.getMethods()) {
            if (m == null) continue;
            String name = m.getName();
            if (name == null) continue;
            String lower = name.toLowerCase();
            if (!(lower.contains("title")
                    || lower.contains("label")
                    || lower.contains("text")
                    || lower.contains("item")
                    || lower.contains("tab")
                    || lower.contains("page"))) {
                continue;
            }
            Class<?>[] params = m.getParameterTypes();
            if (params.length != 1 || !(params[0] == int.class || params[0] == Integer.TYPE)) {
                continue;
            }
            if (m.getReturnType() == Void.TYPE) {
                continue;
            }
            return m;
        }
        return null;
    }

    private static Integer resolveIndexedCount(Object obj) {
        for (String countName : new String[] {"getItemCount", "getTabCount", "getPageCount"}) {
            try {
                Method m = obj.getClass().getMethod(countName);
                int value = toInt(m.invoke(obj));
                if (value > 0) return value;
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static String asDisplayLabel(Object value) {
        if (value == null) return "";
        if (value instanceof String) {
            return ((String) value).trim();
        }

        for (String methodName : new String[] {"getText", "getTitle", "getLabel", "getName"}) {
            try {
                Method m = value.getClass().getMethod(methodName);
                Object v = m.invoke(value);
                String s = v == null ? "" : v.toString().trim();
                if (!s.isEmpty() && !"null".equalsIgnoreCase(s)) return s;
            } catch (Exception ignored) {}
        }

        for (String fieldName : new String[] {"text", "title", "label", "caption", "name", "displayName"}) {
            String s = readFieldAsString(value, fieldName);
            if (!s.isEmpty() && !"null".equalsIgnoreCase(s)) return s;
        }

        String fallback = value.toString().trim();
        if (fallback.contains("@") || fallback.startsWith(value.getClass().getName())) {
            return "";
        }
        return fallback;
    }

    private static String readFieldAsString(Object obj, String fieldName) {
        if (obj == null || fieldName == null || fieldName.isEmpty()) return "";
        Class<?> c = obj.getClass();
        while (c != null) {
            try {
                Field f = c.getDeclaredField(fieldName);
                f.setAccessible(true);
                Object v = f.get(obj);
                return v == null ? "" : v.toString().trim();
            } catch (Exception ignored) {}
            c = c.getSuperclass();
        }
        return "";
    }

    private static int toInt(Object value) {
        if (value instanceof Number) return ((Number) value).intValue();
        if (value == null) return -1;
        try {
            return Integer.parseInt(value.toString());
        } catch (NumberFormatException ignored) {
            return -1;
        }
    }

    /** Converts a method return value to a safe display string. */
    private static String stringify(Object value) {
        if (value == null) return "null";
        if (value.getClass().isArray()) {
            return arrayToString(value);
        }
        return value.toString();
    }

    private static String arrayToString(Object array) {
        if (array instanceof Object[]) {
            Object[] arr = (Object[]) array;
            StringBuilder sb = new StringBuilder("[");
            for (int i = 0; i < arr.length; i++) {
                if (i > 0) sb.append(", ");
                sb.append(arr[i] == null ? "null" : arr[i].toString());
            }
            sb.append("]");
            return sb.toString();
        }
        // Primitive arrays — use java.util.Arrays
        if (array instanceof int[])     return Arrays.toString((int[])     array);
        if (array instanceof long[])    return Arrays.toString((long[])    array);
        if (array instanceof double[])  return Arrays.toString((double[])  array);
        if (array instanceof boolean[]) return Arrays.toString((boolean[]) array);
        return array.toString();
    }
}
