package com.pyebsdom.agent;

import javax.swing.SwingUtilities;
import java.awt.Component;
import java.lang.reflect.Method;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Drives an Oracle EWT {@code oracle.ewt.dTree.DTree} programmatically —
 * <b>select / expand / collapse / activate</b> a node identified by its
 * {@code treePath}, with <b>no Robot click</b> and no screen coordinates.
 *
 * <p>
 * Every method/field this relies on was confirmed against the live reflective
 * surface (probe_report.py / ReflectionProbe):
 * <ul>
 * <li>navigation: {@code DTree.getRoot()} → item, then
 * {@code getItemCount()} / {@code getItem(int)} / {@code getLabel()};</li>
 * <li>expand/collapse: {@code DTreeItem.setExpanded(boolean)} (guarded by
 * {@code isExpandable()} / {@code isExpanded()});</li>
 * <li>select: {@code DTree.getSelection()} →
 * {@code oracle.ewt.dTree.DTreeSingleSelection.selectItem(DTreeItem)}, plus
 * {@code DTree.setFocusedItem(DTreeItem)};</li>
 * <li>activate: {@code DTreeItem.activate()} or
 * {@code DTree.itemActivate(DTreeItem)};</li>
 * <li>visibility: {@code DTree.makeVisible(DTreeItem)}.</li>
 * </ul>
 *
 * <p>
 * <b>Threading:</b> all calls mutate live Swing/AWT state and therefore run on
 * the EDT (marshalled with {@link SwingUtilities#invokeAndWait}), mirroring
 * {@code TreeItemExpander}. <b>Reflection:</b> resolved with
 * {@code getDeclaredMethods()} up the hierarchy + {@code setAccessible(true)},
 * because EWT declares many of these on non-public base classes.
 *
 * <h3>Wiring</h3>
 * Call {@link #act(Component, String, Op)} from {@code ActionExecutor} for a
 * new command verb (e.g. {@code "treeAction"} carrying a tree locator, a
 * {@code treePath}, and an op). Resolve the {@code DTree} component with the
 * existing locator/{@code ComponentResolver} path, then hand it here.
 */
public final class TreeItemActuator {

    private TreeItemActuator() {
    }

    public enum Op {
        SELECT, EXPAND, COLLAPSE, ACTIVATE
    }

    /** Outcome of an actuation. */
    public static final class Result {
        public boolean ok;
        public String matchedLabel; // label of the resolved node (null if unresolved)
        public String message; // diagnostic / error detail

        static Result fail(String m) {
            Result r = new Result();
            r.ok = false;
            r.message = m;
            return r;
        }

        static Result done(String label, String m) {
            Result r = new Result();
            r.ok = true;
            r.matchedLabel = label;
            r.message = m;
            return r;
        }
    }

    /**
     * Resolve the node named by {@code treePath} and perform {@code op} on it.
     *
     * @param tree     the {@code oracle.ewt.dTree.DTree} component
     * @param treePath root→leaf labels, "/"-separated. A leading segment equal
     *                 to the tree's own name (e.g. {@code "Orders Tree"}) is
     *                 tolerated: {@code "Orders Tree/Personal Folders"} and
     *                 {@code "Personal Folders"} both resolve.
     */
    public static Result act(final Component tree, final String treePath, final Op op) {
        final AtomicReference<Result> out = new AtomicReference<Result>();
        Runnable work = new Runnable() {
            public void run() {
                try {
                    out.set(actOnEdt(tree, treePath, op));
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

    private static Result actOnEdt(Component tree, String treePath, Op op) {
        if (tree == null)
            return Result.fail("tree is null");
        if (treePath == null || treePath.trim().isEmpty())
            return Result.fail("empty treePath");

        Object root = invokeQuiet(tree, "getRoot");
        if (root == null)
            return Result.fail("getRoot() returned null");

        String[] segs = treePath.split("/");
        int start = 0;
        // Tolerate a leading tree-name prefix that is not itself a root child.
        if (segs.length > 1 && childByLabel(root, stripLevel(segs[0].trim())) == null) {
            start = 1;
        }

        Object item = root;
        StringBuilder walked = new StringBuilder();
        for (int i = start; i < segs.length; i++) {
            String want = stripLevel(segs[i].trim());
            if (want.isEmpty())
                continue;
            // Deferring parents instantiate children only once expanded.
            if (i > start)
                setExpanded(item, true);
            Object child = childByLabel(item, want);
            if (child == null) {
                setExpanded(item, true); // one forced retry
                child = childByLabel(item, want);
            }
            if (child == null) {
                String where = walked.length() == 0 ? "<root>" : walked.toString();
                return Result.fail("no child '" + want + "' under '" + where + "'");
            }
            item = child;
            if (walked.length() > 0)
                walked.append('/');
            walked.append(want);
        }
        if (item == root)
            return Result.fail("path resolved to the root, not a node");

        String label = str(invokeQuiet(item, "getLabel"));
        switch (op) {
            case EXPAND:
                setExpanded(item, true);
                break;
            case COLLAPSE:
                setExpanded(item, false);
                break;
            case SELECT:
                doSelect(tree, item);
                break;
            case ACTIVATE:
                doActivate(tree, item);
                break;
        }
        invokeQuiet1(tree, "makeVisible", item); // best-effort scroll-into-view
        return Result.done(label, op + " ok on '" + label + "'");
    }

    // ── tree-specific operations ──────────────────────────────────────────

    private static void setExpanded(Object item, boolean expand) {
        Object expandable = invokeQuiet(item, "isExpandable");
        if (expandable instanceof Boolean && !((Boolean) expandable).booleanValue())
            return; // leaf — nothing to expand/collapse
        Method m = findMethod(item.getClass(), "setExpanded", 1);
        if (m != null) {
            try {
                m.invoke(item, Boolean.valueOf(expand));
            } catch (Throwable ignored) {
            }
        }
    }

    private static void doSelect(Object tree, Object item) {
        Object sel = invokeQuiet(tree, "getSelection"); // DTreeSingleSelection
        if (sel != null) {
            Method m = findMethod(sel.getClass(), "selectItem", 1);
            if (m != null) {
                try {
                    m.invoke(sel, item);
                } catch (Throwable ignored) {
                }
            }
        }
        Method f = findMethod(tree.getClass(), "setFocusedItem", 1);
        if (f != null) {
            try {
                f.invoke(tree, item);
            } catch (Throwable ignored) {
            }
        }
    }

    private static void doActivate(Object tree, Object item) {
        Method a = findMethod(item.getClass(), "activate", 0);
        if (a != null) {
            try {
                a.invoke(item);
                return;
            } catch (Throwable ignored) {
            }
        }
        Method t = findMethod(tree.getClass(), "itemActivate", 1);
        if (t != null) {
            try {
                t.invoke(tree, item);
            } catch (Throwable ignored) {
            }
        }
    }

    // ── child lookup by visible label ─────────────────────────────────────

    private static Object childByLabel(Object parent, String wantLabel) {
        if (wantLabel == null)
            return null;
        Object cntObj = invokeQuiet(parent, "getItemCount");
        if (!(cntObj instanceof Integer))
            return null;
        int cnt = ((Integer) cntObj).intValue();
        if (cnt <= 0)
            return null;
        Method getItem = findMethod(parent.getClass(), "getItem", 1);
        if (getItem == null)
            return null;
        for (int i = 0; i < cnt; i++) {
            Object child;
            try {
                child = getItem.invoke(parent, Integer.valueOf(i));
            } catch (Throwable t) {
                continue;
            }
            if (child == null)
                continue;
            String lbl = stripLevel(str(invokeQuiet(child, "getLabel")));
            if (lbl != null && lbl.equalsIgnoreCase(wantLabel))
                return child;
        }
        return null;
    }

    // ── reflection helpers (mirror TreeItemExpander) ──────────────────────

    /** Invoke a 0-arg method by name; null on any failure. */
    private static Object invokeQuiet(Object o, String name) {
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

    /** Invoke a 1-arg method by name; null on any failure. */
    private static Object invokeQuiet1(Object o, String name, Object arg) {
        if (o == null)
            return null;
        Method m = findMethod(o.getClass(), name, 1);
        if (m == null)
            return null;
        try {
            return m.invoke(o, arg);
        } catch (Throwable t) {
            return null;
        }
    }

    /**
     * First method matching name + parameter count, searched declared-first up
     * the whole hierarchy (so non-public EWT bases are found) then via public
     * {@code getMethods()}; always made accessible.
     */
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

    /** Oracle EWT sometimes renders labels as "Level 0 Foo" — drop that prefix. */
    private static String stripLevel(String label) {
        if (label == null)
            return null;
        String s = label.trim();
        if (s.startsWith("Level ")) {
            int sp = s.indexOf(' ', 6);
            if (sp > 0 && sp + 1 < s.length())
                return s.substring(sp + 1).trim();
        }
        return s;
    }

    private static String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }
}
