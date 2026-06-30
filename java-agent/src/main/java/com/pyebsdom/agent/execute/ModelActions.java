package com.pyebsdom.agent.execute;

import com.pyebsdom.agent.runtime.Edt;
import com.pyebsdom.agent.runtime.Reflect;

import javax.swing.AbstractButton;
import javax.swing.JComboBox;
import javax.swing.JList;
import javax.swing.JRadioButton;
import javax.swing.text.JTextComponent;
import java.awt.Component;
import java.awt.Rectangle;
import java.awt.event.KeyEvent;
import java.awt.event.MouseEvent;
import java.lang.reflect.Method;
import java.util.Locale;

/**
 * Non-robotic action primitives — the core of the new execution model.
 *
 * <p>
 * Every action drives the target component <b>inside the JVM</b>: it calls
 * the component's own model methods ({@code doClick}, {@code setText},
 * {@code setSelectedIndex}, {@code setSelectionRow}) or, when a Forms-custom
 * widget exposes no such method, dispatches a synthetic AWT event straight to
 * that component. Nothing here moves the OS mouse pointer or injects global
 * keystrokes, so actions are deterministic, headless-friendly, and immune to
 * focus stealing by other windows.
 *
 * <p>
 * There is deliberately <b>no automatic Robot fallback</b>. If a primitive
 * cannot drive a widget it returns {@code false} and the caller surfaces a
 * clear error; the old Robot implementation is retained only as reference code
 * under {@code backup-reference/RobotFallback.java} and is never invoked at
 * runtime.
 *
 * <p>
 * All methods marshal to the EDT themselves and may be called from the
 * agent thread. Each returns a short <em>technique</em> string (or {@code null}
 * on failure) so the action layer can report how the action was performed.
 */
public final class ModelActions {

    private ModelActions() {
        /* static utility */ }

