package com.pyebsdom.agent;

import com.pyebsdom.agent.io.FileUtil;
import com.pyebsdom.agent.json.Results;

import java.lang.instrument.Instrumentation;

/**
 * The agent's real entry point and command loop.
 *
 * <h3>Loading modes</h3>
 * <ol>
 *   <li><b>Static</b> ({@code -javaagent:ebs-dom-agent.jar=...}): the JVM calls
 *       {@link #premain}. Used in development.</li>
 *   <li><b>Dynamic</b> (Attach API): the JVM calls {@link #agentmain}. The
 *       production path, driven by {@code attach.AttachLauncher} via the thin
 *       {@code AgentBootstrap} shim.</li>
 * </ol>
 * Both delegate to {@link #runCommand}, which is synchronous: it returns only
 * after the result (or an error envelope) has been written to the {@code out}
 * file. No thread is left running afterwards.
 *
 * <p>All exceptions are caught and written as a JSON error envelope; the agent
 * never throws out of its entry points.
 */
public final class AgentMain {

    private AgentMain() { /* static entry-point class */ }

    /** Called by the JVM when loaded via {@code -javaagent:}. */
    public static void premain(String agentArgs, Instrumentation inst) {
        runCommand(agentArgs, inst);
    }

    /** Called by the JVM when loaded dynamically via the Attach API. */
    public static void agentmain(String agentArgs, Instrumentation inst) {
        runCommand(agentArgs, inst);
    }

    /** Parse → route → write. Reflected into by {@code AgentBootstrap}. */
    public static void runCommand(String agentArgs, Instrumentation inst) {
        AgentCommand cmd = null;
        String outputFile = null;

        try {
            cmd = AgentCommand.parse(agentArgs);
            outputFile = cmd.getOutputFile();

            System.err.println("[ebs-dom-agent] executing command='" + cmd.getCommand()
                    + "'  out='" + outputFile + "'");

            String content = CommandRouter.dispatch(cmd);

            FileUtil.writeUtf8(outputFile, content);
            System.err.println("[ebs-dom-agent] result written to: " + outputFile);

        } catch (Exception e) {
            if (outputFile == null) {
                outputFile = AgentCommand.extractOutParam(agentArgs);
            }
            String cmdName = cmd != null ? cmd.getCommand() : "unknown";
            String message = e.getMessage() != null ? e.getMessage() : e.getClass().getName();

            System.err.println("[ebs-dom-agent] ERROR command='" + cmdName
                    + "': " + e.getClass().getName() + ": " + message);
            e.printStackTrace(System.err);

            String errorJson = Results.error(cmdName, message, e);
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
}
