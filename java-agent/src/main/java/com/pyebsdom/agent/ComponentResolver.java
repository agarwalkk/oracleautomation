package com.pyebsdom.agent;

import javax.accessibility.Accessible;
import javax.accessibility.AccessibleContext;
import javax.swing.*;
import java.awt.*;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

/**
 * Resolves a live AWT/Swing {@link Component} from the parameters of an agent
 * command.
 *
 * <h3>Determinism contract (changed)</h3>
 * The resolver now refuses to guess. The high-priority strategies require an
 * <b>exact, unique</b> match; if a locator matches more than one component the
 * resolver does NOT silently return the first — it requires an ordinal
 * ({@code locatorOrdinal} / {@code locatorRecordIndex}) and/or a scope
 * ({@code locatorScope}) to disambiguate, exactly as
 * {@link IdentityResolver} encoded at scan time. The old
 * "contains, first match wins" fallback is gated behind
 * {@code locatorAllowFuzzy=true} so it can never fire during normal replay.
 *
 * <h3>Strategies (in priority order)</h3>
 * <ol>
 * <li><b>locatorSemanticId</b> — the stable {@code <scope>::<label>::<ordinal>}
 * identity; resolved by reconstructing the same identity for live
 * components.</li>
 * <li><b>locatorTreePath</b> — a tree row by its root→leaf label chain.</li>
 * <li><b>locatorCanonicalLabel</b> (+ optional scope + ordinal) — the
 * business label, scoped/ordinal-qualified for uniqueness.</li>
 * <li><b>locatorAccessibleName</b> (unique, or +ordinal).</li>
 * <li><b>locatorName</b> / <b>locatorText</b> (unique only).</li>
 * <li><b>locatorBounds</b> — screen-rectangle overlap (last resort; used for
 * Robot clicks on rows/tabs that have no model selector).</li>
 * <li><b>contains fuzzy</b> — only if {@code locatorAllowFuzzy=true}.</li>
 * </ol>
 *
 * <p>
 * Must be called on the EDT.
 */
public final class ComponentResolver {

    private ComponentResolver() {
    }

    // ── Public API ────────────────────────────────────────────────────────

