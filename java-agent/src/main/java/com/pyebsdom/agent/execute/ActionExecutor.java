package com.pyebsdom.agent.execute;

import com.pyebsdom.agent.AgentCommand;
import com.pyebsdom.agent.capture.ScreenCapture;
import com.pyebsdom.agent.json.Json;
import com.pyebsdom.agent.json.Results;
import com.pyebsdom.agent.runtime.AwtContext;
import com.pyebsdom.agent.runtime.Edt;

import javax.swing.JWindow;
import javax.swing.SwingUtilities;
import java.awt.Color;
import java.awt.Component;
import java.awt.Container;
import java.awt.KeyboardFocusManager;
import java.awt.Point;
import java.awt.Window;
import java.nio.charset.StandardCharsets;
import java.util.Base64;

/**
 * Executes UI actions against a resolved {@link Component} — <b>non-robotically</b>.
 *
 * <p>Every action runs inside the target JVM through {@link ModelActions}:
 * model-method calls where the widget exposes them, otherwise an AWT event
 * dispatched straight to the component. No OS mouse movement, no global key
 * injection, and (by design) <b>no automatic Robot fallback</b>. Two commands
 * are inherently pixel-oriented and remain so because they only ever read:
 * {@code screenshot} (captures pixels via {@link ScreenCapture}) and
 * {@code elementat} (maps a screen coordinate the recorder produced back to a
 * live component).
 *
 * <p>Component resolution and state reads run on the EDT; the technique each
 * action used is reported back in the JSON for traceability.
 */
public final class ActionExecutor {

    private ActionExecutor() {}

    // ── focus ─────────────────────────────────────────────────────────────

