package com.pyebsdom.agent.runtime;

import java.lang.reflect.Method;

/**
 * Small, defensive reflection helpers shared across the agent.
 *
 * <p>The agent drives Oracle Forms / EWT widgets whose useful operations are
 * only reachable reflectively (no compile-time dependency on Oracle jars).
 * The same three patterns recurred in the original code — "find a method up
 * the class hierarchy", "invoke it without throwing", and "call a zero-arg
 * getter" — so they live here once.
 *
 * <p>These helpers never throw on a missing or failing method; they return
 * {@code null} (or the typed default) so callers can fall through to the next
 * strategy. They do NOT enforce the read-only allow-list — that policy lives in
 * {@code extract.ComponentReader}, which is the only place that bulk-invokes
 * arbitrary getters during extraction.
 */
public final class Reflect {

    private Reflect() { /* static utility */ }

    /**
     * Finds a method by name and parameter types, searching {@code type} and
     * all superclasses, and makes it accessible. Returns {@code null} if not
     * found.
     */
    public static Method method(Class<?> type, String name, Class<?>... paramTypes) {
        for (Class<?> k = type; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Method m = k.getDeclaredMethod(name, paramTypes);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException ignored) {
                // try the superclass
            } catch (Throwable ignored) {
                return null;
            }
        }
        // also try public methods declared on interfaces / deep hierarchy
        try {
            Method m = type.getMethod(name, paramTypes);
            m.setAccessible(true);
            return m;
        } catch (Throwable ignored) {
            return null;
        }
    }

    /**
     * Invokes {@code m} on {@code target} with {@code args}, returning the
     * result or {@code null} on any failure.
     */
    public static Object invoke(Object target, Method m, Object... args) {
        if (m == null) return null;
        try {
            return m.invoke(target, args);
        } catch (Throwable ignored) {
            return null;
        }
    }

    /**
     * Convenience: look up a zero-arg method by name on {@code target}'s class
     * and invoke it, returning the result or {@code null}.
     */
    public static Object call(Object target, String name) {
        if (target == null) return null;
        return invoke(target, method(target.getClass(), name));
    }

    /**
     * Convenience: invoke a one-int-arg method by name (e.g. {@code getRowBounds},
     * {@code setSelectedIndex}) on {@code target}, returning the result or
     * {@code null}.
     */
    public static Object callInt(Object target, String name, int arg) {
        if (target == null) return null;
        return invoke(target, method(target.getClass(), name, int.class), arg);
    }

    /** Coerces a reflective result to an int, or {@code dflt} when not a number. */
    public static int asInt(Object v, int dflt) {
        if (v instanceof Number) return ((Number) v).intValue();
        if (v == null) return dflt;
        try {
            return Integer.parseInt(String.valueOf(v));
        } catch (NumberFormatException e) {
            return dflt;
        }
    }

    /** True when {@code target} declares (anywhere in its hierarchy) a callable method. */
    public static boolean has(Object target, String name, Class<?>... paramTypes) {
        return target != null && method(target.getClass(), name, paramTypes) != null;
    }
}