    public static Component resolve(AgentCommand cmd) {
        String locHandlerId = cmd.getParam("locatorhandlerid");
        String locPath = cmd.getParam("locatorpath");
        String locSemanticId = cmd.getParam("locatorsemanticid");
        String locTreePath = cmd.getParam("locatortreepath");
        String locCanonical = cmd.getParam("locatorcanonicallabel");
        String locScope = cmd.getParam("locatorscope");
        String locOrdinalS = firstNonBlank(cmd.getParam("locatorordinal"),
                cmd.getParam("locatorrecordindex"));
        String locName = cmd.getParam("locatorname");
        String locAccName = cmd.getParam("locatoraccessiblename");
        String locText = cmd.getParam("locatortext");
        String locClass = cmd.getParam("locatorclassname");
        boolean allowFuzzy = "true".equalsIgnoreCase(cmd.getParam("locatorallowfuzzy"));
        int ordinal = parseInt(locOrdinalS, -1);

        List<Component> all = collectAllVisible();

        // -1) Forms item handler id — the strongest within-session locator:
        // Forms-native, unique per item, exact. Tried first; on a stale/old
        // recording replayed in a new session it simply won't match and we fall
        // through to path/semanticId/label, so there is no downside to trying it.
        if (notBlank(locHandlerId)) {
            List<Component> m = new ArrayList<>();
            for (Component c : all) {
                if (locHandlerId.equals(FormsHandler.handlerId(c)))
                    m.add(c);
            }
            Component r = pick(m, ordinal);
            if (r != null)
                return r;
        }

        // 0) exact DOM path — deterministic, unique by construction. Structural
        // components (tab bars, panels, scroll boxes) have no semantic label, so
        // path is the load-bearing locator for them (e.g. the studio clicks a
        // tab bar via locatorPath + tab_index). MUST run first so these resolve
        // to the right component instead of falling through to the bounds
        // strategy with parent-relative coordinates.
        if (notBlank(locPath)) {
            for (Component c : all) {
                if (locPath.equals(componentPath(c)))
                    return c;
            }
        }

        // 1) semanticId — reconstruct the same identity and match exactly-one.
        if (notBlank(locSemanticId)) {
            List<Component> m = new ArrayList<>();
            for (Component c : all) {
                if (locSemanticId.equals(liveSemanticId(c, all)))
                    m.add(c);
            }
            Component r = pick(m, ordinal);
            if (r != null)
                return r;
        }

        // 2) treePath — a row inside a tree.
        if (notBlank(locTreePath)) {
            Component r = resolveTreePath(all, locTreePath);
            if (r != null)
                return r;
        }

        // 3) canonicalLabel, optionally scoped + ordinal.
        if (notBlank(locCanonical)) {
            List<Component> m = new ArrayList<>();
            for (Component c : all) {
                if (!locCanonical.equals(canonicalLabel(c)))
                    continue;
                if (notBlank(locScope) && !locScope.equals(liveScopeId(c, all)))
                    continue;
                m.add(c);
            }
            Component r = pick(m, ordinal);
            if (r != null)
                return r;
        }

        // 4) accessibleName — exact, unique (or +ordinal).
        if (notBlank(locAccName)) {
            List<Component> m = new ArrayList<>();
            for (Component c : all)
                if (locAccName.equals(accessibleName(c)))
                    m.add(c);
            Component r = pick(m, ordinal);
            if (r != null)
                return r;
        }

        // 5) name / text — unique only.
        if (notBlank(locName)) {
            Component r = uniqueOrNull(all, c -> locName.equals(c.getName()));
            if (r != null)
                return r;
        }
        if (notBlank(locText)) {
            Component r = uniqueOrNull(all, c -> locText.equals(componentText(c)));
            if (r != null)
                return r;
        }

        // 6) class + name / class + accessibleName (unique).
        if (notBlank(locClass) && notBlank(locName)) {
            Component r = uniqueOrNull(all,
                    c -> classMatch(c, locClass) && locName.equals(c.getName()));
            if (r != null)
                return r;
        }
        if (notBlank(locClass) && notBlank(locAccName)) {
            Component r = uniqueOrNull(all,
                    c -> classMatch(c, locClass) && locAccName.equals(accessibleName(c)));
            if (r != null)
                return r;
        }

        // 7) bounds — screen overlap (Robot-click targets: rows, tabs) [DISABLED -
        // purely DOM-based]
        /*
         * if (notBlank(locBounds)) {
         * int[] bb = parseBounds(locBounds);
         * if (bb != null) {
         * Component r = findByBounds(all, bb[0], bb[1], bb[2], bb[3]);
         * if (r != null)
         * return r;
         * }
         * }
         */

        // 8) fuzzy contains — DISABLED unless explicitly allowed (healing only).
        if (allowFuzzy) {
            String contains = firstNonBlank(locCanonical, locName, locAccName, locText);
            if (notBlank(contains)) {
                String lower = contains.toLowerCase(java.util.Locale.ROOT);
                for (Component c : all) {
                    if (containsIgnoreCase(c.getName(), lower)
                            || containsIgnoreCase(accessibleName(c), lower)
                            || containsIgnoreCase(componentText(c), lower)) {
                        return c;
                    }
                }
            }
        }

        return null;
    }

    /**
     * Returns the single match, the n-th match when an ordinal is supplied, or
     * {@code null} when the match set is empty or ambiguous-without-ordinal.
     * This is the core "no first-match-wins" guarantee.
     */
    private static Component pick(List<Component> matches, int ordinal) {
        if (matches.isEmpty())
            return null;
        if (matches.size() == 1)
            return matches.get(0);
        if (ordinal >= 0 && ordinal < matches.size()) {
            // Stable order so ordinal is reproducible across scans.
            matches.sort((a, b) -> Integer.compare(topToBottom(a), topToBottom(b)));
            return matches.get(ordinal);
        }
        return null; // ambiguous and no ordinal — refuse to guess
    }

    private interface Pred {
        boolean test(Component c);
    }

    private static Component uniqueOrNull(List<Component> all, Pred p) {
        Component found = null;
        for (Component c : all) {
            if (p.test(c)) {
                if (found != null)
                    return null; // ambiguous
                found = c;
            }
        }
        return found;
    }

