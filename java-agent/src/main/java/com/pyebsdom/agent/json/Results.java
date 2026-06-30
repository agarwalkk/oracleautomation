package com.pyebsdom.agent.json;

import java.io.PrintWriter;
import java.io.StringWriter;

/**
 * Builds the JSON result envelopes the agent writes to its output file.
 *
 * <p>Three shapes are shared across commands:
 * <ul>
 *   <li>{@link #health()} — the {@code health} liveness response.</li>
 *   <li>{@link #ok(String, String)} — a success envelope carrying an optional
 *       pre-rendered {@code component} object.</li>
 *   <li>{@link #error(String, String, Throwable)} — the error envelope.</li>
 * </ul>
 * Commands that need extra fields (key, screenshot path, coordinates) build
 * their own object with {@link Json#quoted} rather than overloading this class.
 */
public final class Results {

    public static final String AGENT_NAME    = "ebs-dom-agent";
    public static final String AGENT_VERSION = "0.2.0";

    private Results() { /* static utility */ }

    /** The {@code health} command success response. */
    public static String health() {
        return "{\n"
             + "  \"status\": \"ok\",\n"
             + "  \"command\": \"health\",\n"
             + "  \"agent\": \"" + AGENT_NAME + "\",\n"
             + "  \"version\": \"" + AGENT_VERSION + "\",\n"
             + "  \"message\": \"Agent command executed successfully\"\n"
             + "}";
    }

    /**
     * Success envelope: {@code {"status":"ok","command":..,"component":..}}.
     *
     * @param command       command name
     * @param componentJson a pre-rendered JSON object/array, or {@code null}
     *                      (rendered as the JSON literal {@code null})
     */
    public static String ok(String command, String componentJson) {
        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"status\":\"ok\",");
        sb.append("\"command\":").append(Json.quoted(command)).append(',');
        sb.append("\"component\":").append(componentJson == null ? "null" : componentJson);
        sb.append('}');
        return sb.toString();
    }

    /**
     * Error envelope. When {@code cause} is non-null an {@code "error"} block
     * with type, message, and stack trace is included.
     */
    public static String error(String command, String message, Throwable cause) {
        String cmdEsc = Json.escape(command != null ? command : "unknown");
        String msgEsc = Json.escape(message != null ? message : "An unexpected error occurred");

        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"error\",\n");
        sb.append("  \"command\": \"").append(cmdEsc).append("\",\n");
        sb.append("  \"message\": \"").append(msgEsc).append("\"");

        if (cause != null) {
            String typeEsc  = Json.escape(cause.getClass().getName());
            String cmsgEsc  = Json.escape(cause.getMessage() != null ? cause.getMessage() : "");
            String traceEsc = Json.escape(stackTrace(cause));

            sb.append(",\n");
            sb.append("  \"error\": {\n");
            sb.append("    \"type\": \"").append(typeEsc).append("\",\n");
            sb.append("    \"message\": \"").append(cmsgEsc).append("\",\n");
            sb.append("    \"stackTrace\": \"").append(traceEsc).append("\"\n");
            sb.append("  }");
        }

        sb.append("\n}");
        return sb.toString();
    }

    private static String stackTrace(Throwable t) {
        StringWriter sw = new StringWriter(512);
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }
}
