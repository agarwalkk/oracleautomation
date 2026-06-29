package com.pyebsdom.agent;

import javax.swing.SwingUtilities;
import java.awt.Component;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Sets Oracle Forms field values through the <b>Forms handler / event
 * pipeline</b>
 * rather than a Robot — so the change reaches the server, marks the item dirty,
 * and fires validation (WHEN-VALIDATE-ITEM / POST-CHANGE), instead of only
 * repainting the widget.
 *
 * <h3>Why the handler, not the widget</h3>
 * Forms is client/server: the EWT widget is a thin view. Calling the widget's
 * visual setter ({@code VTextField.setText}, {@code LWCheckbox.setState})
 * updates pixels but can leave the server-side {@code :BLOCK.ITEM} unchanged
 * and
 * skip triggers. The faithful path is the item's handler
 * ({@code oracle.forms.handler.*}, reached via the component's {@code mHandler}
 * field), which owns the model value and the server message.
 *
 * <h3>Text fields — confirmed</h3>
 * {@code oracle.forms.ui.VTextField} → {@code mHandler} →
 * {@code oracle.forms.handler.TextFieldItem}, which exposes
 * {@code setTextValue(String)}, {@code setDirty(boolean)},
 * {@code sendFinalMessage(boolean)}, {@code sendFocusEvent(boolean,boolean)},
 * {@code isLocked()}.
 *
 * <h3>Checkboxes — confirmed</h3>
 * The Forms wrapper {@code oracle.apps.fnd.ui.CheckBox} holds {@code mHandler}
 * →
 * {@code oracle.forms.handler.CheckboxItem}. That handler has no direct value
 * setter — it reacts to {@code itemStateChanged(ItemEvent)}. So the faithful
 * change is to fire the inner {@code LWCheckbox}'s event via
 * {@code simulatePush()} (which routes through the handler to the server), then
 * {@code sendFinalMessage(true)} to flush. {@link #setChecked} accepts either
 * the wrapper or the inner widget and normalises between them.
 *
 * <p>
 * Every action runs on the EDT and self-verifies by reading the value back.
 */
public final class FieldActuator {

    private FieldActuator() {
    }

    /** Outcome of a field actuation, with before/after state for diagnosis. */
    public static final class Result {
        public boolean ok;
        public String message;
        public String before;
        public String after;

        static Result fail(String m) {
            Result r = new Result();
            r.ok = false;
            r.message = m;
            return r;
        }
    }

    // ── text ──────────────────────────────────────────────────────────────

    /**
     * Set a text item's value via its Forms handler and flush to the server.
     *
     * <p>
     * Sequence: {@code handler.setTextValue(value)} → {@code setDirty(true)} →
     * flush. The flush call is the one runtime unknown — {@code sendFinalMessage}
     * is the primary; if your form validates only on navigation, switch
     * {@link #FLUSH} to {@code sendFocusEvent}. The method reads the value back
     * so you can see empirically whether it took.
     */
    public static Result setText(final Component field, final String value) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (field == null)
                    return Result.fail("field is null");
                Object handler = mHandler(field);
                if (handler == null)
                    return Result.fail("no mHandler on " + field.getClass().getName()
                            + " — not a Forms item (or wrong layer)");

                Object locked = invoke0(handler, "isLocked");
                if (Boolean.TRUE.equals(locked))
                    return Result.fail("item is locked (read-only at the server)");

                Result r = new Result();
                r.before = str(invoke0(field, "getText"));

                if (!invoke1(handler, "setTextValue", String.class, value))
                    return Result.fail("setTextValue(String) not found on handler "
                            + handler.getClass().getName());
                invoke1(handler, "setDirty", boolean.class, Boolean.TRUE);
                flush(handler); // server round-trip + validation

                r.after = str(invoke0(field, "getText"));
                Object stillDirty = invoke0(handler, "isDirty");
                r.ok = value.equals(r.after);
                r.message = r.ok
                        ? "setText ok (dirty=" + stillDirty + ")"
                        : "value not reflected after set (before='" + r.before
                                + "', after='" + r.after + "'); consider switching FLUSH"
                                + " or adding a widget setText";
                return r;
            }
        });
    }

    /** Flush strategy — see {@link #setText}. Swap the body to retune. */
    private static final String FLUSH = "sendFinalMessage";

    private static void flush(Object handler) {
        if ("sendFinalMessage".equals(FLUSH)) {
            invoke1(handler, "sendFinalMessage", boolean.class, Boolean.TRUE);
        } else {
            // Emulate focus-lost: (gained=false, temporary=false).
            Method m = findMethod(handler.getClass(), "sendFocusEvent", 2);
            if (m != null) {
                try {
                    m.invoke(handler, Boolean.FALSE, Boolean.FALSE);
                } catch (Throwable ignored) {
                }
            }
        }
    }

    // ── checkbox (INTERIM — see class doc) ────────────────────────────────

    /**
     * Set a checkbox to {@code checked} through the Forms event pipeline.
     * Accepts the wrapper ({@code oracle.apps.fnd.ui.CheckBox}) or the inner
     * {@code LWCheckbox}. Toggles via {@code simulatePush()} (fires
     * {@code itemStateChanged} → server), then flushes with
     * {@code sendFinalMessage(true)}, and self-verifies the state.
     */
    public static Result setChecked(final Component checkbox, final boolean checked) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (checkbox == null)
                    return Result.fail("checkbox is null");

                // Normalise to: inner LWCheckbox (getState/simulatePush) + Forms
                // wrapper oracle.apps.fnd.ui.CheckBox (mHandler -> CheckboxItem).
                Component wrapper = checkbox;
                Object inner = invoke0(checkbox, "getLWCheckBox");
                if (inner == null && findMethod(checkbox.getClass(), "getState", 0) != null) {
                    inner = checkbox; // handed the inner widget
                    wrapper = checkbox.getParent(); // its oracle.apps.fnd.ui.CheckBox
                }
                if (inner == null)
                    return Result.fail("could not locate the checkbox widget "
                            + "(no getLWCheckBox()/getState() on " + checkbox.getClass().getName() + ")");

                Object handler = (wrapper != null) ? mHandler(wrapper) : null;

                boolean state = Boolean.TRUE.equals(invoke0(inner, "getState"));
                Result r = new Result();
                r.before = String.valueOf(state);
                if (state != checked) {
                    // Faithful toggle: fires ItemEvent -> CheckboxItem.itemStateChanged -> server.
                    Method push = findMethod(inner.getClass(), "simulatePush", 0);
                    if (push != null) {
                        try {
                            push.invoke(inner);
                        } catch (Throwable ignored) {
                        }
                    } else if (!invoke2(inner, "setStateInternal", boolean.class, Boolean.valueOf(checked),
                            boolean.class, Boolean.TRUE)) {
                        invoke1(inner, "setState", boolean.class, Boolean.valueOf(checked)); // visual-only last resort
                    }
                    // Flush to the server (no-op if itemStateChanged already sent).
                    if (handler != null)
                        invoke1(handler, "sendFinalMessage", boolean.class, Boolean.TRUE);
                }
                boolean now = Boolean.TRUE.equals(invoke0(inner, "getState"));
                r.after = String.valueOf(now);
                r.ok = (now == checked);
                r.message = r.ok ? "setChecked ok" : "state did not change to " + checked;
                return r;
            }
        });
    }

    // ── EDT plumbing ──────────────────────────────────────────────────────

    private interface EdtCall {
        Result run();
    }

    private static Result onEdt(final EdtCall call) {
        final AtomicReference<Result> out = new AtomicReference<Result>();
        Runnable work = new Runnable() {
            public void run() {
                try {
                    out.set(call.run());
                } catch (Throwable t) {
                    out.set(Result.fail(t.getClass().getName() + ": " + t.getMessage()));
                }
            }
        };
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                work.run();
            } else {
                SwingUtilities.invokeAndWait(work);
            }
        } catch (Throwable t) {
            return Result.fail("EDT dispatch failed: " + t);
        }
        Result r = out.get();
        return r != null ? r : Result.fail("no result");
    }

    // ── reflection helpers (mirror TreeItemActuator) ──────────────────────

    /** The Forms handler behind a UI component (its {@code mHandler} field). */
    private static Object mHandler(Component c) {
        for (Class<?> k = c.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Field f = k.getDeclaredField("mHandler");
                f.setAccessible(true);
                Object v = f.get(c);
                if (v != null)
                    return v;
            } catch (NoSuchFieldException ignored) {
            } catch (Throwable t) {
                return null;
            }
        }
        return null;
    }

    private static Object invoke0(Object o, String name) {
        if (o == null)
            return null;
        Method m = findMethod(o.getClass(), name, 0);
        if (m == null)
            return null;
        try {
            return m.invoke(o);
        } catch (Throwable t) {
            return null;
        }
    }

    private static boolean invoke1(Object o, String name, Class<?> p, Object arg) {
        Method m = findMethodSig(o.getClass(), name, p);
        if (m == null)
            return false;
        try {
            m.invoke(o, arg);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    private static boolean invoke2(Object o, String name, Class<?> p1, Object a1, Class<?> p2, Object a2) {
        Method m = findMethodSig(o.getClass(), name, p1, p2);
        if (m == null)
            return false;
        try {
            m.invoke(o, a1, a2);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    /** First method by name + parameter count, declared-first up the hierarchy. */
    private static Method findMethod(Class<?> c, String name, int argCount) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                for (Method m : k.getDeclaredMethods()) {
                    if (m.getName().equals(name) && m.getParameterCount() == argCount) {
                        m.setAccessible(true);
                        return m;
                    }
                }
            } catch (Throwable ignored) {
            }
        }
        try {
            for (Method m : c.getMethods()) {
                if (m.getName().equals(name) && m.getParameterCount() == argCount) {
                    m.setAccessible(true);
                    return m;
                }
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    /** Method by name + exact parameter types (declared-first up the hierarchy). */
    private static Method findMethodSig(Class<?> c, String name, Class<?>... params) {
        for (Class<?> k = c; k != null && k != Object.class; k = k.getSuperclass()) {
            try {
                Method m = k.getDeclaredMethod(name, params);
                m.setAccessible(true);
                return m;
            } catch (NoSuchMethodException ignored) {
            } catch (Throwable ignored) {
            }
        }
        try {
            Method m = c.getMethod(name, params);
            m.setAccessible(true);
            return m;
        } catch (Throwable ignored) {
        }
        return null;
    }

    private static String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }
}
