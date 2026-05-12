package com.pyebsdom.agent;

import java.lang.instrument.Instrumentation;

/**
 * pyebsdom Java Agent — entry point.
 *
 * <h3>Loading modes</h3>
 * <ol>
 *   <li><b>Static load</b> (JVM startup): the JVM calls {@link #premain}.
 *       Useful for development/testing with {@code -javaagent:ebs-dom-agent.jar=...}</li>
 *   <li><b>Dynamic load</b> (Attach API): the JVM calls {@link #agentmain}.
 *       This is the production path used by {@link AttachAgentByPid}.</li>
 * </ol>
 * Both entry points delegate to {@link #runCommand}, which is synchronous and
 * returns only after the result has been written to disk.  No thread is left
 * running after the method returns.
 *
 * <h3>Argument format</h3>
 * <pre>
 *   command=&lt;name&gt;;out=&lt;absolutePath&gt;[;&lt;key&gt;=&lt;value&gt;...]
 * </pre>
 *
 * <h3>Commands</h3>
 * <ul>
 *   <li>{@code health}     — liveness/sanity check; no AWT access.</li>
 *   <li>{@code scan}       — visible component tree as JSON.</li>
 *   <li>{@code raw}        — full component tree (visible + invisible) as JSON.</li>
 *   <li>{@code layout}     — human-readable plain-text tree layout.</li>
 *   <li>{@code tables}     — detect all tables/grids and return structured JSON.</li>
 *   <li>{@code focus}      — request focus on a located component.</li>
 *   <li>{@code click}      — left-click the centre of a located component.</li>
 *   <li>{@code settext}    — clear and type new text into a located component.</li>
 *   <li>{@code clear}      — clear the text content of a located component.</li>
 *   <li>{@code presskey}   — press a named key or key combo (TAB, CTRL+S, etc.).</li>
 *   <li>{@code screenshot} — capture the screen (or component region) to a PNG.</li>
 *   <li>{@code highlight}  — flash a coloured overlay over a located component.</li>
 *   <li>{@code elementat}  — return component metadata at screen coordinates.</li>
 * </ul>
 *
 * <h3>Error handling</h3>
 * All exceptions are caught and written as a JSON error envelope to the
 * {@code out} file.  The agent never throws out of its entry points.
 */
public final class EbsDomAgent {

    private EbsDomAgent() { /* static entry-point class */ }

    // ── Agent entry points ────────────────────────────────────────────────

    /** Called by the JVM when loaded via {@code -javaagent:}. */
    public static void premain(String agentArgs, Instrumentation inst) {
        runCommand(agentArgs, inst);
    }

    /** Called by the JVM when loaded dynamically via the Attach API. */
    public static void agentmain(String agentArgs, Instrumentation inst) {
        runCommand(agentArgs, inst);
    }

    // ── Core execution ────────────────────────────────────────────────────

    public static void runCommand(String agentArgs, Instrumentation inst) {
        AgentCommand cmd        = null;
        String       outputFile = null;

        try {
            cmd        = AgentCommandParser.parse(agentArgs);
            outputFile = cmd.getOutputFile();

            System.err.println("[ebs-dom-agent] executing command='" + cmd.getCommand()
                    + "'  out='" + outputFile + "'");

            String content = dispatch(cmd, inst);

            FileUtil.writeUtf8(outputFile, content);
            System.err.println("[ebs-dom-agent] result written to: " + outputFile);

        } catch (Exception e) {
            if (outputFile == null) {
                outputFile = AgentCommandParser.extractOutParam(agentArgs);
            }

            String cmdName = cmd != null ? cmd.getCommand() : "unknown";
            String message = e.getMessage() != null ? e.getMessage() : e.getClass().getName();

            System.err.println("[ebs-dom-agent] ERROR command='" + cmdName
                    + "': " + e.getClass().getName() + ": " + message);
            e.printStackTrace(System.err);

            String errorJson = JsonUtil.errorResult(cmdName, message, e);

            if (outputFile != null) {
                try {
                    FileUtil.writeUtf8(outputFile, errorJson);
                    System.err.println("[ebs-dom-agent] error written to: " + outputFile);
                } catch (Exception writeEx) {
                    System.err.println("[ebs-dom-agent] could not write error file ("
                            + writeEx.getMessage() + ")");
                    System.err.println(errorJson);
                }
            } else {
                System.err.println("[ebs-dom-agent] no output file; dumping to stderr:");
                System.err.println(errorJson);
            }
        }
    }

    // ── Command dispatch ──────────────────────────────────────────────────

    private static String dispatch(AgentCommand cmd, Instrumentation inst) throws Exception {
        switch (cmd.getCommand()) {
            case "health":  return executeHealth(cmd);
            case "scan":    return executeScan(cmd, false);
            case "raw":     return executeScan(cmd, true);
            case "layout":  return executeLayout(cmd);
            case "tables":     return executeTables(cmd);
            case "focus":      return ActionExecutor.executeFocus(cmd);
            case "click":      return ActionExecutor.executeClick(cmd);
            case "settext":    return ActionExecutor.executeSetText(cmd);
            case "clear":      return ActionExecutor.executeClear(cmd);
            case "presskey":   return ActionExecutor.executePressKey(cmd);
            case "screenshot": return ActionExecutor.executeScreenshot(cmd);
            case "highlight":  return ActionExecutor.executeHighlight(cmd);
            case "elementat":  return ActionExecutor.executeElementAt(cmd);

            default:
                return JsonUtil.errorResult(
                        cmd.getCommand(),
                        "Unknown command: '" + cmd.getCommand() + "'."
                                + " Supported commands: health, scan, raw, layout, tables,"
                                + " focus, click, settext, clear, presskey, screenshot, highlight, elementat",
                        null);
        }
    }

    // ── Command handlers ──────────────────────────────────────────────────

    /**
     * {@code health} — verifies the agent loaded and can execute code in
     * the target JVM.  No AWT/Swing interaction is performed.
     */
    private static String executeHealth(AgentCommand cmd) {
        return JsonUtil.healthResult();
    }

    /**
     * {@code scan} / {@code raw} — walk the AWT/Swing component tree on
     * the Event Dispatch Thread and return a JSON DOM snapshot.
     *
     * @param raw {@code true} for the {@code raw} command (include invisible
     *            components); {@code false} for the {@code scan} command.
     */
    private static String executeScan(AgentCommand cmd, boolean raw) throws Exception {
        DomScanner.ScanResult result = DomScanner.scan(raw);
        return result.toJson(cmd.getCommand());
    }

    /**
     * {@code layout} — produce a plain-text human-readable tree layout.
     * Internally runs a normal (non-raw) scan first.
     */
    private static String executeLayout(AgentCommand cmd) throws Exception {
        DomScanner.ScanResult result = DomScanner.scan(false);
        return TextLayoutWriter.write(result);
    }

    /**
     * {@code tables} — detect tables/grids using all four strategies and
     * return a JSON array of {@link TableModel} objects.
     */
    private static String executeTables(AgentCommand cmd) throws Exception {
        return DomScanner.scanTables();
    }
}
