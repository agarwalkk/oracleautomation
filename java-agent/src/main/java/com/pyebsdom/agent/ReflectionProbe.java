package com.pyebsdom.agent;

import java.awt.Component;
import java.awt.Container;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Locale;

/**
 * DIAGNOSTIC ONLY. Dumps the full reflective surface of a live Oracle Forms
 * item and its ancestor canvas chain, so we can discover whether a field's
 * <b>tab-page / canvas / block</b> ownership is reachable (it is NOT exposed by
 * the public API or the accessible name on most fields).
 *
 * <p>
 * For each inspected object it records:
 * <ul>
 * <li>{@code methods} — every 0-arg getter's "{@code ReturnType name()}"
 * (names + types only; cheap, no invocation), so no candidate is missed;</li>
 * <li>{@code fields} — every declared field "{@code Type name}" across the
 * whole class hierarchy (names + types only);</li>
 * <li>{@code methodValues} / {@code fieldValues} — the actual values of any
 * member whose NAME or TYPE matches a tab/canvas/block keyword;</li>
 * <li>{@code nested} — one level of indirection into a keyword-matched member
 * that returns an {@code oracle.*} object (the Forms handler/peer, where
 * block/canvas/tabpage frequently live).</li>
 * </ul>
 *
 * <p>
 * Everything is wrapped in try/catch and capped, so a hostile getter cannot
 * abort a scan. Read-only: it never mutates the target.
 *
 * <h3>How to use</h3>
 * Wire {@link #probe(Component)} into {@code DomScanner.buildNode} (see
 * {@code DomScanner.PROBE.patch.java}) so the focused field (and the first
 * field
 * of the scan) get a {@code "_probe"} attribute. Click a <i>prefix-less</i>
 * field
 * on the target tab (e.g. "Held By" on the Holds tab) so it is focused, run one
 * scan, and inspect {@code _probe} in the dump with
 * {@code scripts/probe_report.py}.
 */
public final class ReflectionProbe {

    private ReflectionProbe() {
    }

    /** Members whose name/type contains any of these get their value captured. */
    private static final String[] KEYWORDS = {
            "canvas", "tab", "page", "block", "item", "sheet", "module", "mmb",
            "prompt", "owner", "handler", "peer", "impl", "fmx", "navig",
            "container", "region", "parent", "label", "name", "title"
    };

    private static final int MAX_ANCESTORS = 8;
    private static final int MAX_VALUE_LEN = 160;

