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
 * <h3>Buttons — confirmed</h3>
 * {@code oracle.apps.fnd.ui.Button} ({@code ButtonItem}) and
 * {@code oracle.forms.ui.VButton} ({@code IconicButtonItem}) both expose
 * {@code simulatePush()} on the widget, which fires {@code actionPerformed} →
 * handler → {@code sendButtonPressedMessage} → server. {@link #press} uses it,
 * with the handler's {@code sendButtonPressedMessage} as a fallback.
 *
 * <h3>Poplists &amp; T-lists — confirmed</h3>
 * {@code VPopList} ({@code PopListItem}) and {@code VTList}
 * ({@code TListItem}).
 * {@link #selectValue} sets the value on the widget — {@code select(String)}
 * for
 * poplists; for index-only lists it resolves value→row index and calls
 * {@code deselectAll()} + {@code select(int)} — then flushes via
 * {@code sendFinalMessage(true)} and verifies via {@code getSelectedItem()}.
 *
 * <h3>Radio groups — confirmed</h3>
 * Each EBS radio option is an {@code oracle.apps.fnd.ui.RadioGroup} wrapping an
 * {@code ExtendedCheckbox} ({@code getCheckBox()}); {@code mHandler} is
 * {@code RadioButtonItem}. {@link #selectRadio} pushes that option's checkbox
 * ({@code simulatePush()} → server; the shared {@code VRadioGroup} deselects
 * siblings), then flushes.
 *
 * <h3>LOV (open) — confirmed</h3>
 * Any LOV-enabled item exposes {@code sendLOVButtonPressedMessage()} on its
 * handler. {@link #openLov} fires it to open the chooser window, whose rows are
 * a {@code ListView}/tree of items — select a row then press OK to finish. (For
 * a known unique value, {@link #setText} often resolves it via auto-validation
 * without opening the popup at all.)
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

    // ── button ────────────────────────────────────────────────────────────

    /**
     * Press a Forms button — coordinate-free. Works for both
     * {@code oracle.apps.fnd.ui.Button} (ButtonItem) and
     * {@code oracle.forms.ui.VButton} (IconicButtonItem) via the widget's
     * {@code simulatePush()} (fires actionPerformed → handler → server). Falls
     * back to the handler's {@code sendButtonPressedMessage(MessageHandler)}.
     * No read-back to verify (a press triggers server logic, not a value), so
     * {@code ok} means the press was dispatched.
     */
    public static Result press(final Component button) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (button == null)
                    return Result.fail("button is null");
                Result r = new Result();
                r.before = str(invoke0(button, "getLabel"));

                // Primary: the widget's own push event.
                Object widget = button;
                Method push = findMethod(button.getClass(), "simulatePush", 0);
                if (push == null) {
                    Object inner = invoke0(button, "getLWButton");
                    if (inner != null) {
                        Method p = findMethod(inner.getClass(), "simulatePush", 0);
                        if (p != null) {
                            widget = inner;
                            push = p;
                        }
                    }
                }
                if (push != null) {
                    try {
                        push.invoke(widget);
                        r.ok = true;
                        r.after = r.before;
                        r.message = "press ok (simulatePush)";
                        return r;
                    } catch (Throwable ignored) {
                        // fall through to the handler path
                    }
                }

                // Fallback: ask the handler to send the button-pressed message.
                Object h = mHandler(button);
                if (h != null) {
                    Object mh = invoke0(h, "getHandler"); // MessageHandler getHandler()
                    Method spm = findMethod(h.getClass(), "sendButtonPressedMessage", 1);
                    if (spm != null && mh != null) {
                        try {
                            spm.invoke(h, mh);
                            r.ok = true;
                            r.after = r.before;
                            r.message = "press ok (sendButtonPressedMessage)";
                            return r;
                        } catch (Throwable ignored) {
                        }
                    }
                }
                return Result.fail("no simulatePush()/sendButtonPressedMessage path on "
                        + button.getClass().getName());
            }
        });
    }

    // ── poplist / dropdown ────────────────────────────────────────────────

    /**
     * Select a value in a Forms poplist ({@code VPopList}) or T-list
     * ({@code VTList}). Tries {@code select(String)} (poplist); otherwise
     * resolves the value to a row index and calls {@code deselectAll()} +
     * {@code select(int)}/{@code setSelectedIndex(int)} (T-list). A purely
     * numeric {@code value} is treated as an index. Flushes via
     * {@code sendFinalMessage(true)} and verifies via {@code getSelectedItem()}.
     */
    public static Result selectValue(final Component poplist, final String value) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (poplist == null)
                    return Result.fail("poplist is null");
                Object handler = mHandler(poplist);
                if (Boolean.TRUE.equals(invoke0(handler, "isLocked")))
                    return Result.fail("item is locked (read-only at the server)");

                Result r = new Result();
                r.before = str(invoke0(poplist, "getSelectedItem"));
                if (value.equals(r.before)) {
                    r.ok = true;
                    r.after = r.before;
                    r.message = "already '" + value + "'";
                    return r;
                }

                // 1) Poplist: select by the visible value string.
                boolean set = invoke1(poplist, "select", String.class, value);
                // 2) Index-only lists (T-list): resolve value -> row index, then
                // deselectAll() + select(int)/setSelectedIndex(int).
                if (!set) {
                    int idx = indexOfItem(poplist, value);
                    if (idx < 0) {
                        try {
                            idx = Integer.parseInt(value.trim());
                        } catch (NumberFormatException ignore) {
                            idx = -1;
                        }
                    }
                    if (idx >= 0) {
                        invoke0(poplist, "deselectAll"); // no-op if absent (single-select)
                        Integer ix = Integer.valueOf(idx);
                        set = invoke1(poplist, "select", int.class, ix)
                                || invoke1(poplist, "setSelectedIndex", int.class, ix);
                    }
                }
                if (!set)
                    return Result.fail("could not select '" + value + "' on "
                            + poplist.getClass().getName()
                            + " (no select(String)/select(int) match)");

                // Flush the change to the server (+ validation).
                if (handler != null)
                    invoke1(handler, "sendFinalMessage", boolean.class, Boolean.TRUE);

                r.after = str(invoke0(poplist, "getSelectedItem"));
                r.ok = value.equals(r.after);
                r.message = r.ok
                        ? "selectValue ok"
                        : "value not selected (after='" + r.after + "'); check the exact option text";
                return r;
            }
        });
    }

    /** First row index whose item text equals {@code value}, or -1. */
    private static int indexOfItem(Object list, String value) {
        Object cntObj = invoke0(list, "getItemCount");
        if (!(cntObj instanceof Integer))
            return -1;
        int cnt = ((Integer) cntObj).intValue();
        Method getItem = findMethodSig(list.getClass(), "getItem", int.class);
        if (getItem == null)
            return -1;
        for (int i = 0; i < cnt; i++) {
            try {
                Object item = getItem.invoke(list, Integer.valueOf(i));
                if (item != null && value.equals(String.valueOf(item)))
                    return i;
            } catch (Throwable ignored) {
            }
        }
        return -1;
    }

    // ── radio group ───────────────────────────────────────────────────────

    /**
     * Select a Forms radio option. The recorder resolves the specific option
     * ({@code oracle.apps.fnd.ui.RadioGroup}); this pushes that option's
     * {@code ExtendedCheckbox} ({@code getCheckBox()}) — the shared
     * {@code VRadioGroup} deselects its siblings — then flushes. Accepts the
     * option wrapper or the inner checkbox.
     */
    public static Result selectRadio(final Component radio) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (radio == null)
                    return Result.fail("radio is null");

                Object inner = invoke0(radio, "getCheckBox");
                if (inner == null && findMethod(radio.getClass(), "getState", 0) != null)
                    inner = radio; // handed the inner checkbox directly
                if (inner == null)
                    return Result.fail("could not locate the radio's checkbox "
                            + "(no getCheckBox()/getState() on " + radio.getClass().getName() + ")");

                Object handler = mHandler(radio);
                Result r = new Result();
                Object beforeObj = invoke0(inner, "getState");
                r.before = str(beforeObj);
                boolean acted = Boolean.TRUE.equals(beforeObj); // already selected → nothing to do
                if (!acted) {
                    Method push = findMethod(inner.getClass(), "simulatePush", 0);
                    if (push != null) {
                        try {
                            push.invoke(inner);
                            acted = true;
                        } catch (Throwable ignored) {
                        }
                    }
                    if (!acted) {
                        acted = invoke2(inner, "setStateInternal", boolean.class, Boolean.TRUE,
                                boolean.class, Boolean.TRUE)
                                || invoke1(inner, "setState", boolean.class, Boolean.TRUE);
                    }
                    if (handler != null)
                        invoke1(handler, "sendFinalMessage", boolean.class, Boolean.TRUE);
                }

                Object nowObj = invoke0(inner, "getState");
                if (nowObj instanceof Boolean) {
                    r.after = str(nowObj);
                    r.ok = ((Boolean) nowObj).booleanValue();
                    r.message = r.ok ? "selectRadio ok" : "radio did not select";
                } else {
                    r.after = null;
                    r.ok = acted; // can't read back — trust the dispatched push
                    r.message = acted ? "selectRadio dispatched (no read-back)"
                            : "no select path on " + radio.getClass().getName();
                }
                return r;
            }
        });
    }

    // ── LOV (open) ────────────────────────────────────────────────────────

    /**
     * Open a field's List-of-Values popup via its handler
     * ({@code sendLOVButtonPressedMessage()}) — no Robot. Only fires when the
     * item actually has an LOV. The popup appears as a separate window whose
     * rows are a {@code ListView}/tree; select a row then press OK. Returns once
     * the open was dispatched (the window opens asynchronously).
     */
    public static Result openLov(final Component field) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (field == null)
                    return Result.fail("field is null");
                Object h = mHandler(field);
                if (h == null)
                    return Result.fail("no mHandler on " + field.getClass().getName());
                boolean hasLov = Boolean.TRUE.equals(invoke0(h, "isLOVButtonDisplayed"))
                        || Boolean.TRUE.equals(invoke0(h, "hasLovButton"));
                if (!hasLov)
                    return Result.fail("item has no LOV button (isLOVButtonDisplayed=false)");
                Method m = findMethod(h.getClass(), "sendLOVButtonPressedMessage", 0);
                if (m == null)
                    return Result.fail("no sendLOVButtonPressedMessage() on handler "
                            + h.getClass().getName());
                try {
                    m.invoke(h);
                } catch (Throwable t) {
                    return Result.fail("sendLOVButtonPressedMessage failed: " + t);
                }
                Result r = new Result();
                r.ok = true;
                r.message = "openLov dispatched";
                return r;
            }
        });
    }

    /**
     * Select a row in an open LOV chooser ({@code oracle.forms.ui.ListView}) by
     * matching {@code value} against the row's cell text — exact cell match
     * first, then a row-contains fallback, and a numeric index as a last resort.
     * Uses the widget's {@code setSelectedRow(int)} (no Robot). This only
     * highlights the row; the caller presses OK (via {@code pressButton}) to
     * commit the value into the field.
     */
    public static Result lovSelect(final Component listView, final String value) {
        return onEdt(new EdtCall() {
            public Result run() {
                if (listView == null)
                    return Result.fail("listView is null");
                Object rcObj = invoke0(listView, "getRowCount");
                Object ccObj = invoke0(listView, "getColumnCount");
                if (!(rcObj instanceof Integer) || !(ccObj instanceof Integer))
                    return Result.fail("no getRowCount()/getColumnCount() on "
                            + listView.getClass().getName());
                int rows = ((Integer) rcObj).intValue();
                int cols = ((Integer) ccObj).intValue();
                Method getCell = findMethodSig(listView.getClass(), "getCellData", int.class, int.class);
                if (getCell == null)
                    return Result.fail("no getCellData(int,int) on " + listView.getClass().getName());

                String want = value == null ? "" : value.trim();
                int match = -1;
                int containsMatch = -1;
                for (int row = 0; row < rows && match < 0; row++) {
                    StringBuilder joined = new StringBuilder();
                    for (int col = 0; col < cols; col++) {
                        Object cell;
                        try {
                            cell = getCell.invoke(listView, Integer.valueOf(col), Integer.valueOf(row));
                        } catch (Throwable t) {
                            cell = null;
                        }
                        String s = cell == null ? "" : String.valueOf(cell).trim();
                        if (s.equalsIgnoreCase(want)) {
                            match = row; // exact cell match wins
                            break;
                        }
                        if (joined.length() > 0)
                            joined.append(' ');
                        joined.append(s);
                    }
                    if (match < 0 && containsMatch < 0 && !want.isEmpty()
                            && joined.toString().toLowerCase().contains(want.toLowerCase()))
                        containsMatch = row;
                }
                if (match < 0)
                    match = containsMatch;
                if (match < 0) {
                    try {
                        int idx = Integer.parseInt(want);
                        if (idx >= 0 && idx < rows)
                            match = idx;
                    } catch (NumberFormatException ignore) {
                    }
                }
                if (match < 0)
                    return Result.fail("no LOV row matching '" + value + "' (" + rows + " rows)");

                Result r = new Result();
                r.before = str(invoke0(listView, "getSelectedRow"));
                if (!invoke1(listView, "setSelectedRow", int.class, Integer.valueOf(match)))
                    return Result.fail("setSelectedRow(int) failed on " + listView.getClass().getName());

                Object now = invoke0(listView, "getSelectedRow");
                r.after = str(now);
                r.ok = (now instanceof Integer) && ((Integer) now).intValue() == match;
                r.message = r.ok
                        ? "lovSelect ok (row " + match + ") — press OK to commit"
                        : "selected row " + match + " but getSelectedRow=" + now;
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
        runOnEdt(work);
        Result r = out.get();
        return r != null ? r : Result.fail("no result");
    }

    /**
     * Run on the AWT EDT, but tolerate the attach-listener thread having no AWT
     * AppContext (where {@code invokeLater}/{@code invokeAndWait} throw NPE) by
     * running the work directly — exactly as {@code ActionExecutor.invokeOnEDT}
     * does for the other commands.
     */
    private static void runOnEdt(Runnable r) {
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                r.run();
            } else {
                final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);
                SwingUtilities.invokeLater(new Runnable() {
                    public void run() {
                        try {
                            r.run();
                        } finally {
                            latch.countDown();
                        }
                    }
                });
                latch.await();
            }
        } catch (NullPointerException appContextMissing) {
            r.run(); // no AppContext on the attach thread → run directly
        } catch (InterruptedException ie) {
            Thread.currentThread().interrupt();
        }
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
