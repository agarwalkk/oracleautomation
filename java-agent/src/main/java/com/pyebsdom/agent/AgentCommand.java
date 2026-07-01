package com.pyebsdom.agent;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A parsed agent invocation — both the immutable command model and its parser.
 *
 * <h3>Wire format (unchanged contract)</h3>
 * <pre>
 *   command=&lt;name&gt;;out=&lt;path&gt;[;&lt;key&gt;=&lt;value&gt;...]
 * </pre>
 * <ul>
 *   <li>Pairs are separated by semicolons; key and value by the <em>first</em>
 *       {@code =} so values containing {@code =} (e.g. Base64) are preserved.</li>
 *   <li>Keys are lower-cased and trimmed.</li>
 *   <li>{@code command} and {@code out} are required.</li>
 *   <li>Empty segments ({@code ;;}) are skipped.</li>
 * </ul>
 *
 * <p>The Python clients ({@code qcs_java_agent}, {@code qcs_replay}) depend on
 * this format and on lower-cased keys, so it is intentionally identical to the
 * original parser.
 */
public final class AgentCommand {

    private final String command;
    private final String outputFile;
    private final Map<String, String> params;

    private AgentCommand(String command, String outputFile, Map<String, String> params) {
        this.command = command;
        this.outputFile = outputFile;
        this.params = Collections.unmodifiableMap(params);
    }

    // ── Accessors ─────────────────────────────────────────────────────────

    /** The {@code command} value (already lower-cased). */
    public String getCommand() {
        return command;
    }

    /** The {@code out} value — absolute path for the JSON result file. */
    public String getOutputFile() {
        return outputFile;
    }

    /** All parsed key=value pairs (keys lower-cased), including command and out. */
    public Map<String, String> getParams() {
        return params;
    }

    /** Value for {@code key}, or {@code null}. Keys are stored lower-cased. */
    public String getParam(String key) {
        return params.get(key);
    }

    /** Value for {@code key}, or {@code defaultValue} when absent. */
    public String getParam(String key, String defaultValue) {
        String v = params.get(key);
        return v != null ? v : defaultValue;
    }

    /** Integer value for {@code key}, or {@code defaultValue} if absent/invalid. */
    public int getIntParam(String key, int defaultValue) {
        String raw = params.get(key);
        if (raw == null || raw.trim().isEmpty()) {
            return defaultValue;
        }
        try {
            return Integer.parseInt(raw.trim());
        } catch (Exception ignored) {
            return defaultValue;
        }
    }

    @Override
    public String toString() {
        return "AgentCommand{command='" + command + "', out='" + outputFile + "', params=" + params + '}';
    }

    // ── Parsing ───────────────────────────────────────────────────────────

    /**
     * Parses {@code agentArgs} into an {@link AgentCommand}.
     *
     * @throws IllegalArgumentException if the string is null/empty, a segment is
     *         malformed, or a required field ({@code command}/{@code out}) is absent
     */
    public static AgentCommand parse(String agentArgs) {
        if (agentArgs == null || agentArgs.trim().isEmpty()) {
            throw new IllegalArgumentException(
                    "agentArgs is null or empty. Expected: command=<name>;out=<path>");
        }

        Map<String, String> params = new LinkedHashMap<>();
        for (String segment : agentArgs.split(";", -1)) {
            String s = segment.trim();
            if (s.isEmpty()) continue;

            int eq = s.indexOf('=');
            if (eq <= 0) {
                throw new IllegalArgumentException(
                        "Malformed segment (expected key=value): '" + s + "'"
                        + "  in agentArgs: " + agentArgs);
            }
            String key = s.substring(0, eq).trim().toLowerCase();
            String value = s.substring(eq + 1).trim();
            if (key.isEmpty()) {
                throw new IllegalArgumentException("Empty key in segment: '" + s + "'");
            }
            params.put(key, value);
        }

        if (!params.containsKey("command")) {
            throw new IllegalArgumentException(
                    "Required field 'command' is missing in agentArgs: " + agentArgs);
        }
        if (!params.containsKey("out")) {
            throw new IllegalArgumentException(
                    "Required field 'out' is missing in agentArgs: " + agentArgs);
        }

        String command = params.get("command");
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
     * Best-effort extraction of {@code out} from a possibly-unparseable argument
     * string — used during error handling so an error file can still be written.
     */
    public static String extractOutParam(String agentArgs) {
        if (agentArgs == null) return null;
        for (String segment : agentArgs.split(";", -1)) {
            int eq = segment.indexOf('=');
            if (eq > 0) {
                String key = segment.substring(0, eq).trim().toLowerCase();
                String value = segment.substring(eq + 1).trim();
                if ("out".equals(key) && !value.isEmpty()) return value;
            }
        }
        return null;
    }
}
