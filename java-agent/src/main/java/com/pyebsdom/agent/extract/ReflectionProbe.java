package com.pyebsdom.agent.extract;

import com.pyebsdom.agent.json.Json;
import com.pyebsdom.agent.model.DomNode;

import java.awt.Component;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/**
 * Diagnostic reflection inspector. When a {@code scan} runs with
 * {@code probe=true}, matched nodes get an {@code attributes._probe} JSON blob
 * describing the live component's <b>full method / field surface</b> (and a few
 * ownership-related nested objects). This is how we discover, on a real screen,
 * which <em>non-robotic</em> API a widget actually exposes — e.g. whether an
 * Oracle EWT {@code TabBar} has a {@code setSelectedIndex(int)} /
 * {@code select(int)}
 * we can call instead of clicking a pixel.
 *
 * <p>
 * Pairs with the Python {@code probe_scan.py} (which sends
 * {@code probe=true;target=<selector>}) and {@code probe_report.py --full}
 * (which prints the inventory). Output shape per probed node:
 *
 * <pre>
 * {"target": &lt;obj&gt;, "ancestors": [&lt;obj&gt;, ...]}
 *   obj = {"class","methods","fields","methodValues","fieldValues","nested":[{"via","obj"}]}
 * </pre>
 *
 * <p>
 * The probe is read-mostly: it lists every method signature, but only
 * <em>invokes</em> no-arg getters ({@code get*}, {@code is*}, or {@code has*})
 * for {@code methodValues}.
 * It is a deliberate, opt-in diagnostic — never part of a normal scan.
 *
 * <h3>Selectors</h3>
 * {@code class:VButton} · {@code role:LOV} · {@code label:Held By} ·
 * {@code handler:1204} · bare text (treated as a label substring). A
 * {@code null}
 * selector with probe on = default mode: the first {@code Field} under each
 * scroll-box region.
 */
public final class ReflectionProbe {

    private ReflectionProbe() {
    }

    private static volatile boolean ON;
    private static volatile String SELECTOR; // null => default mode
    private static final Set<String> DEFAULT_SEEN = new HashSet<>();
    private static int count;

    private static final int MAX_PROBES = 60;
    private static final int MAX_VALUE_LEN = 160;

    /** Arm the probe for one scan. */
    public static void begin(boolean on, String selector) {
        ON = on;
        SELECTOR = (selector != null && !selector.trim().isEmpty()) ? selector.trim() : null;
        DEFAULT_SEEN.clear();
        count = 0;
    }

    /** Disarm after the scan. */
    public static void end() {
        ON = false;
        SELECTOR = null;
        DEFAULT_SEEN.clear();
    }

    /**
     * Hook called by {@code DomScanner.buildNode}; no-op unless armed + matched.
     */
    public static void maybe(DomNode node, Component comp) {
        if (!ON || node == null || comp == null || count >= MAX_PROBES)
            return;
        try {
            if (!matches(node))
                return;
            node.attributes.put("_probe", probeJson(comp));
            count++;
        } catch (Throwable t) {
            node.attributes.put("_probeError", t.getClass().getName() + ": " + t.getMessage());
        }
    }

    // ── Selector matching ─────────────────────────────────────────────────

    private static boolean matches(DomNode n) {
        if (SELECTOR == null) {
            // Default: first Field per scroll-box parent region.
            if (!"Field".equals(n.semanticType))
                return false;
            String parent = n.parentPath == null ? "" : n.parentPath;
            if (DEFAULT_SEEN.contains(parent))
                return false;
            DEFAULT_SEEN.add(parent);
            return true;
        }
        int c = SELECTOR.indexOf(':');
        String mode = c > 0 ? SELECTOR.substring(0, c).trim().toLowerCase(Locale.ROOT) : "label";
        String val = c > 0 ? SELECTOR.substring(c + 1).trim() : SELECTOR;
        switch (mode) {
            case "class":
                return contains(n.simpleClassName, val) || eqIgnore(n.className, val);
            case "role":
                return eqIgnore(n.semanticType, val);
            case "handler":
                return contains(n.name, val) || contains(n.path, val);
            case "label":
            default:
                return contains(n.canonicalLabel, val) || contains(n.accessibleName, val)
                        || contains(n.name, val);
        }
    }

    // ── Probe JSON ────────────────────────────────────────────────────────

