package com.pyebsdom.agent;

import com.pyebsdom.agent.execute.ActionExecutor;
import com.pyebsdom.agent.extract.DomScanner;
import com.pyebsdom.agent.json.Results;
import com.pyebsdom.agent.render.DomJson;
import com.pyebsdom.agent.render.TextLayout;

/**
 * Maps a command name to its handler and returns the JSON (or text) payload.
 *
 * <p>
 * The router is the one place that crosses layer boundaries: extraction
 * ({@code extract}) produces the model, rendering ({@code render}) serialises
 * it, and the action layer ({@code execute}) performs UI operations. Keeping
 * the wiring here means each layer stays unaware of the others' output formats.
 *
 * <h3>Commands</h3>
 * <ul>
 * <li>{@code health} — liveness check (no AWT access).</li>
 * <li>{@code scan}/{@code raw} — component tree as JSON.</li>
 * <li>{@code layout} — human-readable plain-text tree.</li>
 * <li>{@code tables} — detected tables/grids as JSON.</li>
 * <li>{@code focus},{@code click},{@code settext},{@code clear},{@code presskey},
 * {@code screenshot},{@code highlight},{@code elementat} — actions.</li>
 * </ul>
 */
final class CommandRouter {

    private CommandRouter() {
    }

    private static boolean isProbe(AgentCommand cmd) {
        return "true".equalsIgnoreCase(cmd.getParam("probe"));
    }

    static String dispatch(AgentCommand cmd) throws Exception {
        switch (cmd.getCommand()) {
            case "health":
                return Results.health();

            case "scan":
                return DomJson.scanResult(
                        DomScanner.scan(false, isProbe(cmd), cmd.getParam("target")), "scan");
            case "raw":
                return DomJson.scanResult(
                        DomScanner.scan(true, isProbe(cmd), cmd.getParam("target")), "raw");
            case "layout":
                return TextLayout.write(DomScanner.scan(false));
            case "tables":
                return DomJson.tables(DomScanner.detectTables());

            case "focus":
                return ActionExecutor.executeFocus(cmd);
            case "click":
                return ActionExecutor.executeClick(cmd);
            case "doubleclick":
                return ActionExecutor.executeDoubleClick(cmd);
            case "settext":
                return ActionExecutor.executeSetText(cmd);
            case "clear":
                return ActionExecutor.executeClear(cmd);
            case "selectoption":
                return ActionExecutor.executeSelectOption(cmd);
            case "setcheck":
                return ActionExecutor.executeSetCheck(cmd);
            case "expandtree":
                return ActionExecutor.executeExpandTree(cmd);
            case "collapsetree":
                return ActionExecutor.executeExpandTree(cmd);
            case "presskey":
                return ActionExecutor.executePressKey(cmd);
            case "screenshot":
                return ActionExecutor.executeScreenshot(cmd);
            case "highlight":
                return ActionExecutor.executeHighlight(cmd);
            case "elementat":
                return ActionExecutor.executeElementAt(cmd);

            default:
                return Results.error(
                        cmd.getCommand(),
                        "Unknown command: '" + cmd.getCommand() + "'."
                                + " Supported commands: health, scan, raw, layout, tables,"
                                + " focus, click, doubleclick, settext, clear, selectoption, setcheck,"
                                + " expandtree, collapsetree, presskey, screenshot, highlight, elementat",
                        null);
        }
    }
}
