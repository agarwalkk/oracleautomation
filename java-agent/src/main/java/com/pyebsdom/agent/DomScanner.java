package com.pyebsdom.agent;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.swing.*;
import java.awt.*;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Scans the live AWT/Swing component tree inside the target JVM and
 * produces a list of {@link DomNode} trees — one per top-level
 * {@link Window}.
 *
 * <h3>Thread safety</h3>
 * All component-tree traversal is performed on the AWT Event Dispatch
 * Thread via {@link SwingUtilities#invokeAndWait}.  This guarantees
 * that component state is read consistently and that no concurrent
 * modification happens while we iterate.  {@link #scan(boolean)} itself
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

    private DomScanner() {}

    // ── Public entry points ───────────────────────────────────────────────

    /**
     * Scans the entire component tree and returns a {@link ScanResult}.
     *
     * @param raw {@code true} to include invisible/empty components
     */
    public static ScanResult scan(final boolean raw) throws Exception {
        final ScanResult[] holder = { null };
        final Exception[]  error  = { null };

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

        if (error[0] != null) throw error[0];
        return holder[0];
    }

    /**
     * Runs the {@link TableDetector} on the EDT and serialises the result
     * to a JSON string compatible with the {@code tables} command response.
     */
    public static String scanTables() throws Exception {
        @SuppressWarnings("unchecked")
        final List<TableModel>[] holder = new List[] { null };
        final Exception[]        error  = { null };

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

        if (error[0] != null) throw error[0];

        List<TableModel> tables = holder[0];
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"tables\",");
        sb.append("\"tableCount\":").append(tables.size()).append(',');
        sb.append("\"tables\":[");
        for (int i = 0; i < tables.size(); i++) {
            if (i > 0) sb.append(',');
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
            if (!raw && !safeIsVisible(window)) continue;
            try {
                DomNode node = buildNode(window, null, 0, 0, windows.length, idGen, raw);
                windowNodes.add(node);
            } catch (Exception e) {
                // Produce a minimal error node so the window is still represented.
                DomNode errNode = new DomNode();
                errNode.id           = idGen.incrementAndGet();
                errNode.semanticType = "Window";
                errNode.type         = window.getClass().getName();
                errNode.attributes.put("_scanError",
                        e.getClass().getName() + ": " + e.getMessage());
                errNode.attributes.put("_scanErrorStack", stackTrace(e));
                windowNodes.add(errNode);
            }
        }

        int visible = 0;
        for (Window w : windows) {
            if (safeIsVisible(w)) visible++;
        }

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
        node.id           = idGen.incrementAndGet();
        node.depth        = depth;
        node.index        = index;
        node.siblingCount = siblingCount;
        node.parentPath   = parentPath;

        // ── Class metadata ────────────────────────────────────────────────
        Class<?> clazz = comp.getClass();
        node.type            = clazz.getName();
        node.className       = clazz.getName();
        node.simpleClassName = clazz.getSimpleName();
        node.packageName     = clazz.getPackage() != null
                               ? clazz.getPackage().getName() : "";
        node.semanticType    = safeClassify(comp);

        // ── Basic state ───────────────────────────────────────────────────
        node.visible   = safeIsVisible(comp);
        node.showing   = safeIsShowing(comp);
        node.enabled   = safeIsEnabled(comp);
        node.focusable = safeIsFocusable(comp);
        node.focused   = safeIsFocusOwner(comp);
        Cursor cursor  = safeGetCursor(comp);
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
        } catch (Exception ignored) {}

        node.bounds       = new Bounds(r.x, r.y, r.width, r.height);
        node.screenBounds = new Bounds(r.x, r.y, r.width, r.height,
                                       sx, sy, r.width, r.height);

        // ── Name from component API ───────────────────────────────────────
        try { node.name = comp.getName(); } catch (Exception ignored) {}

        if (comp instanceof Frame)  {
            try { node.title = ((Frame)  comp).getTitle(); } catch (Exception ignored) {}
        } else if (comp instanceof Dialog) {
            try { node.title = ((Dialog) comp).getTitle(); } catch (Exception ignored) {}
        }

        // ── Accessibility ─────────────────────────────────────────────────
        if (comp instanceof Accessible) {
            try {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null) {
                    node.accessibleName        = ac.getAccessibleName();
                    node.accessibleDescription = ac.getAccessibleDescription();
                    if (ac.getAccessibleRole() != null) {
                        node.accessibleRole = ac.getAccessibleRole().toDisplayString();
                    }
                }
            } catch (Exception ignored) {}
        }

        // ── Reflection extraction ─────────────────────────────────────────
        try {
            Map<String, String> refMap = ReflectionExtractor.extract(comp);
            node.reflection.putAll(refMap);

            // Promote common reflection values to first-class fields
            node.text    = firstNonNull(refMap.get("getText"),   node.text);
            node.tooltip = firstNonNull(refMap.get("getToolTipText"), node.tooltip);
            node.value   = firstNonNull(
                    refMap.get("getValue"),
                    refMap.get("getSelectedItem"),
                    refMap.get("getSelectedValue"),
                    node.value);

            String editable = refMap.get("isEditable");
            if (editable != null) node.editable = "true".equalsIgnoreCase(editable);
            String selected = refMap.get("isSelected");
            if (selected != null) node.selected = "true".equalsIgnoreCase(selected);
            String checked = refMap.get("isChecked");
            if (checked != null) node.selected = "true".equalsIgnoreCase(checked);

            // Extra attributes worth surfacing
            for (String key : new String[] {
                    "getItemCount", "getRowCount", "getColumnCount", "getSelectedIndex" }) {
                if (refMap.containsKey(key)) {
                    node.attributes.put(key, refMap.get(key));
                }
            }

            List<String> options = ReflectionExtractor.extractOptions(comp, 100);
            if (!options.isEmpty()) {
                node.valueOptions.addAll(options);
                node.attributes.put("valueOptionCount", Integer.toString(options.size()));
            }
        } catch (Exception ignored) {}

        // ── DrawnPanel prompt extraction ──────────────────────────────────
        // oracle.forms.ui.DrawnPanel renders column headers by painting text
        // directly via Graphics2D; the standard accessibility API returns no
        // useful text.  We try a cascade of Oracle-internal field/method names
        // that hold the prompt string across different Forms runtime versions.
        if ("oracle.forms.ui.DrawnPanel".equals(comp.getClass().getName())) {
            String prompt = drawnPanelPrompt(comp);
            if (prompt != null && !prompt.isEmpty()) {
                node.text = prompt;
                node.displayName = prompt;
                node.confidence  = 0.70;
                node.reflection.put("drawnPanelPrompt", prompt);
            }
        }

        // ── Build path ────────────────────────────────────────────────────
        String label = coalesce(node.name, node.title, node.accessibleName,
                                node.text, node.simpleClassName);
        String pathSegment = node.simpleClassName + "[" + index + "]";
        node.path = parentPath != null && !parentPath.isEmpty()
                    ? parentPath + "/" + pathSegment
                    : pathSegment;

        // ── displayName + confidence ──────────────────────────────────────
        resolveDisplayName(node);

        // ── Locators ──────────────────────────────────────────────────────
        buildLocators(node);

        // ── Children ──────────────────────────────────────────────────────
        if (comp instanceof Container) {
            Component[] children = safeGetChildren((Container) comp);
            for (int i = 0; i < children.length; i++) {
                Component child = children[i];
                if (!raw && !safeIsVisible(child)) continue;
                try {
                    DomNode childNode = buildNode(child, node.path, depth + 1,
                                                  i, children.length, idGen, raw);
                    node.children.add(childNode);
                } catch (Exception e) {
                    DomNode errChild = new DomNode();
                    errChild.id           = idGen.incrementAndGet();
                    errChild.semanticType = "Unknown";
                    errChild.type         = child.getClass().getName();
                    errChild.depth        = depth + 1;
                    errChild.index        = i;
                    errChild.siblingCount = children.length;
                    errChild.parentPath   = node.path;
                    errChild.path         = node.path + "/" + child.getClass().getSimpleName() + "[" + i + "]";
                    errChild.attributes.put("_scanError",
                            e.getClass().getName() + ": " + e.getMessage());
                    errChild.attributes.put("_scanErrorStack", stackTrace(e));
                    node.children.add(errChild);
                }
            }
        }

        return node;
    }

    // ── displayName resolution ────────────────────────────────────────────

    private static void resolveDisplayName(DomNode n) {
        // Prefer accessible name (usually comes from form metadata)
        if (notBlank(n.accessibleName)) {
            n.displayName = n.accessibleName;
            n.confidence  = 0.85;
        } else if (notBlank(n.title)) {
            n.displayName = n.title;
            n.confidence  = 0.80;
        } else if (notBlank(n.name)) {
            n.displayName = n.name;
            n.confidence  = 0.75;
        } else if (notBlank(n.text)) {
            n.displayName = n.text;
            n.confidence  = 0.60;
        } else if (notBlank(n.value)) {
            n.displayName = n.value;
            n.confidence  = 0.40;
        } else {
            n.displayName = n.simpleClassName;
            n.confidence  = 0.10;
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
            if (v != null && !v.equals("null")) return v;
        }
        return null;
    }

    private static String coalesce(String... values) {
        for (String v : values) {
            if (notBlank(v)) return v;
        }
        return "";
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
        try { return comp.isVisible(); } catch (Throwable ignored) { return false; }
    }

    private static boolean safeIsShowing(Component comp) {
        try { return comp.isShowing(); } catch (Throwable ignored) { return false; }
    }

    private static boolean safeIsEnabled(Component comp) {
        try { return comp.isEnabled(); } catch (Throwable ignored) { return false; }
    }

    private static Cursor safeGetCursor(Component comp) {
        try { return comp.getCursor(); } catch (Throwable ignored) { return Cursor.getDefaultCursor(); }
    }

    private static boolean safeIsFocusable(Component comp) {
        try { return comp.isFocusable(); } catch (Throwable ignored) { return false; }
    }

    private static boolean safeIsFocusOwner(Component comp) {
        try { return comp.isFocusOwner(); } catch (Throwable ignored) { return false; }
    }

    private static Rectangle safeGetBounds(Component comp) {
        try {
            Rectangle bounds = comp.getBounds();
            if (bounds != null) return bounds;
        } catch (Throwable ignored) {}
        return new Rectangle(0, 0, 0, 0);
    }

    private static Component[] safeGetChildren(Container container) {
        try {
            Component[] children = container.getComponents();
            if (children != null) return children;
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
     * patch levels.  Returns {@code null} if nothing useful is found.
     *
     * <p>The cascade (first non-blank result wins):
     * <ol>
     *   <li>{@code getLabel()} — present on some patch levels</li>
     *   <li>{@code getPrompt()} — older Forms runtime</li>
     *   <li>{@code getItemLabel()} — Forms 12c variant</li>
     *   <li>Private field {@code mItemLabel} — common internal field name</li>
     *   <li>Private field {@code mLabel} — alternate naming</li>
     *   <li>Private field {@code mPromptText} — another variant</li>
     * </ol>
     */
    private static String drawnPanelPrompt(Component comp) {
        Class<?> clazz = comp.getClass();

        // Try zero-arg methods first (public API, no setAccessible needed)
        for (String methodName : new String[]{"getLabel", "getPrompt", "getItemLabel"}) {
            try {
                java.lang.reflect.Method m = clazz.getMethod(methodName);
                Object result = m.invoke(comp);
                if (result != null) {
                    String s = result.toString().trim();
                    if (!s.isEmpty()) return s;
                }
            } catch (Exception ignored) {}
        }

        // Try private fields via setAccessible (safe read-only access)
        for (String fieldName : new String[]{"mItemLabel", "mLabel", "mPromptText", "promptText", "label"}) {
            try {
                java.lang.reflect.Field f = findField(clazz, fieldName);
                if (f == null) continue;
                f.setAccessible(true);
                Object result = f.get(comp);
                if (result != null) {
                    String s = result.toString().trim();
                    if (!s.isEmpty()) return s;
                }
            } catch (Exception ignored) {}
        }

        return null;
    }

    /** Walk the class hierarchy to find a declared field by name. */
    private static java.lang.reflect.Field findField(Class<?> clazz, String name) {
        for (Class<?> c = clazz; c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                return c.getDeclaredField(name);
            } catch (NoSuchFieldException ignored) {}
        }
        return null;
    }

    // ── ScanResult inner class ────────────────────────────────────────────

    /** Aggregates the output of a single scan invocation. */
    public static final class ScanResult {

        public final List<DomNode> windows;
        public final int           windowCount;
        public final int           visibleWindowCount;
        public final boolean       raw;
        public final String        timestamp;

        public ScanResult(List<DomNode> windows,
                          int windowCount,
                          int visibleWindowCount,
                          boolean raw,
                          String timestamp) {
            this.windows            = windows;
            this.windowCount        = windowCount;
            this.visibleWindowCount = visibleWindowCount;
            this.raw                = raw;
            this.timestamp          = timestamp;
        }

        /** Serialises the entire scan result to a JSON string. */
        public String toJson(String command) {
            StringBuilder sb = new StringBuilder();
            sb.append('{');
            sb.append("\"status\":\"ok\",");
            sb.append("\"command\":").append(JsonUtil.quoted(command)).append(',');
            sb.append("\"agent\":{")
              .append("\"name\":\"ebs-dom-agent\",")
              .append("\"version\":\"0.1.0\"")
              .append("},");
            sb.append("\"scan\":{")
              .append("\"timestamp\":").append(JsonUtil.quoted(timestamp)).append(',')
              .append("\"windowCount\":").append(windowCount).append(',')
              .append("\"visibleWindowCount\":").append(visibleWindowCount).append(',')
              .append("\"raw\":").append(raw)
              .append("},");
            sb.append("\"windows\":[");
            for (int i = 0; i < windows.size(); i++) {
                if (i > 0) sb.append(',');
                sb.append(windows.get(i).toJson(true));
            }
            sb.append("]}");
            return sb.toString();
        }
    }
}
