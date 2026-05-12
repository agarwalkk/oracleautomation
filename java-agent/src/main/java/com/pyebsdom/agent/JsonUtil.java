package com.pyebsdom.agent;

import java.io.PrintWriter;
import java.io.StringWriter;

/**
 * Produces the JSON payloads written by the agent to the output file.
 *
 * <p>No external JSON library is used so the agent JAR stays small and avoids
 * any classloader conflicts inside the target JVM.  All string values are
 * escaped via {@link #escapeJson(String)} before being embedded in literals.
 */
public final class JsonUtil {

    static final String AGENT_NAME    = "ebs-dom-agent";
    static final String AGENT_VERSION = "0.1.0";

    private JsonUtil() { /* static utility */ }

    // ── Convenience quoted-string helper ──────────────────────────────────

    /**
     * Returns {@code value} as a JSON double-quoted string (including the
     * surrounding quotes), or {@code "null"} if {@code value} is {@code null}.
     *
     * <p>This is the companion to {@link #escapeJson(String)} that adds the
     * enclosing quotes, for use in hand-crafted JSON builders throughout the
     * agent code (e.g. {@link DomNode}, {@link LocatorCandidate}).
     */
    public static String quoted(String value) {
        if (value == null) return "null";
        return '"' + escapeJson(value) + '"';
    }

    // ── Success payloads ──────────────────────────────────────────────────

    /**
     * Builds the {@code health} command success response:
     * <pre>
     * {
     *   "status": "ok",
     *   "command": "health",
     *   "agent": "ebs-dom-agent",
     *   "version": "0.1.0",
     *   "message": "Agent command executed successfully"
     * }
     * </pre>
     */
    public static String healthResult() {
        return "{\n"
             + "  \"status\": \"ok\",\n"
             + "  \"command\": \"health\",\n"
             + "  \"agent\": \"" + AGENT_NAME + "\",\n"
             + "  \"version\": \"" + AGENT_VERSION + "\",\n"
             + "  \"message\": \"Agent command executed successfully\"\n"
             + "}";
    }

    // ── Error payload ─────────────────────────────────────────────────────

    /**
     * Builds an error response envelope.
     *
     * <p>If {@code cause} is non-null the {@code "error"} block is included:
     * <pre>
     * {
     *   "status": "error",
     *   "command": "...",
     *   "message": "...",
     *   "error": {
     *     "type": "...",
     *     "message": "...",
     *     "stackTrace": "..."
     *   }
     * }
     * </pre>
     *
     * @param command the command name, or {@code "unknown"} if unavailable
     * @param message a human-readable description of the failure
     * @param cause   the originating exception, or {@code null}
     */
    public static String errorResult(String command, String message, Throwable cause) {
        String cmdEsc = escapeJson(command  != null ? command  : "unknown");
        String msgEsc = escapeJson(message  != null ? message  : "An unexpected error occurred");

        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"error\",\n");
        sb.append("  \"command\": \"").append(cmdEsc).append("\",\n");
        sb.append("  \"message\": \"").append(msgEsc).append("\"");

        if (cause != null) {
            String typeEsc  = escapeJson(cause.getClass().getName());
            String cmsgEsc  = escapeJson(cause.getMessage() != null ? cause.getMessage() : "");
            String traceEsc = escapeJson(stackTraceToString(cause));

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

    // ── Helpers ───────────────────────────────────────────────────────────

    /**
     * Converts a {@link Throwable}'s full stack trace to a single string.
     */
    private static String stackTraceToString(Throwable t) {
        StringWriter sw = new StringWriter(512);
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }

    /**
     * Escapes a string for safe embedding inside a JSON double-quoted value.
     * Handles all RFC 8259 mandatory escape sequences.
     *
     * @param s the raw string; {@code null} returns an empty string
     * @return the escaped string (without surrounding quotes)
     */
    public static String escapeJson(String s) {
        if (s == null) return "";
        StringBuilder out = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b");  break;
                case '\f': out.append("\\f");  break;
                case '\n': out.append("\\n");  break;
                case '\r': out.append("\\r");  break;
                case '\t': out.append("\\t");  break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}