    private static int topToBottom(Component c) {
        try {
            if (c.isShowing()) {
                Point loc = c.getLocationOnScreen();
                return loc.y * 100000 + loc.x;
            }
        } catch (Exception ignored) {
        }
        return Integer.MAX_VALUE;
    }

    // ── Tree-path resolution ──────────────────────────────────────────────

    /**
     * Resolves a tree row by its label chain (e.g. "Orders Tree/Personal
     * Folders") to the OWNING tree component, and returns it only when the leaf
     * row actually exists.
     *
     * <p>
     * The precise per-row action is handled two ways, both deterministic:
     * (a) the materialised tree-item node carries real screen bounds, so the
     * recorder stores {@code locatorBounds} and the executor Robot-clicks the
     * row centre; (b) {@link #treeRowForLabel} gives the model row index, which
     * {@code ActionExecutor} can pass to the tree's {@code selectRow(int)} for a
     * model-level selection. This method intentionally does not mutate the live
     * component — resolution and action stay separated.
     */
    private static Component resolveTreePath(List<Component> all, String treePath) {
        String[] parts = treePath.split("/");
        if (parts.length == 0)
            return null;
        String treeName = parts[0];
        String leaf = parts[parts.length - 1];

        for (Component c : all) {
            if (!isTreeLike(c))
                continue;
            String an = accessibleName(c);
            if (an != null && parts.length > 1 && !an.equals(treeName))
                continue;
            if (treeRowForLabel(c, leaf) >= 0)
                return c;
            // EWT DTree exposes no JTree row API, so treeRowForLabel returns -1.
            // Confirm the item exists via the accessibility tree (the same source
            // the scanner materialised it from) and return the tree component.
            if (c instanceof Accessible) {
                AccessibleContext root = ((Accessible) c).getAccessibleContext();
                if (root != null && findLabeledDescendant(root, stripLevel(leaf)) != null)
                    return c;
            }
        }
        return null;
    }

    /**
     * Resolve the {@link AccessibleContext} of a tree item addressed by a
     * treePath ("Tree Name/Parent/Leaf"). Oracle EWT {@code DTree} items are
     * accessibility nodes, not AWT components, so this is how the executor
     * reaches them for a pure-DOM {@code AccessibleAction}. Returns null if not
     * found. Must be called on the EDT.
     */
    public static AccessibleContext resolveTreeItemAccessible(String treePath) {
        if (treePath == null || treePath.trim().isEmpty())
            return null;
        String[] parts = treePath.split("/");
        if (parts.length == 0)
            return null;
        boolean named = parts.length > 1;
        String treeName = parts[0];
        List<Component> all = collectAllVisible();
        for (Component c : all) {
            if (!isTreeLike(c) || !(c instanceof Accessible))
                continue;
            if (named) {
                String an = accessibleName(c);
                if (an != null && !an.equals(treeName))
                    continue;
            }
            AccessibleContext cur = ((Accessible) c).getAccessibleContext();
            if (cur == null)
                continue;
            boolean ok = true;
            for (int i = (named ? 1 : 0); i < parts.length; i++) {
                cur = findLabeledDescendant(cur, parts[i].trim());
                if (cur == null) {
                    ok = false;
                    break;
                }
            }
            if (ok && cur != null)
                return cur;
        }
        return null;
    }

    /**
     * Nearest descendant (any depth) whose level-stripped accessibleName equals
     * {@code want}. Tolerates the unlabelled wrapper nodes EWT DTree inserts
     * between the tree root and its visible items.
     */
    private static AccessibleContext findLabeledDescendant(AccessibleContext node, String want) {
        int n;
        try {
            n = node.getAccessibleChildrenCount();
        } catch (Throwable t) {
            return null;
        }
        for (int i = 0; i < n; i++) {
            Accessible ch;
            try {
                ch = node.getAccessibleChild(i);
            } catch (Throwable t) {
                continue;
            }
            if (ch == null)
                continue;
            AccessibleContext cc;
            try {
                cc = ch.getAccessibleContext();
            } catch (Throwable t) {
                continue;
            }
            if (cc == null)
                continue;
            String lbl;
            try {
                lbl = stripLevel(String.valueOf(cc.getAccessibleName()).trim());
            } catch (Throwable t) {
                lbl = "";
            }
            if (want.equals(lbl))
                return cc;
            AccessibleContext deep = findLabeledDescendant(cc, want);
            if (deep != null)
                return deep;
        }
        return null;
    }