    /** Request keyboard focus. Returns the technique or {@code null}. */
    public static String focus(final Component c) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            if (c.requestFocusInWindow()) {
                r[0] = "requestFocusInWindow";
            } else {
                c.requestFocus();
                r[0] = "requestFocus";
            }
        });
        return r[0];
    }

    /**
     * Activate a control. Prefers a model call ({@link AbstractButton#doClick()}
     * or a reflective {@code doClick}); otherwise dispatches a targeted
     * mouse press/release/click to the component centre (component-local
     * coordinates — no screen cursor movement).
     */
    public static String click(final Component c) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            if (c instanceof AbstractButton) {
                AbstractButton b = (AbstractButton) c;
                // Radio: selecting is idempotent within a group, so set the
                // state rather than toggle. (Checkbox toggling stays on doClick;
                // use setChecked() to drive a checkbox to a specific state.)
                if (b instanceof JRadioButton || isRadioLike(b)) {
                    b.setSelected(true);
                    r[0] = "setSelected(radio)";
                    return;
                }
                b.doClick();
                r[0] = "doClick";
                return;
            }
            // Forms-custom controls: a reflective doClick() is void, so a
            // non-throwing call counts as success.
            Method doClick = Reflect.method(c.getClass(), "doClick");
            if (doClick != null) {
                Reflect.invoke(c, doClick);
                r[0] = "doClick(reflect)";
                return;
            }
            dispatchClick(c);
            r[0] = "dispatchMouseEvent";
        });
        return r[0];
    }

    /**
     * Double-click a control (e.g. open a record from a grid/list). Dispatches
     * the canonical press/release/click pair twice, the second with
     * {@code clickCount = 2} — to the component, not the OS pointer.
     */
    public static String doubleClick(final Component c) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            int x = Math.max(0, c.getWidth() / 2);
            int y = Math.max(0, c.getHeight() / 2);
            for (int click = 1; click <= 2; click++) {
                long when = System.currentTimeMillis();
                c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_PRESSED, when, 0,
                        x, y, click, false, MouseEvent.BUTTON1));
                c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_RELEASED, when, 0,
                        x, y, click, false, MouseEvent.BUTTON1));
                c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_CLICKED, when, 0,
                        x, y, click, false, MouseEvent.BUTTON1));
            }
            r[0] = "dispatchDoubleClick";
        });
        return r[0];
    }

    /**
     * Drive a checkbox (or any toggle) to a specific state idempotently: reads
     * the current selected state and only flips it when it differs. Uses
     * {@code doClick()} for the flip so the control's listeners fire.
     */
    public static String setChecked(final Component c, final boolean desired) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            Boolean current = selectedState(c);
            if (current != null && current.booleanValue() == desired) {
                r[0] = "already=" + desired;
                return;
            }
            if (c instanceof AbstractButton) {
                ((AbstractButton) c).doClick(); // fires listeners; we know it differs
                r[0] = "doClick(toggle)";
                return;
            }
            Method setSelected = Reflect.method(c.getClass(), "setSelected", boolean.class);
            if (setSelected != null) {
                Reflect.invoke(c, setSelected, desired);
                r[0] = "setSelected(reflect)";
            }
        });
        return r[0];
    }

    /**
     * Select an option in a combo / poplist / list by its display value, via
     * the model ({@code setSelectedItem} / {@code setSelectedValue} /
     * index match) — never by typing or pixel-clicking the dropdown.
     */
    public static String selectOption(final Component c, final String value) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            if (c instanceof JComboBox) {
                ((JComboBox<?>) c).setSelectedItem(value);
                r[0] = "setSelectedItem";
                return;
            }
            if (c instanceof JList) {
                ((JList<?>) c).setSelectedValue(value, true);
                r[0] = "setSelectedValue";
                return;
            }
            Method setItem = Reflect.method(c.getClass(), "setSelectedItem", Object.class);
            if (setItem != null) {
                Reflect.invoke(c, setItem, value);
                r[0] = "setSelectedItem(reflect)";
                return;
            }
            // Last resort: match the option text to an index and select it.
            int idx = indexOfOption(c, value);
            if (idx >= 0) {
                Method setIdx = Reflect.method(c.getClass(), "setSelectedIndex", int.class);
                if (setIdx != null) {
                    Reflect.invoke(c, setIdx, idx);
                    r[0] = "setSelectedIndex(" + idx + ")";
                }
            }
        });
        return r[0];
    }

    /**
     * Expand or collapse a tree node by model row index
     * ({@code expandRow} / {@code collapseRow}); no clicking the +/- handle.
     */
    public static String expandTreeRow(final Component tree, final int row, final boolean expand)
            throws Exception {
        if (tree == null || row < 0)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            String name = expand ? "expandRow" : "collapseRow";
            Method m = Reflect.method(tree.getClass(), name, int.class);
            if (m != null) {
                Reflect.invoke(tree, m, row);
                r[0] = name + "(" + row + ")";
                return;
            }
            // Fallback: setExpandedState(path, expand) via the row's path.
            Object path = Reflect.invoke(tree, Reflect.method(tree.getClass(), "getPathForRow", int.class), row);
            Method setExpanded = path == null ? null
                    : Reflect.method(tree.getClass(), "setExpandedState", path.getClass(), boolean.class);
            if (setExpanded != null) {
                Reflect.invoke(tree, setExpanded, path, expand);
                r[0] = "setExpandedState(" + expand + ")";
            }
        });
        return r[0];
    }

    // ── widget probes ─────────────────────────────────────────────────────

    private static boolean isRadioLike(Component c) {
        String n = c.getClass().getName().toLowerCase(java.util.Locale.ROOT);
        return n.contains("radio");
    }

    /** Current selected state of a toggle, or {@code null} if not determinable. */
    private static Boolean selectedState(Component c) {
        if (c instanceof AbstractButton)
            return ((AbstractButton) c).isSelected();
        Object v = Reflect.call(c, "isSelected");
        if (v instanceof Boolean)
            return (Boolean) v;
        Object s = Reflect.call(c, "getState");
        if (s instanceof Boolean)
            return (Boolean) s;
        return null;
    }

    /**
     * Match {@code value} against an item-bearing control's options, returning its
     * index or -1.
     */
    private static int indexOfOption(Component c, String value) {
        Object countObj = Reflect.call(c, "getItemCount");
        int count = Reflect.asInt(countObj, -1);
        Method getItemAt = Reflect.method(c.getClass(), "getItemAt", int.class);
        if (count < 0 || getItemAt == null)
            return -1;
        for (int i = 0; i < count; i++) {
            Object item = Reflect.invoke(c, getItemAt, i);
            if (item != null && value != null && value.equals(item.toString()))
                return i;
        }
        return -1;
    }

    /**
     * Replace a field's contents with {@code text}. Uses
     * {@link JTextComponent#setText(String)} or a reflective {@code setText} /
     * {@code setValue}, after requesting focus so Forms item-level validation
     * fires on the correct item.
     */
    public static String setText(final Component c, final String text) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            c.requestFocusInWindow();
            if (c instanceof JTextComponent) {
                ((JTextComponent) c).setText(text);
                r[0] = "setText";
                return;
            }
            Method setText = Reflect.method(c.getClass(), "setText", String.class);
            if (setText != null) {
                Reflect.invoke(c, setText, text);
                r[0] = "setText(reflect)";
                return;
            }
            Method setValue = Reflect.method(c.getClass(), "setValue", Object.class);
            if (setValue != null) {
                Reflect.invoke(c, setValue, text);
                r[0] = "setValue(reflect)";
            }
        });
        return r[0];
    }

    /** Clear a field's contents via the model (no select-all keystrokes). */
    public static String clear(final Component c) throws Exception {
        if (c == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            c.requestFocusInWindow();
            if (c instanceof JTextComponent) {
                ((JTextComponent) c).setText("");
                r[0] = "setText";
                return;
            }
            Method setText = Reflect.method(c.getClass(), "setText", String.class);
            if (setText != null) {
                Reflect.invoke(c, setText, "");
                r[0] = "setText(reflect)";
            }
        });
        return r[0];
    }

    /**
     * Dispatch a named key (or combo) to {@code target}. Builds a
     * {@link KeyEvent} via {@link KeyMap} and dispatches it to the component —
     * never to global OS input. Returns {@code null} if the key name is
     * unknown.
     */
    public static String pressKey(final Component target, final String keyName) throws Exception {
        final KeyMap.Stroke stroke = KeyMap.resolve(keyName);
        if (stroke == null || target == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            long when = System.currentTimeMillis();
            target.dispatchEvent(new KeyEvent(
                    target, KeyEvent.KEY_PRESSED, when, stroke.modifiers,
                    stroke.keyCode, KeyEvent.CHAR_UNDEFINED));
            target.dispatchEvent(new KeyEvent(
                    target, KeyEvent.KEY_RELEASED, when, stroke.modifiers,
                    stroke.keyCode, KeyEvent.CHAR_UNDEFINED));
            r[0] = "dispatchKeyEvent";
        });
        return r[0];
    }

    /**
     * Select a tab by index. Prefers a model method
     * ({@code setSelectedIndex}/{@code selectTab}/…); Oracle EWT {@code TabBar}
     * exposes none, so the fallback computes the tab's own rectangle and
     * dispatches a targeted {@link MouseEvent} to the tab bar at that rect —
     * component-local coordinates, in-JVM, no Robot and no screen cursor. This
     * is the same spot the old pixel Robot-click hit, just delivered as an event.
     */
    public static String selectTab(final Component tabContainer, final int index) throws Exception {
        if (tabContainer == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            for (String name : new String[] {
                    "setSelectedIndex", "selectTab", "setSelectedTab", "setSelectedTabIndex" }) {
                Method m = Reflect.method(tabContainer.getClass(), name, int.class);
                if (m != null) {
                    Reflect.invoke(tabContainer, m, index);
                    r[0] = name;
                    return;
                }
            }
            // EWT TabBar: no index setter — dispatch a click to the tab's rect.
            Rectangle tab = ewtTabBounds(tabContainer, index);
            if (tab != null) {
                dispatchClickAt(tabContainer, tab.x + tab.width / 2, tab.y + tab.height / 2);
                r[0] = "dispatchTabEvent";
            }
        });
        return r[0];
    }

    /**
     * Select a tree row by model index ({@code setSelectionRow}/{@code selectRow}).
     */
    public static String selectTreeRow(final Component tree, final int row) throws Exception {
        if (tree == null)
            return null;
        final String[] r = { null };
        Edt.run(() -> {
            for (String name : new String[] { "setSelectionRow", "selectRow" }) {
                Method m = Reflect.method(tree.getClass(), name, int.class);
                if (m != null) {
                    Reflect.invoke(tree, m, row);
                    r[0] = name;
                    return;
                }
            }
        });
        return r[0];
    }

    // ── helpers ───────────────────────────────────────────────────────────

    /** Dispatch press/release/click to the component centre (local coords). */
    private static void dispatchClick(Component c) {
        dispatchClickAt(c, Math.max(0, c.getWidth() / 2), Math.max(0, c.getHeight() / 2));
    }

    /**
     * Dispatch press/release/click to a component-local point (no Robot, no
     * cursor).
     */
    private static void dispatchClickAt(Component c, int x, int y) {
        long when = System.currentTimeMillis();
        c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_PRESSED, when, 0,
                x, y, 1, false, MouseEvent.BUTTON1));
        c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_RELEASED, when, 0,
                x, y, 1, false, MouseEvent.BUTTON1));
        c.dispatchEvent(new MouseEvent(c, MouseEvent.MOUSE_CLICKED, when, 0,
                x, y, 1, false, MouseEvent.BUTTON1));
    }

    // ── EWT tab geometry (component-local; for event dispatch, not Robot) ──

    /**
     * Local rectangle of tab {@code index} within an EWT tab bar, or {@code null}.
     */
    private static Rectangle ewtTabBounds(Component comp, int index) {
        Rectangle r = queryTabRect(comp, index);
        if (r != null)
            return r;
        // Via getItem(index)/getPage(index) -> getBounds() on the tab item.
        for (Class<?> k = comp.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            for (Method m : k.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 1 && pt[0] == int.class) {
                    String name = m.getName().toLowerCase(Locale.ROOT);
                    if (name.equals("getitem") || name.equals("getpage")) {
                        try {
                            m.setAccessible(true);
                            Object item = m.invoke(comp, index);
                            if (item != null) {
                                Rectangle ir = queryNoArgRect(item);
                                if (ir != null)
                                    return ir;
                            }
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        }
        return null;
    }

    private static Rectangle queryTabRect(Component comp, int index) {
        for (Class<?> k = comp.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            for (Method m : k.getDeclaredMethods()) {
                Class<?>[] pt = m.getParameterTypes();
                if (pt.length == 1 && pt[0] == int.class
                        && Rectangle.class.isAssignableFrom(m.getReturnType())) {
                    String name = m.getName().toLowerCase(Locale.ROOT);
                    if (name.contains("tab") || name.contains("item")
                            || name.contains("rect") || name.contains("bounds")) {
                        try {
                            m.setAccessible(true);
                            Rectangle r = (Rectangle) m.invoke(comp, index);
                            if (r != null)
                                return r;
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        }
        return null;
    }

    private static Rectangle queryNoArgRect(Object item) {
        for (Class<?> k = item.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            for (Method m : k.getDeclaredMethods()) {
                if (m.getParameterTypes().length == 0
                        && Rectangle.class.isAssignableFrom(m.getReturnType())) {
                    String name = m.getName().toLowerCase(Locale.ROOT);
                    if (name.equals("getbounds") || name.equals("getrect") || name.contains("bounds")) {
                        try {
                            m.setAccessible(true);
                            Rectangle r = (Rectangle) m.invoke(item);
                            if (r != null)
                                return r;
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        }
        return null;
    }
}