    private static String probeJson(Component comp) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"target\":").append(objJson(comp, true)).append(',');
        sb.append("\"ancestors\":[");
        int n = 0;
        Component cur = comp.getParent();
        while (cur != null && n < 6) {
            String scn = cur.getClass().getSimpleName().toLowerCase(Locale.ROOT);
            if (scn.contains("canvas") || scn.contains("scrollbox")
                    || scn.contains("tabpanel") || scn.contains("frame") || scn.contains("block")) {
                if (n > 0)
                    sb.append(',');
                sb.append(objJson(cur, false));
                n++;
            }
            cur = cur.getParent();
        }
        sb.append("]}");
        return sb.toString();
    }

    private static String objJson(Object o, boolean withNested) {
        StringBuilder sb = new StringBuilder("{");
        sb.append("\"class\":").append(Json.quoted(o.getClass().getName())).append(',');
        sb.append("\"methods\":").append(Json.quoted(methodSigs(o.getClass()))).append(',');
        sb.append("\"fields\":").append(Json.quoted(fieldSigs(o.getClass()))).append(',');
        sb.append("\"methodValues\":").append(Json.quoted(methodValues(o))).append(',');
        sb.append("\"fieldValues\":").append(Json.quoted(fieldValues(o))).append(',');
        sb.append("\"nested\":[");
        if (withNested) {
            String[] getters = {
                    "getCanvas", "getTabPage", "getModel",
                    "getSelectionModel", "getBlock", "getSheet", "getModule" };
            int n = 0;
            for (String g : getters) {
                Object child = call0(o, g);
                if (child != null) {
                    if (n > 0)
                        sb.append(',');
                    sb.append("{\"via\":").append(Json.quoted(g))
                            .append(",\"obj\":").append(objJson(child, false)).append('}');
                    n++;
                }
            }
            // Follow getPage(0) (int-arg) so probing a FormsTabPanel reveals the
            // TabPanelPage surface — i.e. which method returns its component.
            Object page0 = callInt0(o, "getPage", 0);
            if (page0 != null) {
                if (n > 0)
                    sb.append(',');
                sb.append("{\"via\":\"getPage(0)\",\"obj\":")
                        .append(objJson(page0, false)).append('}');
                n++;
            }
        }
        sb.append("]}");
        return sb.toString();
    }

    /** Every public method as {@code name(p1,p2):ret}, joined by " | ". */
    private static String methodSigs(Class<?> c) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Method m : c.getMethods()) {
            if ("getClass".equals(m.getName()))
                continue;
            if (!first)
                sb.append(" | ");
            first = false;
            sb.append(m.getName()).append('(');
            Class<?>[] pt = m.getParameterTypes();
            for (int i = 0; i < pt.length; i++) {
                if (i > 0)
                    sb.append(',');
                sb.append(simple(pt[i]));
            }
            sb.append("):").append(simple(m.getReturnType()));
        }
        return sb.toString();
    }

    private static String fieldSigs(Class<?> c) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Field f : c.getFields()) {
            if (!first)
                sb.append(" | ");
            first = false;
            sb.append(f.getName()).append(':').append(simple(f.getType()));
        }
        return sb.toString();
    }

    /** Invoke no-arg getters and record {@code name=value | ...} (read-mostly). */
    private static String methodValues(Object o) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Method m : o.getClass().getMethods()) {
            if (m.getParameterTypes().length != 0)
                continue;
            if (m.getReturnType() == void.class)
                continue;
            String name = m.getName();
            if ("getClass".equals(name))
                continue;
            if (!(name.startsWith("get") || name.startsWith("is") || name.startsWith("has")))
                continue;
            try {
                m.setAccessible(true);
                Object v = m.invoke(o);
                if (v == null)
                    continue;
                if (!first)
                    sb.append(" | ");
                first = false;
                sb.append(name).append('=').append(truncate(String.valueOf(v)));
            } catch (Throwable ignored) {
            }
        }
        return sb.toString();
    }

    private static String fieldValues(Object o) {
        StringBuilder sb = new StringBuilder();
        boolean first = true;
        for (Field f : o.getClass().getFields()) {
            try {
                f.setAccessible(true);
                Object v = f.get(o);
                if (v == null)
                    continue;
                if (!first)
                    sb.append(" | ");
                first = false;
                sb.append(f.getName()).append('=').append(truncate(String.valueOf(v)));
            } catch (Throwable ignored) {
            }
        }
        return sb.toString();
    }

    // ── helpers ───────────────────────────────────────────────────────────

    private static Object callInt0(Object o, String name, int arg) {
        try {
            Method m = o.getClass().getMethod(name, int.class);
            m.setAccessible(true);
            return m.invoke(o, arg);
        } catch (Throwable t) {
            return null;
        }
    }

    private static Object call0(Object o, String name) {
        try {
            Method m = o.getClass().getMethod(name);
            m.setAccessible(true);
            return m.invoke(o);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String simple(Class<?> c) {
        return c == null ? "?" : c.getSimpleName();
    }

    private static String truncate(String s) {
        s = s.replace('\n', ' ').replace('\r', ' ');
        return s.length() > MAX_VALUE_LEN ? s.substring(0, MAX_VALUE_LEN) + "…" : s;
    }

    private static boolean contains(String hay, String needle) {
        return hay != null && needle != null
                && hay.toLowerCase(Locale.ROOT).contains(needle.toLowerCase(Locale.ROOT));
    }

    private static boolean eqIgnore(String a, String b) {
        return a != null && b != null && a.equalsIgnoreCase(b);
    }
}
