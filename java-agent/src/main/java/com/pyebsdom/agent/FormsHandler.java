package com.pyebsdom.agent;

import java.awt.Component;
import java.lang.reflect.Field;
import java.lang.reflect.Method;

/**
 * Reads Oracle Forms item facts from a live UI component's handler peer.
 *
 * <p>
 * Every Forms UI item (VTextField, VButton, …) holds a private
 * {@code mHandler} reference to its {@code oracle.forms.handler.*Item} peer.
 * The full-inventory probe confirmed the handler exposes, beyond identity, the
 * exact item capabilities the .fmx defines:
 * <ul>
 * <li>{@code getHandlerId()} — Forms-native item id (unique per item; the
 * strongest within-session locator);</li>
 * <li>{@code isLOVButtonDisplayed()} / {@code mHasLovButton} — whether the
 * item has a List-of-Values (the GROUND TRUTH behind the
 * "&lt;label&gt; List of Values" accessibleName suffix);</li>
 * <li>{@code mReqdFlag} — required/mandatory item;</li>
 * <li>{@code isLocked()} / {@code mLocked} — runtime-locked (read-only).</li>
 * </ul>
 *
 * <p>
 * All access is read-only reflection, fully guarded; a missing accessor
 * (e.g. on a non-text item type) degrades to "no fact", never an error.
 */
public final class FormsHandler {

    private FormsHandler() {
    }

    /**
     * Stamps all available item facts onto {@code node} in a single handler
     * read. No-op when {@code comp} is not a Forms item.
     */
    public static void populate(Component comp, DomNode node) {
        if (comp == null || node == null)
            return;
        Object h = handlerOf(comp);
        if (h == null)
            return;

        String id = handlerIdOf(h);
        if (id != null && !id.isEmpty()) {
            node.handlerId = id;
            node.locators.add(new LocatorCandidate("handlerId", id, 0.97));
        }
        if (Boolean.TRUE.equals(boolGetterOrField(h, "isLOVButtonDisplayed", "mHasLovButton"))) {
            node.hasLov = true;
        }
        if (Boolean.TRUE.equals(boolField(h, "mReqdFlag"))) {
            node.required = true;
        }
        if (Boolean.TRUE.equals(boolGetterOrField(h, "isLocked", "mLocked"))) {
            node.locked = true;
        }

        // Forms-native item type from the handler peer itself (e.g.
        // "TextFieldItem", "CheckboxItem") — the authoritative item kind,
        // independent of the (sometimes ambiguous) AWT widget class.
        node.formsType = h.getClass().getSimpleName();

        // Authoritative owning tab, straight from the Forms runtime. Supersedes
        // the prefix/FScrollBox heuristic in StructureAnnotator and also covers
        // items (e.g. tree rows) the heuristic cannot place.
        String tab = stringGetter(h, "getParentTabName");
        if (tab != null && !tab.trim().isEmpty()) {
            node.formsTabName = tab.trim();
        }
    }

    /**
     * Forms item handler id, or {@code null}. Used by {@link ComponentResolver}.
     */
    public static String handlerId(Component comp) {
        Object h = handlerOf(comp);
        return h == null ? null : handlerIdOf(h);
    }

    // ── internals ─────────────────────────────────────────────────────────

    private static Object handlerOf(Component comp) {
        return readFieldDeep(comp, "mHandler");
    }

    private static String handlerIdOf(Object handler) {
        try {
            Method m = handler.getClass().getMethod("getHandlerId");
            Object v = m.invoke(handler);
            if (v != null)
                return String.valueOf(v);
        } catch (Throwable ignored) {
        }
        Object f = readFieldDeep(handler, "mHandlerId");
        return f != null ? String.valueOf(f) : null;
    }

    /**
     * Try a boolean getter, then a boolean field, returning null if neither works.
     */
    private static Boolean boolGetterOrField(Object o, String getter, String field) {
        try {
            Method m = o.getClass().getMethod(getter);
            Object v = m.invoke(o);
            if (v instanceof Boolean)
                return (Boolean) v;
        } catch (Throwable ignored) {
        }
        return boolField(o, field);
    }

    private static Boolean boolField(Object o, String field) {
        Object v = readFieldDeep(o, field);
        return (v instanceof Boolean) ? (Boolean) v : null;
    }

    /** Invoke a 0-arg getter and return its value as a String, or null. */
    private static String stringGetter(Object o, String getter) {
        try {
            Method m = o.getClass().getMethod(getter);
            Object v = m.invoke(o);
            return v == null ? null : String.valueOf(v);
        } catch (Throwable ignored) {
        }
        return null;
    }

    private static Object readFieldDeep(Object target, String name) {
        for (Class<?> c = target.getClass(); c != null && c != Object.class; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f.get(target);
            } catch (NoSuchFieldException ignored) {
                // try superclass
            } catch (Throwable t) {
                return null;
            }
        }
        return null;
    }

    /**
     * Forms-engine current item id (focus-proof; works with the OS window
     * inactive).
     */
    public static String currentItemHandlerId(Component anyFormsItem) {
        if (anyFormsItem == null)
            return null;
        Object h = readFieldDeep(anyFormsItem, "mHandler"); // your existing helper
        Object rf = h == null ? null : call0(h, "getDispatcher");
        Object it = rf == null ? null : call0(rf, "getFocusOwner");
        Object id = it == null ? null : call0(it, "getHandlerId");
        return id == null ? null : String.valueOf(id);
    }

    private static Object call0(Object o, String m) {
        try {
            java.lang.reflect.Method me = o.getClass().getMethod(m);
            me.setAccessible(true);
            return me.invoke(o);
        } catch (Throwable t) {
            return null;
        }
    }

}