    /** Build a probe report (JSON) for {@code comp} plus its ancestor chain. */
    public static String probe(Component comp) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"target\":").append(inspect(comp, 1));
        sb.append(",\"ancestors\":[");
        try {
            Container p = comp.getParent();
            int i = 0;
            while (p != null && i < MAX_ANCESTORS) {
                if (i > 0)
                    sb.append(',');
                sb.append(inspect(p, 0)); // depth 0: no further nesting for ancestors
                p = p.getParent();
                i++;
            }
        } catch (Throwable ignored) {
        }
        sb.append("]}");
        return sb.toString();
    }

    /**
     * Mines the Forms engine / applet context from a component's handler.
     */
    public static String probeRuntime(Component comp) {
        if (comp == null)
            return "{}";
        try {
            // Find mHandler of comp
            Object handler = null;
            for (Class<?> c = comp.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
                try {
                    Field f = c.getDeclaredField("mHandler");
                    f.setAccessible(true);
                    handler = f.get(comp);
                    if (handler != null)
                        break;
                } catch (Throwable ignored) {}
            }
            if (handler == null)
                return "{}";

            // Find dispatcher/Runform and applet/Main from handler
            Object dispatcher = null;
            try {
                Method m = handler.getClass().getMethod("getDispatcher");
                m.setAccessible(true);
                dispatcher = m.invoke(handler);
            } catch (Throwable ignored) {}
            if (dispatcher == null) {
                for (Class<?> c = handler.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
                    try {
                        Field f = c.getDeclaredField("mDispatcher");
                        f.setAccessible(true);
                        dispatcher = f.get(handler);
                        if (dispatcher != null)
                            break;
                    } catch (Throwable ignored) {}
                }
            }

            Object applet = null;
            try {
                Method m = handler.getClass().getMethod("getApplet");
                m.setAccessible(true);
                applet = m.invoke(handler);
            } catch (Throwable ignored) {}
            if (applet == null) {
                for (Class<?> c = handler.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
                    try {
                        Field f = c.getDeclaredField("mMainApplet");
                        f.setAccessible(true);
                        applet = f.get(handler);
                        if (applet != null)
                            break;
                    } catch (Throwable ignored) {}
                }
            }

            StringBuilder sb = new StringBuilder();
            sb.append("{");
            sb.append("\"dispatcher\":").append(dispatcher != null ? inspect(dispatcher, 1) : "null");
            sb.append(",\"applet\":").append(applet != null ? inspect(applet, 1) : "null");
            sb.append("}");
            return sb.toString();
        } catch (Throwable ignored) {}
        return "{}";
    }

    /**
     * Inspect one object. {@code nestDepth} > 0 allows following one level of
     * indirection into keyword-matched oracle.* members.
     */
    private static String inspect(Object o, int nestDepth) {
        if (o == null)
            return "null";
        Class<?> c = o.getClass();
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"class\":").append(JsonUtil.quoted(c.getName()));

        // ── method inventory (0-arg getters: names + return types) ────────
        StringBuilder methods = new StringBuilder();
        StringBuilder methodValues = new StringBuilder();
        StringBuilder nested = new StringBuilder();
        try {
            for (Method m : c.getMethods()) {
                if (m.getParameterCount() != 0)
                    continue;
                String mn = m.getName();
                if ("getClass".equals(mn))
                    continue;
                if (!(mn.startsWith("get") || mn.startsWith("is") || mn.startsWith("find")))
                    continue;
                Class<?> rt = m.getReturnType();
                if (rt == void.class)
                    continue;
                if (methods.length() > 0)
                    methods.append(", ");
                methods.append(rt.getSimpleName()).append(' ').append(mn).append("()");

                if (matches(mn) || matches(rt.getSimpleName())) {
                    Object v = safeInvoke(m, o);
                    if (v != null) {
                        if (methodValues.length() > 0)
                            methodValues.append(" | ");
                        methodValues.append(mn).append("()=").append(stringify(v));
                        maybeNest(nested, mn + "()", v, nestDepth);
                    }
                }
            }
        } catch (Throwable ignored) {
        }
        sb.append(",\"methods\":").append(JsonUtil.quoted(methods.toString()));
        sb.append(",\"methodValues\":").append(JsonUtil.quoted(methodValues.toString()));

        // ── field inventory (declared, all superclasses: names + types) ───
        StringBuilder fields = new StringBuilder();
        StringBuilder fieldValues = new StringBuilder();
        try {
            for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
                for (Field f : k.getDeclaredFields()) {
                    if (Modifier.isStatic(f.getModifiers()))
                        continue;
                    String fn = f.getName();
                    String ft = f.getType().getSimpleName();
                    if (fields.length() > 0)
                        fields.append(", ");
                    fields.append(ft).append(' ').append(fn);
                    if (matches(fn) || matches(ft)) {
                        Object v = safeField(f, o);
                        if (v != null) {
                            if (fieldValues.length() > 0)
                                fieldValues.append(" | ");
                            fieldValues.append(fn).append('=').append(stringify(v));
                            maybeNest(nested, fn, v, nestDepth);
                        }
                    }
                }
            }
        } catch (Throwable ignored) {
        }
        sb.append(",\"fields\":").append(JsonUtil.quoted(fields.toString()));
        sb.append(",\"fieldValues\":").append(JsonUtil.quoted(fieldValues.toString()));

        if (nested.length() > 0) {
            sb.append(",\"nested\":[").append(nested).append(']');
        }
        sb.append('}');
        return sb.toString();
    }

    /** Follow one level into a keyword-matched oracle.* (non-Component) value. */
    private static void maybeNest(StringBuilder nested, String via, Object v, int nestDepth) {
        if (nestDepth <= 0 || v == null)
            return;
        if (v instanceof Component)
            return; // already in the AWT tree
        Class<?> vc = v.getClass();
        String pkg = vc.getName();
        if (!pkg.startsWith("oracle."))
            return; // only Forms internals
        if (vc.isPrimitive() || v instanceof String || v instanceof Number
                || v instanceof Boolean)
            return;
        try {
            if (nested.length() > 0)
                nested.append(',');
            nested.append("{\"via\":").append(JsonUtil.quoted(via))
                    .append(",\"obj\":").append(inspect(v, 0)).append('}');
        } catch (Throwable ignored) {
        }
    }

    private static boolean matches(String s) {
        if (s == null)
            return false;
        String l = s.toLowerCase(Locale.ROOT);
        for (String k : KEYWORDS)
            if (l.contains(k))
                return true;
        return false;
    }

    private static Object safeInvoke(Method m, Object o) {
        try {
            m.setAccessible(true);
            return m.invoke(o);
        } catch (Throwable t) {
            return null;
        }
    }

    private static Object safeField(Field f, Object o) {
        try {
            f.setAccessible(true);
            return f.get(o);
        } catch (Throwable t) {
            return null;
        }
    }

    private static String stringify(Object v) {
        if (v == null)
            return "null";
        try {
            String s;
            if (v instanceof Component) {
                s = v.getClass().getName() + "#" + System.identityHashCode(v);
            } else {
                s = v.getClass().getName() + " => " + String.valueOf(v);
            }
            s = s.replace('\n', ' ').replace('\r', ' ');
            return s.length() > MAX_VALUE_LEN ? s.substring(0, MAX_VALUE_LEN) + "…" : s;
        } catch (Throwable t) {
            return v.getClass().getName();
        }
    }
}
