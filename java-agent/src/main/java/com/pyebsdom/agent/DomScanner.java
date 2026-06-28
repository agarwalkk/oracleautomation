package com.pyebsdom.agent;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleComponent;
import javax.accessibility.AccessibleContext;
import javax.accessibility.AccessibleRole;
import javax.accessibility.AccessibleState;
import javax.accessibility.AccessibleStateSet;
import javax.swing.*;
import java.awt.*;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.lang.reflect.Method;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Scans the live AWT/Swing component tree inside the target JVM and
 * produces a list of {@link DomNode} trees — one per top-level
 * {@link Window}.
 *
 * <h3>Thread safety</h3>
 * All component-tree traversal is performed on the AWT Event Dispatch
 * Thread via {@link SwingUtilities#invokeAndWait}. This guarantees
 * that component state is read consistently and that no concurrent
 * modification happens while we iterate. {@link #scan(boolean)} itself
 * blocks until the EDT work is complete.
 *
 * <h3>Raw mode</h3>
 * In <em>normal</em> mode ({@code raw=false}) only windows that are
 * currently {@link Window#isVisible()} are included; their subtrees are
 * also filtered to visible components.
 * In <em>raw</em> mode ({@code raw=true}) all windows and components are
 * included regardless of visibility, giving a complete snapshot of every
 * object that has been constructed in the JVM.
 *
 * <h3>Error isolation</h3>
 * Exceptions thrown by individual component methods are caught per
 * component so that a single broken widget cannot abort the whole scan.
 * The error is recorded in {@link DomNode#attributes} under the key
 * {@code "_scanError"}.
 */
public final class DomScanner {

    private DomScanner() {
    }

    // ── Public entry points ───────────────────────────────────────────────

    /**
     * Scans the entire component tree and returns a {@link ScanResult}.
     *
     * @param raw {@code true} to include invisible/empty components
     */
    public static ScanResult scan(final boolean raw) throws Exception {
        final ScanResult[] holder = { null };
        final Exception[] error = { null };

        Runnable work = new Runnable() {
            public void run() {
                try {
                    holder[0] = doScanOnEDT(raw);
                } catch (Exception e) {
                    error[0] = e;
                }
            }
        };

        runOnEdtOrCurrent(work);

        if (error[0] != null)
            throw error[0];
        return holder[0];
    }

    /**
     * Runs the {@link TableDetector} on the EDT and serialises the result
     * to a JSON string compatible with the {@code tables} command response.
     */
    public static String scanTables() throws Exception {
        @SuppressWarnings("unchecked")
        final List<TableModel>[] holder = new List[] { null };
        final Exception[] error = { null };

        Runnable work = new Runnable() {
            public void run() {
                try {
                    holder[0] = TableDetector.detect(AwtContext.getWindows());
                } catch (Exception e) {
                    error[0] = e;
                }
            }
        };

        runOnEdtOrCurrent(work);

        if (error[0] != null)
            throw error[0];

        List<TableModel> tables = holder[0];
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"tables\",");
        sb.append("\"tableCount\":").append(tables.size()).append(',');
        sb.append("\"tables\":[");
        for (int i = 0; i < tables.size(); i++) {
            if (i > 0)
                sb.append(',');
            sb.append(tables.get(i).toJson());
        }
        sb.append("]}");
        return sb.toString();
    }

    // ── EDT scan logic ────────────────────────────────────────────────────

    private static void runOnEdtOrCurrent(Runnable work) throws Exception {
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                work.run();
            } else {
                SwingUtilities.invokeAndWait(work);
            }
        } catch (NullPointerException appContextMissing) {
            System.err.println("[ebs-dom-agent] AWT AppContext unavailable on attach thread; "
                    + "running scan directly.");
            work.run();
        }
    }

    /** Must be called on the Event Dispatch Thread. */
    private static ScanResult doScanOnEDT(boolean raw) {
        Window[] windows = AwtContext.getWindows();

        AtomicInteger idGen = new AtomicInteger(0);
        List<DomNode> windowNodes = new ArrayList<>();

        for (Window window : windows) {
            if (!raw && !safeIsVisible(window))
                continue;
            try {
                DomNode node = buildNode(window, null, 0, 0, windows.length, idGen, raw);
                // (annotateGridRows is REMOVED — superseded by StructureAnnotator)
                StructureAnnotator.annotate(node); // tabs/grids/mirrors → explicit
                windowNodes.add(node);
            } catch (Exception e) {
                DomNode errNode = new DomNode();
                errNode.id = idGen.incrementAndGet();
                errNode.semanticType = "Window";
                errNode.type = window.getClass().getName();
                errNode.attributes.put("_scanError",
                        e.getClass().getName() + ": " + e.getMessage());
                errNode.attributes.put("_scanErrorStack", stackTrace(e));
                windowNodes.add(errNode);
            }
        }

        // Identity is resolved across ALL windows together so uniqueness checks
        // see the whole scan (e.g. a label duplicated across two open frames).
        IdentityResolver.resolve(windowNodes);

        int visible = 0;
        for (Window w : windows)
            if (safeIsVisible(w))
                visible++;

        return new ScanResult(windowNodes, windows.length, visible, raw,
                Instant.now().toString());
    }

    // ── Node construction ─────────────────────────────────────────────────

    private static DomNode buildNode(Component comp,
            String parentPath,
            int depth,
            int index,
            int siblingCount,
            AtomicInteger idGen,
            boolean raw) {
        DomNode node = new DomNode();
        node.id = idGen.incrementAndGet();
        node.depth = depth;
        node.index = index;
        node.siblingCount = siblingCount;
        node.parentPath = parentPath;

        // ── Class metadata ────────────────────────────────────────────────
        Class<?> clazz = comp.getClass();
        node.type = clazz.getName();
        node.className = clazz.getName();
        node.simpleClassName = clazz.getSimpleName();
        node.packageName = clazz.getPackage() != null
                ? clazz.getPackage().getName()
                : "";
        node.semanticType = safeClassify(comp);

        // ── Basic state ───────────────────────────────────────────────────
        node.visible = safeIsVisible(comp);
        node.showing = safeIsShowing(comp);
        node.enabled = safeIsEnabled(comp);
        node.focusable = safeIsFocusable(comp);
        node.focused = safeIsFocusOwner(comp);
        Cursor cursor = safeGetCursor(comp);
        node.cursorType = cursor != null ? cursor.getType() : Cursor.DEFAULT_CURSOR;
        node.cursorName = cursor != null ? cursor.getName() : "Default Cursor";

        // ── Geometry ──────────────────────────────────────────────────────
        java.awt.Rectangle r = safeGetBounds(comp);
        int sx = -1, sy = -1;
        try {
            if (node.showing) {
                java.awt.Point loc = comp.getLocationOnScreen();
                sx = loc.x;
                sy = loc.y;
            }
        } catch (Exception ignored) {
        }

        node.bounds = new Bounds(r.x, r.y, r.width, r.height);
        node.screenBounds = new Bounds(r.x, r.y, r.width, r.height,
                sx, sy, r.width, r.height);

        // ── Name from component API ───────────────────────────────────────
        try {
            node.name = comp.getName();
        } catch (Exception ignored) {
        }

        if (comp instanceof Frame) {
            try {
                node.title = ((Frame) comp).getTitle();
            } catch (Exception ignored) {
            }
        } else if (comp instanceof Dialog) {
            try {
                node.title = ((Dialog) comp).getTitle();
            } catch (Exception ignored) {
            }
        }

        // ── Accessibility ─────────────────────────────────────────────────
        if (comp instanceof Accessible) {
            try {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null) {
                    node.accessibleName = ac.getAccessibleName();
                    node.accessibleDescription = ac.getAccessibleDescription();
                    if (ac.getAccessibleRole() != null) {
                        node.accessibleRole = ac.getAccessibleRole().toDisplayString();
                    }
                    // Read checked/selected state from AccessibleStateSet.
                    // Oracle Forms LWCheckbox does not expose isSelected()
                    // or isChecked() via reflection, but the accessibility
                    // layer correctly reports CHECKED state.
                    AccessibleStateSet stateSet = ac.getAccessibleStateSet();
                    if (stateSet != null && stateSet.contains(AccessibleState.CHECKED)) {
                        node.selected = true;
                    }
                }
            } catch (Exception ignored) {
            }
        }

        // ── Reflection extraction ─────────────────────────────────────────
        try {
            Map<String, String> refMap = ReflectionExtractor.extract(comp);
            node.reflection.putAll(refMap);

            // Promote common reflection values to first-class fields
            node.text = firstNonNull(refMap.get("getText"), node.text);
            node.tooltip = firstNonNull(refMap.get("getToolTipText"), node.tooltip);
            node.value = firstNonNull(
                    refMap.get("getValue"),
                    refMap.get("getSelectedItem"),
                    refMap.get("getSelectedValue"),
                    node.value);

            String editable = refMap.get("isEditable");
            if (editable != null)
                node.editable = "true".equalsIgnoreCase(editable);
            String selected = refMap.get("isSelected");
            if (selected != null)
                node.selected = "true".equalsIgnoreCase(selected);
            String checked = refMap.get("isChecked");
            if (checked != null)
                node.selected = "true".equalsIgnoreCase(checked);
            // Oracle Forms LWCheckbox/ExtendedCheckbox: getState() returns
            // a string like "true"/"false" or an integer state indicator.
            String state = refMap.get("getState");
            if (state != null && !node.selected) {
                node.selected = "true".equalsIgnoreCase(state)
                        || "1".equals(state)
                        || "on".equalsIgnoreCase(state);
            }

            // Extra attributes worth surfacing
            for (String key : new String[] {
                    "getItemCount", "getRowCount", "getColumnCount", "getSelectedIndex",
                    "getSelectedRow", "getSelectedRows", "getLeadSelectionIndex",
                    "getBackground", "getForeground" }) {
                if (refMap.containsKey(key)) {
                    node.attributes.put(key, refMap.get(key));
                }
            }

            List<String> options = ReflectionExtractor.extractOptions(comp, 100);
            if (!options.isEmpty()) {
                node.valueOptions.addAll(options);
                node.attributes.put("valueOptionCount", Integer.toString(options.size()));
            }

            List<String> treeRows = null;

            // For Oracle Forms ListView, use dedicated extractor first
            // (getCellData uses column-major arg order: col, row)
            if ("ListView".equals(node.simpleClassName)) {
                try {
                    treeRows = ReflectionExtractor.extractListViewRows(comp, 256);
                } catch (Throwable lvErr) {
                    node.attributes.put("_listViewError", lvErr.getClass().getName() + ": " + lvErr.getMessage());
                }
            }

            // Generic tree/grid extraction for non-ListView or as fallback
            if (treeRows == null || treeRows.isEmpty()) {
                try {
                    treeRows = ReflectionExtractor.extractTreeRows(comp, 256);
                } catch (Throwable treeErr) {
                    node.attributes.put("_treeError", treeErr.getClass().getName() + ": " + treeErr.getMessage());
                }
            }

            if (treeRows != null && !treeRows.isEmpty()) {
                node.attributes.put("treeRowCount", Integer.toString(treeRows.size()));
                node.attributes.put("treeRows", String.join(" || ", treeRows));
            }

            // Probe ListView-style components for available accessor methods
            if (node.simpleClassName != null
                    && (node.simpleClassName.equals("ListView")
                            || node.simpleClassName.contains("List"))
                    && (treeRows == null || treeRows.isEmpty())
                    && node.valueOptions.isEmpty()) {
                StringBuilder methodProbe = new StringBuilder();
                for (Method m : comp.getClass().getMethods()) {
                    String mn = m.getName();
                    if ((mn.startsWith("get") || mn.startsWith("is"))
                            && m.getParameterCount() <= 2
                            && !mn.equals("getClass")) {
                        Class<?>[] pts = m.getParameterTypes();
                        StringBuilder sig = new StringBuilder(mn).append("(");
                        for (int pi = 0; pi < pts.length; pi++) {
                            if (pi > 0)
                                sig.append(",");
                            sig.append(pts[pi].getSimpleName());
                        }
                        sig.append(")");
                        if (methodProbe.length() > 0)
                            methodProbe.append(" | ");
                        methodProbe.append(sig);
                    }
                }
                node.attributes.put("_listMethodProbe", methodProbe.toString());
            }

            // Tab captions (Main / Shipping / Financials / ...), if available.
            List<String> tabTitles = ReflectionExtractor.extractTabTitles(comp, 32);
            if (!tabTitles.isEmpty()) {
                for (String title : tabTitles) {
                    if (title != null && !title.isEmpty() && !node.valueOptions.contains(title)) {
                        node.valueOptions.add(title);
                    }
                }
                node.attributes.put("tabTitleCount", Integer.toString(tabTitles.size()));
                node.attributes.put("tabTitles", String.join(" | ", tabTitles));

                String selectedTabTitle = ReflectionExtractor.extractSelectedTabTitle(comp, tabTitles);
                if (selectedTabTitle != null && !selectedTabTitle.isEmpty()) {
                    node.attributes.put("tabSelectedTitle", selectedTabTitle);
                }

                // Per-tab enabled/visible state ("1,1 | 0,1 | 1,0 | ...")
                String tabStates = ReflectionExtractor.extractTabStates(comp, tabTitles.size());
                if (tabStates != null) {
                    node.attributes.put("tabStates", tabStates);
                }
            }
        } catch (Exception ignored) {
        }

        // ── Menu items (Oracle EWT LWMenu / Swing JMenu) ──────────────────
        if ("Menu".equals(node.semanticType)) {
            try {
                List<String> menuItems = ReflectionExtractor.extractMenuItems(comp, 64);
                if (!menuItems.isEmpty()) {
                    node.attributes.put("menuItemCount", Integer.toString(menuItems.size()));
                    node.attributes.put("menuItems", String.join(" | ", menuItems));
                }
            } catch (Exception ignored) {
            }
            // Also extract via Accessibility API (role + checked state)
            try {
                List<String> accItems = ReflectionExtractor.extractAccessibleMenuItems(comp, 64);
                if (!accItems.isEmpty()) {
                    node.attributes.put("accessibleMenuItems", String.join(" || ", accItems));
                }
            } catch (Exception ignored) {
            }
        }

        // oracle.forms.ui.DrawnPanel renders column headers by painting text
        // directly via Graphics2D; the standard accessibility API returns no
        // useful text. We try a cascade of Oracle-internal field/method names
        // that hold the prompt string across different Forms runtime versions.
        if ("oracle.forms.ui.DrawnPanel".equals(comp.getClass().getName())) {
            String prompt = drawnPanelPrompt(comp);
            if (prompt != null && !prompt.isEmpty()) {
                node.text = prompt;
                node.displayName = prompt;
                node.confidence = 0.70;
                node.reflection.put("drawnPanelPrompt", prompt);
            }
        }

        // Promote tree rows to real child nodes with bounds + deterministic
        // locators. WHY: previously a DTree's rows lived only in the flat
        // `treeRows` string attribute and lost their id/bounds/locators/identity
        // inside the JVM. Now each row is a first-class DomNode.
        if (TreeItemExpander.isTree(node)) {
            // node.path is needed by the expander; build it first if not set.
            if (node.path == null) {
                String pseg = node.simpleClassName + "[" + index + "]";
                node.path = (parentPath != null && !parentPath.isEmpty())
                        ? parentPath + "/" + pseg
                        : pseg;
            }
            TreeItemExpander.expand(node, comp, idGen);
        }

        // ── Build path ────────────────────────────────────────────────────
        String pathSegment = node.simpleClassName + "[" + index + "]";
        node.path = parentPath != null && !parentPath.isEmpty()
                ? parentPath + "/" + pathSegment
                : pathSegment;

        // ── displayName + confidence ──────────────────────────────────────
        resolveDisplayName(node);

        // ── Locators ──────────────────────────────────────────────────────
        buildLocators(node);

        // IMPORTANT: call BEFORE the children loop so items exposed via
        // accessibility/model do NOT get duplicated as regular Container children.
        // Returns true when logical children were added — then we skip the
        // natural Container loop for Toolbar/Tab nodes whose children are the
        // same set of items (EWT LWMenuBar's getComponents() mirrors
        // its accessible children; TabBar getComponents() also mirrors tab items).
        boolean logicalChildrenAdded = expandLogicalChildren(node, comp, idGen, raw);

        // ── Children ──────────────────────────────────────────────────────
        // WHY: Toolbar and Tab containers (TabBar, FormsTabPanel) have
        // BOTH accessible children (menu items, tab headers) AND natural
        // Container children (content panels). We must run BOTH scans
        // — accessibility for the headers, Container loop for the content.
        // The dedup logic in expandLogicalChildren prevents duplicates.
        //
        // Only skip the Container loop when the node is a leaf-level tab
        // item (TabPanelPage) whose children are purely accessible.
        boolean skipContainerLoop = logicalChildrenAdded &&
                (("Toolbar".equals(node.semanticType) && !"TabBar".equals(node.simpleClassName)
                        && !"FormsTabPanel".equals(node.simpleClassName))
                        || ("Tab".equals(node.semanticType) && !"TabBar".equals(node.simpleClassName)
                                && !"FormsTabPanel".equals(node.simpleClassName)));
        if (comp instanceof Container && !skipContainerLoop) {
            Component[] children = safeGetChildren((Container) comp);
            for (int i = 0; i < children.length; i++) {
                Component child = children[i];
                if (!raw && !safeIsVisible(child))
                    continue;
                try {
                    DomNode childNode = buildNode(child, node.path, depth + 1,
                            i, children.length, idGen, raw);
                    node.children.add(childNode);
                } catch (Exception e) {
                    DomNode errChild = new DomNode();
                    errChild.id = idGen.incrementAndGet();
                    errChild.semanticType = "Unknown";
                    errChild.type = child.getClass().getName();
                    errChild.depth = depth + 1;
                    errChild.index = i;
                    errChild.siblingCount = children.length;
                    errChild.parentPath = node.path;
                    errChild.path = node.path + "/" + child.getClass().getSimpleName() + "[" + i + "]";
                    errChild.attributes.put("_scanError",
                            e.getClass().getName() + ": " + e.getMessage());
                    errChild.attributes.put("_scanErrorStack", stackTrace(e));
                    node.children.add(errChild);
                }
            }
        }

        return node;
    }

    // ── Logical child expansion (menu / toolbar) ─────────────────────────

    /**
     * Expands menu bar, menu, submenu, toolbar, and tab bar nodes by
     * discovering their items via the Accessibility API (primary path)
     * with a reflection model-walk fallback.
     *
     * <p>
     * Only acts when {@code parent.semanticType} is one of
     * {@code "Menu"}, {@code "MenuItem"}, {@code "Toolbar"}, or
     * {@code "Tab"}.
     * For all other nodes this is a no-op.
     *
     * <p>
     * WHY: Oracle EWT LWMenu / LWMenuBar items live in a model
     * (getItemCount()/getItem(i)), NOT in getComponents(). Closed Swing
     * JMenu items sit in a popup. Oracle Forms TabBar / TabPage items
     * similarly live in a model and are invisible to Container-children
     * recursion. This method makes every item a real DomNode so it
     * receives its own id (eN), bounds, and locators and flows into the
     * Python repo as a proper Element.
     */
    private static boolean expandLogicalChildren(DomNode parent,
            Component comp,
            AtomicInteger idGen,
            boolean raw) {
        String st = parent.semanticType;
        if (!("Menu".equals(st) || "MenuItem".equals(st) || "Toolbar".equals(st) || "Tab".equals(st))) {
            return false;
        }

        // ── Build identity set of Components already added as children ──
        // We must NOT double-emit a node that natural Container recursion
        // already handled. IdentityHashMap is essential — we compare by
        // reference, not by equals().
        // IdentityHashSet via IdentityHashMap key set
        IdentityHashMap<Component, Boolean> identitySet = new IdentityHashMap<>();

        // Also collect from the Container's getComponents() directly
        if (comp instanceof Container) {
            Component[] containerKids = safeGetChildren((Container) comp);
            for (Component c : containerKids) {
                identitySet.put(c, Boolean.TRUE);
            }
        }

        // Deduplication for synthesized nodes (non-Component proxies)
        Set<String> synthesizedDedup = new HashSet<>();

        boolean hasAny = false;

        // ── Accessibility-first discovery ────────────────────────────────
        if (comp instanceof Accessible) {
            try {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null) {
                    int count = ac.getAccessibleChildrenCount();
                    if (count > 0) {
                        for (int i = 0; i < count && i < 256; i++) {
                            try {
                                Accessible accChild = ac.getAccessibleChild(i);
                                if (accChild == null)
                                    continue;

                                if (accChild instanceof Component) {
                                    Component cChild = (Component) accChild;
                                    // Bypass !visible guard — closed-menu items
                                    // report isVisible()==false but are legit.
                                    if (identitySet.containsKey(cChild))
                                        continue;
                                    identitySet.put(cChild, Boolean.TRUE);
                                    try {
                                        DomNode dn = buildNode(cChild, parent.path,
                                                parent.depth + 1, parent.children.size(),
                                                0 /* will be fixed below */,
                                                idGen, true /* force raw — bypass visibility */);
                                        if (dn != null) {
                                            dn.index = parent.children.size();
                                            parent.children.add(dn);
                                            hasAny = true;
                                        }
                                    } catch (Exception e) {
                                        // swallow — one broken item cannot abort scan
                                    }
                                } else {
                                    // Non-Component proxy — synthesize
                                    String dedupKey = dedupKey(accChild, i);
                                    if (synthesizedDedup.contains(dedupKey))
                                        continue;
                                    synthesizedDedup.add(dedupKey);
                                    DomNode dn = buildAccessibleItemNode(
                                            accChild, parent, parent.children.size(),
                                            0, idGen);
                                    if (dn != null) {
                                        parent.children.add(dn);
                                        hasAny = true;
                                    }
                                }
                            } catch (Exception ignored) {
                            }
                        }
                    }
                }
            } catch (Exception ignored) {
            }
        }

        // Fix sibling counts for all children (including existing ones)
        for (int i = 0; i < parent.children.size(); i++) {
            parent.children.get(i).index = i;
            parent.children.get(i).siblingCount = parent.children.size();
        }

        // ── FALLBACK: model-walk only if accessible pass produced zero ──
        if (!hasAny) {
            expandModelChildren(parent, comp, idGen, identitySet, synthesizedDedup);
            // Re-check: model walk may have added children
            hasAny = !parent.children.isEmpty();
        }
        return hasAny;
    }

    /**
     * Model-based fallback for components whose Accessibility API returns
     * no children but whose model API exposes items.
     */
    private static void expandModelChildren(DomNode parent,
            Component comp,
            AtomicInteger idGen,
            IdentityHashMap<Component, Boolean> identitySet,
            Set<String> dedup) {
        // Collect items for each model strategy
        List<Object> items = new ArrayList<>();

        try {
            // JMenu -> getMenuComponents()
            if (comp instanceof JMenu) {
                Component[] mc = ((JMenu) comp).getMenuComponents();
                for (Component c : mc) {
                    if (!identitySet.containsKey(c)) {
                        identitySet.put(c, Boolean.TRUE);
                        items.add(c);
                    }
                }
            }
            // JPopupMenu -> getComponents()
            else if (comp instanceof JPopupMenu) {
                Component[] pc = ((JPopupMenu) comp).getComponents();
                for (Component c : pc) {
                    if (!identitySet.containsKey(c)) {
                        identitySet.put(c, Boolean.TRUE);
                        items.add(c);
                    }
                }
            }
            // Generic model: getItemCount() + int-arg getter
            else {
                Method countMethod = findMethod(comp.getClass(), "getItemCount");
                if (countMethod != null) {
                    Object cntObj = countMethod.invoke(comp);
                    int count = toInt(cntObj);
                    if (count > 0 && count <= 256) {
                        Method getMethod = findIntMethod(comp.getClass(),
                                "getItem", "getMenuItem", "getItemAt", "getComponentAtIndex");
                        if (getMethod != null) {
                            for (int i = 0; i < count; i++) {
                                try {
                                    Object item = getMethod.invoke(comp, i);
                                    if (item != null)
                                        items.add(item);
                                } catch (Exception ignored) {
                                }
                            }
                        }
                    }
                }
            }
        } catch (Exception ignored) {
        }

        for (Object item : items) {
            try {
                if (item instanceof Component) {
                    Component cItem = (Component) item;
                    if (identitySet.containsKey(cItem))
                        continue;
                    identitySet.put(cItem, Boolean.TRUE);
                    DomNode dn = buildNode(cItem, parent.path,
                            parent.depth + 1, parent.children.size(),
                            0, idGen, true);
                    if (dn != null) {
                        dn.index = parent.children.size();
                        parent.children.add(dn);
                    }
                } else {
                    String dk = dedupKey(item, parent.children.size());
                    if (dedup.contains(dk))
                        continue;
                    dedup.add(dk);
                    // Try via accessible if possible
                    if (item instanceof Accessible) {
                        DomNode dn = buildAccessibleItemNode(
                                (Accessible) item, parent,
                                parent.children.size(), 0, idGen);
                        if (dn != null)
                            parent.children.add(dn);
                    }
                }
            } catch (Exception ignored) {
            }
        }

        // Fix sibling counts
        for (int i = 0; i < parent.children.size(); i++) {
            parent.children.get(i).index = i;
            parent.children.get(i).siblingCount = parent.children.size();
        }
    }

    /**
     * Builds a DomNode for an {@link Accessible} that is NOT a
     * {@link java.awt.Component} (e.g. an EWT proxy).
     *
     * <p>
     * Returns {@code null} if the item has no AccessibleContext or
     * no usable label.
     */
    private static DomNode buildAccessibleItemNode(Accessible item,
            DomNode parent,
            int index,
            int siblingCount,
            AtomicInteger idGen) {
        try {
            if (item == null)
                return null;
            AccessibleContext ac = item.getAccessibleContext();
            if (ac == null)
                return null;

            DomNode n = new DomNode();
            n.id = idGen.incrementAndGet();
            n.depth = parent.depth + 1;
            n.index = index;
            n.siblingCount = siblingCount;
            n.parentPath = parent.path;

            n.accessibleName = ac.getAccessibleName();
            n.accessibleDescription = ac.getAccessibleDescription();
            n.text = (notBlank(n.accessibleName))
                    ? n.accessibleName
                    : n.accessibleDescription;

            // semanticType: map accessible role via ComponentClassifier
            AccessibleRole role = ac.getAccessibleRole();
            if (role != null) {
                String ds = role.toDisplayString();
                n.accessibleRole = ds;
                if (ds != null) {
                    n.semanticType = ComponentClassifier.classifyAccessibleRole(
                            ds.toLowerCase());
                } else {
                    n.semanticType = "Toolbar".equals(parent.semanticType)
                            ? "Button"
                            : "MenuItem";
                }
            } else {
                n.semanticType = "Toolbar".equals(parent.semanticType)
                        ? "Button"
                        : "MenuItem";
            }

            Class<?> clazz = item.getClass();
            n.type = clazz.getName();
            n.className = clazz.getName();
            n.simpleClassName = clazz.getSimpleName();

            // ── State from AccessibleStateSet ──────────────────────────
            AccessibleStateSet ss = ac.getAccessibleStateSet();
            if (ss != null) {
                n.enabled = ss.contains(AccessibleState.ENABLED);
                n.visible = ss.contains(AccessibleState.VISIBLE);
                n.showing = ss.contains(AccessibleState.SHOWING);
                n.selected = ss.contains(AccessibleState.CHECKED)
                        || ss.contains(AccessibleState.SELECTED);
                n.focusable = ss.contains(AccessibleState.FOCUSABLE);
            } else {
                n.enabled = true;
                n.visible = true;
                n.focusable = true;
            }

            // ── Geometry (best-effort, try/catch) ──────────────────────
            try {
                AccessibleComponent acomp = ac.getAccessibleComponent();
                if (acomp != null) {
                    java.awt.Rectangle b = acomp.getBounds();
                    if (b != null) {
                        if (n.showing) {
                            try {
                                java.awt.Point sp = acomp.getLocationOnScreen();
                                n.bounds = new Bounds(b.x, b.y, b.width, b.height);
                                n.screenBounds = new Bounds(b.x, b.y,
                                        b.width, b.height,
                                        sp.x, sp.y, b.width, b.height);
                            } catch (Exception e2) {
                                n.bounds = new Bounds(b.x, b.y, b.width, b.height);
                                n.screenBounds = new Bounds(b.x, b.y,
                                        b.width, b.height);
                            }
                        } else {
                            n.bounds = new Bounds(b.x, b.y, b.width, b.height);
                            n.screenBounds = new Bounds(b.x, b.y,
                                    b.width, b.height);
                        }
                    } else {
                        n.bounds = new Bounds(0, 0, 0, 0);
                        n.screenBounds = new Bounds(0, 0, 0, 0);
                    }
                } else {
                    n.bounds = new Bounds(0, 0, 0, 0);
                    n.screenBounds = new Bounds(0, 0, 0, 0);
                }
            } catch (Exception e) {
                n.bounds = new Bounds(0, 0, 0, 0);
                n.screenBounds = new Bounds(0, 0, 0, 0);
            }

            // ── Path ──────────────────────────────────────────────────
            String pathSegment = n.simpleClassName + "[" + index + "]";
            n.path = (parent.path != null && !parent.path.isEmpty())
                    ? parent.path + "/" + pathSegment
                    : pathSegment;

            // ── displayName + confidence ───────────────────────────────
            resolveDisplayName(n);

            // ── Locators ───────────────────────────────────────────────
            buildLocators(n);

            // ── AccessibleAction replay handle ─────────────────────────
            try {
                javax.accessibility.AccessibleAction action = ac.getAccessibleAction();
                if (action != null && action.getAccessibleActionCount() > 0) {
                    String a0 = action.getAccessibleActionDescription(0);
                    if (a0 != null && !a0.isEmpty()) {
                        n.attributes.put("accessibleAction", a0);
                        n.locators.add(new LocatorCandidate(
                                "accessibleAction", a0, 0.95));
                    }
                }
            } catch (Exception ignored) {
            }

            // ── Recurse submenus ───────────────────────────────────────
            String st = n.semanticType;
            if ("Menu".equals(st) || "MenuItem".equals(st) || "Toolbar".equals(st) || "Tab".equals(st)) {
                // Build a synthetic "comp" context for recursion
                // We use the parent's accessible context to find grand-children
                try {
                    int subCount = ac.getAccessibleChildrenCount();
                    if (subCount > 0 && subCount <= 256) {
                        int startIdx = n.children.size();
                        for (int i = 0; i < subCount; i++) {
                            try {
                                Accessible subChild = ac.getAccessibleChild(i);
                                if (subChild == null)
                                    continue;
                                DomNode sub = buildAccessibleItemNode(
                                        subChild, n, startIdx + n.children.size(),
                                        0, idGen);
                                if (sub != null) {
                                    n.children.add(sub);
                                }
                            } catch (Exception ignored) {
                            }
                        }
                        for (int i = 0; i < n.children.size(); i++) {
                            n.children.get(i).index = i;
                            n.children.get(i).siblingCount = n.children.size();
                        }
                    }
                } catch (Exception ignored) {
                }
            }

            return n;
        } catch (Exception e) {
            return null;
        }
    }

    // ── Helper utility for expandLogicalChildren ───────────────────────

    /** Builds a dedup key from accessible name + role + index. */
    private static String dedupKey(Object item, int index) {
        if (item instanceof Accessible) {
            try {
                AccessibleContext ac = ((Accessible) item).getAccessibleContext();
                if (ac != null) {
                    String name = ac.getAccessibleName();
                    AccessibleRole r = ac.getAccessibleRole();
                    String roleStr = r != null ? r.toDisplayString() : "";
                    return (name != null ? name : "") + "\t" + roleStr + "\t" + index;
                }
            } catch (Exception ignored) {
            }
        }
        return item.getClass().getName() + "\t" + index;
    }

    /** Resolves a zero-arg method by name. */
    private static Method findMethod(Class<?> clazz, String name) {
        for (Method m : clazz.getMethods()) {
            if (m.getName().equals(name) && m.getParameterCount() == 0) {
                return m;
            }
        }
        return null;
    }

    /** Resolves a single-int-arg method from a list of candidate names. */
    private static Method findIntMethod(Class<?> clazz, String... names) {
        for (String name : names) {
            for (Method m : clazz.getMethods()) {
                if (m.getName().equals(name)
                        && m.getParameterCount() == 1
                        && m.getParameterTypes()[0] == int.class) {
                    return m;
                }
            }
        }
        return null;
    }

    /** Safe numeric conversion from an Object (Number, String, Boolean). */
    private static int toInt(Object value) {
        if (value instanceof Number)
            return ((Number) value).intValue();
        if (value instanceof String) {
            try {
                return Integer.parseInt((String) value);
            } catch (Exception ignored) {
            }
        }
        if (value instanceof Boolean)
            return ((Boolean) value) ? 1 : 0;
        return 0;
    }

    // ── displayName resolution ────────────────────────────────────────────

    private static void resolveDisplayName(DomNode n) {
        // Prefer accessible name (usually comes from form metadata)
        if (notBlank(n.accessibleName)) {
            n.displayName = n.accessibleName;
            n.confidence = 0.85;
        } else if (notBlank(n.title)) {
            n.displayName = n.title;
            n.confidence = 0.80;
        } else if (notBlank(n.name)) {
            n.displayName = n.name;
            n.confidence = 0.75;
        } else if (notBlank(n.text)) {
            n.displayName = n.text;
            n.confidence = 0.60;
        } else if (notBlank(n.value)) {
            n.displayName = n.value;
            n.confidence = 0.40;
        } else {
            n.displayName = n.simpleClassName;
            n.confidence = 0.10;
        }
    }

    // ── Locator generation ────────────────────────────────────────────────

    private static void buildLocators(DomNode n) {
        // path is always available
        n.locators.add(new LocatorCandidate("path", n.path, 0.50));

        if (notBlank(n.accessibleName)) {
            n.locators.add(new LocatorCandidate("accessibleName", n.accessibleName, 0.90));
        }
        if (notBlank(n.name) && !n.name.startsWith("null")) {
            n.locators.add(new LocatorCandidate("name", n.name, 0.80));
        }
        if (notBlank(n.title)) {
            n.locators.add(new LocatorCandidate("title", n.title, 0.75));
        }
        if (notBlank(n.text)) {
            n.locators.add(new LocatorCandidate("text", n.text, 0.65));
        }
    }

    // ── Utility ───────────────────────────────────────────────────────────

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty() && !"null".equals(s);
    }

    private static String firstNonNull(String... values) {
        for (String v : values) {
            if (v != null && !v.equals("null"))
                return v;
        }
        return null;
    }

    private static String safeClassify(Component comp) {
        try {
            return ComponentClassifier.classify(comp);
        } catch (Throwable t) {
            return comp instanceof Window ? "Window"
                    : comp instanceof Container ? "Panel" : "Unknown";
        }
    }

    private static boolean safeIsVisible(Component comp) {
        try {
            return comp.isVisible();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static boolean safeIsShowing(Component comp) {
        try {
            return comp.isShowing();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static boolean safeIsEnabled(Component comp) {
        try {
            return comp.isEnabled();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static Cursor safeGetCursor(Component comp) {
        try {
            return comp.getCursor();
        } catch (Throwable ignored) {
            return Cursor.getDefaultCursor();
        }
    }

    private static boolean safeIsFocusable(Component comp) {
        try {
            return comp.isFocusable();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static boolean safeIsFocusOwner(Component comp) {
        try {
            return comp.isFocusOwner();
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static Rectangle safeGetBounds(Component comp) {
        try {
            Rectangle bounds = comp.getBounds();
            if (bounds != null)
                return bounds;
        } catch (Throwable ignored) {
        }
        return new Rectangle(0, 0, 0, 0);
    }

    private static Component[] safeGetChildren(Container container) {
        try {
            Component[] children = container.getComponents();
            if (children != null)
                return children;
        } catch (Throwable t) {
            System.err.println("[ebs-dom-agent] getComponents failed for "
                    + container.getClass().getName() + ": "
                    + t.getClass().getName() + ": " + t.getMessage());
        }
        return new Component[0];
    }

    private static String stackTrace(Throwable t) {
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }

    // ── DrawnPanel prompt extraction ──────────────────────────────────────

    /**
     * Attempts to read the painted column-header label from an
     * {@code oracle.forms.ui.DrawnPanel} instance using a cascade of
     * Oracle-internal field and method names observed across Forms R12
     * patch levels. Returns {@code null} if nothing useful is found.
     *
     * <p>
     * The cascade (first non-blank result wins):
     * <ol>
     * <li>{@code getLabel()} — present on some patch levels</li>
     * <li>{@code getPrompt()} — older Forms runtime</li>
     * <li>{@code getItemLabel()} — Forms 12c variant</li>
     * <li>Private field {@code mItemLabel} — common internal field name</li>
     * <li>Private field {@code mLabel} — alternate naming</li>
     * <li>Private field {@code mPromptText} — another variant</li>
     * </ol>
     */
    private static String drawnPanelPrompt(Component comp) {
        Class<?> clazz = comp.getClass();

        // Try zero-arg methods first (public API, no setAccessible needed)
        for (String methodName : new String[] { "getLabel", "getPrompt", "getItemLabel" }) {
            try {
                java.lang.reflect.Method m = clazz.getMethod(methodName);
                Object result = m.invoke(comp);
                if (result != null) {
                    String s = result.toString().trim();
                    if (!s.isEmpty())
                        return s;
                }
            } catch (Exception ignored) {
            }
        }

        // Try private fields via setAccessible (safe read-only access)
        for (String fieldName : new String[] { "mItemLabel", "mLabel", "mPromptText", "promptText", "label" }) {
            try {
                java.lang.reflect.Field f = findField(clazz, fieldName);
                if (f == null)
                    continue;
                f.setAccessible(true);
                Object result = f.get(comp);
                if (result != null) {
                    String s = result.toString().trim();
                    if (!s.isEmpty())
                        return s;
                }
            } catch (Exception ignored) {
            }
        }

        return null;
    }

    /** Walk the class hierarchy to find a declared field by name. */
    private static java.lang.reflect.Field findField(Class<?> clazz, String name) {
        for (Class<?> c = clazz; c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                return c.getDeclaredField(name);
            } catch (NoSuchFieldException ignored) {
            }
        }
        return null;
    }

    // ── ScanResult inner class ────────────────────────────────────────────

    /** Aggregates the output of a single scan invocation. */
    public static final class ScanResult {

        public final List<DomNode> windows;
        public final int windowCount;
        public final int visibleWindowCount;
        public final boolean raw;
        public final String timestamp;

        public ScanResult(List<DomNode> windows,
                int windowCount,
                int visibleWindowCount,
                boolean raw,
                String timestamp) {
            this.windows = windows;
            this.windowCount = windowCount;
            this.visibleWindowCount = visibleWindowCount;
            this.raw = raw;
            this.timestamp = timestamp;
        }

        /** Serialises the entire scan result to a JSON string. */
        public String toJson(String command) {
            StringBuilder sb = new StringBuilder();
            sb.append('{');
            sb.append("\"status\":\"ok\",");
            sb.append("\"command\":").append(JsonUtil.quoted(command)).append(',');
            sb.append("\"agent\":{")
                    .append("\"name\":\"ebs-dom-agent\",")
                    .append("\"version\":\"0.2.0\"")
                    .append(",\"schema\":\"2.0\"")
                    .append("},");
            sb.append("\"scan\":{")
                    .append("\"timestamp\":").append(JsonUtil.quoted(timestamp)).append(',')
                    .append("\"windowCount\":").append(windowCount).append(',')
                    .append("\"visibleWindowCount\":").append(visibleWindowCount).append(',')
                    .append("\"raw\":").append(raw)
                    .append("},");
            sb.append("\"windows\":[");
            for (int i = 0; i < windows.size(); i++) {
                if (i > 0)
                    sb.append(',');
                sb.append(windows.get(i).toJson(true));
            }
            sb.append("]}");
            return sb.toString();
        }
    }
}