    /**
     * Public helper for {@code ActionExecutor}: the model row index for a tree
     * row label, or -1. Lets the executor call {@code selectRow(int)} for a
     * deterministic, coordinate-free tree selection.
     */
    public static int resolveTreeRowIndex(Component tree, String leafLabel) {
        return treeRowForLabel(tree, leafLabel);
    }

    private static boolean isTreeLike(Component c) {
        String n = c.getClass().getName().toLowerCase(java.util.Locale.ROOT);
        return c instanceof JTree || n.contains("tree");
    }

    private static int treeRowForLabel(Component tree, String leafLabel) {
        try {
            Method rowCount = tree.getClass().getMethod("getRowCount");
            Method pathForRow = intMethod(tree.getClass(), "getPathForRow");
            if (pathForRow == null)
                return -1;
            int n = toInt(rowCount.invoke(tree));
            for (int i = 0; i < n; i++) {
                Object tp = pathForRow.invoke(tree, i);
                if (tp == null)
                    continue;
                String label = lastPathLabel(tp);
                if (leafLabel.equals(stripLevel(label)))
                    return i;
            }
        } catch (Exception ignored) {
        }
        return -1;
    }

    // ── Live identity reconstruction (mirror of IdentityResolver) ─────────

    /**
     * Rebuilds the {@code <scope>::<label>::<ordinal>} identity for a live
     * component so semanticId locators resolve without a fresh full scan.
     * NOTE: ordinal here is best-effort (focus/Z-order); when the locator
     * carries an explicit ordinal, {@link #pick} uses it instead.
     */
    private static String liveSemanticId(Component c, List<Component> all) {
        String scope = liveScopeId(c, all);
        String label = canonicalLabel(c);
        return scope + "::" + (label == null ? "" : label) + "::0";
    }

    private static String liveScopeId(Component c, List<Component> all) {
        Component cur = c.getParent();
        while (cur != null) {
            String scn = cur.getClass().getSimpleName();
            if ("TabPanelSheet".equals(scn)) {
                String an = accessibleName(cur);
                if (an != null)
                    return "tab:" + an;
            }
            if (cur instanceof Window || "ExtendedFrame".equals(scn) || cur instanceof Dialog) {
                String t = windowTitle(cur);
                if (t != null)
                    return "form:" + t;
            }
            cur = cur.getParent();
        }
        // Fall back to the volatile prefix's stable part.
        String an = accessibleName(c);
        if (an != null) {
            int idx = an.indexOf(" tab page ");
            if (idx > 0)
                return "tab:" + an.substring(0, idx).trim();
        }
        return "form:root";
    }

