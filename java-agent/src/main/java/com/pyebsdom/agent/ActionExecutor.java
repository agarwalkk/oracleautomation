package com.pyebsdom.agent;

import javax.swing.*;
import java.awt.*;
import java.awt.event.KeyEvent;
import java.awt.image.BufferedImage;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Executes UI actions against a resolved {@link Component} in the target JVM.
 *
 * <h3>EDT / Robot threading model</h3>
 * All component-state reads and focus requests are performed on the AWT EDT
 * via {@link SwingUtilities#invokeAndWait}. After the EDT work returns,
 * {@link SafeRobot} is called on the <em>agent thread</em> (not the EDT).
 * This avoids deadlocks that could occur if Robot waited for EDT-generated
 * events while we held the EDT lock.
 *
 * <h3>Result format</h3>
 * Each action returns a JSON string:
 * 
 * <pre>
 * {
 *   "status": "ok",
 *   "command": "&lt;name&gt;",
 *   "component": { "name": "...", "screenX": 123, "screenY": 456, ... }
 * }
 * </pre>
 * 
 * On error the standard {@link JsonUtil#errorResult} envelope is returned.
 */
public final class ActionExecutor {

    private ActionExecutor() {
    }

    // ── focus ─────────────────────────────────────────────────────────────

    /**
     * Request keyboard focus on the resolved component.
     *
     * <p>
     * First attempts {@link Component#requestFocusInWindow()}. If that
     * fails or the component is not yet focusable, falls back to a Robot
     * left-click at the component's centre.
     */
    public static String executeFocus(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "focus");
        Point centre = screenCentre(comp);

        requestFocusBestEffort(comp);

        // If component is not showing, no Robot fallback needed
        if (centre != null && !comp.isFocusOwner()) {
            SafeRobot robot = newSafeRobot();
            robot.click(centre.x, centre.y);
            robot.delay(SafeRobot.POST_ACTION_DELAY_MS);
        }

        return okResult("focus", comp);
    }

    // ── click ─────────────────────────────────────────────────────────────

    /**
     * Left-click the centre of the resolved component using Robot.
     */
    public static String executeClick(AgentCommand cmd) throws Exception {
        // Tree items (EWT DTree) are accessibility nodes, not AWT components, so
        // resolveOrThrow cannot find a component for them. When the click targets
        // a treePath, act via the item's AccessibleAction — pure DOM, no pixels.
        // Runs BEFORE resolveOrThrow (which would otherwise throw "not found").
        String treePath = cmd.getParam("locatortreepath");
        if (treePath != null && !treePath.trim().isEmpty()) {
            String viaA11y = clickTreeItemViaAccessibility(treePath);
            if (viaA11y != null)
                return viaA11y;
        }

        Component comp = resolveOrThrow(cmd, "click");
        Point centre = null;

        String tabIndexStr = cmd.getParam("tab_index");
        if (tabIndexStr != null && !tabIndexStr.isEmpty()) {
            final int index = Integer.parseInt(tabIndexStr);

            // Resolve the actual TabBar component from comp (e.g. if comp is FormsTabPanel)
            final AtomicReference<Component> targetRef = new AtomicReference<>(comp);
            invokeOnEDT(() -> {
                try {
                    java.lang.reflect.Method getTabBar = comp.getClass().getMethod("getTabBar");
                    Component tb = (Component) getTabBar.invoke(comp);
                    if (tb != null) {
                        targetRef.set(tb);
                    }
                } catch (Exception ignored) {
                }
            });
            Component target = targetRef.get();

            final AtomicReference<Rectangle> rectRef = new AtomicReference<>();
            invokeOnEDT(() -> {
                rectRef.set(getEwtTabBounds(target, index));
            });
            Rectangle rect = rectRef.get();
            if (rect != null) {
                final AtomicReference<Point> locRef = new AtomicReference<>();
                invokeOnEDT(() -> {
                    if (target.isShowing()) {
                        locRef.set(target.getLocationOnScreen());
                    }
                });
                Point loc = locRef.get();
                if (loc != null) {
                    centre = new Point(loc.x + rect.x + rect.width / 2,
                            loc.y + rect.y + rect.height / 2);
                }
            } else {
                // Fallback: divide width of target evenly
                String countStr = cmd.getParam("tab_count");
                int count = 1;
                if (countStr != null && !countStr.isEmpty()) {
                    count = Integer.parseInt(countStr);
                }
                if (count > 0) {
                    final AtomicReference<Point> locRef = new AtomicReference<>();
                    final int finalCount = count;
                    invokeOnEDT(() -> {
                        if (target.isShowing()) {
                            Point loc = target.getLocationOnScreen();
                            int tabW = target.getWidth() / finalCount;
                            locRef.set(new Point(loc.x + index * tabW + tabW / 2,
                                    loc.y + target.getHeight() / 2));
                        }
                    });
                    centre = locRef.get();
                }
            }
        }

        if (centre == null) {
            centre = screenCentreOrThrow(comp, "click");
        }

        SafeRobot robot = newSafeRobot();
        robot.click(centre.x, centre.y);
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        return okResult("click", comp);
    }

    // ── setText ───────────────────────────────────────────────────────────

    /**
     * Clear the component's current content and type new text.
     *
     * <p>
     * Sequence:
     * <ol>
     * <li>Focus the component (EDT + Robot fallback)</li>
     * <li>Select all existing text (Ctrl+A)</li>
     * <li>Type the new text via {@link SafeRobot#typeText(String)}</li>
     * </ol>
     *
     * @param cmd must include a {@code text} parameter
     */
    public static String executeSetText(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "setText");
        String text64 = cmd.getParam("text64", "");
        String text = text64.isEmpty()
                ? cmd.getParam("text", "")
                : new String(Base64.getDecoder().decode(text64), StandardCharsets.UTF_8);

        // Focus
        requestFocusBestEffort(comp);
        Point centre = screenCentre(comp);
        SafeRobot robot = newSafeRobot();
        if (centre != null) {
            robot.click(centre.x, centre.y);
        }
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        // Select all + type
        robot.pressCombo(KeyEvent.VK_CONTROL, KeyEvent.VK_A);
        robot.delay(SafeRobot.DEFAULT_DELAY_MS);
        robot.typeText(text);
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        return okResult("setText", comp);
    }

    // ── clear ─────────────────────────────────────────────────────────────

    /**
     * Clear the component's content (Ctrl+A then Delete).
     */
    public static String executeClear(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "clear");

        requestFocusBestEffort(comp);
        Point centre = screenCentre(comp);
        SafeRobot robot = newSafeRobot();
        if (centre != null) {
            robot.click(centre.x, centre.y);
        }
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        // Ctrl+A then Delete
        robot.pressCombo(KeyEvent.VK_CONTROL, KeyEvent.VK_A);
        robot.delay(SafeRobot.DEFAULT_DELAY_MS);
        robot.pressKey(KeyEvent.VK_DELETE);
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        return okResult("clear", comp);
    }

    // ── pressKey ─────────────────────────────────────────────────────────

    /**
     * Press a named key or key combination.
     *
     * <p>
     * If a {@code locatorPath} (or other locator) is provided, the target
     * component is focused first. The {@code key} parameter is required.
     *
     * @param cmd must include a {@code key} parameter
     */
    public static String executePressKey(AgentCommand cmd) throws Exception {
        String keyName = cmd.getParam("key", "");
        if (keyName.trim().isEmpty()) {
            return JsonUtil.errorResult("pressKey",
                    "Required parameter 'key' is missing or empty.", null);
        }

        // Optional: focus a component first
        Component comp = resolveOptional(cmd);
        if (comp != null) {
            requestFocusBestEffort(comp);
        }

        SafeRobot robot = newSafeRobot();
        robot.delay(SafeRobot.DEFAULT_DELAY_MS);
        boolean pressed = robot.pressNamedKey(keyName);
        robot.delay(SafeRobot.POST_ACTION_DELAY_MS);

        if (!pressed) {
            return JsonUtil.errorResult("pressKey",
                    "Unrecognised key name: '" + keyName + "'."
                            + " Supported: TAB, ENTER, ESC, F1-F12, UP, DOWN, LEFT, RIGHT,"
                            + " CTRL+S, CTRL+A, CTRL+C, CTRL+V, CTRL+Z, etc.",
                    null);
        }

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"pressKey\",");
        sb.append("\"key\":").append(JsonUtil.quoted(keyName));
        if (comp != null) {
            sb.append(",\"component\":").append(ComponentResolver.componentJson(comp));
        }
        sb.append('}');
        return sb.toString();
    }

    // ── screenshot ────────────────────────────────────────────────────────

    /**
     * Capture a screenshot and write it as PNG to the path given in
     * {@code screenshotOut}.
     *
     * <p>
     * If a locator resolves to a component, only that component's screen
     * bounds are captured. Otherwise the full primary screen is captured.
     *
     * @param cmd must include a {@code screenshotout} parameter (case-insensitive)
     */
    public static String executeScreenshot(AgentCommand cmd) throws Exception {
        String outPath = cmd.getParam("screenshotout");
        if (outPath == null || outPath.trim().isEmpty()) {
            return JsonUtil.errorResult("screenshot",
                    "Required parameter 'screenshotOut' is missing.", null);
        }

        // Robot must be constructed on the EDT to avoid ExceptionInInitializerError
        // when called from the Attach Listener thread (AWT static classes are not
        // yet initialised in that thread's context). The capture is also done on
        // the EDT so the graphics environment is fully available.
        final AtomicReference<BufferedImage> imgRef = new AtomicReference<>();
        final AtomicReference<String> modeRef = new AtomicReference<>("fullscreen");
        final AtomicReference<String> compJsonRef = new AtomicReference<>("null");

        Component comp = resolveOptional(cmd);

        invokeOnEDT(() -> {
            SafeRobot robot;
            try {
                robot = new SafeRobot();
            } catch (AWTException e) {
                throw new RuntimeException("SafeRobot init failed: " + e.getMessage(), e);
            }
            if (comp != null && comp.isShowing()) {
                try {
                    Point loc = comp.getLocationOnScreen();
                    Rectangle rect = new Rectangle(loc.x, loc.y, comp.getWidth(), comp.getHeight());
                    imgRef.set(robot.captureRegion(rect));
                    modeRef.set("component");
                    compJsonRef.set(ComponentResolver.componentJson(comp));
                } catch (Exception e) {
                    imgRef.set(robot.captureFullScreen());
                    modeRef.set("fullscreen-fallback");
                }
            } else {
                imgRef.set(robot.captureFullScreen());
            }
        });

        BufferedImage img = imgRef.get();
        String captureMode = modeRef.get();
        String compJson = compJsonRef.get();

        // Write PNG (file I/O off the EDT)
        File pngFile = new File(outPath);
        File parent = pngFile.getParentFile();
        if (parent != null && !parent.exists())
            parent.mkdirs();
        PngWriter.write(img, pngFile);

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"screenshot\",");
        sb.append("\"screenshotOut\":").append(JsonUtil.quoted(outPath)).append(',');
        sb.append("\"captureMode\":").append(JsonUtil.quoted(captureMode)).append(',');
        sb.append("\"width\":").append(img.getWidth()).append(',');
        sb.append("\"height\":").append(img.getHeight()).append(',');
        sb.append("\"component\":").append(compJson);
        sb.append('}');
        return sb.toString();
    }

    // ── highlight ─────────────────────────────────────────────────────────

    /**
     * Flash a semi-transparent red overlay over the component for ~500 ms.
     *
     * <p>
     * If the component is not showing (no screen bounds), or if an
     * overlay window cannot be created, just returns the component's bounds
     * in the JSON response without any visual effect.
     */
    public static String executeHighlight(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "highlight");

        Point loc = null;
        try {
            if (comp.isShowing())
                loc = comp.getLocationOnScreen();
        } catch (Exception ignored) {
        }

        if (loc != null) {
            final int hx = loc.x;
            final int hy = loc.y;
            final int hw = comp.getWidth();
            final int hh = comp.getHeight();

            // Show a transparent flash overlay on the EDT
            invokeOnEDT(() -> {
                JWindow overlay = new JWindow();
                overlay.setBackground(new Color(255, 0, 0, 80)); // translucent red
                overlay.setBounds(hx, hy, hw, hh);
                overlay.setAlwaysOnTop(true);
                overlay.setVisible(true);

                // Schedule removal on a daemon thread after 500 ms
                Thread t = new Thread(() -> {
                    try {
                        Thread.sleep(500);
                    } catch (InterruptedException ignored2) {
                    }
                    SwingUtilities.invokeLater(() -> {
                        overlay.setVisible(false);
                        overlay.dispose();
                    });
                });
                t.setDaemon(true);
                t.start();
            });
        }

        return okResult("highlight", comp);
    }

    // ── elementAt ────────────────────────────────────────────────────────

    /**
     * Return the deepest visible component under absolute screen coordinates.
     *
     * <p>
     * This lets the Python recorder keep Java DOM matching local and
     * deterministic: AI sees only screenshots, returns coordinates, and the
     * agent maps those coordinates to a real Forms component inside the JVM.
     */
    public static String executeElementAt(AgentCommand cmd) throws Exception {
        int x = parseInt(cmd.getParam("x", "0"));
        int y = parseInt(cmd.getParam("y", "0"));

        final AtomicReference<Component> ref = new AtomicReference<>();
        invokeOnEDT(() -> ref.set(componentAtScreenPoint(x, y)));
        Component comp = ref.get();
        if (comp == null) {
            return JsonUtil.errorResult(
                    "elementat",
                    "No visible component found at screen coordinates " + x + "," + y,
                    null);
        }

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"elementat\",");
        sb.append("\"x\":").append(x).append(',');
        sb.append("\"y\":").append(y).append(',');
        sb.append("\"path\":").append(JsonUtil.quoted(ComponentResolver.componentPath(comp))).append(',');
        sb.append("\"component\":").append(ComponentResolver.componentJson(comp));
        sb.append('}');
        return sb.toString();
    }

    // ── Internal helpers ──────────────────────────────────────────────────

    /**
     * Resolve a component from the command; throw with a clear message if none
     * found.
     */
    private static Component resolveOrThrow(AgentCommand cmd, String commandName)
            throws Exception {
        final AtomicReference<Component> ref = new AtomicReference<>();
        invokeOnEDT(() -> ref.set(ComponentResolver.resolve(cmd)));
        Component comp = ref.get();
        if (comp == null) {
            throw new IllegalArgumentException(
                    "Component not found for command '" + commandName + "'."
                            + " Locator params: " + cmd.getParams());
        }
        return comp;
    }

    /**
     * Resolve a component from the command; return {@code null} if none found
     * (used by commands where a target is optional, e.g. pressKey, screenshot).
     */
    private static Component resolveOptional(AgentCommand cmd) throws Exception {
        // Only attempt resolution if at least one locator param is present
        if (cmd.getParam("locatorpath") == null
                && cmd.getParam("locatorname") == null
                && cmd.getParam("locatoraccessiblename") == null
                && cmd.getParam("locatortext") == null) {
            return null;
        }
        final AtomicReference<Component> ref = new AtomicReference<>();
        invokeOnEDT(() -> ref.set(ComponentResolver.resolve(cmd)));
        return ref.get();
    }

    /**
     * Pure-DOM click for an EWT DTree item: resolve its AccessibleContext by
     * treePath and invoke its default AccessibleAction. Falls back to selecting
     * the node through its parent's AccessibleSelection — still coordinate-free.
     * Returns success JSON, or {@code null} if the item / action could not be
     * found so the caller falls through to normal component resolution.
     */
    private static String clickTreeItemViaAccessibility(String treePath) throws Exception {
        final AtomicReference<String> done = new AtomicReference<>(null);
        invokeOnEDT(() -> {
            try {
                javax.accessibility.AccessibleContext item = ComponentResolver.resolveTreeItemAccessible(treePath);
                if (item == null)
                    return;
                // 1) Default accessible action on the item itself.
                javax.accessibility.AccessibleAction action = item.getAccessibleAction();
                if (action != null && action.getAccessibleActionCount() > 0
                        && action.doAccessibleAction(0)) {
                    done.set("action");
                    return;
                }
                // 2) Fallback: select the node via its parent's AccessibleSelection
                // (no coordinates — still pure DOM).
                javax.accessibility.Accessible parent = item.getAccessibleParent();
                int idx = item.getAccessibleIndexInParent();
                if (parent != null && idx >= 0) {
                    javax.accessibility.AccessibleContext pac = parent.getAccessibleContext();
                    if (pac != null) {
                        javax.accessibility.AccessibleSelection sel = pac.getAccessibleSelection();
                        if (sel != null) {
                            sel.addAccessibleSelection(idx);
                            done.set("selection");
                        }
                    }
                }
            } catch (Throwable ignored) {
            }
        });
        String how = done.get();
        if (how == null)
            return null;
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(JsonUtil.quoted("click")).append(',');
        sb.append("\"via\":").append(JsonUtil.quoted("accessibility:" + how)).append(',');
        sb.append("\"treePath\":").append(JsonUtil.quoted(treePath));
        sb.append('}');
        return sb.toString();
    }

    /** Build the standard success JSON with a component descriptor. */
    private static String okResult(String command, Component comp) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(JsonUtil.quoted(command)).append(',');
        sb.append("\"component\":").append(ComponentResolver.componentJson(comp));
        sb.append('}');
        return sb.toString();
    }

    /**
     * Return the screen-coordinate centre of a component, or {@code null}
     * if the component is not showing.
     */
    private static Point screenCentre(Component comp) {
        try {
            if (comp != null && comp.isShowing()) {
                Point loc = comp.getLocationOnScreen();
                return new Point(loc.x + comp.getWidth() / 2,
                        loc.y + comp.getHeight() / 2);
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static Point screenCentreOrThrow(Component comp, String commandName)
            throws Exception {
        Point p = screenCentre(comp);
        if (p == null) {
            throw new IllegalStateException(
                    "Component for '" + commandName + "' is not showing on screen;"
                            + " cannot compute click target.");
        }
        return p;
    }

    private static void requestFocusBestEffort(Component comp) {
        if (comp == null)
            return;
        try {
            invokeOnEDT(() -> {
                try {
                    comp.requestFocusInWindow();
                } catch (Throwable ignored) {
                    // Robot click fallback owns real focus for Forms actions.
                }
            });
        } catch (Throwable ignored) {
            // Focus will be attempted through Robot click when screen bounds exist.
        }
    }

    private static Component componentAtScreenPoint(int screenX, int screenY) {
        for (Window window : AwtContext.getWindows()) {
            if (window == null || !window.isVisible() || !window.isShowing())
                continue;
            try {
                Component candidate = findDeepestRealComponent(window, screenX, screenY);
                if (candidate != null)
                    return candidate;
            } catch (Exception ignored) {
            }
        }
        return null;
    }

    private static Component findDeepestRealComponent(Component comp, int screenX, int screenY) {
        if (comp == null || !comp.isVisible() || !comp.isShowing())
            return null;
        try {
            Point loc = comp.getLocationOnScreen();
            if (screenX < loc.x || screenY < loc.y
                    || screenX >= loc.x + comp.getWidth()
                    || screenY >= loc.y + comp.getHeight()) {
                return null;
            }

            if (comp instanceof Container) {
                Component[] children = ((Container) comp).getComponents();
                for (int i = children.length - 1; i >= 0; i--) {
                    Component child = findDeepestRealComponent(children[i], screenX, screenY);
                    if (child != null)
                        return child;
                }
            }

            return isCoordinateOverlay(comp) ? null : comp;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static boolean isCoordinateOverlay(Component comp) {
        String simple = comp.getClass().getSimpleName();
        String fqn = comp.getClass().getName();
        String text = (simple + " " + fqn).toLowerCase(java.util.Locale.ROOT);
        return text.contains("glassmousegrabprovider")
                || text.contains("focustransfercomp")
                || text.equals("proxy")
                || text.contains("glasspane");
    }

    private static int parseInt(String raw) {
        try {
            return Integer.parseInt(raw);
        } catch (Exception ignored) {
            return 0;
        }
    }

    private static SafeRobot newSafeRobot() throws Exception {
        final AtomicReference<SafeRobot> ref = new AtomicReference<>();
        invokeOnEDT(() -> {
            try {
                ref.set(new SafeRobot());
            } catch (AWTException e) {
                throw new RuntimeException("SafeRobot init failed: " + e.getMessage(), e);
            }
        });
        SafeRobot robot = ref.get();
        if (robot == null) {
            throw new IllegalStateException("SafeRobot init failed: no robot was created");
        }
        return robot;
    }

    /**
     * Run a {@link Runnable} on the EDT, blocking until it finishes.
     * Safe to call from both EDT and non-EDT threads.
     */
    private static void invokeOnEDT(Runnable r) throws Exception {
        try {
            if (SwingUtilities.isEventDispatchThread()) {
                r.run();
            } else {
                final CountDownLatch latch = new CountDownLatch(1);
                final AtomicReference<Exception> err = new AtomicReference<>();
                SwingUtilities.invokeLater(() -> {
                    try {
                        r.run();
                    } catch (Exception e) {
                        err.set(e);
                    } finally {
                        latch.countDown();
                    }
                });
                latch.await();
                if (err.get() != null)
                    throw err.get();
            }
        } catch (NullPointerException appContextMissing) {
            System.err.println("[ebs-dom-agent] AWT AppContext unavailable on attach thread; "
                    + "running action step directly.");
            r.run();
        }
    }

    private static Rectangle getEwtTabBounds(Component comp, int index) {
        // 1. Direct method lookup on comp (including non-public declared methods)
        Rectangle r = queryBoundsMethod(comp, index);
        if (r != null) {
            return r;
        }

        // 2. Lookup via item/page object (e.g. TabBar.getItem(index) ->
        // TabBarItem.getBounds())
        try {
            Object item = null;
            Class<?> clazz = comp.getClass();
            while (clazz != null && clazz != Object.class) {
                for (java.lang.reflect.Method m : clazz.getDeclaredMethods()) {
                    if (m.getParameterTypes().length == 1 && m.getParameterTypes()[0] == int.class) {
                        String name = m.getName().toLowerCase();
                        if (name.equals("getitem") || name.equals("getpage")) {
                            try {
                                m.setAccessible(true);
                                item = m.invoke(comp, index);
                                if (item != null) {
                                    break;
                                }
                            } catch (Exception ignored) {
                            }
                        }
                    }
                }
                if (item != null)
                    break;
                clazz = clazz.getSuperclass();
            }

            if (item != null) {
                // Query bounds on the item object
                Class<?> itemClass = item.getClass();
                while (itemClass != null && itemClass != Object.class) {
                    for (java.lang.reflect.Method m : itemClass.getDeclaredMethods()) {
                        if (m.getParameterTypes().length == 0) {
                            if (Rectangle.class.isAssignableFrom(m.getReturnType())) {
                                String name = m.getName().toLowerCase();
                                if (name.equals("getbounds") || name.equals("getrect") || name.contains("bounds")) {
                                    try {
                                        m.setAccessible(true);
                                        Rectangle rect = (Rectangle) m.invoke(item);
                                        if (rect != null) {
                                            return rect;
                                        }
                                    } catch (Exception ignored) {
                                    }
                                }
                            }
                        }
                    }
                    itemClass = itemClass.getSuperclass();
                }
            }
        } catch (Exception ignored) {
        }

        return null;
    }

    private static Rectangle queryBoundsMethod(Component comp, int index) {
        Class<?> clazz = comp.getClass();
        while (clazz != null && clazz != Object.class) {
            for (java.lang.reflect.Method m : clazz.getDeclaredMethods()) {
                if (m.getParameterTypes().length == 1 && m.getParameterTypes()[0] == int.class) {
                    if (Rectangle.class.isAssignableFrom(m.getReturnType())) {
                        String name = m.getName().toLowerCase();
                        if (name.contains("tab") || name.contains("item") || name.contains("rect")
                                || name.contains("bounds")) {
                            try {
                                m.setAccessible(true);
                                Rectangle r = (Rectangle) m.invoke(comp, index);
                                if (r != null) {
                                    return r;
                                }
                            } catch (Exception ignored) {
                            }
                        }
                    }
                }
            }
            clazz = clazz.getSuperclass();
        }
        return null;
    }

    public static String executeTreeAction(AgentCommand cmd) throws Exception {
        String op = cmd.getParam("op", "");
        if (op.trim().isEmpty()) {
            return JsonUtil.errorResult("treeAction",
                    "Required parameter 'op' is missing."
                            + " Use one of: select, expand, collapse, activate.",
                    null);
        }
        TreeItemActuator.Op parsedOp;
        try {
            parsedOp = TreeItemActuator.Op.valueOf(op.trim().toUpperCase(java.util.Locale.ROOT));
        } catch (IllegalArgumentException e) {
            return JsonUtil.errorResult("treeAction",
                    "Unsupported op '" + op + "'."
                            + " Use one of: select, expand, collapse, activate.",
                    null);
        }

        String treePath = cmd.getParam("locatortreepath");
        if (treePath == null || treePath.trim().isEmpty()) {
            return JsonUtil.errorResult("treeAction",
                    "Required parameter 'locatorTreePath' is missing.", null);
        }

        // Resolve the DTree component (or any component inside it) from locators,
        // then make sure we hand TreeItemActuator the DTree itself.
        Component resolved = resolveOrThrow(cmd, "treeAction");
        final Component resolvedFinal = resolved;
        final AtomicReference<Component> treeRef = new AtomicReference<Component>();
        invokeOnEDT(() -> treeRef.set(findEnclosingDTree(resolvedFinal)));
        Component tree = treeRef.get();
        if (tree == null) {
            tree = resolved; // best-effort; actuator reports if it isn't a tree
        }

        TreeItemActuator.Result r = TreeItemActuator.act(tree, treePath, parsedOp);
        if (!r.ok) {
            return JsonUtil.errorResult("treeAction", r.message, null);
        }

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(JsonUtil.quoted("treeAction")).append(',');
        sb.append("\"op\":").append(JsonUtil.quoted(op)).append(',');
        sb.append("\"via\":").append(JsonUtil.quoted("reflection:DTree")).append(',');
        sb.append("\"treePath\":").append(JsonUtil.quoted(treePath)).append(',');
        if (r.matchedLabel != null) {
            sb.append("\"matchedLabel\":").append(JsonUtil.quoted(r.matchedLabel)).append(',');
        }
        sb.append("\"detail\":").append(JsonUtil.quoted(r.message)).append(',');
        sb.append("\"component\":").append(ComponentResolver.componentJson(tree));
        sb.append('}');
        return sb.toString();
    }

    /**
     * Return the DTree at or around {@code comp}: {@code comp} itself, the
     * nearest DTree ancestor, or the first DTree descendant. {@code null} if
     * none. Must be called on the EDT.
     */
    private static Component findEnclosingDTree(Component comp) {
        for (Component c = comp; c != null; c = c.getParent()) {
            if (isDTree(c)) {
                return c;
            }
        }
        return findDTreeDescendant(comp);
    }

    private static boolean isDTree(Component c) {
        for (Class<?> k = c.getClass(); k != null && k != Object.class; k = k.getSuperclass()) {
            if (k.getName().equals("oracle.ewt.dTree.DTree")) {
                return true;
            }
        }
        return false;
    }

    private static Component findDTreeDescendant(Component comp) {
        if (comp == null) {
            return null;
        }
        if (isDTree(comp)) {
            return comp;
        }
        if (comp instanceof Container) {
            for (Component ch : ((Container) comp).getComponents()) {
                Component d = findDTreeDescendant(ch);
                if (d != null) {
                    return d;
                }
            }
        }
        return null;
    }
}
