package com.pyebsdom.agent;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Parses the agent argument string into an {@link AgentCommand}.
 *
 * <h3>Format</h3>
 * <pre>
 *   command=&lt;name&gt;;out=&lt;path&gt;[;&lt;key&gt;=&lt;value&gt;...]
 * </pre>
 *
 * <ul>
 *   <li>Pairs are separated by semicolons ({@code ;}).</li>
 *   <li>Key and value are separated by the <em>first</em> {@code =} sign so
 *       that values containing {@code =} (e.g. Base64 strings) are preserved.</li>
 *   <li>Keys are converted to lower-case; surrounding whitespace is trimmed.</li>
 *   <li>{@code command} and {@code out} are required; all other keys are
 *       optional and command-specific.</li>
 *   <li>Empty segments (consecutive {@code ;;}) are silently skipped.</li>
 * </ul>
 *
 * <h3>Examples</h3>
 * <pre>
 *   command=health;out=C:\Temp\pyebsdom-health.json
 *   command=get-field;out=/tmp/result.json;locator=form.ITEM_NUM
 * </pre>
 */
public final class AgentCommandParser {

    private AgentCommandParser() { /* static utility */ }

    /**
     * Parses {@code agentArgs} and returns an {@link AgentCommand}.
     *
     * @param agentArgs the raw agent argument string passed by the Attach API
     * @return a fully populated {@link AgentCommand}
     * @throws IllegalArgumentException if the string is null/empty, any
     *         segment is malformed, or a required field is absent
     */
    public static AgentCommand parse(String agentArgs) {
        if (agentArgs == null || agentArgs.trim().isEmpty()) {
            throw new IllegalArgumentException(
                    "agentArgs is null or empty. Expected: command=<name>;out=<path>");
        }

        Map<String, String> params = new LinkedHashMap<>();

        String[] segments = agentArgs.split(";", -1);
        for (String segment : segments) {
            String s = segment.trim();
            if (s.isEmpty()) continue;          // skip blanks between delimiters

            int eq = s.indexOf('=');
            if (eq <= 0) {
                throw new IllegalArgumentException(
                        "Malformed segment (expected key=value): '" + s + "'"
                        + "  in agentArgs: " + agentArgs);
            }

            String key   = s.substring(0, eq).trim().toLowerCase();
            String value = s.substring(eq + 1).trim();

            if (key.isEmpty()) {
                throw new IllegalArgumentException(
                        "Empty key in segment: '" + s + "'");
            }

            params.put(key, value);
        }

        // ── Required field validation ────────────────────────────────────
        if (!params.containsKey("command")) {
            throw new IllegalArgumentException(
                    "Required field 'command' is missing in agentArgs: " + agentArgs);
        }
        if (!params.containsKey("out")) {
            throw new IllegalArgumentException(
                    "Required field 'out' is missing in agentArgs: " + agentArgs);
        }

        String command    = params.get("command");
        String outputFile = params.get("out");

        if (command.isEmpty()) {
            throw new IllegalArgumentException("'command' value must not be empty");
        }
        if (outputFile.isEmpty()) {
            throw new IllegalArgumentException("'out' value must not be empty");
        }

        return new AgentCommand(command, outputFile, params);
    }

    /**
     * Best-effort extraction of the {@code out} value from a raw, potentially
     * unparseable argument string.  Used as a fallback in error handling to
     * write the error JSON even when full parsing failed.
     *
     * @return the value, or {@code null} if not found
     */
    public static String extractOutParam(String agentArgs) {
        if (agentArgs == null) return null;
        for (String segment : agentArgs.split(";", -1)) {
            int eq = segment.indexOf('=');
            if (eq > 0) {
                String key   = segment.substring(0, eq).trim().toLowerCase();
                String value = segment.substring(eq + 1).trim();
                if ("out".equals(key) && !value.isEmpty()) {
                    return value;
                }
            }
        }
        return null;
    }
}