    private static String canonicalLabel(Component c) {
        try {
            if (c instanceof Accessible) {
                AccessibleContext ac = ((Accessible) c).getAccessibleContext();
                if (ac != null) {
                    String desc = ac.getAccessibleDescription();
                    if (notBlank(desc))
                        return desc.trim();
                    String name = ac.getAccessibleName();
                    if (notBlank(name)) {
                        int idx = name.indexOf(" tab page ");
                        return idx >= 0 ? name.substring(idx + 10).trim() : name.trim();
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    // ── Component JSON (unchanged contract; adds resolved row when present) ─

    public static String componentJson(Component comp) {
        if (comp == null)
            return "null";
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"className\":").append(JsonUtil.quoted(comp.getClass().getName())).append(',');
        sb.append("\"simpleName\":").append(JsonUtil.quoted(comp.getClass().getSimpleName())).append(',');
        sb.append("\"name\":").append(JsonUtil.quoted(comp.getName())).append(',');
        sb.append("\"accessibleName\":").append(JsonUtil.quoted(accessibleName(comp))).append(',');
        sb.append("\"text\":").append(JsonUtil.quoted(componentText(comp))).append(',');

        Rectangle b = comp.getBounds();
        sb.append("\"bounds\":{")
                .append("\"x\":").append(b.x).append(',')
                .append("\"y\":").append(b.y).append(',')
                .append("\"width\":").append(b.width).append(',')
                .append("\"height\":").append(b.height)
                .append("},");

        int sx = -1, sy = -1;
        try {
            if (comp.isShowing()) {
                Point p = comp.getLocationOnScreen();
                sx = p.x;
                sy = p.y;
            }
        } catch (Exception ignored) {
        }
        sb.append("\"screenX\":").append(sx).append(',');
        sb.append("\"screenY\":").append(sy);
        sb.append('}');
        return sb.toString();
    }

    // ── Component traversal ───────────────────────────────────────────────

    private static List<Component> collectAllVisible() {
        List<Component> result = new ArrayList<>();
        for (Window w : AwtContext.getWindows()) {
            if (w.isVisible())
                collectComponents(w, result);
        }
        return result;
    }

    private static void collectComponents(Component comp, List<Component> out) {
        if (!comp.isVisible())
            return;
        out.add(comp);
        if (comp instanceof Container) {
            for (Component child : ((Container) comp).getComponents()) {
                collectComponents(child, out);
            }
        }
    }

    public static String componentPath(Component comp) {
        if (comp == null)
            return "";
        List<String> segments = new ArrayList<>();
        Component current = comp;
        while (current != null) {
            Container parent = current.getParent();
            int index = 0;
            if (parent != null) {
                Component[] siblings = parent.getComponents();
                for (int i = 0; i < siblings.length; i++) {
                    if (siblings[i] == current) {
                        index = i;
                        break;
                    }
                }
            }
            segments.add(0, current.getClass().getSimpleName() + "[" + index + "]");
            if (current instanceof Window)
                break;
            current = parent;
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < segments.size(); i++) {
            if (i > 0)
                sb.append('/');
            sb.append(segments.get(i));
        }
        return sb.toString();
    }

    // ── Helper extractors ─────────────────────────────────────────────────

    private static String accessibleName(Component comp) {
        try {
            if (comp instanceof Accessible) {
                AccessibleContext ac = ((Accessible) comp).getAccessibleContext();
                if (ac != null && ac.getAccessibleName() != null)
                    return ac.getAccessibleName();
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static String componentText(Component comp) {
        for (String method : new String[] { "getText", "getTitle" }) {
            try {
                Object val = comp.getClass().getMethod(method).invoke(comp);
                if (val != null)
                    return val.toString();
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static String windowTitle(Component c) {
        if (c instanceof Frame)
            return ((Frame) c).getTitle();
        if (c instanceof Dialog)
            return ((Dialog) c).getTitle();
        return accessibleName(c);
    }

    private static boolean classMatch(Component comp, String locClass) {
        String name = comp.getClass().getSimpleName();
        String fqn = comp.getClass().getName();
        return name.equalsIgnoreCase(locClass) || fqn.equalsIgnoreCase(locClass)
                || name.toLowerCase(java.util.Locale.ROOT)
                        .contains(locClass.toLowerCase(java.util.Locale.ROOT));
    }

    private static boolean containsIgnoreCase(String target, String lowerSubstring) {
        if (target == null || target.isEmpty())
            return false;
        return target.toLowerCase(java.util.Locale.ROOT).contains(lowerSubstring);
    }

    // ── TreePath label helpers ────────────────────────────────────────────

    private static String lastPathLabel(Object treePath) {
        try {
            Method m = treePath.getClass().getMethod("getLastPathComponent");
            Object last = m.invoke(treePath);
            if (last != null)
                return String.valueOf(last).trim();
        } catch (Exception ignored) {
        }
        return String.valueOf(treePath).trim();
    }

    private static String stripLevel(String s) {
        if (s != null && s.startsWith("Level ")) {
            int sp = s.indexOf(' ', 6);
            if (sp > 0 && sp + 1 < s.length())
                return s.substring(sp + 1).trim();
        }
        return s == null ? "" : s.trim();
    }

    // ── Utility ───────────────────────────────────────────────────────────

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

    private static boolean notBlank(String s) {
        return s != null && !s.trim().isEmpty();
    }

    private static String firstNonBlank(String... values) {
        for (String v : values)
            if (notBlank(v))
                return v;
        return null;
    }

    private static int parseInt(String s, int dflt) {
        try {
            return s == null ? dflt : Integer.parseInt(s.trim());
        } catch (Exception e) {
            return dflt;
        }
    }
}