    public static String executeFocus(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "focus");
        String technique = ModelActions.focus(comp);
        return okResult("focus", comp, technique);
    }

    // ── click ─────────────────────────────────────────────────────────────

    public static String executeClick(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "click");

        String tabIndexStr = cmd.getParam("tab_index");
        if (tabIndexStr != null && !tabIndexStr.isEmpty()) {
            int index = parseInt(tabIndexStr, -1);
            if (index >= 0) {
                // Non-robotic tab selection: drive the tab container's model
                // (setSelectedIndex / selectTab) instead of clicking a pixel.
                Component tabContainer = resolveTabContainer(comp);
                String technique = ModelActions.selectTab(tabContainer, index);
                if (technique == null) {
                    technique = ModelActions.selectTab(comp, index);
                }
                if (technique == null) {
                    return Results.error("click",
                            "Tab container exposes no selectable model method"
                            + " (setSelectedIndex/selectTab) for tab_index=" + index, null);
                }
                return okResult("click", comp, technique + "[tab=" + index + "]");
            }
        }

        String technique = ModelActions.click(comp);
        if (technique == null) {
            return Results.error("click", "Could not activate the resolved component.", null);
        }
        return okResult("click", comp, technique);
    }

    // ── setText ───────────────────────────────────────────────────────────

    public static String executeSetText(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "setText");
        String text64 = cmd.getParam("text64", "");
        String text = text64.isEmpty()
                ? cmd.getParam("text", "")
                : new String(Base64.getDecoder().decode(text64), StandardCharsets.UTF_8);

        String technique = ModelActions.setText(comp, text);
        if (technique == null) {
            return Results.error("setText",
                    "Resolved component is not a writable text field"
                    + " (no setText/setValue).", null);
        }
        return okResult("setText", comp, technique);
    }

    // ── clear ─────────────────────────────────────────────────────────────

    public static String executeClear(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "clear");
        String technique = ModelActions.clear(comp);
        if (technique == null) {
            return Results.error("clear",
                    "Resolved component is not a writable text field.", null);
        }
        return okResult("clear", comp, technique);
    }

    // ── pressKey ──────────────────────────────────────────────────────────

    public static String executePressKey(AgentCommand cmd) throws Exception {
        String keyName = cmd.getParam("key", "");
        if (keyName.trim().isEmpty()) {
            return Results.error("pressKey", "Required parameter 'key' is missing or empty.", null);
        }

        Component target = resolveOptional(cmd);
        if (target == null) {
            target = Edt.get(() ->
                    KeyboardFocusManager.getCurrentKeyboardFocusManager().getFocusOwner());
        }
        if (target == null) {
            return Results.error("pressKey",
                    "No target component and no focus owner to receive key '" + keyName + "'.", null);
        }

        String technique = ModelActions.pressKey(target, keyName);
        if (technique == null) {
            return Results.error("pressKey",
                    "Unrecognised key name: '" + keyName + "'."
                    + " Supported: TAB, ENTER, ESC, F1-F12, UP, DOWN, LEFT, RIGHT,"
                    + " CTRL+S, CTRL+A, CTRL+C, CTRL+V, CTRL+Z, etc.", null);
        }

        final Component t = target;
        String compJson = Edt.get(() -> ComponentResolver.componentJson(t));
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"pressKey\",");
        sb.append("\"key\":").append(Json.quoted(keyName)).append(',');
        sb.append("\"technique\":").append(Json.quoted(technique)).append(',');
        sb.append("\"component\":").append(compJson);
        sb.append('}');
        return sb.toString();
    }

    // ── screenshot ────────────────────────────────────────────────────────

    public static String executeScreenshot(AgentCommand cmd) throws Exception {
        String outPath = cmd.getParam("screenshotout");
        if (outPath == null || outPath.trim().isEmpty()) {
            return Results.error("screenshot", "Required parameter 'screenshotOut' is missing.", null);
        }

        Component comp = resolveOptional(cmd);
        ScreenCapture.Result cap = ScreenCapture.capture(comp, outPath);

        String compJson = "null";
        if (comp != null && "component".equals(cap.mode)) {
            final Component c = comp;
            compJson = Edt.get(() -> ComponentResolver.componentJson(c));
        }

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"screenshot\",");
        sb.append("\"screenshotOut\":").append(Json.quoted(outPath)).append(',');
        sb.append("\"captureMode\":").append(Json.quoted(cap.mode)).append(',');
        sb.append("\"width\":").append(cap.width).append(',');
        sb.append("\"height\":").append(cap.height).append(',');
        sb.append("\"component\":").append(compJson);
        sb.append('}');
        return sb.toString();
    }

    // ── highlight ─────────────────────────────────────────────────────────

    public static String executeHighlight(AgentCommand cmd) throws Exception {
        final Component comp = resolveOrThrow(cmd, "highlight");

        Point loc = Edt.get(() -> {
            try {
                return comp.isShowing() ? comp.getLocationOnScreen() : null;
            } catch (Exception e) {
                return null;
            }
        });

        if (loc != null) {
            final int hx = loc.x, hy = loc.y;
            final int hw = comp.getWidth(), hh = comp.getHeight();
            Edt.run(() -> {
                JWindow overlay = new JWindow();
                overlay.setBackground(new Color(255, 0, 0, 80)); // translucent red
                overlay.setBounds(hx, hy, hw, hh);
                overlay.setAlwaysOnTop(true);
                overlay.setVisible(true);
                Thread t = new Thread(() -> {
                    try { Thread.sleep(500); } catch (InterruptedException ignored) {}
                    SwingUtilities.invokeLater(() -> {
                        overlay.setVisible(false);
                        overlay.dispose();
                    });
                });
                t.setDaemon(true);
                t.start();
            });
        }

        return okResult("highlight", comp, "overlay");
    }

    // ── elementAt ─────────────────────────────────────────────────────────

    public static String executeElementAt(AgentCommand cmd) throws Exception {
        final int x = parseInt(cmd.getParam("x", "0"), 0);
        final int y = parseInt(cmd.getParam("y", "0"), 0);

        Component comp = Edt.get(() -> componentAtScreenPoint(x, y));
        if (comp == null) {
            return Results.error("elementat",
                    "No visible component found at screen coordinates " + x + "," + y, null);
        }

        final Component c = comp;
        String[] out = Edt.get(() -> new String[] {
                ComponentResolver.componentPath(c),
                ComponentResolver.componentJson(c)
        });

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":\"elementat\",");
        sb.append("\"x\":").append(x).append(',');
        sb.append("\"y\":").append(y).append(',');
        sb.append("\"path\":").append(Json.quoted(out[0])).append(',');
        sb.append("\"component\":").append(out[1]);
        sb.append('}');
        return sb.toString();
    }

    // ── expand / collapse tree ────────────────────────────────────────────

    public static String executeExpandTree(AgentCommand cmd) throws Exception {
        Component tree = resolveOrThrow(cmd, "expandTree");
        boolean expand = !"collapsetree".equals(cmd.getCommand());
        String expandParam = cmd.getParam("expand");
        if (expandParam != null) expand = !"false".equalsIgnoreCase(expandParam);

        int row = resolveTreeRow(cmd, tree);
        if (row < 0) {
            return Results.error(cmd.getCommand(),
                    "Could not resolve a tree row to " + (expand ? "expand" : "collapse")
                    + " (need tree_row or locatorTreePath).", null);
        }
        String technique = ModelActions.expandTreeRow(tree, row, expand);
        if (technique == null) {
            return Results.error(cmd.getCommand(),
                    "Tree exposes no expandRow/collapseRow/setExpandedState method.", null);
        }
        return okResult(expand ? "expandTree" : "collapseTree", tree, technique);
    }

    // ── select option (combo / poplist / list) ────────────────────────────

    public static String executeSelectOption(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "selectOption");
        String value64 = cmd.getParam("value64", "");
        String value = value64.isEmpty()
                ? cmd.getParam("value", "")
                : new String(Base64.getDecoder().decode(value64), StandardCharsets.UTF_8);

        String technique = ModelActions.selectOption(comp, value);
        if (technique == null) {
            return Results.error("selectOption",
                    "Resolved component is not a selectable combo/list,"
                    + " or value '" + value + "' was not found among its options.", null);
        }
        return okResult("selectOption", comp, technique);
    }

    // ── double-click ──────────────────────────────────────────────────────

    public static String executeDoubleClick(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "doubleClick");
        String technique = ModelActions.doubleClick(comp);
        return okResult("doubleClick", comp, technique);
    }

    // ── set checkbox / toggle state (idempotent) ──────────────────────────

    public static String executeSetCheck(AgentCommand cmd) throws Exception {
        Component comp = resolveOrThrow(cmd, "setCheck");
        boolean desired = !"false".equalsIgnoreCase(cmd.getParam("value", "true"));
        String technique = ModelActions.setChecked(comp, desired);
        if (technique == null) {
            return Results.error("setCheck",
                    "Resolved component is not a checkbox/toggle (no setSelected/isSelected).", null);
        }
        return okResult("setCheck", comp, technique);
    }

    // ── Internal helpers ──────────────────────────────────────────────────

    /** Resolve a tree row index from an explicit {@code tree_row} or the leaf of {@code locatorTreePath}. */
    private static int resolveTreeRow(AgentCommand cmd, final Component tree) throws Exception {
        String rowStr = cmd.getParam("tree_row");
        if (rowStr != null && !rowStr.isEmpty()) {
            return parseInt(rowStr, -1);
        }
        String tp = cmd.getParam("locatortreepath");
        if (tp != null && !tp.isEmpty()) {
            String[] parts = tp.split("/");
            final String leaf = parts[parts.length - 1];
            return Edt.get(() -> ComponentResolver.resolveTreeRowIndex(tree, leaf));
        }
        return -1;
    }

    private static Component resolveOrThrow(AgentCommand cmd, String commandName) throws Exception {
        Component comp = Edt.get(() -> ComponentResolver.resolve(cmd));
        if (comp == null) {
            throw new IllegalArgumentException(
                    "Component not found for command '" + commandName + "'."
                    + " Locator params: " + cmd.getParams());
        }
        return comp;
    }

    private static Component resolveOptional(AgentCommand cmd) throws Exception {
        if (cmd.getParam("locatorpath") == null
                && cmd.getParam("locatorsemanticid") == null
                && cmd.getParam("locatorcanonicallabel") == null
                && cmd.getParam("locatortreepath") == null
                && cmd.getParam("locatorname") == null
                && cmd.getParam("locatoraccessiblename") == null
                && cmd.getParam("locatortext") == null
                && cmd.getParam("locatorbounds") == null) {
            return null;
        }
        return Edt.get(() -> ComponentResolver.resolve(cmd));
    }

    /** Resolve the tab container for a tab click — the component itself or its {@code getTabBar()}. */
    private static Component resolveTabContainer(final Component comp) throws Exception {
        return Edt.get(() -> {
            try {
                Object tb = comp.getClass().getMethod("getTabBar").invoke(comp);
                if (tb instanceof Component) return (Component) tb;
            } catch (Exception ignored) {}
            return comp;
        });
    }

    /** Success envelope including the technique used and the component descriptor. */
    private static String okResult(String command, final Component comp, String technique)
            throws Exception {
        String compJson = Edt.get(() -> ComponentResolver.componentJson(comp));
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(Json.quoted(command)).append(',');
        sb.append("\"technique\":").append(Json.quoted(technique)).append(',');
        sb.append("\"component\":").append(compJson);
        sb.append('}');
        return sb.toString();
    }

    private static int parseInt(String raw, int dflt) {
        try {
            return Integer.parseInt(raw.trim());
        } catch (Exception ignored) {
            return dflt;
        }
    }

    // ── elementAt traversal (read-only coordinate → component mapping) ─────

    private static Component componentAtScreenPoint(int screenX, int screenY) {
        for (Window window : AwtContext.getWindows()) {
            if (window == null || !window.isVisible() || !window.isShowing()) continue;
            try {
                Component candidate = findDeepestRealComponent(window, screenX, screenY);
                if (candidate != null) return candidate;
            } catch (Exception ignored) {}
        }
        return null;
    }

    private static Component findDeepestRealComponent(Component comp, int screenX, int screenY) {
        if (comp == null || !comp.isVisible() || !comp.isShowing()) return null;
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
                    if (child != null) return child;
                }
            }
            return isCoordinateOverlay(comp) ? null : comp;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static boolean isCoordinateOverlay(Component comp) {
        String text = (comp.getClass().getSimpleName() + " " + comp.getClass().getName())
                .toLowerCase(java.util.Locale.ROOT);
        return text.contains("glassmousegrabprovider")
                || text.contains("focustransfercomp")
                || text.equals("proxy")
                || text.contains("glasspane");
    }
}
